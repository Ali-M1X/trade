"""Example evaluations and signals used by the message tests, STRATEGY.md and the PR."""
from agent.layers.technical import Evaluation
from agent.layers.trade import TradePlan

NOW = 1_759_276_800_000            # 2025-10-01 00:00 UTC
ALL_GATES = {"regime": True, "phase": True, "rr": True, "confirmation": True}


def plan(side=1, entry=152.30, sl=148.90, tp1=158.10, tp2=163.20, kind="cluster", tf="4h",
         order="limit", **sized):
    risk = abs(entry - sl)
    return TradePlan(side, order, entry, entry - 0.4, entry + 0.4, sl, tp1, tp2,
                     abs(tp1 - entry) / risk, abs(tp2 - entry) / risk, True, entry, kind, tf, 2,
                     2.0, **sized)


def signal_eval() -> Evaluation:
    """The example from the spec: SOL long, A grade, pullback to 4H support + 1H engulfing."""
    p = plan(risk_pct=1.0, sl_pct=2.23, size_pct=44.8, leverage=10, margin_pct=4.48)
    return Evaluation("SOL", 1, score=82.5, grade="A", gates=ALL_GATES, plan=p,
                      confirmations=["retest", "trigger_candle", "rvol"], trigger_1h="engulfing")


def confirm_eval() -> Evaluation:
    """trade.entry_mode = confirm_4h: the plan is a reference at the level (152.30); SL/TP
    shown are from the level price and are rebuilt at the 4H-confirmed fill."""
    p = plan(order="confirm_4h", risk_pct=1.0, sl_pct=2.23, size_pct=44.8, leverage=10,
             margin_pct=4.48)
    return Evaluation("SOL", 1, score=82.5, grade="A", gates=ALL_GATES, plan=p,
                      confirmations=["retest", "trigger_candle", "rvol"], trigger_1h="engulfing")


def watch_eval() -> Evaluation:
    p = plan(entry=0.6810, sl=0.6590, tp1=0.7260, tp2=0.7480, kind="flip", tf="1d")
    return Evaluation("ARB", 1, score=66, grade="Watch", plan=p,
                      gates={**ALL_GATES, "confirmation": False}, flags=["not_confirmed"],
                      confirmations=["retest"], labels=["BREAKOUT_WATCH"])


def short_eval() -> Evaluation:
    p = plan(side=-1, entry=2.415, sl=2.488, tp1=2.268, tp2=2.195, kind="prev_high", tf="1d",
             order="market", risk_pct=0.5, sl_pct=3.0, size_pct=16.7, leverage=10, margin_pct=1.67)
    return Evaluation("APT", -1, score=71, grade="B", gates=ALL_GATES, plan=p,
                      confirmations=["choch", "rsi", "rvol"], labels=["EXHAUSTION"],
                      notes=["pattern:double_top"])


def funnel():
    return {"regime": {"name": "neutral", "usdt_d": -1, "btc_d": 0, "total2": 1},
            "majors": {"btc": 3, "eth": 2, "ethbtc": 1, "ethbtc_d": 1},
            "shortlist": [{"base": b, "side": 1, "score": s, "labels": ["EARLY_TREND"]}
                          for b, s in (("SOON", 96), ("GRASS", 91), ("ARB", 80), ("SOL", 78),
                                       ("PUMP", 74))]}


def open_signal(ev: Evaluation) -> dict:
    return {"symbol": ev.base, "side": "long" if ev.side == 1 else "short", "status": "active",
            "grade": ev.grade, "score": ev.score,
            "payload": {"evaluation": ev.to_dict(), "plan": ev.plan.to_dict()}}
