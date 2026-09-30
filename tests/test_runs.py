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
    assert res.shortlist[0].base == "SOL" and res.shortlist[0].score >= 90
    assert all(c.side == 1 for c in res.shortlist)              # alt season: longs only
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
