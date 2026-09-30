"""Fractal pivots and swing sequences.

A pivot at bar i with `right` candles on its right side is only known at bar
i + right. Every swing carries that `confirmed` index so callers never use a
pivot before it exists.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class Swing:
    idx: int          # bar of the pivot
    confirmed: int    # first bar at which the pivot is known
    price: float
    kind: str         # "H" or "L"


def pivots(df: pd.DataFrame, left: int, right: int) -> list[Swing]:
    """Pivot high: high strictly above the `left` previous highs and >= the `right` next
    highs (so an equal-high plateau yields one pivot, the first bar). Lows mirror it."""
    high, low = df["high"].to_numpy(), df["low"].to_numpy()
    out: list[Swing] = []
    for i in range(left, len(df) - right):
        if high[i] > high[i - left:i].max() and high[i] >= high[i + 1:i + right + 1].max():
            out.append(Swing(i, i + right, float(high[i]), "H"))
        if low[i] < low[i - left:i].min() and low[i] <= low[i + 1:i + right + 1].min():
            out.append(Swing(i, i + right, float(low[i]), "L"))
    out.sort(key=lambda s: (s.idx, s.kind))
    return out


def known_at(swings: list[Swing], bar: int) -> list[Swing]:
    """Swings confirmed on or before `bar`."""
    return [s for s in swings if s.confirmed <= bar]


def alternate(swings: list[Swing]) -> list[Swing]:
    """Force H/L alternation: of consecutive same-kind swings keep the more extreme.
    A bar that is both a pivot high and low keeps whichever continues the alternation."""
    out: list[Swing] = []
    for s in swings:
        if out and out[-1].kind == s.kind:
            better = s.price > out[-1].price if s.kind == "H" else s.price < out[-1].price
            if better:
                out[-1] = s
        elif out and out[-1].idx == s.idx:
            continue  # same bar, opposite kind: keep the first to preserve alternation
        else:
            out.append(s)
    return out


def swings_for(df: pd.DataFrame, timeframe: str, cfg: dict) -> list[Swing]:
    n = cfg["swings"]["pivot_lr"][timeframe]
    return alternate(pivots(df, n, n))


@dataclass(frozen=True)
class Leg:
    start: int
    end: int
    direction: int    # +1 up (L -> H), -1 down (H -> L)
    bars: int
    size: float       # absolute price move


def legs(swings: list[Swing]) -> list[Leg]:
    """Legs between consecutive alternating swings."""
    out = []
    for a, b in zip(swings, swings[1:]):
        out.append(Leg(a.idx, b.idx, 1 if b.kind == "H" else -1, b.idx - a.idx,
                       abs(b.price - a.price)))
    return out


def mean_leg_bars(leg_list: list[Leg], last: int) -> float:
    """Average leg length in bars over the last `last` legs (nan if none)."""
    tail = leg_list[-last:]
    return float(np.mean([l.bars for l in tail])) if tail else float("nan")
