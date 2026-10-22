"""Market regime from prices: bull / range / bear (2026-10-22).

The dominance regime (regime.py) needs USDT.D, BTC.D and TOTAL2, which only have a year of
history. This one uses BTC and an equal-weight index of large alts, which exist for many
years, so it can be backtested over several cycles (scripts/price_regime.py compares the
definitions over 2019 onwards).

The market is bull or bear only when BTC and the alt index agree; otherwise range. A new
state must hold `hold_days` daily closes before the regime switches. Everything here works
on closed daily candles; the caller labels day d with the state at the close of d-1.

Definitions (daily closes):
  trend_adx  ADX(14) >= adx_min is a trend, its side from close vs MA(ma); else range
  ma200      bull: close > MA200 and MA50 > MA200; bear: both below; else range
  weinstein  weekly close vs the 30-week MA and its 4-week slope (Stage 2 / Stage 4)
"""
from __future__ import annotations

import numpy as np
import pandas as pd

DAY = 86_400_000
NAMES = {1: "bull", 0: "range", -1: "bear"}


def adx(df: pd.DataFrame, n: int = 14) -> pd.Series:
    up, dn = df["high"].diff(), -df["low"].diff()
    plus = np.where((up > dn) & (up > 0), up, 0.0)
    minus = np.where((dn > up) & (dn > 0), dn, 0.0)
    tr = pd.concat([df["high"] - df["low"], (df["high"] - df["close"].shift()).abs(),
                    (df["low"] - df["close"].shift()).abs()], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1 / n, adjust=False).mean()
    pdi = 100 * pd.Series(plus, index=df.index).ewm(alpha=1 / n, adjust=False).mean() / atr
    mdi = 100 * pd.Series(minus, index=df.index).ewm(alpha=1 / n, adjust=False).mean() / atr
    dx = 100 * (pdi - mdi).abs() / (pdi + mdi)
    return dx.ewm(alpha=1 / n, adjust=False).mean()


def raw_state(df: pd.DataFrame, how: str, p: dict | None = None) -> pd.Series:
    """+1 / 0 / -1 per daily close (index = day open ts); NaN while warming up."""
    p = p or {}
    c = df["close"]
    if how == "trend_adx":
        ma, a = c.rolling(p.get("ma", 100)).mean(), adx(df, p.get("adx_period", 14))
        s = np.where(a >= p.get("adx_min", 20), np.where(c > ma, 1, -1), 0)
        ok = ma.notna()
    elif how == "ma200":
        m50, m200 = c.rolling(50).mean(), c.rolling(200).mean()
        s = np.where((c > m200) & (m50 > m200), 1, np.where((c < m200) & (m50 < m200), -1, 0))
        ok = m200.notna()
    elif how == "weinstein":
        w = c.groupby(c.index // (7 * DAY)).last()
        ma = w.rolling(30).mean()
        slope = ma - ma.shift(4)
        ws = pd.Series(np.where((w > ma) & (slope > 0), 1, np.where((w < ma) & (slope < 0), -1, 0)),
                       index=w.index).where(ma.notna() & slope.notna())
        # a weekly state is known once the week closed: it applies from the next week
        return pd.Series((c.index // (7 * DAY) - 1).map(ws), index=c.index)
    else:
        raise ValueError(f"unknown price regime definition {how!r}")
    return pd.Series(s, index=c.index).where(ok)


def hold(states: pd.Series, days: int) -> pd.Series:
    """A new state replaces the current one only after `days` closes in a row."""
    out, cur, pending, count = [], None, None, 0
    for v in states:
        if np.isnan(v):
            out.append(np.nan)
            continue
        if cur is None:
            cur = v
        elif v != cur:
            pending, count = (v, count + 1) if v == pending else (v, 1)
            if count >= days:
                cur, count = v, 0
        else:
            count = 0
        out.append(cur)
    return pd.Series(out, index=states.index, dtype=float)


def market_states(btc: pd.DataFrame, alts: pd.DataFrame, p: dict) -> pd.DataFrame:
    """Per day (index = day open ts): `state` (+1/0/-1 after hold, known at that day's close)
    and `alts` (the alt index's own state, for TOTAL2-like direction)."""
    how = p.get("definition", "trend_adx")
    b = raw_state(btc, how, p)
    a = raw_state(alts, how, p).reindex(btc.index)
    both = pd.Series(np.where(b == a, b, 0), index=btc.index).where(b.notna() & a.notna())
    return pd.DataFrame({"state": hold(both, p.get("hold_days", 3)), "alts": a})


def alt_index(frames: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Equal-weight index of daily candles (index = day open ts): the average log return of
    the coins that trade that day; high/low from their average relative range."""
    rets = pd.DataFrame({b: np.log(f["close"]).diff() for b, f in frames.items()}).sort_index()
    hi = pd.DataFrame({b: np.log(f["high"] / f["close"]) for b, f in frames.items()}).sort_index()
    lo = pd.DataFrame({b: np.log(f["low"] / f["close"]) for b, f in frames.items()}).sort_index()
    close = np.exp(rets.mean(axis=1, skipna=True).fillna(0).cumsum()) * 100
    return pd.DataFrame({"close": close, "high": close * np.exp(hi.mean(axis=1)),
                         "low": close * np.exp(lo.mean(axis=1))})
