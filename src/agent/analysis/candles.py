"""Candle primitives: engulfing, pin bar, strong close, body size in ATR, trend weakening.

`side` is +1 for long, -1 for short throughout.
"""
from __future__ import annotations

import pandas as pd


def body(c) -> float:
    return abs(c["close"] - c["open"])


def upper_wick(c) -> float:
    return c["high"] - max(c["open"], c["close"])


def lower_wick(c) -> float:
    return min(c["open"], c["close"]) - c["low"]


def color(c) -> int:
    return 1 if c["close"] > c["open"] else -1 if c["close"] < c["open"] else 0


def engulfing(prev, cur, side: int) -> bool:
    """Current body opposite in colour to the previous one and covering its whole body."""
    if color(cur) != side or color(prev) != -side:
        return False
    lo, hi = min(prev["open"], prev["close"]), max(prev["open"], prev["close"])
    return min(cur["open"], cur["close"]) <= lo and max(cur["open"], cur["close"]) >= hi and body(cur) > body(prev)


def pin_bar(c, side: int, t: dict) -> bool:
    """Rejection wick (lower for long, upper for short) >= pinbar_wick_body x body and
    >= pinbar_min_wick_range of the range; body <= pinbar_max_body_range of the range;
    opposite wick <= pinbar_max_opposite_range of the range. `t` = technical.candles config."""
    rng = c["high"] - c["low"]
    if rng <= 0:
        return False
    wick, other = (lower_wick(c), upper_wick(c)) if side == 1 else (upper_wick(c), lower_wick(c))
    eps = 1e-12 * rng
    return bool(wick >= t["pinbar_wick_body"] * body(c) - eps
            and wick >= t["pinbar_min_wick_range"] * rng - eps
            and body(c) <= t["pinbar_max_body_range"] * rng + eps
            and other <= t["pinbar_max_opposite_range"] * rng + eps)


def strong_close(c, side: int, pct: float) -> bool:
    """Close in the top pct% of the range for a long (bottom pct% for a short)."""
    rng = c["high"] - c["low"]
    if rng <= 0:
        return False
    pos = (c["close"] - c["low"]) / rng
    return pos >= 1 - pct / 100 if side == 1 else pos <= pct / 100


def body_atr(c, atr_value: float) -> float:
    return body(c) / atr_value if atr_value else float("nan")


def size_class(c, atr_value: float, body_min: float, body_max: float, chase: float) -> str:
    """'small' | 'ok' (body_min..body_max ATR) | 'large' | 'chase' (> chase ATR: don't enter)."""
    r = body_atr(c, atr_value)
    if r > chase:
        return "chase"
    if r > body_max:
        return "large"
    if r >= body_min:
        return "ok"
    return "small"


def trigger_candle(df: pd.DataFrame, i: int, side: int, cfg: dict) -> str | None:
    """Name of the first valid trigger pattern at bar i, or None."""
    t = cfg["technical"]["candles"]
    cur = df.iloc[i]
    if i > 0 and engulfing(df.iloc[i - 1], cur, side):
        return "engulfing"
    if pin_bar(cur, side, t):
        return "pin_bar"
    if color(cur) == side and strong_close(cur, side, t["strong_close_pct"]):
        return "strong_close"
    return None


def weakening(df: pd.DataFrame, i: int, side: int, bars: int) -> bool:
    """`bars` candles in a row in the trend direction ending at i, with shrinking bodies
    and growing wicks against the trend (upper wick in an up-trend)."""
    if i + 1 < bars:
        return False
    w = df.iloc[i - bars + 1:i + 1]
    if any(color(c) != side for _, c in w.iterrows()):
        return False
    bodies = [body(c) for _, c in w.iterrows()]
    wicks = [upper_wick(c) if side == 1 else lower_wick(c) for _, c in w.iterrows()]
    return (all(b2 < b1 for b1, b2 in zip(bodies, bodies[1:]))
            and all(w2 > w1 for w1, w2 in zip(wicks, wicks[1:])))
