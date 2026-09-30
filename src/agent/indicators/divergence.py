"""Regular divergence between price swings and an oscillator (RSI, MACD histogram)."""
from __future__ import annotations

import math

import pandas as pd

from .swings import Swing


def divergence(swings: list[Swing], osc: pd.Series) -> int:
    """Compare the last two swing highs and the last two swing lows.

    -1 bearish: price higher high, oscillator lower high.
    +1 bullish: price lower low, oscillator higher low.
     0 none. If both exist, the more recent pair wins.
    """
    values = osc.to_numpy()
    found = []
    for kind, sign in (("H", -1), ("L", 1)):
        pts = [s for s in swings if s.kind == kind][-2:]
        if len(pts) < 2:
            continue
        a, b = pts
        oa, ob = values[a.idx], values[b.idx]
        if math.isnan(oa) or math.isnan(ob):
            continue
        if kind == "H" and b.price > a.price and ob < oa:
            found.append((b.idx, sign))
        if kind == "L" and b.price < a.price and ob > oa:
            found.append((b.idx, sign))
    return max(found)[1] if found else 0
