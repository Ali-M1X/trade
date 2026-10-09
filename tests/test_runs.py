import numpy as np
import pandas as pd
import pytest

from agent.data.dominance import merged_history, reconstruct
from agent.data.market import TF_MS, LiveMarket, closed_only
from agent.data.synthetic import ratio_candles
from agent.runs import run_1h, run_4h, summarize_evaluations, summarize_funnel
from agent.store.repository import Repository
from builders import from_closes, trend

DAY = 86_400_000
NOW = 1_760_000_000_000 - (1_760_000_000_000 % DAY)          # a day boundary


def test_closed_only_drops_forming_candle():
    df = pd.DataFrame({"ts": [0, TF_MS["4h"], 2 * TF_MS["4h"]]})
    assert list(closed_only(df, "4h", 2 * TF_MS["4h"] + 5)["ts"]) == [0, TF_MS["4h"]]
    assert len(closed_only(df, "4h", 3 * TF_MS["4h"])) == 3


def test_ratio_candles():
    a = pd.DataFrame({"ts": [1, 2, 3], "open": [10, 11, 12], "high": [12, 12, 13],
                      "low": [9, 10, 11], "close": [11, 12, 12.5], "volume": [5, 6, 7]})
    b = pd.DataFrame({"ts": [2, 3], "open": [2, 2], "high": [2.5, 2], "low": [1.5, 2],
                      "close": [2, 2.5], "volume": [1, 1]})
    r = ratio_candles(a, b)
    assert list(r["ts"]) == [2, 3] and r["close"].tolist() == [6, 5]
    assert (r["high"] >= r[["open", "close"]].max(axis=1)).all()
    assert (r["low"] <= r[["open", "close"]].min(axis=1)).all()


def test_repository_prune():
    repo = Repository()
    repo.upsert_candles("okx", "X", "1h", [[i, 1, 1, 1, 1, 1] for i in range(10)])
    repo.prune_candles("okx", "X", "1h", 4)
    assert list(repo.get_candles("okx", "X", "1h")["ts"]) == [6, 7, 8, 9]


def test_reconstruct_and_merge():
    d = [0, DAY, 2 * DAY]
    charts = {"bitcoin": [[t, 60] for t in d], "tether": [[t, 5] for t in d],
              "ethereum": [[t, 15] for t in d], "solana": [[t + 3600, 20] for t in d]}
    rec = reconstruct(charts)
    assert rec["total_mcap"].tolist() == [100, 100, 100] and rec["btc_mcap"].iloc[0] == 60
    repo = Repository()
    for r in rec.itertuples():
        repo.add_dominance(r.ts, "recon", r.total_mcap, r.btc_mcap, r.eth_mcap, r.usdt_mcap)
    repo.add_dominance(2 * DAY, "global", 200, 110, 30, 10)     # global covers more coins
    m = merged_history(repo)
    assert m["ts"].tolist() == [0, DAY, 2 * DAY]
    assert m["total_mcap"].tolist() == [200, 200, 200]          # scaled to the join
    assert m["btc_mcap"].iloc[0] == pytest.approx(110)


class FakeClient:
    """Serves candles from a dict; mimics ExchangeClient.fetch_history."""
    names = ["okx"]

    def __init__(self, series):
        self.series, self.calls = series, 0

    def fetch_history(self, symbol, tf, since, until):
        self.calls += 1
        df = self.series[(symbol, tf)]
        rows = df[(df["ts"] >= since) & (df["ts"] <= until)]
        return "okx", rows[["ts", "open", "high", "low", "close", "volume"]].values.tolist()


def test_live_market_caches_and_filters(cfg):
    closes = trend(400)
    df = from_closes(closes, tf="4h", t0=NOW - 400 * TF_MS["4h"])
    client = FakeClient({("SOL/USDT:USDT", "4h"): df})
    repo = Repository()
    now = NOW - TF_MS["4h"] // 2                               # the last candle is still open
    m = LiveMarket(cfg, repo, client, now)
    out = m.candles("SOL", "4h")
    assert out["ts"].iloc[-1] + TF_MS["4h"] <= now
    assert len(out) <= cfg["analysis"]["window_bars"] + cfg["storage"]["warmup_bars"]
    assert m.candles("SOL", "4h") is out and client.calls == 1  # memoised within a run


# ------------------------------------------------------------ end to end
class FakeMarket:
    """Synthetic data for a handful of bases: majors trend up, SOL breaks out of a range."""

    def __init__(self):
        self.cache = {}

    def candles(self, base, tf):
        key = (base, tf)
        if key not in self.cache:
            n = 300
            t0 = NOW - n * TF_MS[tf]
            if base == "SOL":
                closes = np.r_[100 + 0.5 * np.sin(np.arange(n - 1)), 104]
                vols = np.r_[np.full(n - 1, 1000.0), 3000]
            else:
                start = {"BTC": 60000, "ETH": 3000}.get(base, 50)
                closes = trend(n, start, start * 0.002, start * 0.01)
                vols = None
            self.cache[key] = from_closes(closes, vols, tf=tf, t0=t0)
        return self.cache[key]

    def ratio(self, base, quote_base, tf):
        return ratio_candles(self.candles(base, tf), self.candles(quote_base, tf))

    def funding_history(self, base):
        return [0.0001] * 5

    def funding_now(self, base):
        return 0.0001

    def open_interest(self, base):
        return None

    def perp_bases_and_spreads(self):
        return {"BTC", "ETH", "SOL", "DOGE"}, {"SOL": 0.02}


