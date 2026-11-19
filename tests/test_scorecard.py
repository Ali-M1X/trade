import copy

from agent import scorecard as SC
from agent.store.repository import Repository

DAY = 86_400_000
SINCE = SC._ms("2026-11-19")
NOW = SINCE + 3 * DAY


def test_display_sections_do_not_change_the_rules_fingerprint(cfg):
    base = SC.fingerprints(cfg)
    c = copy.deepcopy(cfg)
    c.setdefault("panel", {})["balance_usd"] = 500
    c["pulse"] = {"h1_pct": 0.9}
    c["scorecard"] = {"min_trades": 50}
    assert SC.fingerprints(c) == base
    c["trade"] = {**c["trade"], "min_rr": 9}
    assert SC.fingerprints(c)[SC.S1] != base[SC.S1]


def test_the_live_config_gives_the_same_fingerprint_as_the_base_one(cfg):
    from agent.config import live_config
    assert SC.fingerprints(live_config(cfg)) == SC.fingerprints(cfg)


def test_record_notes_each_change_once(cfg):
    repo = Repository(":memory:")
    h = SC.record(cfg, repo, NOW)
    assert all(v[0][0] == 0 for v in h.values())                  # first sight: in force since `since`
    assert SC.record(cfg, repo, NOW + 60_000) == h
    c = copy.deepcopy(cfg)
    c["trade"] = {**c["trade"], "min_rr": 9}
    h = SC.record(c, repo, NOW + 120_000)
    assert h[SC.S1][-1][0] == NOW + 120_000 and len(h[SC.S1]) == 2


def trade(system, approved, created, net_r, closed=True, risk=3.0):
    return {"system": system, "approved": approved, "created": created, "filled_at": created + 1,
            "closed_at": created + 3_600_000 if closed else None, "status": "tp2" if closed and net_r > 0 else "sl" if closed else "active",
            "open": not closed, "net_r": net_r, "cost_r": 0.1, "risk_pct": risk, "sl_pct": 2.0, "partials": [],
            "open_r": None if closed else 0.0, "base": "AAA"}


def test_view_counts_from_the_freeze_and_judges_the_approved_trades(cfg):
    c = {**cfg, "scorecard": {"since": "2026-11-19", "min_trades": 4}}
    old = trade(SC.S1, True, SINCE - DAY, -1.0)                  # before the freeze: not counted
    ts = [trade(SC.S1, True, SINCE + k * 3_600_000, r) for k, r in enumerate((1.0, -1.0, 2.0, 0.5))]
    w = [trade(SC.S1, False, SINCE + 1000, -1.0), trade(SC.S1, True, SINCE + 2000, 0.0, closed=False)]
    v = SC.view(c, [old] + ts + w, {}, NOW)
    a = v["groups"]["approved"]
    assert a["closed"] == 4 and a["open"] == 1 and a["avg_r"] == 0.625 and a["pf"] == 3.5
    assert v["groups"]["watch"]["closed"] == 1 and v["status"] == "pass"
    assert v["checks"] == {"avg_r": True, "pf": True, "max_dd": True}
    v = SC.view({**c, "scorecard": {"since": "2026-11-19"}}, ts, {}, NOW)
    assert v["status"] == "measuring"
    assert SC.view({**c, "scorecard": {"since": "2026-11-19"}}, ts, {}, SINCE + 29 * DAY)["status"] == "extend"


def test_a_deep_drawdown_fails_at_any_count_and_a_rule_change_restarts_that_system(cfg):
    c = {**cfg, "scorecard": {"since": "2026-11-19"}}
    losses = [trade(SC.S1, True, SINCE + k * 3_600_000, -1.0, risk=5.0) for k in range(5)]
    assert SC.view(c, losses, {}, NOW)["status"] == "fail"
    fp = SC.fingerprints(c)
    hist = {SC.S1: [[0, "old"], [SINCE + 2 * 3_600_000 + 1, fp[SC.S1]]]}
    v = SC.view(c, losses, hist, NOW)
    assert v["systems"][SC.S1]["changed_at"] == SINCE + 2 * 3_600_000 + 1
    assert v["groups"]["approved"]["closed"] == 2                 # only the trades after the change
    assert SC.view(c, losses, {SC.S1: [[0, "old"]]}, NOW)["systems"][SC.S1]["pending"]
