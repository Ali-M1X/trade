import copy
import json

import pytest

from agent.panel import build, collect, simulate
from agent.signals.lifecycle import new_signal
from agent.store.repository import Repository

DAY = 86_400_000
START = 1_793_923_200_000                      # 2026-11-06 00:00 UTC
NOW = START + 8 * DAY


def _plan(side=1, entry=100.0, sl=95.0, order="market"):
    return {"side": side, "entry": entry, "sl": sl, "tp1": entry + side * 10,
            "tp2": entry + side * 15, "order": order, "risk_pct": 1.0}


def _cfg(cfg, tmp_path, backfill=None):
    c = copy.deepcopy(cfg)
    c["storage"]["db_path"] = str(tmp_path / "state.db")
    c["system2"] = {"enabled": True, "variant": "s2_test", "db_path": str(tmp_path / "state_s2.db")}
    c["shadow"] = {"enabled": True, "db_path": str(tmp_path / "sh.db"), "s2_db_path": str(tmp_path / "sh2.db")}
    c["panel"] = {"start": "2026-11-06", "balance_usd": 100, "backfill": str(backfill or tmp_path / "none.json")}
    return c


def _closed(lc, status, r, ts):
    lc.update(status=status, realized_r=r, closed_at=ts, open_frac=0.0)
    return lc


def _add(repo, base, t, status, lc, grade="A", side="long", payload=None):
    sid = repo.add_signal(t, base, side, grade, 80, status, {"lifecycle": lc, "plan": _plan(), **(payload or {})})
    if lc.get("filled_at"):
        repo.add_event(lc["filled_at"], "filled", {"price": lc["entry"]}, sid)
    if status in ("sl", "tp3", "breakeven"):
        repo.add_event(lc["closed_at"], status, {"price": lc["entry"]}, sid)
    return sid


def test_accounts_book_confirmed_and_watch_trades(cfg, tmp_path):
    c = _cfg(cfg, tmp_path)
    repo = Repository(c["storage"]["db_path"])
    win = _closed(new_signal(_plan(), START + DAY, c), "tp3", 2.0, START + 2 * DAY)
    _add(repo, "WINNER", START + DAY, "tp3", win)
    old = _closed(new_signal(_plan(), START - 3 * DAY, c), "sl", -1.0, START - 2 * DAY)
    _add(repo, "BEFORE", START - 3 * DAY, "sl", old)              # before the panel's start
    _add(repo, "OPENCOIN", NOW - 3_600_000, "active", new_signal(_plan(), NOW - 3_600_000, c))
    repo.upsert_candles("gate", "OPENCOIN/USDT:USDT", "5m", [[NOW - 300_000, 104, 106, 103, 105.0, 1]])
    repo.set_state("last_runs", {"run-1h": NOW - 60_000, "run-15m": NOW - 60_000})
    repo.set_state("evaluations", {"ts": NOW, "items": [
        {"base": "NEAR<x>", "side": -1, "score": 62.0, "grade": "Watch", "price": 1.5,
         "flags": ["not_confirmed"], "gates": {"regime": True, "confirmation": False}}]})
    repo.set_state("funnel", {"regime": {"name": "bear", "bias": "short"},
                              "majors": {"btc": -3, "btc_weak": True},
                              "shortlist": [{"base": "GUN", "side": -1}],
                              "watchlist": {"XLM": ["SQUEEZE"]}, "ts": NOW})
    repo.close()
    Repository(c["system2"]["db_path"]).close()
    sh = Repository(c["shadow"]["db_path"])
    loss = _closed(new_signal(_plan(), START + 3 * DAY, c), "sl", -1.0, START + 4 * DAY)
    _add(sh, "WATCHED", START + 3 * DAY, "sl", loss, grade="Watch", payload={"approved": False})
    sh.set_state("shadow_since", START)
    sh.close()

    data = collect(c, NOW)
    acc = data["accounts"]
    assert [t["base"] for t in acc["approved"]["closed"]] == ["WINNER"]
    assert [t["base"] for t in acc["watch"]["closed"]] == ["WATCHED"]
    win_row = acc["approved"]["closed"][0]
    assert win_row["risk_usd"] == pytest.approx(1.0)                # 1% of $100 at the fill
    assert win_row["net_r"] < 2.0 and win_row["pnl_usd"] == pytest.approx(win_row["net_r"])
    assert acc["approved"]["balance"] == pytest.approx(100 + win_row["net_r"])
    assert acc["watch"]["balance"] < 100
    assert acc["all"]["balance"] == pytest.approx(
        100 + sum(t["pnl_usd"] for t in acc["all"]["closed"]))
    (pos,) = acc["approved"]["open"]
    assert pos["base"] == "OPENCOIN" and pos["open_r"] == pytest.approx(1.0)   # (105-100)/5

    repo = Repository(c["storage"]["db_path"])                          # the same bar from spot and swap
    for sym in ("OPENCOIN/USDT", "OPENCOIN/USDT:USDT"):
        repo.upsert_candles("gate", sym, "1h", [[NOW - k * 3_600_000, 100, 106, 99, 105, 7] for k in range(3)])
    repo.close()
    out = tmp_path / "panel"
    page = build(c, out, NOW).read_text(encoding="utf-8")
    data = json.loads((out / "data.json").read_text(encoding="utf-8"))
    bases = {t["base"] for t in data["trades"]}
    assert {"WINNER", "WATCHED", "OPENCOIN"} <= bases and "BEFORE" not in bases
    assert "NEAR<x>" not in page and "NEAR\\u003cx\\u003e" in page          # no raw HTML from coin names
    cands = [x for s in data["systems"] for x in s["candidates"]]
    assert any(x["base"] == "NEAR<x>" and "تأیید ورود ندارد" in x["why"] for x in cands)   # why not a signal
    assert "s2_test" in page and "bear" in page and "GUN" in page
    assert set(data["compare"]) == {"سیستم ۱", "سیستم ۲"}
    bars = json.loads((out / data["candles"]["OPENCOIN"]).read_text())["1h"]
    assert [b[0] for b in bars] == sorted({b[0] for b in bars}) and len(bars) == 3
    assert (out / "assets" / "lightweight-charts.standalone.production.js").exists()
    assert "IRANSansWeb.woff2" not in page                                # no licensed font, no 404
    assert not list(out.glob(".*.tmp"))


