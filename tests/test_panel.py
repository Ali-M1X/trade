import copy

from agent.panel import build
from agent.signals.lifecycle import new_signal
from agent.store.repository import Repository

NOW = 1_794_679_441_000


def _plan(side=1, entry=100.0, sl=95.0, order="market"):
    return {"side": side, "entry": entry, "sl": sl, "tp1": entry + side * 10,
            "tp2": entry + side * 15, "order": order}


def _cfg(cfg, tmp_path):
    c = copy.deepcopy(cfg)
    c["storage"]["db_path"] = str(tmp_path / "state.db")
    c["system2"] = {"enabled": True, "variant": "s2_test", "db_path": str(tmp_path / "state_s2.db")}
    return c


def test_panel_shows_open_closed_and_candidates(cfg, tmp_path):
    c = _cfg(cfg, tmp_path)
    repo = Repository(c["storage"]["db_path"])
    lc = new_signal(_plan(), NOW - 3_600_000, c)
    repo.add_signal(NOW - 3_600_000, "OPENCOIN", "long", "A", 80, "active", {"lifecycle": lc})
    done = new_signal(_plan(side=-1, entry=10.0, sl=11.0), NOW - 86_400_000, c)
    done.update(status="sl", realized_r=-1.0, closed_at=NOW - 7_200_000)
    repo.add_signal(NOW - 86_400_000, "LOSER", "short", "B", 70, "sl", {"lifecycle": done})
    repo.upsert_candles("gate", "OPENCOIN/USDT:USDT", "5m",
                        [[NOW - 300_000, 104, 106, 103, 105.0, 1]])
    repo.set_state("last_runs", {"run-1h": NOW - 60_000, "run-15m": NOW - 60_000})
    repo.set_state("evaluations", {"ts": NOW, "items": [
        {"base": "NEAR<x>", "side": -1, "score": 62.0, "grade": "Watch", "price": 1.5,
         "gates": {"regime": True, "confirmation": False}}]})
    repo.set_state("funnel", {"regime": {"name": "bear", "bias": "short"},
                              "majors": {"btc": -3, "btc_weak": True},
                              "shortlist": [{"base": "GUN", "side": -1, "score": 74}],
                              "watchlist": {"XLM": ["SQUEEZE"]}, "ts": NOW})
    repo.close()
    Repository(c["system2"]["db_path"]).close()

    page = build(c, tmp_path / "panel", NOW).read_text(encoding="utf-8")

    assert "OPENCOIN" in page and "+1.00R" in page        # (105 - 100) / 5 from the last 5m close
    assert "LOSER" in page and "-1.00R" in page
    assert "NEAR&lt;x&gt;" in page and "NEAR<x>" not in page   # names are escaped
    assert "تأیید ورود" in page                            # why the candidate was not signalled
    assert "سیستم ۲" in page and "s2_test" in page
    assert "bear" in page and "GUN" in page
    assert not (tmp_path / "panel" / ".index.html.tmp").exists()


def test_panel_with_empty_databases(cfg, tmp_path):
    c = _cfg(cfg, tmp_path)
    Repository(c["storage"]["db_path"]).close()
    page = build(c, tmp_path / "panel", NOW).read_text(encoding="utf-8")
    assert "پوزیشن یا سفارش بازی نیست" in page
    assert "هنوز معامله‌ی بسته‌شده‌ای نیست" in page


def test_panel_does_not_create_missing_databases(cfg, tmp_path):
    c = _cfg(cfg, tmp_path)
    build(c, tmp_path / "panel", NOW)
    assert not (tmp_path / "state.db").exists()