class FakeCG:
    def __init__(self):
        self.rows = [
            {"id": "bitcoin", "symbol": "btc", "market_cap": 1e12, "total_volume": 3e10,
             "price_change_percentage_7d_in_currency": 3},
            {"id": "ethereum", "symbol": "eth", "market_cap": 4e11, "total_volume": 1e10,
             "price_change_percentage_7d_in_currency": 2},
            {"id": "solana", "symbol": "sol", "market_cap": 8e10, "total_volume": 3e9,
             "price_change_percentage_7d_in_currency": 12},
            {"id": "tether", "symbol": "usdt", "market_cap": 1.5e11, "total_volume": 5e10},
        ]

    def markets(self, per_page=250, page=1, category=None):
        if category == "ai":
            return [{"id": "dogecoin", "symbol": "doge", "market_cap": 2e10,
                     "total_volume": 1e9, "price_change_percentage_7d_in_currency": 20}]
        return self.rows

    def top_universe(self):
        return [r for r in self.rows if r["id"] != "tether"]

    def trending_ids(self):
        return []

    def markets_by_ids(self, ids):
        return []

    def global_data(self):
        return {"total_market_cap": {"usd": 2e12},
                "market_cap_percentage": {"btc": 50, "eth": 20, "usdt": 7.5}}

    def market_chart(self, coin_id, days):
        caps = {"bitcoin": 1e12, "tether": 1.5e11, "ethereum": 4e11}.get(coin_id, 1e11)
        i = np.arange(days)
        # BTC.D and USDT.D fall, alts rise: an alt season
        growth = {"bitcoin": 1 + 0.0005 * i, "tether": 1 - 0.0005 * i}.get(coin_id, 1 + 0.002 * i)
        wave = 1 + 0.02 * np.sin(i / 4)
        return {"market_caps": [[NOW - (days - k) * DAY, caps * growth[k] * wave[k]]
                                for k in range(days)]}

    def coin_categories(self, coin_id):
        return {"solana": ["AI", "Layer 1"], "bitcoin": ["Layer 1"],
                "ethereum": ["Layer 1", "AI"]}.get(coin_id, [])

    def categories_list(self):
        return [{"category_id": "ai", "name": "AI"}]


def test_run_4h_then_1h(cfg):
    repo = Repository()
    res = run_4h(cfg, repo, FakeMarket(), FakeCG(), NOW)
    state = repo.get_state("funnel")
    assert res.regime.name == state["regime"]["name"] == "alt_season"
    assert res.watchlist["SOL"] == ["EARLY_TREND", "RS_LEADER", "HOT_SECTOR"]
    assert "AI" in res.hot
    assert res.watchlist["DOGE"] == ["HOT_SECTOR"]              # joined via the hot category
    sol = next(c for c in res.shortlist if c.base == "SOL")
    assert sol.score >= 90 and sol.score == max(c.score for c in res.shortlist)
    # SOL broke out far above its flip level, so coins sitting near a level rank first
    assert res.shortlist[-1].base == "SOL" and sol.level_atr > cfg["shortlist"]["near_level_atr"]
    assert all(c.near_level(cfg) for c in res.shortlist[:-1])
    assert all(c.side == 1 for c in res.shortlist)              # alt season: longs only
    watch = repo.get_state("breakout_watch")
    assert watch["SOL"]["labels"] == ["EARLY_TREND", "RS_LEADER"]
    assert watch["SOL"]["level"] == pytest.approx(sol.flip_level) and sol.flip_level < 104
    assert len(repo.get_dominance("recon")) == cfg["regime"]["backfill_days"]
    assert "regime:" in summarize_funnel(res)

    evs = run_1h(cfg, repo, FakeMarket(), NOW)
    assert [e.base for e in evs] == [c["base"] for c in state["shortlist"]]
    assert repo.get_state("evaluations")["items"] == [e.to_dict() for e in evs]
    by = {e.base: e for e in evs}
    assert by["SOL"].rejected == "no_level"                     # broke out, nothing to lean on
    assert by["BTC"].is_signal and by["BTC"].plan.leverage >= 1
    assert "BTC" in summarize_evaluations(evs)


def test_run_1h_without_state(cfg):
    assert run_1h(cfg, Repository(), FakeMarket(), NOW) == []


