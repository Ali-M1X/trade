import pytest

from agent.store.repository import Repository


def test_candles_upsert_is_idempotent_and_ordered():
    repo = Repository()
    rows = [[2000, 2, 3, 1, 2.5, 10], [1000, 1, 2, 0.5, 1.5, 5]]
    repo.upsert_candles("okx", "BTC/USDT:USDT", "4h", rows)
    repo.upsert_candles("okx", "BTC/USDT:USDT", "4h", [[2000, 2, 3, 1, 2.7, 11]])
    df = repo.get_candles("okx", "BTC/USDT:USDT", "4h")
    assert list(df["ts"]) == [1000, 2000]
    assert df.iloc[1]["close"] == 2.7
    assert repo.last_candle_ts("okx", "BTC/USDT:USDT", "4h") == 2000
    assert repo.get_candles("okx", "BTC/USDT:USDT", "1h").empty
    assert len(repo.get_candles("okx", "BTC/USDT:USDT", "4h", since=1500)) == 1


def test_http_cache_expires():
    repo = Repository()
    repo.cache_put("k", {"a": 1}, now=100)
    assert repo.cache_get("k", 50, now=140) == {"a": 1}
    assert repo.cache_get("k", 50, now=151) is None


def test_state_signals_events_roundtrip(tmp_path):
    path = tmp_path / "sub" / "state.db"
    repo = Repository(path)
    repo.set_state("regime", {"name": "neutral", "risk": 0.5})
    sid = repo.add_signal(1, "SOL/USDT:USDT", "long", "A", 81, "new", {"entry": [142.3, 143.1]})
    repo.update_signal(sid, "active", {"entry": [142.3, 143.1], "filled": 142.5}, 2)
    repo.add_event(2, "filled", {"price": 142.5}, sid)
    repo.close()

    repo = Repository(path)
    assert repo.get_state("regime")["name"] == "neutral"
    assert repo.get_state("missing", 7) == 7
    [sig] = repo.get_signals(["active"])
    assert sig["payload"]["filled"] == 142.5 and sig["updated_at"] == 2
    assert repo.get_signals(["new"]) == []
    assert repo.get_events(sid)[0]["kind"] == "filled"


def test_dominance_series():
    from agent.data.dominance import dominance_series
    repo = Repository()
    repo.add_dominance(1, "global", 1000, 550, 150, 50)
    s = dominance_series(repo.get_dominance("global")).iloc[0]
    assert s["usdt_d"] == pytest.approx(5) and s["btc_d"] == pytest.approx(55)
    assert s["total2"] == 450
