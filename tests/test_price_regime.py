import numpy as np
import pandas as pd

from agent.backtest.run import simulate
from agent.layers.price_regime import alt_index, hold, market_states, raw_state
from agent.layers.regime import make_price_regime
from synthetic_history import build

DAY = 86_400_000


def daily(closes, t0=0):
    c = pd.Series(closes, dtype=float, index=t0 + np.arange(len(closes)) * DAY)
    return pd.DataFrame({"high": c * 1.01, "low": c * 0.99, "close": c})


def test_hold_needs_n_closes_in_a_row():
    s = pd.Series([np.nan, 1, 1, -1, -1, 1, -1, -1, -1, -1], dtype=float)
    assert hold(s, 3).tolist()[1:] == [1, 1, 1, 1, 1, 1, 1, -1, -1]


def test_trend_adx_reads_a_trend_and_a_range():
    up = daily(100 * np.exp(np.linspace(0, 1.5, 300)))
    assert raw_state(up, "trend_adx").iloc[-1] == 1
    assert raw_state(daily(100 * np.exp(-np.linspace(0, 1.5, 300))), "trend_adx").iloc[-1] == -1
    flat = daily(100 + np.sin(np.arange(300)) * 0.5)
    assert raw_state(flat, "trend_adx").iloc[-1] == 0
    assert raw_state(up, "trend_adx").iloc[:99].isna().all()     # MA100 warming up


def test_market_is_range_when_btc_and_alts_disagree():
    up = daily(100 * np.exp(np.linspace(0, 1.5, 300)))
    down = daily(100 * np.exp(-np.linspace(0, 1.5, 300)))
    p = {"definition": "trend_adx", "hold_days": 3}
    assert market_states(up, up, p)["state"].iloc[-1] == 1
    st = market_states(up, down, p)
    assert st["state"].iloc[-1] == 0 and st["alts"].iloc[-1] == -1


def test_alts_can_lead_the_regime():
    up = daily(100 * np.exp(np.linspace(0, 1.5, 300)))
    down = daily(100 * np.exp(-np.linspace(0, 1.5, 300)))
    p = {"definition": "trend_adx", "hold_days": 3, "combine": "alts"}
    assert market_states(up, down, p)["state"].iloc[-1] == -1


def test_alt_index_averages_returns_of_coins_that_trade():
    a = daily([100, 110, 121])
    b = daily([50, 50], t0=DAY)                      # listed a day later
    idx = alt_index({"A": a, "B": b})
    r = np.log(idx["close"]).diff().round(6).tolist()[1:]
    # day 1: B has no return yet (its first close), so only A counts
    assert r == [round(np.log(1.1), 6), round(np.log(1.1) / 2, 6)]


def test_price_regime_reads_config(cfg):
    r = make_price_regime(-1, 1, cfg)
    assert (r.name, r.bias, r.total2) == ("bear", "both", 1)
    cfg["regime"]["price"]["regimes"]["bear"] = {"bias": "short", "risk": 0.5}
    assert not make_price_regime(-1, 0, cfg).allows(1)


def test_backtest_with_price_regime_and_replay_window(cfg):
    repo = build(days=200)
    cfg["regime"]["source"] = "price"
    cfg["regime"]["price"]["basket"] = ["SOL", "ADA", "LINK", "DOT"]
    meta = repo.get_state("bt_meta")
    cfg["backtest"]["replay_start"] = meta["end"] - 40 * DAY
    cfg["backtest"]["replay_end"] = meta["end"] - 20 * DAY
    res = simulate(cfg, repo)
    assert res["meta"]["days"] == 20 and res["stats"]["funnel_runs"] == 120
    assert set(res["regime_hours"]) <= {"bull", "range", "bear"} and res["regime_hours"]
    assert all(t["regime"] in {"bull", "range", "bear"} for t in res["trades"])


def test_each_regime_runs_its_own_system(cfg, monkeypatch):
    from agent.backtest import engine as engine_mod
    seen = []
    real = engine_mod.evaluate

    def spy(base, side, frames, regime, majors, c, **kw):
        seen.append((regime.name, c["trade"]["base_risk_pct"]))
        return real(base, side, frames, regime, majors, c, **kw)
    monkeypatch.setattr(engine_mod, "evaluate", spy)
    repo = build(days=200)
    cfg["regime"]["source"] = "price"
    cfg["regime"]["price"]["basket"] = ["SOL", "ADA", "LINK", "DOT"]
    cfg["regime"]["price"]["systems"] = {"bull": {"trade": {"base_risk_pct": 2.0}},
                                         "range": {"trade": {"base_risk_pct": 3.0}}, "bear": "off"}
    meta = repo.get_state("bt_meta")
    cfg["backtest"]["replay_start"] = meta["end"] - 60 * DAY
    res = simulate(cfg, repo)
    want = {"bull": 2.0, "range": 3.0}
    assert seen and all(want[n] == r for n, r in seen)
    assert all(t["regime"] != "bear" for t in res["trades"])
