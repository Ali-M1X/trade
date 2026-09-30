import numpy as np

from agent.analysis.frame import make_frame
from agent.hold.scanner import (btc_pair_higher_low, ma99_rising, plan_levels, scan_hold,
                                w_cycle)
from agent.notify.formatter import hold_message
from builders import from_closes, trend


def frame(cfg, closes, tf):
    return make_frame(from_closes(closes, tf=tf), tf, cfg, True)


def test_conditions(cfg):
    up_w, up_d = frame(cfg, trend(), "1w"), frame(cfg, trend(), "1d")
    down_w = frame(cfg, trend(drift=-0.5, start=250), "1w")
    assert w_cycle(up_w, cfg) and not w_cycle(down_w, cfg)
    assert btc_pair_higher_low(up_w) and not btc_pair_higher_low(down_w)
    assert not btc_pair_higher_low(None)
    assert ma99_rising(up_d, cfg) and not ma99_rising(frame(cfg, trend(drift=-0.5, start=250), "1d"), cfg)


def test_plan_levels_are_below_and_above_price(cfg):
    ranging = 100 + 8 * np.sin(np.arange(220) / 4) + np.arange(220) * 0.05
    d, w = frame(cfg, ranging, "1d"), frame(cfg, ranging, "1w")
    steps, inval, targets = plan_levels(d.close, d, w, cfg)
    assert 1 <= len(steps) <= cfg["hold"]["buy_steps"]
    assert all(p < d.close for p in steps) and steps == sorted(steps, reverse=True)
    assert inval == steps[-1]
    assert targets and all(p > d.close for p in targets)
    gap = cfg["swings"]["level_cluster_atr"] * d.atr
    for group in (steps, targets):
        assert all(abs(a - b) >= gap for a, b in zip(group, group[1:]))


def test_spaced():
    from agent.hold.scanner import spaced
    assert spaced([2.598, 2.597, 2.596, 2.4, 2.39, 2.1], 0.05) == [2.598, 2.4, 2.1]


def test_scan_hold_needs_four_of_six(cfg):
    up_d, up_w = frame(cfg, trend(), "1d"), frame(cfg, trend(), "1w")
    flat = 100 + 0.2 * np.sin(np.arange(220))
    btc_d = frame(cfg, flat, "1d")
    strong = {"base": "SOL", "info": {"ath_change_percentage": -10}, "d": up_d, "w": up_w,
              "w_btc": up_w, "categories": ["AI"]}
    weak = {"base": "OLD", "info": {}, "d": frame(cfg, flat, "1d"), "w": frame(cfg, flat, "1w"),
            "w_btc": None, "categories": []}
    ideas = scan_hold([strong, weak], btc_d, {"AI"}, cfg)
    assert [i.base for i in ideas] == ["SOL"]
    assert ideas[0].met == {"w_cycle": True, "btc_pair_hl": True, "ma99_d": True, "rs_90d": True,
                            "hot_category": True, "ath_acc": False}
    msg = hold_message(ideas, 1_759_276_800_000)
    assert "گزارش هفتگی HOLD" in msg and "SOL — 5 از 6 شرط" in msg
    assert "امروز کوینی" in hold_message([], 0)


def test_weekly_and_new_entrants(cfg):
    import io

    from agent.config import load_secrets
    from agent.notify.telegram import Notifier
    from agent.runs import hold_new_entrants, weekly
    from agent.store.repository import Repository
    from test_runs import NOW, FakeCG, FakeMarket
    repo = Repository()
    n = Notifier(cfg, load_secrets({}), out=io.StringIO())
    ideas = weekly(cfg, repo, FakeMarket(), FakeCG(), n, NOW)
    assert n.sent[-2].startswith("💎 گزارش هفتگی HOLD") and n.sent[-1].startswith("📊 عملکرد")
    assert repo.get_state("hold") == sorted(i.base for i in ideas)
    n.sent.clear()
    assert hold_new_entrants(cfg, repo, FakeMarket(), FakeCG(), n, NOW) == [] and n.sent == []
    repo.set_state("hold", ["SOMETHING_ELSE"])
    new = hold_new_entrants(cfg, repo, FakeMarket(), FakeCG(), n, NOW)
    assert [i.base for i in new] == [i.base for i in ideas]
    if new:
        assert n.sent[-1].startswith("💎 کوین جدید در فهرست HOLD")
