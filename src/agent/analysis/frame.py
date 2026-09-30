"""A timeframe prepared for analysis: candles with indicators, swings and structure,
all as known at the last row (the evaluation bar)."""
from __future__ import annotations

import math
from dataclasses import dataclass

import pandas as pd

from ..indicators.core import add_indicators
from ..indicators.swings import Swing, swings_for
from .structure import StructureEvent, direction, dow_trend, structure_events


@dataclass
class Frame:
    tf: str
    df: pd.DataFrame
    swings: list[Swing]
    events: list[StructureEvent]
    dow: int
    direction: int

    @property
    def last(self) -> pd.Series:
        return self.df.iloc[-1]

    @property
    def close(self) -> float:
        return float(self.df["close"].iloc[-1])

    @property
    def atr(self) -> float:
        return float(self.df["atr"].iloc[-1])

    @property
    def n(self) -> int:
        return len(self.df)

    def col(self, name: str) -> pd.Series:
        return self.df[name]

    def bars_since(self, idx: int) -> int:
        return self.n - 1 - idx

    @property
    def last_event(self) -> StructureEvent | None:
        return self.events[-1] if self.events else None


def make_frame(df: pd.DataFrame, tf: str, cfg: dict, with_indicators: bool = False) -> Frame:
    """df must end at the evaluation bar. If it has no indicator columns yet, pass
    with_indicators=True (indicators are causal, so they can also be precomputed on a
    longer history and sliced, which is what the backtest does)."""
    if with_indicators or "atr" not in df:
        df = add_indicators(df, cfg)
    df = df.iloc[-cfg["analysis"]["window_bars"]:].reset_index(drop=True)
    swings = swings_for(df, tf, cfg)
    events = structure_events(df, swings)
    ind = cfg["indicators"]
    last = df.iloc[-1]
    dow = dow_trend(swings)
    d = direction(dow, float(last["close"]), float(last[f"ma{ind['ma_mid']}"]),
                  float(last[f"ma{ind['ma_slow']}"]), cfg["structure"]["direction_min_votes"])
    return Frame(tf, df, swings, events, dow, d)


def is_nan(x) -> bool:
    return x is None or (isinstance(x, float) and math.isnan(x))
