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

    page = build(c, tmp_path / "panel", NOW).read_text(encoding="utf-8")
    assert "WINNER" in page and "WATCHED" in page and "OPENCOIN" in page and "BEFORE" not in page
    assert "NEAR&lt;x&gt;" in page and "NEAR<x>" not in page                # names are escaped
    assert "تأیید ورود ندارد" in page                                     # why it wasn't a signal
    assert "s2_test" in page and "bear" in page and "GUN" in page
    assert not (tmp_path / "panel" / ".index.html.tmp").exists()


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
    page = build(c, tmp_path / "panel", NOW).read_text(encoding="utf-8")
    assert "الان پوزیشن بازی نیست" in page
    assert "هنوز معامله‌ی بسته‌شده‌ای نیست" in page


def test_panel_does_not_create_missing_databases(cfg, tmp_path):
    c = _cfg(cfg, tmp_path)
    build(c, tmp_path / "panel", NOW)
    assert not (tmp_path / "state.db").exists() and not (tmp_path / "sh.db").exists()
