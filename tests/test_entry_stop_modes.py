"""Optional entry/stop/target modes (trade.entry_mode, trade.stop_mode = atr, trade.tp1_mode).

With every switch at its default the plans, signals and trades must be exactly what the
code produced before these modes existed: tests/fixtures/baseline_snapshot.json was
written by `snapshot()` below running on the previous code (origin/main e72071c).
"""
import copy
import json
from pathlib import Path

import pandas as pd
import pytest

from agent.analysis.frame import Frame
from agent.indicators.levels import Level
from agent.layers.trade import Rejected, build_trade, replan_at_fill
from agent.signals.lifecycle import H4, new_signal, on_4h_close, on_candle, on_time

FIXTURE = Path(__file__).parent / "fixtures" / "baseline_snapshot.json"
T0 = 1_760_000_000_000 // H4 * H4
HOUR = 3_600_000


def snapshot(cfg) -> dict:
    """Every L6 plan and every backtest trade of a 45-day replay of synthetic history. To get
    trades out of synthetic data, every evaluation with a plan is promoted to grade A after
    it is recorded, so the real plans go through the signal book, lifecycle and costs."""
    from agent.backtest import engine as engine_mod
    from agent.backtest.run import simulate
    from agent.layers.trade import size_position
    from synthetic_history import build

    plans = []
    real = engine_mod.evaluate

    def recording(*a, **kw):
        ev = real(*a, **kw)
        if ev.plan is not None:
            plans.append({"base": ev.base, "side": ev.side, "score": round(ev.score, 9),
                          "grade": ev.grade, **{k: round(v, 9) if isinstance(v, float) else v
                                                for k, v in ev.plan.to_dict().items()}})
            ev.grade = "A"
            size_position(ev.plan, 1.0, cfg)
        return ev
    engine_mod.evaluate = recording
    try:
        res = simulate(cfg, build(days=150), days=45)
    finally:
        engine_mod.evaluate = real
    keys = ("base", "side", "grade", "score", "status", "order", "entry", "sl", "created",
            "filled_at", "closed_at", "gross_r", "net_r", "events")
    trades = [{k: (round(t[k], 9) if isinstance(t[k], float) else t[k]) for k in keys}
              for t in res["trades"]]
    return json.loads(json.dumps({"plans": plans, "trades": trades, "stats": res["stats"]}))


def test_defaults_reproduce_the_previous_plans_and_trades(cfg):
    want = json.loads(FIXTURE.read_text(encoding="utf-8"))
    got = snapshot(cfg)
    assert len(got["plans"]) == len(want["plans"]) and got["plans"] == want["plans"]
    assert got["trades"] == want["trades"]
    assert got["stats"] == want["stats"]
    assert want["plans"], "the snapshot should contain plans"


# ------------------------------------------------------------------ plan modes
def fake_h4(close, atr, swings=()):
    from agent.indicators.swings import Swing
    df = pd.DataFrame({"close": [close], "atr": [atr]})
    return Frame("4h", df, [Swing(i, i, p, k) for i, (p, k) in enumerate(swings)], [], 0, 0)


def lvl(price, tf="4h", members=None):
    return Level(price, 2, tf, "cluster", 2, members or [price])


def modes(cfg, **kw):
    c = copy.deepcopy(cfg)
    c["trade"].update(kw)
    return c


def test_default_plans_have_no_replan_key(cfg):
    p = build_trade(1, fake_h4(143.0, 2.0), [lvl(142.7, members=[142.5, 142.9]), lvl(152.0)], cfg)
    assert "replan" not in p.to_dict() and p.order == "market"


def test_atr_stop(cfg):
    levels = [lvl(142.7, members=[142.5, 142.9]), lvl(160.0)]
    p = build_trade(1, fake_h4(143.0, 2.0), levels, modes(cfg, stop_mode="atr"))
    assert p.sl == pytest.approx(143.0 - 1.5 * 2.0)
    p = build_trade(-1, fake_h4(99.5, 1.0), [lvl(100.0, members=[99.8, 100.3]), lvl(80.0)],
                    modes(cfg, stop_mode="atr", atr_stop_mult=2.0))
    assert p.sl == pytest.approx(100.0 + 2.0)
    # the max_sl_atr cap still applies
    assert build_trade(1, fake_h4(143.0, 2.0), levels,
                       modes(cfg, stop_mode="atr", atr_stop_mult=3.5)) == Rejected("sl_too_wide")


def test_fixed_r_targets_skip_the_level_rr_gate(cfg):
    levels = [lvl(142.7), lvl(145.0), lvl(170.0, tf="1d")]           # 145 is only 1.5R away
    assert build_trade(1, fake_h4(143.0, 2.0), levels, cfg) == Rejected("rr")
    p = build_trade(1, fake_h4(143.0, 2.0), levels, modes(cfg, tp1_mode="fixed_r"))
    risk = 143.0 - p.sl
    assert p.tp1 == pytest.approx(143.0 + 2 * risk) and p.tp2 == pytest.approx(143.0 + 3 * risk)
    assert (p.tp1_r, p.tp2_r) == pytest.approx((2.0, 3.0))


