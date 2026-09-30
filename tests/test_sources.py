import ccxt
import pytest

from agent.data.coingecko import CoinGecko, CoinGeckoError, filter_universe
from agent.data.exchange import AllExchangesFailed, ExchangeClient, UnsupportedExchange
from agent.data.sources_check import check_sources, format_report, working_exchanges
from agent.config import load_secrets
from agent.store.repository import Repository


class FakeExchange:
    has = {"fetchOpenInterestHistory": True}

    def __init__(self, name, fail=False):
        self.name, self.fail, self.calls = name, fail, 0

    def load_markets(self):
        if self.fail:
            raise ccxt.ExchangeNotAvailable("451 restricted location")
        return {"BTC/USDT:USDT": {}}

    def fetch_ohlcv(self, symbol, timeframe, since=None, limit=None):
        self.calls += 1
        if self.fail:
            raise ccxt.ExchangeNotAvailable("451 restricted location")
        return [[1, 1, 2, 0.5, 1.5, 10], [2, 1.5, 2, 1, 1.8, 12]]

    def fetch_funding_rate(self, symbol):
        return {"fundingRate": 0.0001}

    def fetch_open_interest_history(self, symbol, timeframe, since=None, limit=None):
        return [{}, {}]

    def parse_timeframe(self, tf):
        return 1

    def milliseconds(self):
        return 5000


def factory_with(failing: set[str], unsupported: set[str] = frozenset()):
    def factory(name, cfg):
        if name in unsupported:
            raise UnsupportedExchange(name)
        return FakeExchange(name, fail=name in failing)
    return factory


def test_client_falls_back_in_order(cfg):
    cfg["exchange"].update(primary="okx", fallbacks=["bitunix", "bitget"])
    client = ExchangeClient(cfg, factory=factory_with({"okx"}, {"bitunix"}), sleep=lambda s: None)
    name, rows = client.fetch_ohlcv("BTC/USDT:USDT", "4h")
    assert name == "bitget" and rows[-1][4] == 1.8


def test_client_raises_when_all_fail(cfg):
    cfg["exchange"].update(primary="okx", fallbacks=["bitget"])
    client = ExchangeClient(cfg, factory=factory_with({"okx", "bitget"}), sleep=lambda s: None)
    with pytest.raises(AllExchangesFailed):
        client.fetch_ohlcv("BTC/USDT:USDT", "4h")


def test_network_errors_are_retried(cfg):
    cfg["exchange"].update(primary="okx", fallbacks=[])
    ex = FakeExchange("okx")
    attempts = []

    def flaky(*a, **k):
        attempts.append(1)
        if len(attempts) < 3:
            raise ccxt.NetworkError("timeout")
        return [[1, 1, 1, 1, 1, 1]]
    ex.fetch_ohlcv = flaky
    client = ExchangeClient(cfg, factory=lambda n, c: ex, sleep=lambda s: None)
    assert client.fetch_ohlcv("X", "4h")[1] == [[1, 1, 1, 1, 1, 1]]
    assert len(attempts) == 3


class FakeResponse:
    def __init__(self, status, body=None, headers=None):
        self.status_code, self._body, self.headers, self.text = status, body, headers or {}, ""

    def json(self):
        return self._body


class FakeSession:
    def __init__(self, responses):
        self.responses, self.headers, self.urls = list(responses), {}, []

    def get(self, url, params=None, timeout=None):
        self.urls.append((url, params))
        return self.responses.pop(0)


def test_coingecko_retries_429_then_caches(cfg):
    sleeps = []
    session = FakeSession([FakeResponse(429, headers={"Retry-After": "7"}),
                           FakeResponse(200, {"data": {"x": 1}})])
    cg = CoinGecko(cfg, repo=Repository(), api_key="k", session=session, sleep=sleeps.append,
                   clock=lambda: 0.0)
    assert cg.global_data() == {"x": 1}
    assert cg.global_data() == {"x": 1}          # served from cache
    assert len(session.urls) == 2
    assert 7.0 in sleeps
    assert session.headers[cfg["coingecko"]["demo_header"]] == "k"


def test_coingecko_gives_up(cfg):
    cfg["coingecko"]["retries"] = 1
    cg = CoinGecko(cfg, session=FakeSession([FakeResponse(500), FakeResponse(500)]),
                   sleep=lambda s: None)
    with pytest.raises(CoinGeckoError):
        cg.ping()


def test_filter_universe_removes_stables_and_wrapped():
    markets = [
        {"id": "bitcoin", "symbol": "btc", "market_cap": 100},
        {"id": "tether", "symbol": "usdt", "market_cap": 90},
        {"id": "wrapped-bitcoin", "symbol": "wbtc", "market_cap": 80},
        {"id": "some-new-stable", "symbol": "xusd", "market_cap": 70},
        {"id": "solana", "symbol": "sol", "market_cap": 60},
        {"id": "no-cap", "symbol": "nc", "market_cap": None},
        {"id": "ripple", "symbol": "xrp", "market_cap": 65},
    ]
    out = filter_universe(markets, {"wrapped-bitcoin", "some-new-stable"}, {"usdt"}, top_n=2)
    assert [m["id"] for m in out] == ["bitcoin", "ripple"]


def test_check_sources_report(cfg):
    cfg["exchange"].update(primary="okx", fallbacks=["bitunix", "bitget"])

    class FakeCG:
        def ping(self): return {"gecko_says": "(V3) To the Moon!"}
        def global_data(self): return {"market_cap_percentage": {"btc": 57.1}}
        def top_universe(self): return [{}] * 100
        def categories(self): raise RuntimeError("HTTP 429")

    probes = check_sources(cfg, load_secrets({}), factory=factory_with({"bitget"}, {"bitunix"}),
                           cg_client=FakeCG())
    assert working_exchanges(probes) == ["okx"]
    report = format_report(probes)
    assert "working exchanges (perp candles): okx" in report
    cats = next(p for p in probes if p.check == "categories")
    assert not cats.ok and "429" in cats.detail
    assert any(p.source == "telegram" and not p.ok for p in probes)


def test_history_falls_back_to_latest_when_since_predates_listing(cfg):
    cfg["exchange"].update(primary="okx", fallbacks=[])
    ex = FakeExchange("okx")
    calls = []

    def fetch(symbol, tf, since=None, limit=None):
        calls.append(since)
        return [] if since is not None else [[4000, 1, 1, 1, 1, 1], [4001, 1, 1, 1, 2, 1]]
    ex.fetch_ohlcv = fetch
    client = ExchangeClient(cfg, factory=lambda n, c: ex, sleep=lambda s: None)
    name, rows = client.fetch_history("NEW/USDT:USDT", "1w", since=0, until=5000)
    assert name == "okx" and [r[0] for r in rows] == [4000, 4001]
    assert calls == [0, None]
