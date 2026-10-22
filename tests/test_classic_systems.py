import numpy as np
import pandas as pd

from agent.backtest.run import simulate
from agent.layers.classic_systems import donchian, evaluate_classic, ma_pullback, meanrev
from synthetic_history import build

DAY = 86_400_000


def frame(closes, spread=0.01, opens=None):
    c = pd.Series(closes, dtype=float)
    o = pd.Series(opens, dtype=float) if opens is not None else c.shift().fillna(c.iloc[0])
    return pd.DataFrame({"ts": np.arange(len(c)) * DAY, "open": o,
                         "high": np.maximum(o, c) * (1 + spread), "low": np.minimum(o, c) * (1 - spread),
                         "close": c})


def test_donchian_fires_on_a_fresh_break_only():
    base = list(100 + np.sin(np.arange(80)) * 2)
    plan, strength = donchian(frame(base + [110]), 1, {"days": 55})
    assert plan.order == "market" and plan.entry == 110 and plan.sl < 110 and plan.tp1_r == 2.0
    assert strength > 0
    assert donchian(frame(base + [110, 109]), 1, {"days": 55}) is None     # back inside
    assert donchian(frame(base + [110]), -1, {"days": 55}) is None


def test_ma_pullback_buys_the_retest_of_ema20_in_an_uptrend():
    up = list(100 * np.exp(np.linspace(0, 1.0, 200)))
    dip = [up[-1] * 0.96, up[-1] * 0.93]                        # back to the EMA20
    closes = up + dip + [up[-1] * 0.99]                         # closes back above it
    opens = [closes[0]] + closes[:-2] + [up[-1] * 0.93]
    hit = ma_pullback(frame(closes, opens=opens), 1, {})
    assert hit is not None and hit[0].sl < min(dip)
    assert ma_pullback(frame(closes, opens=opens), -1, {}) is None


def test_meanrev_buys_the_close_back_inside_the_lower_band():
    flat = list(100 + np.sin(np.arange(60) / 2) * 1.0)
    closes = flat + [96.0, 90.0, 95.0]                          # stretched, then back inside
    plan, _ = meanrev(frame(closes), 1, {"rsi_low": 40, "min_tp1_r": 0.5})
    assert plan.tp1 > plan.entry > plan.sl and plan.tp2 > plan.tp1
    assert meanrev(frame(closes), -1, {"rsi_low": 40, "min_tp1_r": 0.5}) is None


def test_evaluate_classic_sizes_an_a_grade_plan(cfg):
    base = list(100 + np.sin(np.arange(80)) * 2)
    ev, _ = evaluate_classic("SOL", frame(base + [110]), 1, {"kind": "donchian"}, 1.0, cfg)
    assert ev.grade == "A" and ev.is_signal and ev.plan.risk_pct == cfg["trade"]["base_risk_pct"]
    assert evaluate_classic("SOL", frame(base + [110]), 1, {"kind": "donchian"}, 0.0, cfg) is None


def test_backtest_runs_a_rule_system_per_regime(cfg):
    repo = build(days=220)
    cfg["regime"]["source"] = "price"
    cfg["regime"]["price"]["basket"] = ["SOL", "ADA", "LINK", "DOT"]
    sys = {"system": {"kind": "donchian", "days": 20}}
    cfg["regime"]["price"]["systems"] = {"bull": sys, "range": sys, "bear": sys}
    meta = repo.get_state("bt_meta")
    cfg["backtest"]["replay_start"] = meta["end"] - 80 * DAY
    res = simulate(cfg, repo)
    assert res["trades"] and all(t["system"] == "donchian" and t["order"] == "market"
                                 for t in res["trades"])
    assert all(t["created"] % DAY == 0 for t in res["trades"])