def test_backfill_fills_watch_trades_before_the_shadow_book(cfg, tmp_path):
    bf = tmp_path / "backfill.json"
    trade = {"system": "system1", "approved": False, "base": "REPLAYED", "side": -1, "grade": "Watch",
             "score": 60, "created": START + DAY, "status": "sl", "entry": 10.0, "sl": 11.0, "tp1": 8.0,
             "tp2": 7.0, "risk_pct": 0.5, "gross_r": -1.0, "fee_r": 0.1, "slippage_r": 0.1,
             "funding_r": 0.0, "net_r": -1.2, "filled_at": START + DAY, "closed_at": START + 2 * DAY,
             "events": [["sl", START + 2 * DAY, 11.0]]}
    late = {**trade, "base": "LATE", "created": START + 5 * DAY}           # after the shadow start
    ok = {**trade, "base": "REPLAY_OK", "approved": True}
    bf.write_text(json.dumps({"meta": {}, "trades": [trade, late, ok]}))
    c = _cfg(cfg, tmp_path, backfill=bf)
    Repository(c["storage"]["db_path"]).close()
    sh = Repository(c["shadow"]["db_path"])
    sh.set_state("shadow_since", START + 4 * DAY)
    sh.close()
    acc = collect(c, NOW)["accounts"]
    (t,) = acc["watch"]["closed"]
    assert t["base"] == "REPLAYED" and t["source"] == "replay" and t["net_r"] == -1.2
    assert t["pnl_usd"] == pytest.approx(-0.6)                            # 0.5% of $100 x -1.2R
    assert acc["approved"]["closed"] == []                                # replay's own A/B not booked


def test_simulate_compounds_and_tracks_drawdown():
    def tr(t0, r):
        return {"filled_at": t0, "closed_at": t0 + 1, "status": "tp3" if r > 0 else "sl", "risk_pct": 10,
                "net_r": r, "cost_r": 0.0, "open": False, "sl_pct": 1.0}
    a = simulate([tr(1, 1.0), tr(10, -1.0)], 100.0, 0, 100)
    assert a["balance"] == pytest.approx(99.0)                            # 110 then -11
    assert a["max_dd"] == pytest.approx(10.0)
    assert a["wins"] == 1 and len(a["closed"]) == 2


def test_panel_with_empty_databases(cfg, tmp_path):
    c = _cfg(cfg, tmp_path)
    Repository(c["storage"]["db_path"]).close()
    build(c, tmp_path / "panel", NOW)
    data = json.loads((tmp_path / "panel" / "data.json").read_text(encoding="utf-8"))
    assert data["trades"] == [] and data["accounts"]["all"]["balance"] == 100
    assert "<section" in data["strategy_html"]


