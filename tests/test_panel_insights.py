from types import SimpleNamespace
import copy
import json
import time

import pytest

from agent import panel_insights as pi
from agent.cli import _log_run
from agent.panel import build
from agent.panel_api import apply_mark, make_handler
from agent.store.repository import Repository

HOUR = 3_600_000
NOW = 1_794_700_800_000                        # 2026-11-15 00:00 UTC


def _cfg(cfg, tmp_path, **panel):
    c = copy.deepcopy(cfg)
    c["storage"]["db_path"] = str(tmp_path / "state.db")
    c["system2"] = {"enabled": False}
    c["shadow"] = {"enabled": False}
    c["panel"] = {"start": "2026-11-06", "balance_usd": 100, "backfill": str(tmp_path / "none.json"), **panel}
    return c


def test_stats_summary_reads_both_gate_field_styles():
    rows = [{"time": 2, "open_interest_usd": 1200, "lsr_account": 2.0, "long_liq_usd": 10, "short_liq_usd": 5},
            {"time": 1, "open_interest": 100, "mark_price": 10, "long_users": 3, "short_users": 1,
             "long_liq_size": 2, "short_liq_size": 0}]
    s = pi.stats_summary(rows)
    assert s["oi_usd"] == 1200 and s["oi_change"] == 20.0      # oldest first after sorting by time
    assert s["lsr_account"] == 2.0
    assert s["liq_long_usd"] == 30.0 and s["liq_short_usd"] == 5.0
    assert s["hours"] == 2
    assert pi.stats_summary([]) is None


def test_calendar_keeps_every_country_with_kind_and_crypto_weight():
    data = [{"title": "CPI m/m", "country": "USD", "impact": "High", "date": "2026-11-13T08:30:00-05:00", "forecast": "0.3%"},
            {"title": "GDP", "country": "EUR", "impact": "High", "date": "2026-11-13T08:30:00-05:00"},
            {"title": "Bank Holiday", "country": "USD", "impact": "Holiday", "date": "2026-11-11T00:00:00-05:00"},
            {"title": "Trade Balance", "country": "NZD", "impact": "Low", "date": "2026-11-12T00:00:00-05:00"},
            {"title": "Claims", "country": "USD", "impact": "Medium", "date": "bad"}]
    out = pi._calendar(data)
    assert [e["title"] for e in out] == ["Bank Holiday", "Trade Balance", "CPI m/m", "GDP"]
    cpi = out[2]
    assert cpi["ts"] == 1_794_576_600_000 and cpi["forecast"] == "0.3%" and cpi["kind"] == "cpi" and cpi["w"] == 5
    assert out[3]["kind"] == "gdp" and out[3]["w"] == 3
    assert out[0]["kind"] == "holiday" and out[0]["w"] == 1 and out[1]["w"] == 0
    assert pi.event_kind("FOMC Statement") == "fomc" and pi.event_kind("Non-Farm Employment Change") == "nfp"


def test_calendar_merges_this_and_next_week():
    cache = {"calendar": {"v": [{"ts": NOW - 72 * HOUR, "title": "old"}, {"ts": NOW + HOUR, "title": "a", "country": "USD"}]},
             "calendar_next": {"v": [{"ts": NOW + HOUR, "title": "a", "country": "USD"}, {"ts": NOW + 100 * HOUR, "title": "b"}]}}
    assert [e["title"] for e in pi._merge_calendar(cache, NOW)] == ["a", "b"]


def test_calendar_history_keeps_a_week_and_takes_late_actuals():
    cache = {"calendar": {"v": [{"ts": NOW - HOUR, "title": "CPI", "country": "USD", "actual": "", "w": 5},
                                {"ts": NOW - 9 * 24 * HOUR, "title": "old", "country": "EUR", "w": 1}]}}
    hist = pi.remember_calendar(cache, NOW)
    assert [e["title"] for e in hist.values()] == ["CPI"]                 # older than 8 days: dropped
    cache["calendar"]["v"][0]["actual"] = "0.4%"
    pi.remember_calendar(cache, NOW)
    cache["calendar"] = {"v": []}                                         # the source moved on to a new week
    hist = pi.remember_calendar(cache, NOW)
    assert list(hist.values())[0]["actual"] == "0.4%"