def test_confirm_plan_is_a_reference_at_the_level(cfg):
    c = modes(cfg, entry_mode="confirm_4h", stop_mode="atr", tp1_mode="fixed_r")
    p = build_trade(1, fake_h4(144.5, 2.0), [lvl(142.7), lvl(160.0)], c)
    assert p.order == "confirm_4h" and p.entry == 142.7
    assert p.sl == pytest.approx(142.7 - 3.0) and p.tp1 == pytest.approx(142.7 + 6.0)
    rp = p.to_dict()["replan"]
    assert rp["level"] == 142.7 and rp["touched"] is False
    assert rp["zone_edge"] == pytest.approx(143.1)
    # price already inside the zone counts as touched
    p = build_trade(1, fake_h4(143.0, 2.0), [lvl(142.7), lvl(160.0)], c)
    assert p.replan["touched"] is True


def test_replan_at_fill(cfg):
    c = modes(cfg, entry_mode="confirm_4h", stop_mode="atr")
    p = build_trade(1, fake_h4(144.5, 2.0), [lvl(142.7), lvl(150.0), lvl(160.0, tf="1d")], c)
    rp = p.replan
    # fill 144: stop 141, risk 3 -> the 150 level is exactly 2R
    got = replan_at_fill(1, 144.0, rp)
    assert got["sl"] == pytest.approx(141.0) and got["tp1"] == 150.0 and got["tp2"] == 160.0
    # fill 145: risk 3, 150 is under 2R -> the rebuilt trade fails the level R:R gate
    assert replan_at_fill(1, 145.0, rp) == Rejected("rr")


# ------------------------------------------------------------------- lifecycle
def confirm_signal(cfg, touched=False, tp1_mode="fixed_r"):
    c = modes(cfg, entry_mode="confirm_4h", stop_mode="atr", tp1_mode=tp1_mode)
    price = 100.1 if touched else 101.0
    p = build_trade(1, fake_h4(price, 1.0), [lvl(100.0), lvl(110.0)], c)
    return new_signal(p.to_dict(), T0, c), c


def test_confirm_needs_touch_then_a_bullish_close_above_the_level(cfg):
    s, c = confirm_signal(cfg)
    assert s["status"] == "pending" and s["touched"] is False
    # a bullish 4H close above the level without a touch does not fill
    assert on_4h_close(s, T0, 101.5, 99.0, False, 100.8) == []
    # touch of the zone (low <= 100.2) on a 1H candle
    assert on_candle(s, T0 + H4, 100.9, 100.15, c) == [] and s["touched"]
    # bearish close above the level: no fill
    assert on_4h_close(s, T0 + H4, 100.5, 99.0, False, 100.9) == []
    # bullish close above the level: fill at the close, SL/TP rebuilt from it
    ev = on_4h_close(s, T0 + 2 * H4, 101.0, 99.0, False, 100.4)
    assert [e.kind for e in ev] == ["filled"] and ev[0].price == 101.0
    assert s["status"] == "active" and s["filled_at"] == T0 + 3 * H4
    assert s["entry"] == 101.0 and s["sl"] == pytest.approx(99.5) and s["sl_now"] == s["sl"]
    assert s["tp1"] == pytest.approx(104.0) and s["risk"] == pytest.approx(1.5)
    # candles before the fill time are not replayed; later ones manage the trade
    assert on_candle(s, T0 + 3 * H4 - HOUR, 120, 90, c) == []
    ev = on_candle(s, T0 + 3 * H4, 104.5, 100.5, c)
    assert [e.kind for e in ev] == ["tp1"] and ev[0].r == pytest.approx(2.0)


def test_confirm_keeps_the_cancellation_rules(cfg):
    s, c = confirm_signal(cfg)
    tp1 = s["tp1"]
    ev = on_candle(s, T0, tp1 + 0.1, 100.6, c)                       # TP1 before any touch
    assert [e.kind for e in ev] == ["cancelled"] and ev[0].reason == "tp1_before_entry"
    s, c = confirm_signal(cfg, touched=True)
    ev = on_4h_close(s, T0, s["sl"] - 0.1, 99.0, False, 100.0)       # 4H close beyond the SL
    assert ev[0].reason == "closed_beyond_sl"
    s, c = confirm_signal(cfg, touched=True)
    assert on_time(s, T0 + c["lifecycle"]["expiry_bars_4h"] * H4)[0].kind == "expired"


def test_confirm_fill_rejected_when_rebuilt_trade_fails(cfg):
    s, c = confirm_signal(cfg, touched=True, tp1_mode="level")
    # fill at 105: risk 1.5, the 110 level is 3.3R -> ok; at 108 it is 1.3R -> cancelled
    ev = on_4h_close(s, T0, 108.0, 99.0, False, 101.0)
    assert ev[0].kind == "cancelled" and ev[0].reason == "fill_rr"


def test_engine_uses_the_fill_price_for_r(cfg):
    from agent.backtest.engine import Backtest
    from synthetic_history import build
    c = modes(cfg, entry_mode="confirm_4h", stop_mode="atr", tp1_mode="fixed_r")
    repo = build(days=150)
    end = repo.get_state("bt_meta")["end"]
    bt = Backtest(c, repo, end - 60 * 86_400_000, end)
    trades = bt.run()
    sigs = {s["id"]: s for s in bt.book_repo.get_signals()}
    filled = [t for t in trades if t["filled_at"]]
    for t in filled:
        lc = sigs[t["id"]]["payload"]["lifecycle"]
        assert t["entry"] == lc["entry"] and t["sl"] == lc["sl"]
        assert t["order"] == "confirm_4h" and t["filled_at"] % H4 == 0     # fills at 4H closes
    for t in trades:
        assert t["order"] == "confirm_4h"
