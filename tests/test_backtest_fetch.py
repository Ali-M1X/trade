import importlib.util
from pathlib import Path

import pytest

from agent.backtest.data import fetch_history
from agent.store.repository import Repository

DAY = 86_400_000
NOW = 1_794_700_800_000


class FakeClient:
    names = ["gate"]

    def fetch_history(self, symbol, tf, since, until):
        step = {"1w": 7 * DAY, "1d": DAY, "4h": DAY // 6, "1h": DAY // 24}[tf]
        t0 = until - 20 * step
        return "gate", [[t0 + i * step, 10, 11, 9, 10.0 + i * 0.1, 1000] for i in range(20)]

    def _call_one(self, name, method, *args):
        return []

    def exchange(self, name):
        raise RuntimeError("no contract sizes here")


class FakeMarket:
    def perp_bases_and_spreads(self):
        return {"BTC", "ETH", "SOL"}, {}


class FakeCG:
    def __init__(self):
        self.calls = []

    def markets(self, *a, **k):
        return self.top_universe()

    def top_universe(self, top_n=None):
        return [{"id": "bitcoin", "symbol": "btc", "market_cap": 3, "total_volume": 9e9, "circulating_supply": 2},
                {"id": "solana", "symbol": "sol", "market_cap": 1, "total_volume": 1e3, "circulating_supply": 5}]

    def trending_ids(self):
        return []

    def markets_by_ids(self, ids):
        return []

    def coin_categories(self, cid):
        self.calls.append(("categories", cid))
        return ["Layer 1"]

    def market_chart(self, cid, days):
        raise AssertionError("cg_charts off: no daily charts")


def test_fetch_without_cg_charts_builds_history_from_candles(cfg):
    cfg["backtest"].update(cg_charts=False, days=10, max_coins=0)
    cfg["universe"]["add_exchange_trending"] = False
    repo, cg = Repository(), FakeCG()
    repo.add_dominance(NOW - DAY, "recon", 100, 50, 20, 5)        # seeded beforehand
    stats = fetch_history(cfg, repo, FakeClient(), FakeMarket(), cg, NOW)
    assert stats["candles"] > 0
    hist = repo.get_coin_history("solana")
    assert len(hist) == 20 and hist.mcap.iloc[-1] == pytest.approx(5 * 11.9)   # close x supply
    cats = repo.get_state("bt_categories")
    assert cats["solana"] == [] and cats["bitcoin"] == ["Layer 1"]   # only liquid coins asked
    assert cg.calls == [("categories", "bitcoin")]
    assert repo.get_state("bt_meta")["cg_charts"] is False


def test_seed_copies_dominance_and_cache(tmp_path):
    live = Repository(tmp_path / "live.db")
    live.add_dominance(1, "recon", 100, 50, 20, 5)
    live.add_dominance(2, "global", 101, 51, 21, 5)
    live.cache_put("cg:/coins/x", {"categories": ["AI"]})
    live.close()
    spec = importlib.util.spec_from_file_location(
        "seed", Path(__file__).parents[1] / "scripts" / "seed_backtest_db.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.main(str(tmp_path / "live.db"), str(tmp_path / "bt.db"))
    bt = Repository(tmp_path / "bt.db")
    assert len(bt.get_dominance()) == 2
    assert bt.cache_get("cg:/coins/x", 3600) == {"categories": ["AI"]}
