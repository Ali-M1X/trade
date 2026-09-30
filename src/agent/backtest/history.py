"""Stored history, served "as known at time t" to the layers.

Indicators are computed once per full series (they are causal, so slicing afterwards is
exact). A frame is rebuilt only when a new candle of that series has closed.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..analysis.frame import Frame, make_frame
from ..data.exchange import perp_symbol
from ..data.market import TF_MS
from ..data.synthetic import ratio_candles
from ..indicators.core import add_indicators

DAY = 86_400_000


class History:
    def __init__(self, cfg: dict, repo):
        self.cfg, self.repo = cfg, repo
        self.quote = cfg["exchange"]["quote"]
        self._raw: dict = {}
        self._full: dict = {}
        self._ts: dict = {}
        self._frame: dict = {}
        self._funding: dict = {}
        self._coin: dict = {}

    # ---- raw candles
    def raw(self, base: str, tf: str) -> pd.DataFrame:
        key = (base, tf)
        if key not in self._raw:
            symbol = perp_symbol(base, self.quote)
            pairs = [p for p in self.repo.candle_series(tf) if p[1] == symbol]
            frames = [self.repo.get_candles(ex, symbol, tf) for ex, _ in pairs]
            df = max(frames, key=len) if frames else pd.DataFrame(
                columns=["ts", "open", "high", "low", "close", "volume"])
            self._raw[key] = df.reset_index(drop=True)
        return self._raw[key]

    def series(self, base: str, tf: str, quote_base: str | None = None) -> pd.DataFrame:
        """Full candles with indicators. quote_base: synthetic COIN/<quote_base>."""
        key = (base, quote_base, tf)
        if key not in self._full:
            df = self.raw(base, tf)
            if quote_base:
                df = ratio_candles(df, self.raw(quote_base, tf))
            full = add_indicators(df, self.cfg) if len(df) else df
            self._full[key] = full
            self._ts[key] = full["ts"].to_numpy(dtype=np.int64) if len(full) else np.array([], np.int64)
        return self._full[key]

    def closed(self, base: str, tf: str, t: int, quote_base: str | None = None) -> pd.DataFrame:
        """Candles closed by time t (open time + duration <= t)."""
        full = self.series(base, tf, quote_base)
        n = int(np.searchsorted(self._ts[(base, quote_base, tf)], t - TF_MS[tf], side="right"))
        return full.iloc[:n]

    def frame(self, base: str, tf: str, t: int, quote_base: str | None = None) -> Frame | None:
        df = self.closed(base, tf, t, quote_base)
        if len(df) < 2:
            return None
        key = (base, quote_base, tf)
        last = int(df["ts"].iloc[-1])
        cached = self._frame.get(key)
        if cached and cached[0] == last:
            return cached[1]
        f = make_frame(df.iloc[-self.cfg["analysis"]["window_bars"]:], tf, self.cfg)
        self._frame[key] = (last, f)
        return f

    def last_close(self, base: str, tf: str, t: int) -> float | None:
        df = self.closed(base, tf, t)
        return float(df["close"].iloc[-1]) if len(df) else None

    # ---- funding
    def funding_series(self, base: str) -> pd.DataFrame:
        if base not in self._funding:
            self._funding[base] = self.repo.get_funding(perp_symbol(base, self.quote))
        return self._funding[base]

    def funding_until(self, base: str, t: int, n: int) -> list[float]:
        f = self.funding_series(base)
        return f.loc[f["ts"] <= t, "rate"].iloc[-n:].tolist()

    def funding_rate_at(self, base: str, t: int) -> float | None:
        """The rate of the funding event exactly at t, if stored."""
        f = self.funding_series(base)
        hit = f.loc[f["ts"] // 60_000 == t // 60_000, "rate"]
        return float(hit.iloc[0]) if len(hit) else None

    # ---- CoinGecko daily history
    def coin(self, coin_id: str) -> pd.DataFrame:
        if coin_id not in self._coin:
            self._coin[coin_id] = self.repo.get_coin_history(coin_id)
        return self._coin[coin_id]

    def coin_row(self, row: dict, t: int) -> dict | None:
        """A CoinGecko-like market row for the last full day before t: market cap,
        24h volume and 7d/30d change (market-cap based)."""
        h = self.coin(row["id"])
        day = (t // DAY) * DAY
        h = h[h["ts"] < day]
        if h.empty:
            return None
        last = h.iloc[-1]

        def change(days):
            past = h[h["ts"] <= last["ts"] - days * DAY]
            if past.empty or not past["mcap"].iloc[-1]:
                return None
            return (last["mcap"] / past["mcap"].iloc[-1] - 1) * 100
        return {**row, "market_cap": last["mcap"], "total_volume": last["volume"],
                "price_change_percentage_7d_in_currency": change(7),
                "price_change_percentage_30d_in_currency": change(30)}