def test_btc_reaction_from_15m_candles():
    q = 900_000
    t0 = 1_000 * q
    # [open ms, open, close, high, low]; 18 candles, the last one still open
    c = [[t0 + i * q, 100 + i, 101 + i, 102 + i, 99 + i] for i in range(18)]
    r = pi.btc_reaction(c, t0 + 5 * 60_000)                               # released 5 min into the candle
    assert r == {"p0": 100, "m15": 1.0, "h1": 4.0, "h4": 16.0, "swing_h1": round((105 / 99 - 1) * 100, 3)}
    assert "h4" not in pi.btc_reaction(c[:16], t0)                       # the 16th candle is the open one
    assert pi.btc_reaction(c, t0 + 17 * q) is None                        # its candle is still open


class _Resp:
    def __init__(self, data, code=200):
        self.status_code, self._data = code, data

    def json(self):
        return self._data


def test_market_data_caches_and_survives_a_dead_network(cfg, tmp_path):
    c = _cfg(cfg, tmp_path, market_data=True)
    asked = []

    def get(url, params=None, timeout=None, headers=None):
        asked.append(url)
        if url.endswith("/tickers"):
            return _Resp([{"contract": "BTC_USDT", "last": "90000", "funding_rate": "0.0001", "change_percentage": "-1.5",
                           "volume_24h_quote": "1e9", "high_24h": "91000", "low_24h": "88000"}])
        if url.endswith("/contract_stats"):
            return _Resp([{"time": 1, "open_interest_usd": 100}, {"time": 2, "open_interest_usd": 110, "lsr_account": 1.2}])
        if "alternative.me" in url:
            return _Resp({"data": [{"timestamp": "200", "value": "40", "value_classification": "Fear"},
                                   {"timestamp": "100", "value": "30", "value_classification": "Fear"}]})
        return _Resp([{"title": "CPI m/m", "country": "USD", "impact": "High", "date": "2099-01-01T08:30:00-05:00"}])

    m = pi.market_data(c, ["BTC"], NOW, pi._Net(get=get))
    assert m["deriv"]["BTC"]["funding"] == 0.0001 and m["deriv"]["BTC"]["high"] == 91000
    assert m["deriv"]["BTC"]["oi_change"] == 10.0 and m["deriv"]["BTC"]["lsr_account"] == 1.2
    assert m["fng"] == [[100_000, 30, "Fear"], [200_000, 40, "Fear"]]        # oldest first
    assert m["calendar"][0]["title"] == "CPI m/m"
    n = len(asked)

    def dead(*a, **k):
        raise OSError("offline")
    m2 = pi.market_data(c, ["BTC"], NOW, pi._Net(get=dead))           # fresh cache: nothing asked
    assert m2["deriv"]["BTC"]["funding"] == 0.0001 and len(asked) == n
    assert pi.market_data(_cfg(cfg, tmp_path), ["BTC"], NOW) == {}     # switched off