# ------------------------------------------------------------ step 4 wiring
def test_run_1h_publishes_signals_and_respects_correlation(cfg):
    import io

    from agent.config import load_secrets
    from agent.notify.telegram import Notifier
    from agent.signals.manager import SignalBook
    repo = Repository()
    run_4h(cfg, repo, FakeMarket(), FakeCG(), NOW)
    out = io.StringIO()
    notifier = Notifier(cfg, load_secrets({}), out=out)
    evs = run_1h(cfg, repo, FakeMarket(), NOW, notifier)
    signals = [e for e in evs if e.is_signal]
    opened = SignalBook(repo, cfg).open_signals()
    # BTC, ETH and DOGE move identically in the fake data (correlation 1): only 2 per side
    assert len(signals) == 3 and len(opened) == cfg["lifecycle"]["max_correlated_same_side"]
    assert all(m.startswith("🟢 ") and "USDT | LONG | امتیاز " in m for m in notifier.sent)
    assert "dry-run" in out.getvalue()
    # the same setups an hour later are duplicates, nothing new is sent
    notifier.sent.clear()
    run_1h(cfg, repo, FakeMarket(), NOW, notifier)
    assert notifier.sent == [] and len(SignalBook(repo, cfg).open_signals()) == 2


class ScriptedMarket:
    """15m and 4H candles after a signal's creation, for run-15m."""

    def __init__(self, m15, h4):
        self.data = {"15m": m15, "4h": h4}

    def candles(self, base, tf):
        return self.data[tf]


def test_manage_fills_hits_targets_and_reports(cfg):
    from agent.config import load_secrets
    from agent.layers.trade import TradePlan
    from agent.notify.telegram import Notifier
    from agent.runs import daily, manage
    from agent.signals.manager import SignalBook
    repo = Repository()
    book = SignalBook(repo, cfg)

    class Ev:
        base, side, score, grade = "SOL", 1, 80, "A"
        plan = TradePlan(1, "limit", 100.0, 99.8, 100.2, 95.0, 110.0, 115.0, 2, 3, False,
                         100.0, "cluster", "4h", 2, 1.0)
    book.create(Ev(), NOW, {})
    q = 900_000
    m15 = pd.DataFrame({"ts": [NOW - q, NOW + q, NOW + 2 * q, NOW + 3 * q],
                        "open": 0.0, "high": [120, 101, 111, 104], "low": [90, 99.9, 103, 101],
                        "close": 0.0, "volume": 1.0})
    h4 = from_closes(np.full(150, 100.0), tf="4h", t0=NOW - 150 * TF_MS["4h"])
    notifier = Notifier(cfg, load_secrets({}), out=open("/dev/null", "w"))
    events = manage(cfg, repo, ScriptedMarket(m15, h4), notifier, NOW + 4 * q)
    # the candle before creation is ignored; then fill, then TP1
    assert [e.kind for e in events] == ["filled", "tp1"]
    [sig] = book.open_signals()
    assert sig["status"] == "tp1" and sig["payload"]["lifecycle"]["sl_now"] == 100.0
    assert notifier.sent[:2] == ["✅ SOLUSDT | ورود فعال شد | 100.00", "🎯 SOLUSDT | TP1 | +2.0R | SL به ورود"]
    # re-running with the same candles changes nothing
    assert manage(cfg, repo, ScriptedMarket(m15, h4), notifier, NOW + 4 * q) == []
    # expiry does not apply once filled; the daily report lists the open signal
    repo.set_state("funnel", {"regime": {"name": "neutral", "usdt_d": 0, "btc_d": 0, "total2": 0},
                              "majors": {"btc": 0, "eth": 0, "ethbtc": 0}, "shortlist": []})
    text = daily(cfg, repo, notifier, NOW + 4 * q)
    assert "سیگنال باز: 1 از 5" in text
    assert "🟢 SOLUSDT | LONG | امتیاز 80 | ورود 100.00 | SL 95.00 | TP1 110.00 | TP2 115.00 |" in text


def test_system2_keeps_its_own_signals_and_labels_messages(cfg):
    import io

    from agent import system2
    from agent.config import load_secrets
    from agent.notify.telegram import Notifier
    from agent.signals.manager import SignalBook
    repo, repo2 = Repository(), Repository()
    run_4h(cfg, repo, FakeMarket(), FakeCG(), NOW)
    notifier = Notifier(cfg, load_secrets({}), out=io.StringIO())
    run_1h(cfg, repo, FakeMarket(), NOW, notifier)
    live = len(SignalBook(repo, cfg).open_signals())
    notifier.sent.clear()
    system2.run_system2(cfg, repo, repo2, FakeMarket(), notifier, NOW, ["run-15m", "run-1h"])
    cfg2 = system2.system2_config(cfg)
    assert cfg2["technical"]["section_weights"]["phase"] == 20 and cfg2["trade"]["base_risk_pct"] == 2.0
    assert len(SignalBook(repo, cfg).open_signals()) == live        # live book untouched
    assert SignalBook(repo2, cfg2).open_signals()                   # system 2 opened its own
    assert notifier.sent and all(m.startswith(system2.HEADER + "\n") for m in notifier.sent)
    assert repo2.get_state("evaluations")["items"]
