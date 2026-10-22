"""Well-known rule systems for one market regime each (2026-10-22, backtest).

Ali's goal: one system per market type (bull / range / bear). Where the house system loses
in a regime, these published rule sets are tested as replacements. They read closed daily
candles only, trade at the daily close (market order) and hand a TradePlan to the same
signal book and lifecycle as the house system (TP1 closes part and moves the stop to entry,
TP2 another part, the rest trails on the 4H MA25).

  donchian      Turtle-style breakout (Donchian channel; Dennis/Faith, System 2 = 55 days):
                close beyond the prior N-day high (long) / low (short); stop k x ATR.
  ma_pullback   trend pullback (Weinstein Stage 2/4 with an EMA20 retest, as in Minervini's
                and O'Neil's "buy the first pullback"): trend = close vs a rising/falling
                MA150 (~30 weeks); within the last few days price came back to EMA20 and the
                day closes back in the trend direction. Stop beyond the pullback's extreme.
  meanrev       range reversion (Bollinger 20/2 + RSI 14): the day after a close outside a
                band with RSI stretched, a close back inside; TP1 at the middle band, TP2 at
                the opposite band, stop beyond the recent extreme.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .technical import Evaluation
from .trade import TradePlan, size_position

KINDS = ("donchian", "ma_pullback", "meanrev")


def _atr(d: pd.DataFrame, n: int) -> pd.Series:
    tr = pd.concat([d["high"] - d["low"], (d["high"] - d["close"].shift()).abs(),
                    (d["low"] - d["close"].shift()).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / n, adjust=False).mean()


def _rsi(c: pd.Series, n: int = 14) -> pd.Series:
    diff = c.diff()
    up = diff.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-diff.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    return 100 - 100 / (1 + up / dn.replace(0, np.nan))


def _plan(side: int, entry: float, sl: float, tp1: float, tp2: float, atr: float,
          kind: str) -> TradePlan | None:
    risk = (entry - sl) * side
    if risk <= 0 or (tp1 - entry) * side <= 0:
        return None
    tp2 = tp2 if (tp2 - tp1) * side > 0 else tp1 + side * risk
    return TradePlan(side, "market", entry, entry, entry, sl, tp1, tp2,
                     round((tp1 - entry) * side / risk, 3), round((tp2 - entry) * side / risk, 3),
                     False, entry, kind, "1d", 0, atr)


def donchian(d: pd.DataFrame, side: int, p: dict) -> tuple[TradePlan, float] | None:
    n, atr = p.get("days", 55), _atr(d, p.get("atr", 20))
    if len(d) < n + 2:
        return None
    c, a = float(d["close"].iloc[-1]), float(atr.iloc[-1])
    edge = (d["high"] if side == 1 else d["low"]).iloc[-n - 1:-1]
    ref = float(edge.max() if side == 1 else edge.min())
    prev = float(d["close"].iloc[-2])
    # a fresh break: today beyond the channel, yesterday not
    if not ((c - ref) * side > 0 and (prev - ref) * side <= 0):
        return None
    sl = c - side * p.get("stop_atr", 2.0) * a
    r = abs(c - sl)
    plan = _plan(side, c, sl, c + side * p.get("tp1_r", 2.0) * r, c + side * p.get("tp2_r", 4.0) * r,
                 a, "donchian")
    return (plan, (c - ref) * side / a) if plan else None


def ma_pullback(d: pd.DataFrame, side: int, p: dict) -> tuple[TradePlan, float] | None:
    slow_n, look = p.get("slow", 150), p.get("lookback", 3)
    if len(d) < slow_n + 25:
        return None
    c_s, atr = d["close"], _atr(d, 14)
    slow = c_s.rolling(slow_n).mean()
    ema = c_s.ewm(span=p.get("fast", 20), adjust=False).mean()
    c, o, a = float(c_s.iloc[-1]), float(d["open"].iloc[-1]), float(atr.iloc[-1])
    trend = (c - slow.iloc[-1]) * side > 0 and (slow.iloc[-1] - slow.iloc[-21]) * side > 0
    recent = d.iloc[-look:]
    touched = ((recent["low"] <= ema.iloc[-look:]) if side == 1 else (recent["high"] >= ema.iloc[-look:])).any()
    resumed = (c - ema.iloc[-1]) * side > 0 and (c - o) * side > 0
    if not (trend and touched and resumed):
        return None
    extreme = float(recent["low"].min() if side == 1 else recent["high"].max())
    sl = extreme - side * p.get("stop_buffer_atr", 0.5) * a
    r = abs(c - sl)
    if r > p.get("max_stop_atr", 3.0) * a or r < 0.5 * a:
        return None
    plan = _plan(side, c, sl, c + side * p.get("tp1_r", 2.0) * r, c + side * p.get("tp2_r", 3.5) * r,
                 a, "ma_pullback")
    return (plan, float((ema.iloc[-1] - slow.iloc[-1]) * side / a)) if plan else None


def meanrev(d: pd.DataFrame, side: int, p: dict) -> tuple[TradePlan, float] | None:
    n, k = p.get("bb", 20), p.get("bb_k", 2.0)
    if len(d) < n + 15:
        return None
    c_s = d["close"]
    mid, sd = c_s.rolling(n).mean(), c_s.rolling(n).std()
    lower, upper = mid - k * sd, mid + k * sd
    rsi, atr = _rsi(c_s, p.get("rsi", 14)), _atr(d, 14)
    c, prev, a = float(c_s.iloc[-1]), float(c_s.iloc[-2]), float(atr.iloc[-1])
    if side == 1:
        setup = prev < lower.iloc[-2] and rsi.iloc[-2] < p.get("rsi_low", 30) and c > lower.iloc[-1]
    else:
        setup = prev > upper.iloc[-2] and rsi.iloc[-2] > p.get("rsi_high", 70) and c < upper.iloc[-1]
    if not setup:
        return None
    recent = d.iloc[-3:]
    extreme = float(recent["low"].min() if side == 1 else recent["high"].max())
    sl = extreme - side * p.get("stop_buffer_atr", 0.5) * a
    r = abs(c - sl)
    tp1, tp2 = float(mid.iloc[-1]), float(upper.iloc[-1] if side == 1 else lower.iloc[-1])
    if r <= 0 or (tp1 - c) * side < p.get("min_tp1_r", 0.8) * r:
        return None
    plan = _plan(side, c, sl, tp1, tp2, a, "meanrev")
    stretch = (mid.iloc[-2] - prev) * side / a
    return (plan, float(stretch)) if plan else None


SYSTEMS = {"donchian": donchian, "ma_pullback": ma_pullback, "meanrev": meanrev}


def evaluate_classic(base: str, d: pd.DataFrame, side: int, sys: dict, risk_mult: float,
                     cfg: dict) -> tuple[Evaluation, float] | None:
    """An A-grade Evaluation with a sized market plan when the system fires, and its
    strength (to rank coins when the book is nearly full)."""
    hit = SYSTEMS[sys["kind"]](d, side, sys)
    if not hit or risk_mult <= 0:
        return None
    plan, strength = hit
    ev = Evaluation(base, side, score=80.0, grade="A", price=plan.entry, plan=plan,
                    labels=[sys["kind"]])
    size_position(plan, cfg["trade"]["base_risk_pct"] * cfg["grades"]["risk_share"]["A"] * risk_mult, cfg)
    return ev, strength