def test_market_data_keeps_past_releases_with_btc_reaction(cfg, tmp_path):
    c = _cfg(cfg, tmp_path, market_data=True)
    q = 900_000
    ts = NOW - 6 * HOUR
    ts -= ts % q
    t0 = ts - q

    def get(url, params=None, timeout=None, headers=None):
        if url.endswith("/candlesticks"):
            return _Resp([{"t": (t0 + i * q) // 1000, "o": "100", "c": str(100 + i), "h": "200", "l": "50"} for i in range(30)])
        if "faireconomy" in url and "next" not in url:
            iso = time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime(ts / 1000))
            return _Resp([{"title": "CPI m/m", "country": "USD", "impact": "High", "date": iso,
                           "actual": "0.4%", "forecast": "0.3%"}])
        return _Resp([])

    m = pi.market_data(c, [], NOW, pi._Net(get=get))
    past = m["calendar_past"]
    assert [e["title"] for e in past] == ["CPI m/m"]
    assert past[0]["btc"]["h1"] == 4.0 and past[0]["btc"]["h4"] == 16.0   # candle 1 is the release's


def _candles(repo, base, tf, step, closes, start):
    repo.upsert_candles("gate", f"{base}/USDT:USDT", tf,
                        [[start + i * step, c, c * 1.01, c * 0.99, c, 1] for i, c in enumerate(closes)])


def test_excursions_use_the_finest_stored_candles(tmp_path):
    repo = Repository(tmp_path / "s.db")
    _candles(repo, "ABC", "1h", HOUR, [100, 104, 108, 96, 103], NOW)
    t = {"base": "ABC", "side": 1, "entry": 100.0, "sl": 95.0, "filled_at": NOW, "closed_at": NOW + 4 * HOUR}
    pi.excursions(repo, [t], NOW + 10 * HOUR)
    assert t["exc_tf"] == "1h"
    assert t["mfe_r"] == pytest.approx((108 * 1.01 - 100) / 5, abs=1e-3)
    assert t["mae_r"] == pytest.approx((100 - 96 * 0.99) / 5, abs=1e-3)


def test_beta_falls_back_to_4h_candles(tmp_path):
    repo = Repository(tmp_path / "s.db")
    import math
    btc = [100 * (1 + 0.01 * math.sin(i)) for i in range(120)]
    alt = [50 * (1 + 0.02 * math.sin(i)) for i in range(120)]
    start = NOW - 119 * 4 * HOUR
    _candles(repo, "BTC", "4h", 4 * HOUR, btc, start)
    _candles(repo, "ALT", "4h", 4 * HOUR, alt, start)
    b = pi.beta_btc(repo, ["ALT", "BTC", "NONE"], NOW)
    assert b["BTC"] == {"corr": 1.0, "beta": 1.0}
    assert b["ALT"]["corr"] > 0.99 and 1.8 < b["ALT"]["beta"] < 2.2
    assert "NONE" not in b


def test_news_view_scores_and_measures_headlines():
    assert pi.news_stars(0, "media", {}) == 1
    assert pi.news_stars(-2, "exchange", {}) == 4
    assert pi.news_stars(1, "media", {"X": {"1h": 9.0}}) == 4
    assert pi.news_stars(2, "exchange", {"X": {"4h": -12.0}}) == 5          # capped
    repo = Repository(":memory:")
    base = {"source": "okx", "kind": "exchange", "url": "javascript:alert(1)", "alerted": 1}
    repo.add_news({**base, "id": "a", "title": "OKX to list ABC", "seen_at": NOW - HOUR, "level": 1, "rule": "okx_spot_listing",
                   "coins": ["ABC"], "prices": {"ABC": {"p0": 10, "1h": 10.5, "4h": 9.0, "24h": 11}}})
    repo.add_news({**base, "id": "b", "title": "stocks", "seen_at": NOW - HOUR, "level": 0, "rule": "ignored", "coins": []})
    # first read today, published months ago: not on the page
    repo.add_news({**base, "id": "c", "title": "OKX to delist OLD", "ts": NOW - 200 * 24 * HOUR, "seen_at": NOW - HOUR,
                   "level": -2, "rule": "okx_delist", "coins": ["OLD"]})
    v = pi.news_view([repo], NOW)
    assert [i["title"] for i in v["items"]] == ["OKX to list ABC"]
    it = v["items"][0]
    assert it["moves"] == {"ABC": {"1h": 5.0, "4h": -10.0, "24h": 10.0}} and it["stars"] == 5
    assert it["url"] is None and it["rule_fa"] == "لیستینگ اسپات OKX"
    assert v["rules"][0]["n"] == 1 and v["rules"][0]["avg_24h"] == 10.0 and v["rules"][0]["hit"] == 100


def test_marks_are_validated_and_written_atomically(cfg, tmp_path):
    c = _cfg(cfg, tmp_path)
    assert apply_mark(c, {"id": "S1-12", "taken": True})["S1-12"]["taken"] is True
    assert apply_mark(c, {"id": "S1-13", "taken": False})["S1-13"]["taken"] is False
    assert "S1-12" not in apply_mark(c, {"id": "S1-12", "taken": None})
    for bad in ({"id": "../x", "taken": True}, {"id": "S1-1", "taken": "yes"}, {"taken": True}):
        with pytest.raises(ValueError):
            apply_mark(c, bad)
    assert set(pi.read_marks(c)) == {"S1-13"}


def _request(cfg, method, path, body=None, headers=None):
    import http.client
    import threading
    from http.server import HTTPServer
    srv = HTTPServer(("127.0.0.1", 0), make_handler(cfg))
    th = threading.Thread(target=srv.handle_request)
    th.start()
    try:
        conn = http.client.HTTPConnection("127.0.0.1", srv.server_port, timeout=5)
        raw = json.dumps(body) if body is not None else None
        conn.request(method, path, body=raw, headers={"Host": "signal.example", **(headers or {})})
        r = conn.getresponse()
        return r.status, json.loads(r.read() or b"null")
    finally:
        th.join(5)
        srv.server_close()


def test_panel_api_http(cfg, tmp_path):
    c = _cfg(cfg, tmp_path)
    code, body = _request(c, "POST", "/api/mark", {"id": "S2-5", "taken": True}, {"Origin": "https://signal.example"})
    assert code == 200 and body["S2-5"]["taken"] is True
    assert _request(c, "GET", "/api/marks")[1]["S2-5"]["taken"] is True
    assert _request(c, "POST", "/api/mark", {"id": "S2-6", "taken": True}, {"Origin": "https://evil.example"})[0] == 403
    assert _request(c, "POST", "/api/mark", {"id": "S2-6", "taken": 3})[0] == 400
    assert _request(c, "GET", "/api/other")[0] == 404


def test_run_log_keeps_the_last_runs(tmp_path):
    repo = Repository(tmp_path / "s.db")
    for i in range(305):
        client = SimpleNamespace(failures={"gate": 1}, last_error={"gate": "fetch_ohlcv X: BadRequest: too old"}) if i == 304 else None
        _log_run(repo, int(time.time() * 1000) - 5000, ["run-1h"], [], client)
    log = repo.get_state("run_log")
    assert len(log) == 300 and log[-1]["ex"] == {"gate": 1} and 4 <= log[-1]["dur"] < 30
    assert log[-1]["ex_err"]["gate"].startswith("fetch_ohlcv") and log[-2]["ex_err"] == {}


def test_build_carries_the_insights(cfg, tmp_path):
    c = _cfg(cfg, tmp_path, api=True)
    Repository(c["storage"]["db_path"]).set_state("run_log", [{"ts": NOW, "dur": 40, "tasks": ["run-1h"], "failed": [], "ex": {}}])
    apply_mark(c, {"id": "S1-1", "taken": True})
    build(c, tmp_path / "out", NOW)
    d = json.loads((tmp_path / "out" / "data.json").read_text())
    assert d["health"]["runs"][0]["dur"] == 40 and d["marks"]["S1-1"]["taken"] is True
    assert d["panel_api"] is True and d["market"] == {} and d["news_view"]["items"] == []
    v = json.loads((tmp_path / "out" / "version.json").read_text())
    assert v["now"] == NOW and len(v["app"]) == 12 and d["app"] == v["app"]


def test_panel_lists_refused_signals_of_both_systems(cfg, tmp_path):
    from agent.panel import skipped_signals
    from agent.runs import SKIP_KEY
    c = copy.deepcopy(cfg)
    c["storage"]["db_path"] = str(tmp_path / "s1.db")
    c["system2"] = {"enabled": True, "db_path": str(tmp_path / "s2.db")}
    Repository(tmp_path / "s1.db").set_state(SKIP_KEY, [{"ts": 1, "last": 5, "n": 2, "base": "SOL", "side": 1,
                                                         "grade": "A", "score": 80, "reason": "max_active"}])
    Repository(tmp_path / "s2.db").set_state(SKIP_KEY, [{"ts": 7, "last": 9, "n": 1, "base": "ADA", "side": -1,
                                                         "grade": "B", "score": 66, "reason": "correlated"}])
    s = skipped_signals(c)
    assert [(x["base"], x["system"]) for x in s] == [("ADA", "سیستم ۲"), ("SOL", "سیستم ۱")]