def test_panel_uses_iransans_when_its_files_are_given(cfg, tmp_path):
    c = _cfg(cfg, tmp_path)
    fonts = tmp_path / "fonts"
    fonts.mkdir()
    (fonts / "IRANSansWeb.woff2").write_bytes(b"x")
    c["panel"]["font_dir"] = str(fonts)
    page = build(c, tmp_path / "panel", NOW).read_text(encoding="utf-8")
    assert "url(assets/IRANSansWeb.woff2)" in page and "IRANSansWeb_Bold" not in page
    assert (tmp_path / "panel" / "assets" / "IRANSansWeb.woff2").exists()


def test_strategy_page_follows_the_live_config(cfg):
    from agent.panel_strategy import strategy_html
    page = strategy_html(cfg)
    assert "سیستم ۱" in page and "سیستم ۲" in page and "MA25" in page


def test_panel_does_not_create_missing_databases(cfg, tmp_path):
    c = _cfg(cfg, tmp_path)
    build(c, tmp_path / "panel", NOW)
    assert not (tmp_path / "state.db").exists() and not (tmp_path / "sh.db").exists()


def test_panel_pull_takes_the_databases_from_the_data_branch(cfg, tmp_path, monkeypatch):
    """Panel-only server (2026-11-15): `panel --pull` refreshes the databases first."""
    import argparse
    from agent import cli
    from agent.store import storage
    c = _cfg(cfg, tmp_path)
    pulled = []

    class FakeStorage:
        def pull(self):
            Repository(c["storage"]["db_path"]).close()
            pulled.append(True)
            return True
    monkeypatch.setattr(storage, "make_storage", lambda cfg, repo_dir=".": FakeStorage())
    monkeypatch.setattr(cli, "live_config", lambda cfg: cfg)
    assert cli.cmd_panel(argparse.Namespace(out=str(tmp_path / "panel"), pull=True), c, None) == 0
    assert pulled and (tmp_path / "panel" / "index.html").exists()


def test_panel_script_does_not_hide_browser_globals():
    """2026-11-15: a tab function named `history` hid window.history, so clicking a tab did nothing."""
    import re

    from agent.panel import WEB
    js = (WEB / "app.js").read_text(encoding="utf-8")
    declared = set(re.findall(r"\bfunction\s+(\w+)\s*\(", js)) | set(re.findall(r"\bvar\s+(\w+)\s*=", js))
    assert not declared & {"history", "location", "document", "fetch", "localStorage", "setInterval",
                           "navigator", "screen", "scroll", "alert", "Intl"}


def test_trades_without_a_stored_leverage_get_the_sizing_rule(cfg, tmp_path):
    c = _cfg(cfg, tmp_path)
    c["trade"].update(liquidation_sl_multiple=2.5, leverage_cap=10)
    repo = Repository(c["storage"]["db_path"])
    lc = _closed(new_signal(_plan(), START + DAY, c), "tp3", 2.0, START + 2 * DAY)
    _add(repo, "NOLEV", START + DAY, "tp3", lc)                   # plan has no leverage
    repo.close()
    Repository(c["system2"]["db_path"]).close()
    t = next(t for t in collect(c, NOW)["trades"] if t["base"] == "NOLEV")
    assert t["leverage"] == 8                                       # stop 5% -> 1 / (2.5 x 5%)


def test_partial_exits_are_booked_when_they_happen():
    """TP1 at +2R on half the position books +1R at once; the open half stays unrealized."""
    t = {"base": "AAA", "side": 1, "entry": 100.0, "sl": 90.0, "filled_at": 1, "closed_at": None,
         "status": "tp1", "risk_pct": 10, "net_r": 1.0, "cost_r": 0.0, "gross_r": 1.0, "open": True,
         "sl_pct": 10.0, "open_r": 1.5, "open_frac": 0.5, "price": 110.0,
         "events": [["tp1", 5, 120.0]], "partials": [(5, 1.0, 0.5)]}
    a = simulate([t], 100.0, 0, 3 * 3_600_000, {"AAA": [(2, 105.0), (6, 110.0)]})
    assert a["balance"] == pytest.approx(110.0)                 # +1R of $10 booked at TP1
    assert a["equity"] == pytest.approx(115.0)                  # + the open half at +0.5R
    assert len(a["partial"]) == 1 and a["wins"] == 1 and a["pnl"] == pytest.approx(10.0)
    assert a["eq_curve"][-1][1] == pytest.approx(115.0)
    closed = dict(t, status="tp3", open=False, closed_at=50, net_r=1.8, open_r=None,
                  events=[["tp1", 5, 120.0], ["tp3", 50, 116.0]])
    b = simulate([closed], 100.0, 0, 100)
    assert b["balance"] == pytest.approx(118.0)                 # the whole trade books risk x net R
    assert [round(v, 2) for _, v in b["curve"]][:3] == [100.0, 110.0, 118.0]


