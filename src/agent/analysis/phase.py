"""Market phase: TREND_UP / TREND_DOWN / ACC / DIST / RANGE, and reversal signs."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from ..indicators.divergence import divergence
from . import candles as cd
from .frame import Frame

DIRECTION = {"TREND_UP": 1, "ACC": 1, "TREND_DOWN": -1, "DIST": -1, "RANGE": 0}


@dataclass(frozen=True)
class Phase:
    name: str
    event: str | None = None   # spring | breakout (ACC), upthrust | breakdown (DIST)

    @property
    def direction(self) -> int:
        return DIRECTION[self.name]


def detect_phase(f: Frame, cfg: dict) -> Phase:
    p = cfg["technical"]["phase"]
    ind = cfg["indicators"]
    fast, mid, slow = (f"ma{ind[k]}" for k in ("ma_fast", "ma_mid", "ma_slow"))
    df, last, atr = f.df, f.last, f.atr
    if f.n <= p["ma_slope_bars"] or np.isnan(atr):
        return Phase("RANGE")
    slope = df[mid].iloc[-1] - df[mid].iloc[-1 - p["ma_slope_bars"]]
    c = last["close"]
    if c > last[mid] > last[slow] and slope > 0 and f.dow == 1:
        return Phase("TREND_UP")
    if c < last[mid] < last[slow] and slope < 0 and f.dow == -1:
        return Phase("TREND_DOWN")

    n, k = p["acc_min_bars"], p["spring_bars"]
    if f.n < n + 1 or np.isnan(slope) or np.isnan(last[slow]):
        return Phase("RANGE")
    window = df.iloc[-n - 1:-1]              # the range, current bar excluded
    hi, lo = window["high"].max(), window["low"].min()
    # flat and intertwined: MA25 barely moving and MA7 wrapped around it
    flat = abs(slope) < p["flat_ma_atr"] * atr and abs(last[fast] - last[mid]) < p["flat_ma_atr"] * atr
    if hi - lo >= p["acc_max_width_atr"] * atr or not flat:
        return Phase("RANGE")
    # the preceding trend: a range below MA99 follows a decline, above it a rally
    prior = np.sign(window["close"].mean() - last[slow])
    green = window.loc[window["close"] > window["open"], "rvol"].mean()
    red = window.loc[window["close"] < window["open"], "rvol"].mean()
    green, red = np.nan_to_num(green), np.nan_to_num(red)
    base, recent = df.iloc[-n - 1:-k], df.iloc[-k:]
    sup, res = base["low"].min(), base["high"].max()
    spring = bool(((recent["low"] < sup) & (recent["close"] > sup)).any())
    upthrust = bool(((recent["high"] > res) & (recent["close"] < res)).any())
    if prior < 0 and green > red:
        return Phase("ACC", "breakout" if c > hi else "spring" if spring else None)
    if prior > 0 and red > green:
        return Phase("DIST", "breakdown" if c < lo else "upthrust" if upthrust else None)
    return Phase("RANGE")


def climax(f: Frame, trend: int, cfg: dict, bars: int = 3) -> bool:
    """RVOL above climax_rvol in the last `bars` bars with a long wick in the trend
    direction (upper wick >= body in an up-trend, lower wick in a down-trend)."""
    v = cfg["technical"]["volume"]
    for _, c in f.df.iloc[-bars:].iterrows():
        wick = cd.upper_wick(c) if trend == 1 else cd.lower_wick(c)
        if c["rvol"] > v["climax_rvol"] and wick > 0 and wick >= cd.body(c):
            return True
    return False


def reversal_signs(f: Frame, trend: int, cfg: dict) -> list[str]:
    """Signs that the current `trend` is ending: CHoCH against it, RSI or MACD divergence
    against it, a volume climax, a close on the wrong side of MA99."""
    ma_slow = f"ma{cfg['indicators']['ma_slow']}"
    signs = []
    ev = f.last_event
    if ev and ev.kind == "CHoCH" and ev.direction == -trend:
        signs.append("choch")
    if -trend in (divergence(f.swings, f.col("rsi")), divergence(f.swings, f.col("macd_hist"))):
        signs.append("divergence")
    if climax(f, trend, cfg):
        signs.append("climax")
    if (f.close - f.last[ma_slow]) * trend < 0:
        signs.append("ma99")
    return signs
