"""Dow structure (HH/HL, LH/LL), BOS/CHoCH events and the combined direction (+1/0/-1)."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from ..indicators.core import sma
from ..indicators.swings import Swing, known_at, swings_for


def dow_trend(swings: list[Swing]) -> int:
    """+1 when the last two highs and last two lows both rise (HH + HL),
    -1 when both fall (LH + LL), otherwise 0."""
    highs = [s.price for s in swings if s.kind == "H"][-2:]
    lows = [s.price for s in swings if s.kind == "L"][-2:]
    if len(highs) < 2 or len(lows) < 2:
        return 0
    if highs[1] > highs[0] and lows[1] > lows[0]:
        return 1
    if highs[1] < highs[0] and lows[1] < lows[0]:
        return -1
    return 0


@dataclass(frozen=True)
class StructureEvent:
    idx: int
    kind: str         # "BOS" (with the trend) or "CHoCH" (first break against it)
    direction: int    # +1 broke a swing high, -1 broke a swing low
    level: float


def structure_events(df: pd.DataFrame, swings: list[Swing]) -> list[StructureEvent]:
    """Walk the bars. A close above the latest confirmed swing high is a break up,
    below the latest confirmed swing low a break down. A break against the current
    state is a CHoCH, otherwise a BOS. Each swing can be broken once."""
    close = df["close"].to_numpy()
    by_confirm = sorted(swings, key=lambda s: s.confirmed)
    k = 0
    high = low = None
    state = 0
    out: list[StructureEvent] = []
    for i in range(len(close)):
        while k < len(by_confirm) and by_confirm[k].confirmed <= i:
            s = by_confirm[k]
            if s.kind == "H":
                high = s
            else:
                low = s
            k += 1
        if high is not None and close[i] > high.price:
            out.append(StructureEvent(i, "CHoCH" if state == -1 else "BOS", 1, high.price))
            state, high = 1, None
        elif low is not None and close[i] < low.price:
            out.append(StructureEvent(i, "CHoCH" if state == 1 else "BOS", -1, low.price))
            state, low = -1, None
    return out


def direction(dow: int, close: float, ma_mid: float, ma_slow: float, min_votes: int) -> int:
    """Combine Dow structure with the price position against MA25 and MA99.
    votes = dow + sign(close - MA25) + sign(close - MA99); up if >= min_votes, down if <= -min_votes."""
    if np.isnan(ma_mid) or np.isnan(ma_slow):
        return dow
    votes = dow + int(np.sign(close - ma_mid)) + int(np.sign(close - ma_slow))
    if votes >= min_votes:
        return 1
    if votes <= -min_votes:
        return -1
    return 0


@dataclass
class Structure:
    swings: list[Swing]
    events: list[StructureEvent]
    dow: int
    direction: int

    @property
    def last_event(self) -> StructureEvent | None:
        return self.events[-1] if self.events else None


def analyze(df: pd.DataFrame, timeframe: str, cfg: dict, bar: int | None = None) -> Structure:
    """Structure as known at `bar` (default: the last bar)."""
    bar = len(df) - 1 if bar is None else bar
    view = df.iloc[:bar + 1]
    swings = known_at(swings_for(view, timeframe, cfg), bar)
    events = structure_events(view, swings)
    ind = cfg["indicators"]
    close = view["close"]
    dow = dow_trend(swings)
    d = direction(dow, float(close.iloc[-1]),
                  float(sma(close, ind["ma_mid"]).iloc[-1]),
                  float(sma(close, ind["ma_slow"]).iloc[-1]),
                  cfg["structure"]["direction_min_votes"])
    return Structure(swings, events, dow, d)
