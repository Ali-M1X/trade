"""Support/resistance levels: pivot clusters, previous period highs/lows, round numbers."""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import pandas as pd

from .swings import Swing


@dataclass
class Level:
    price: float
    touches: int
    timeframe: str
    kind: str = "cluster"        # cluster | prev_high | prev_low | round
    strength: float = 0.0
    members: list[float] = field(default_factory=list)


def cluster_levels(swings: list[Swing], atr_value: float, tol_atr: float, min_touches: int,
                   timeframe: str, tf_weight: dict) -> list[Level]:
    """Group pivot prices closer than tol_atr * ATR (to the running cluster mean).
    Keep clusters with at least `min_touches`. Strength = touches + timeframe weight."""
    if not swings or not atr_value or math.isnan(atr_value):
        return []
    tol = tol_atr * atr_value
    prices = sorted(s.price for s in swings)
    clusters: list[list[float]] = [[prices[0]]]
    for p in prices[1:]:
        cur = clusters[-1]
        if abs(p - sum(cur) / len(cur)) < tol:
            cur.append(p)
        else:
            clusters.append([p])
    out = []
    for c in clusters:
        if len(c) >= min_touches:
            out.append(Level(sum(c) / len(c), len(c), timeframe, "cluster",
                             len(c) + tf_weight.get(timeframe, 0), c))
    return out


def previous_period_levels(df: pd.DataFrame, timeframe: str, tf_weight: dict) -> list[Level]:
    """High and low of the previous closed candle of a D or W frame (last row = current)."""
    if len(df) < 2:
        return []
    prev = df.iloc[-2]
    w = tf_weight.get(timeframe, 0)
    return [Level(float(prev["high"]), 1, timeframe, "prev_high", 1 + w),
            Level(float(prev["low"]), 1, timeframe, "prev_low", 1 + w)]


def round_step(price: float) -> float:
    """Half of the price's order of magnitude: 142 -> 50, 0.53 -> 0.05, 83700 -> 5000."""
    return 10 ** math.floor(math.log10(price)) / 2


def round_levels(price: float, count: int = 2) -> list[Level]:
    """`count` round numbers below and above the price."""
    step = round_step(price)
    base = math.floor(price / step) * step
    prices = [base - i * step for i in range(count)] + [base + (i + 1) * step for i in range(count)]
    return [Level(round(p, 12), 0, "round", "round", 0.0) for p in sorted(prices) if p > 0]


def nearest(levels: list[Level], price: float) -> tuple[Level | None, Level | None]:
    """Closest level below (support) and above (resistance) the price."""
    below = [l for l in levels if l.price < price]
    above = [l for l in levels if l.price > price]
    return (max(below, key=lambda l: l.price) if below else None,
            min(above, key=lambda l: l.price) if above else None)
