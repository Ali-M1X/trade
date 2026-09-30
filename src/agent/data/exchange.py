"""Exchange public data via ccxt with an ordered fallback list."""
from __future__ import annotations

import logging
import time

import ccxt

log = logging.getLogger(__name__)


class UnsupportedExchange(Exception):
    pass


class AllExchangesFailed(Exception):
    pass


def make_exchange(name: str, cfg: dict):
    if not hasattr(ccxt, name):
        raise UnsupportedExchange(f"{name} is not available in ccxt {ccxt.__version__}")
    ex_cfg = cfg["exchange"]
    return getattr(ccxt, name)({
        "enableRateLimit": True,
        "timeout": ex_cfg["timeout_ms"],
        "options": {"defaultType": ex_cfg["market_type"]},
    })


def perp_symbol(base: str, quote: str = "USDT") -> str:
    """ccxt unified symbol of a linear perpetual, e.g. BTC -> BTC/USDT:USDT."""
    return f"{base}/{quote}:{quote}"


class ExchangeClient:
    """Tries the primary exchange, then each fallback, for every call.

    The name of the exchange that answered is returned with the data so candles
    are stored per exchange and a backtest never mixes sources silently.
    """

    def __init__(self, cfg: dict, factory=make_exchange, sleep=time.sleep):
        ex_cfg = cfg["exchange"]
        self.cfg = cfg
        self.names = [ex_cfg["primary"], *ex_cfg["fallbacks"]]
        self.retries = ex_cfg["retries"]
        self.backoff = ex_cfg["retry_backoff_s"]
        self.limit = ex_cfg["ohlcv_limit"]
        self._factory = factory
        self._sleep = sleep
        self._instances: dict = {}

    def exchange(self, name: str):
        if name not in self._instances:
            self._instances[name] = self._factory(name, self.cfg)
        return self._instances[name]

    def _call_one(self, name: str, method: str, *args, **kwargs):
        ex = self.exchange(name)
        for attempt in range(self.retries):
            try:
                return getattr(ex, method)(*args, **kwargs)
            except (ccxt.NetworkError, ccxt.RateLimitExceeded):
                if attempt == self.retries - 1:
                    raise
                self._sleep(self.backoff * 2 ** attempt)

    def call(self, method: str, *args, **kwargs):
        """Returns (exchange_name, result) from the first exchange that answers."""
        errors = []
        for name in self.names:
            try:
                return name, self._call_one(name, method, *args, **kwargs)
            except (UnsupportedExchange, ccxt.BaseError) as e:
                log.warning("%s.%s failed: %s", name, method, e)
                errors.append(f"{name}: {type(e).__name__}: {e}")
        raise AllExchangesFailed(f"{method} failed on all exchanges: " + " | ".join(errors))

    def fetch_ohlcv(self, symbol: str, timeframe: str, since: int | None = None,
                    limit: int | None = None):
        return self.call("fetch_ohlcv", symbol, timeframe, since, limit or self.limit)

    def fetch_ohlcv_range(self, name: str, symbol: str, timeframe: str, since: int,
                          until: int | None = None) -> list:
        """Paginate one exchange from `since` (ms) to `until` (ms, default now)."""
        ex = self.exchange(name)
        step = ex.parse_timeframe(timeframe) * 1000
        until = until or ex.milliseconds()
        out: list = []
        cursor = since
        while cursor < until:
            batch = self._call_one(name, "fetch_ohlcv", symbol, timeframe, cursor, self.limit)
            if not batch:
                break
            out.extend(r for r in batch if r[0] <= until)
            nxt = batch[-1][0] + step
            if nxt <= cursor:
                break
            cursor = nxt
        dedup = {r[0]: r for r in out}
        return [dedup[k] for k in sorted(dedup)]

    def fetch_funding_rate(self, symbol: str):
        return self.call("fetch_funding_rate", symbol)

    def fetch_open_interest_history(self, symbol: str, timeframe: str = "1d",
                                    since: int | None = None, limit: int | None = None):
        return self.call("fetch_open_interest_history", symbol, timeframe, since, limit)
