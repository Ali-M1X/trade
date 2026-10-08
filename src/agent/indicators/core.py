"""Classic indicators on OHLCV frames (columns: ts, open, high, low, close, volume).

All functions are causal: the value at bar i only uses bars <= i, so the same
code serves live runs and backtests.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def sma(series: pd.Series, n: int) -> pd.Series:
    return series.rolling(n, min_periods=n).mean()


def ema(series: pd.Series, n: int) -> pd.Series:
    return series.ewm(span=n, adjust=False, min_periods=n).mean()


def _wilder(values: np.ndarray, n: int, first: int) -> np.ndarray:
    """Wilder smoothing: seed with the mean of `n` values starting at `first`, then
    avg = (prev * (n - 1) + x) / n."""
    out = np.full(len(values), np.nan)
    seed_end = first + n
    if len(values) < seed_end:
        return out
    out[seed_end - 1] = values[first:seed_end].mean()
    for i in range(seed_end, len(values)):
        out[i] = (out[i - 1] * (n - 1) + values[i]) / n
    return out


def rsi(close: pd.Series, n: int) -> pd.Series:
    """Wilder RSI. First value at bar n (needs n price changes)."""
    diff = close.diff().to_numpy()
    gains = np.where(diff > 0, diff, 0.0)
    losses = np.where(diff < 0, -diff, 0.0)
    avg_gain = _wilder(gains, n, 1)
    avg_loss = _wilder(losses, n, 1)
    with np.errstate(divide="ignore", invalid="ignore"):
        rs = avg_gain / avg_loss
        out = 100 - 100 / (1 + rs)
    out = np.where((avg_loss == 0) & ~np.isnan(avg_gain), 100.0, out)
    return pd.Series(out, index=close.index)


def true_range(df: pd.DataFrame) -> pd.Series:
    prev_close = df["close"].shift()
    tr = pd.concat([df["high"] - df["low"],
                    (df["high"] - prev_close).abs(),
                    (df["low"] - prev_close).abs()], axis=1).max(axis=1)
    tr.iloc[0] = df["high"].iloc[0] - df["low"].iloc[0]
    return tr


def atr(df: pd.DataFrame, n: int) -> pd.Series:
    """Wilder ATR, seeded with the mean of the first n true ranges."""
    return pd.Series(_wilder(true_range(df).to_numpy(), n, 0), index=df.index)


def adx(df: pd.DataFrame, n: int) -> pd.Series:
    """Wilder ADX: smoothed +DM / -DM over the smoothed true range, then the smoothed DX."""
    up, down = df["high"].diff(), -df["low"].diff()
    plus = np.where((up > down) & (up > 0), up, 0.0)
    minus = np.where((down > up) & (down > 0), down, 0.0)
    a = 1 / n
    tr = true_range(df).ewm(alpha=a, adjust=False).mean()
    pdi = 100 * pd.Series(plus, index=df.index).ewm(alpha=a, adjust=False).mean() / tr
    mdi = 100 * pd.Series(minus, index=df.index).ewm(alpha=a, adjust=False).mean() / tr
    dx = (100 * (pdi - mdi).abs() / (pdi + mdi)).fillna(0.0)
    out = dx.ewm(alpha=a, adjust=False).mean()
    out.iloc[:2 * n] = np.nan                           # warm-up
    return out


def macd(close: pd.Series, fast: int, slow: int, signal: int) -> pd.DataFrame:
    line = ema(close, fast) - ema(close, slow)
    sig = line.ewm(span=signal, adjust=False, min_periods=signal).mean()
    return pd.DataFrame({"macd": line, "signal": sig, "hist": line - sig})


def rvol(volume: pd.Series, n: int) -> pd.Series:
    """Volume divided by the mean volume of the previous n bars (current bar excluded)."""
    return volume / volume.shift().rolling(n, min_periods=n).mean()


def obv(df: pd.DataFrame) -> pd.Series:
    direction = np.sign(df["close"].diff().fillna(0))
    return (direction * df["volume"]).cumsum()


def bollinger(close: pd.Series, n: int, k: float) -> pd.DataFrame:
    mid = sma(close, n)
    std = close.rolling(n, min_periods=n).std(ddof=0)
    upper, lower = mid + k * std, mid - k * std
    return pd.DataFrame({"mid": mid, "upper": upper, "lower": lower,
                         "width": (upper - lower) / mid})


def percentile_rank(series: pd.Series, lookback: int) -> pd.Series:
    """Percent of the last `lookback` values (current included) that are <= the current value."""
    def rank(w: np.ndarray) -> float:
        return (w <= w[-1]).mean() * 100
    return series.rolling(lookback, min_periods=lookback).apply(rank, raw=True)


def slope_sign(series: pd.Series, bars: int) -> pd.Series:
    """+1 / -1 / 0: the value now compared with `bars` bars ago."""
    return np.sign(series - series.shift(bars))


def add_indicators(df: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """Return a copy of df with the shared indicator columns from config."""
    c = cfg["indicators"]
    out = df.copy()
    for key in ("ma_fast", "ma_mid", "ma_slow"):
        out[f"ma{c[key]}"] = sma(out["close"], c[key])
    out["rsi"] = rsi(out["close"], c["rsi_period"])
    m = macd(out["close"], c["macd_fast"], c["macd_slow"], c["macd_signal"])
    out[["macd", "macd_signal", "macd_hist"]] = m.to_numpy()
    out["atr"] = atr(out, c["atr_period"])
    out["rvol"] = rvol(out["volume"], c["rvol_lookback"])
    out["obv"] = obv(out)
    out[f"ema{c.get('ema_impulse', 13)}"] = ema(out["close"], c.get("ema_impulse", 13))
    out["adx"] = adx(out, c.get("adx_period", 14))
    out["bb_width"] = bollinger(out["close"], c["bollinger_period"], c["bollinger_std"])["width"]
    return out
