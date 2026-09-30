"""Live market data: closed candles (cached in SQLite), funding, open interest, spreads."""
from __future__ import annotations

import logging

import pandas as pd

from .exchange import AllExchangesFailed, ExchangeClient, perp_symbol
from .synthetic import ratio_candles

log = logging.getLogger(__name__)

TF_MS = {"15m": 900_000, "1h": 3_600_000, "4h": 14_400_000, "1d": 86_400_000,
         "1w": 604_800_000}


def closed_only(df: pd.DataFrame, tf: str, now_ms: int) -> pd.DataFrame:
    """Drop the candle that is still forming (its close time is after `now`)."""
    return df[df["ts"] + TF_MS[tf] <= now_ms].reset_index(drop=True)


class LiveMarket:
    """Candles for COIN/USDT perpetuals. COIN/BTC and COIN/ETH are synthetic ratios."""

    def __init__(self, cfg: dict, repo, client: ExchangeClient, now_ms: int):
        self.cfg, self.repo, self.client, self.now = cfg, repo, client, now_ms
        self.quote = cfg["exchange"]["quote"]
        self.bars = cfg["analysis"]["window_bars"] + cfg["storage"]["warmup_bars"]
        self._mem: dict = {}

    def candles(self, base: str, tf: str) -> pd.DataFrame:
        key = (base, tf)
        if key in self._mem:
            return self._mem[key]
        symbol = perp_symbol(base, self.quote)
        exchange = self.client.names[0]
        last = self.repo.last_candle_ts(exchange, symbol, tf)
        start = self.now - self.bars * TF_MS[tf]
        since = start if last is None or last < start else last
        try:
            name, rows = self.client.fetch_history(symbol, tf, since, self.now)
        except AllExchangesFailed as e:
            log.warning("%s", e)
            name, rows = exchange, []
        if rows:
            self.repo.upsert_candles(name, symbol, tf, rows)
            self.repo.prune_candles(name, symbol, tf, self.cfg["storage"]["keep_bars"])
        df = self.repo.get_candles(name, symbol, tf, since=start)
        df = closed_only(df, tf, self.now)
        self._mem[key] = df
        return df

    def ratio(self, base: str, quote_base: str, tf: str) -> pd.DataFrame:
        return ratio_candles(self.candles(base, tf), self.candles(quote_base, tf))

    def funding_history(self, base: str, limit: int = 10) -> list[float]:
        try:
            _, rows = self.client.call("fetch_funding_rate_history",
                                       perp_symbol(base, self.quote), None, limit)
        except AllExchangesFailed:
            return []
        rows = [r for r in rows if r.get("fundingRate") is not None]
        return [float(r["fundingRate"]) for r in sorted(rows, key=lambda r: r["timestamp"])]

    def funding_now(self, base: str) -> float | None:
        try:
            _, r = self.client.fetch_funding_rate(perp_symbol(base, self.quote))
            return float(r["fundingRate"])
        except (AllExchangesFailed, KeyError, TypeError):
            return None

    def open_interest(self, base: str, days: int = 10) -> pd.DataFrame | None:
        try:
            _, rows = self.client.fetch_open_interest_history(perp_symbol(base, self.quote),
                                                              "1d", None, days)
        except AllExchangesFailed:
            return None
        pts = [(r["timestamp"], r.get("openInterestAmount") or r.get("openInterestValue"))
               for r in rows]
        pts = [(t, float(v)) for t, v in pts if t is not None and v is not None]
        if not pts:
            return None
        return pd.DataFrame(sorted(pts), columns=["ts", "oi"])

    def perp_bases_and_spreads(self) -> tuple[set[str], dict[str, float]]:
        """Bases with a USDT linear perpetual on the primary exchange, and bid/ask spread %."""
        try:
            name, tickers = self.client.call("fetch_tickers")
        except AllExchangesFailed:
            return set(), {}
        ex = self.client.exchange(name)
        bases, spreads = set(), {}
        for sym, t in tickers.items():
            m = ex.markets.get(sym) if ex.markets else None
            if not m or not m.get("swap") or not m.get("linear") or m.get("quote") != self.quote:
                continue
            bases.add(m["base"])
            bid, ask = t.get("bid"), t.get("ask")
            if bid and ask and ask >= bid > 0:
                spreads[m["base"]] = (ask - bid) / ((ask + bid) / 2) * 100
        return bases, spreads