def test_regime_labels_trend_range_and_whipsaw():
    from agent.panel_regime import DEFAULTS, label_bars, segments
    moves = [0.1, -0.1] * 10 + [-1.5] * 6 + [0.2, -0.2] * 6
    sizes = [2.0] * 20 + [3.0] * 6 + [1.5] * 11 + [6.0]
    labs = label_bars(moves, sizes, DEFAULTS)
    assert labs[25] == "trend_down" and labs[-2] == "range_quiet" and labs[-1] == "whipsaw"
    segs = segments(list(range(0, len(moves) * 10, 10)), labs, moves, sizes, 2)
    assert [s["label"] for s in segs] == ["range", "trend_down", "range"]


def test_restate_risk_scales_older_trades_to_todays_base(cfg):
    """2026-11-18 (Ali): system 1 at 3% in every regime, system 2 at 4%, and every earlier trade
    shown at that risk."""
    from agent.config import live_config
    from agent.panel import restate_risk
    c = live_config(cfg)
    rows = [
        {"system": "سیستم ۱", "regime_name": "btc_led", "base_risk": None, "risk_pct": 0.5, "entry": 100, "sl": 95, "leverage": 4},
        {"system": "سیستم ۱", "regime_name": "bear", "base_risk": None, "risk_pct": 1.0, "entry": 100, "sl": 95, "leverage": 4},
        {"system": "سیستم ۲", "regime_name": "risk_off", "base_risk": None, "risk_pct": 2.0, "entry": 100, "sl": 95, "leverage": 4},
        {"system": "سیستم ۲", "regime_name": "risk_off", "base_risk": 4.0, "risk_pct": 4.0, "entry": 100, "sl": 95, "leverage": 4},
        {"system": "سیستم ۲", "regime_name": "neutral", "base_risk": None, "risk_pct": 2.0, "entry": 100, "sl": 99.8, "leverage": 10},
    ]
    restate_risk(rows, c)
    assert [r["risk_pct"] for r in rows[:4]] == pytest.approx([1.5, 1.5, 4.0, 4.0])
    assert rows[4]["risk_pct"] == pytest.approx(2.0)          # 0.2% stop x 10x leverage: no room for 4%
    assert rows[0]["risk_pct_opened"] == 0.5 and rows[1]["risk_pct_opened"] == 1.0


def test_the_nodup_account_keeps_one_of_two_same_coin_same_side_trades(cfg, tmp_path):
    from agent.panel import duplicate_pairs
    c = _cfg(cfg, tmp_path)
    t1, t2 = START + DAY, START + DAY + 600_000
    r1, r2 = Repository(c["storage"]["db_path"]), Repository(c["system2"]["db_path"])
    _add(r1, "DUP", t1, "tp3", _closed(new_signal(_plan(), t1, c), "tp3", 2.0, START + 2 * DAY))
    _add(r2, "DUP", t2, "tp3", _closed(new_signal(_plan(), t2, c), "tp3", 2.0, START + 2 * DAY))
    _add(r2, "DUP", START + 3 * DAY, "sl", _closed(new_signal(_plan(), START + 3 * DAY, c), "sl", -1.0, START + 4 * DAY))  # later: not a twin
    _add(r2, "OTHER", t1, "sl", _closed(new_signal(_plan(), t1, c), "sl", -1.0, START + 2 * DAY))
    r1.close(); r2.close()
    d = collect(c, NOW)
    pairs = duplicate_pairs(d["trades"], NOW)
    assert [(k["system"], k["created"], x["created"]) for k, x in pairs] == [("سیستم ۱", t1, t2)]   # the first filled stays
    acc = d["accounts"]
    assert len(acc["nodup"]["closed"]) == len(acc["all"]["closed"]) - 1
    assert acc["all"]["balance"] > acc["nodup"]["balance"]                  # the twin won: one win less
