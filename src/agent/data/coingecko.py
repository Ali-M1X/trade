"""CoinGecko free/Demo API client with rate limiting, retries and a SQLite cache."""
from __future__ import annotations

import json
import time

import requests


class CoinGeckoError(Exception):
    pass


class CoinGecko:
    def __init__(self, cfg: dict, repo=None, api_key: str | None = None, session=None,
                 sleep=time.sleep, clock=time.monotonic):
        self.c = cfg["coingecko"]
        self.universe_cfg = cfg["universe"]
        self.repo = repo
        self.session = session or requests.Session()
        if api_key:
            self.session.headers[self.c["demo_header"]] = api_key
        self._sleep = sleep
        self._clock = clock
        self._last_call = None

    def _throttle(self) -> None:
        if self._last_call is not None:
            wait = self.c["min_interval_s"] - (self._clock() - self._last_call)
            if wait > 0:
                self._sleep(wait)
        self._last_call = self._clock()

    def get(self, path: str, params: dict | None = None, cache: str | None = None):
        key = f"cg:{path}?{json.dumps(params or {}, sort_keys=True)}"
        if cache and self.repo is not None:
            hit = self.repo.cache_get(key, self.c["cache_ttl_s"][cache])
            if hit is not None:
                return hit
        url = self.c["base_url"] + path
        backoff = self.c["retry_backoff_s"]
        for attempt in range(self.c["retries"] + 1):
            self._throttle()
            try:
                r = self.session.get(url, params=params, timeout=self.c["timeout_s"])
            except requests.RequestException as e:
                if attempt == self.c["retries"]:
                    raise CoinGeckoError(f"{path}: {e}") from e
                self._sleep(backoff * 2 ** attempt)
                continue
            if r.status_code == 429 or r.status_code >= 500:
                if attempt == self.c["retries"]:
                    raise CoinGeckoError(f"{path}: HTTP {r.status_code}")
                retry_after = r.headers.get("Retry-After")
                self._sleep(float(retry_after) if retry_after and retry_after.isdigit()
                            else backoff * 2 ** attempt)
                continue
            if r.status_code != 200:
                raise CoinGeckoError(f"{path}: HTTP {r.status_code} {r.text[:200]}")
            data = r.json()
            if cache and self.repo is not None:
                self.repo.cache_put(key, data)
            return data

    # ---- endpoints
    def ping(self):
        return self.get("/ping")

    def global_data(self):
        return self.get("/global", cache="global")["data"]

    def markets(self, per_page: int = 250, page: int = 1, category: str | None = None):
        params = {"vs_currency": "usd", "order": "market_cap_desc", "per_page": per_page,
                  "page": page, "price_change_percentage": "7d,30d"}
        if category:
            params["category"] = category
        return self.get("/coins/markets", params, cache="markets")

    def categories(self):
        return self.get("/coins/categories", {"order": "market_cap_change_24h_desc"},
                        cache="categories")

    def market_chart(self, coin_id: str, days: int):
        return self.get(f"/coins/{coin_id}/market_chart",
                        {"vs_currency": "usd", "days": days, "interval": "daily"},
                        cache="market_chart")

    def coin_categories(self, coin_id: str) -> list[str]:
        data = self.get(f"/coins/{coin_id}", {"localization": "false", "tickers": "false",
                                               "market_data": "false", "community_data": "false",
                                               "developer_data": "false"},
                        cache="coin_categories")
        return [c for c in data.get("categories") or [] if c]

    def categories_list(self) -> list[dict]:
        """[{category_id, name}] to map category names to ids."""
        return self.get("/coins/categories/list", cache="coin_categories")

    def trending_ids(self) -> list[str]:
        data = self.get("/search/trending", cache="markets")
        return [c["item"]["id"] for c in data.get("coins", [])]

    def markets_by_ids(self, ids: list[str]) -> list[dict]:
        if not ids:
            return []
        params = {"vs_currency": "usd", "ids": ",".join(sorted(ids)),
                  "price_change_percentage": "7d,30d"}
        return self.get("/coins/markets", params, cache="markets")

    # ---- universe
    def excluded_ids(self) -> set[str]:
        ids = set(self.universe_cfg["exclude_ids"])
        for cat in self.universe_cfg["exclude_categories"]:
            try:
                ids.update(c["id"] for c in self.markets(category=cat))
            except CoinGeckoError:
                pass  # static lists still apply
        return ids

    def top_universe(self) -> list[dict]:
        """Top N coins by market cap, without stablecoins and wrapped tokens."""
        return filter_universe(self.markets(), self.excluded_ids(),
                               set(self.universe_cfg["exclude_symbols"]),
                               self.universe_cfg["top_n"])


def filter_universe(markets: list[dict], excluded_ids: set[str], excluded_symbols: set[str],
                    top_n: int) -> list[dict]:
    rows = [m for m in markets
            if m.get("market_cap") and m["id"] not in excluded_ids
            and m["symbol"].lower() not in excluded_symbols]
    rows.sort(key=lambda m: m["market_cap"], reverse=True)
    return rows[:top_n]
