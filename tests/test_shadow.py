import copy

import pytest

from agent.layers.technical import Evaluation
from agent.layers.trade import TradePlan
from agent.signals import shadow
from agent.store.repository import Repository
from agent.store.storage import make_storage

NOW = 1_794_679_441_000
STATE = {"regime": {"name": "bear", "bias": "short"}, "majors": {"btc": -3}}


def _ev(base, grade, score=60.0, plan=True):
    p = TradePlan(1, "limit", 100.0, 99.0, 101.0, 95.0, 110.0, 115.0, 2, 3, False, 100.0,
                  "cluster", "4h", 2, 2.0) if plan else None
    return Evaluation(base, 1, score=score, grade=grade, plan=p)


def test_record_opens_watch_setups_only_once_per_coin(cfg):
    repo = Repository()
    evs = [_ev("AAA", "Watch"), _ev("BBB", "A", 80), _ev("CCC", "Watch", plan=False), _ev("DDD", None)]
    assert len(shadow.record(cfg, repo, evs, NOW, STATE)) == 1
    assert shadow.record(cfg, repo, [_ev("AAA", "Watch")], NOW + 1, STATE) == []    # still open
    (sig,) = repo.get_signals()
    assert sig["symbol"] == "AAA" and sig["grade"] == "Watch" and sig["payload"]["approved"] is False
    want = cfg["trade"]["base_risk_pct"] * cfg["grades"]["risk_share"]["B"]
    assert sig["payload"]["plan"]["risk_pct"] == pytest.approx(want)
    assert repo.get_state("shadow_since") == NOW


def test_storage_keeps_the_shadow_databases_on_the_data_branch(cfg):
    c = copy.deepcopy(cfg)
    c["system2"] = {**c.get("system2", {}), "enabled": True, "db_path": "data/s2.db"}
    c["shadow"] = {"enabled": True, "db_path": "data/sh.db", "s2_db_path": "data/sh2.db"}
    st = make_storage({**c, "storage": {**c["storage"], "backend": "git_branch"}})
    assert [p.name for p in st.extra_dbs] == ["s2.db", "sh.db", "sh2.db"]
    c["shadow"]["enabled"] = False
    st = make_storage({**c, "storage": {**c["storage"], "backend": "git_branch"}})
    assert [p.name for p in st.extra_dbs] == ["s2.db"]
