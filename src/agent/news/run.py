"""The slow news path (every scheduled run, ~15 min on GitHub Actions).

1. Read every source, skip headlines already stored.
2. Score each new headline with the shared rules and find the coins it is about.
3. Alert in Telegram: news sites always; exchange announcements only when the Cloudflare
   worker is not the one alerting them (news.fast_path).
4. Store every headline, and fill in the coin's price at publication and 1h / 4h / 24h later,
   so the effect of each kind of news can be measured later.
5. Return the context the worker shows next to its alerts (open signals, latest L6 grades).

Nothing here touches the signal logic.
"""
from __future__ import annotations

import logging

from ..data.exchange import AllExchangesFailed, perp_symbol
from ..signals.lifecycle import OPEN
from .classify import classify, exchange_coins, media_coins
from .format import news_message
from .sources import fetch_all

log = logging.getLogger(__name__)
HOUR = 3_600_000
HORIZONS = {"p0": 0, "1h": HOUR, "4h": 4 * HOUR, "24h": 24 * HOUR}


def coin_names(cg) -> list[tuple[str, str]]:
    """(TICKER, name) of the top CoinGecko markets (cached by the CoinGecko client)."""
    try:
        return [(m["symbol"].upper(), m.get("name") or "") for m in cg.markets()]
    except Exception as e:
        log.warning("news: coin names failed: %s", e)
        return []


def context(repo, bases: set[str], now: int) -> dict:
    """What the signal system currently says about each coin (for alerts and the worker)."""
    funnel = repo.get_state("funnel") or {}
    coins: dict = {}
    for e in (repo.get_state("evaluations") or {}).get("items", []):
        if e.get("grade"):
            coins.setdefault(e["base"], {})["eval"] = {
                "grade": e["grade"], "score": e.get("score"), "side": e.get("side")}
    for s in repo.get_signals(list(OPEN)):
        coins.setdefault(s["symbol"], {})["signal"] = {
            "side": s["side"], "grade": s["grade"], "status": s["status"]}
    for base in (funnel.get("watchlist") or {}):
        coins.setdefault(base, {})["watch"] = True
    regime = funnel.get("regime") or {}
    return {"ts": now, "regime": {"name": regime.get("name"), "bias": regime.get("bias")},
            "perp_bases": sorted(bases), "coins": coins}


def price_at(client, quote: str, coin: str, ts: int) -> float | None:
    """Close of the 5m candle that contains ts on the perpetual (None if not listed)."""
    try:
        _, rows = client.fetch_ohlcv(perp_symbol(coin, quote), "5m", ts - 5 * 60_000, 3)
    except AllExchangesFailed:
        return None
    rows = [r for r in rows or [] if r[0] <= ts]
    return float(rows[-1][4]) if rows else None


def fill_prices(repo, client, quote: str, bases: set[str], now: int) -> int:
    """Fill p0 / 1h / 4h / 24h for stored headlines whose time has come. Returns updates."""
    n = 0
    for item in repo.get_news(since=now - 26 * HOUR, with_coins=True):
        t0 = item["ts"] or item["seen_at"]
        prices = item["prices"]
        changed = False
        for coin in item["coins"]:
            if coin not in bases:
                continue
            p = prices.setdefault(coin, {})
            for k, dt in HORIZONS.items():
                if k not in p and now >= t0 + dt + 5 * 60_000:
                    v = price_at(client, quote, coin, t0 + dt)
                    if v is not None:
                        p[k] = v
                        changed = True
        if changed:
            repo.set_news_prices(item["id"], prices)
            n += 1
    return n


def run_news(cfg: dict, repo, market, cg, notifier, now: int, session=None,
             bases: set[str] | None = None) -> dict:
    """One pass. Returns {"new": n, "alerts": n, "failed": [...], "context": {...}}."""
    n_cfg = cfg["news"]
    items, failed = fetch_all(cfg, session)
    if bases is None:
        bases, _ = market.perp_bases_and_spreads()
    names = coin_names(cg)
    first_run = repo.get_state("news_seeded") is None
    known = repo.news_known([i["id"] for i in items])
    ctx = context(repo, bases, now)
    new = alerts = 0
    for item in sorted((i for i in items if i["id"] not in known), key=lambda i: i["ts"] or 0):
        sc = classify(item["title"], item["source"])
        coins = (exchange_coins(item["title"]) if item["kind"] == "exchange"
                 else media_coins(item["title"], names))
        fresh = item["ts"] is None or now - item["ts"] <= n_cfg["max_age_min"] * 60_000
        worker_does_it = item["kind"] == "exchange" and n_cfg["fast_path"] == "cloudflare"
        alert = bool(not first_run and fresh and coins and not worker_does_it and
                 (sc.level >= n_cfg["min_alert_level"] or
                  (sc.level < 0 and n_cfg["alert_negative_for_open_signals"] and
                   any("signal" in ctx["coins"].get(c, {}) for c in coins))))
        if alert:
            notifier.send(news_message(item, sc, coins, ctx, now))
            alerts += 1
        repo.add_news({**item, "seen_at": now, "level": sc.level, "rule": sc.rule,
                       "coins": coins, "alerted": alert})
        new += 1
    if first_run:
        repo.set_state("news_seeded", now)
    fill_prices(repo, market.client, cfg["exchange"]["quote"], bases, now)
    return {"new": new, "alerts": alerts, "failed": failed, "context": ctx}
