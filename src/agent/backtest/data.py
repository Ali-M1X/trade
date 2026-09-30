"""Download what the backtest replays into its own SQLite file.

Candles (1w/1d/4h/1h) for the current universe plus BTC/ETH, funding history, and
CoinGecko daily market caps and volumes (for dominance, liquidity and category growth).
The universe rows and coin categories are stored as they are today.
"""
from __future__ import annotations

import logging

from ..data.dominance import reconstruct
from ..data.exchange import AllExchangesFailed, perp_symbol
from ..data.market import TF_MS
from ..runs import load_categories, load_universe

log = logging.getLogger(__name__)
DAY = 86_400_000


def fetch_funding(client, symbol: str, since: int, until: int) -> tuple[str | None, list]:
    """Paginate funding history forward from `since` on the first exchange that answers."""
    for name in client.names:
        try:
            ex = client.exchange(name)
            out, cursor = [], since
            while cursor < until:
                batch = client._call_one(name, "fetch_funding_rate_history", symbol, cursor, 100)
                batch = [r for r in batch or [] if r.get("timestamp") and r.get("fundingRate") is not None]
                if not batch:
                    break
                out += [(r["timestamp"], r["fundingRate"]) for r in batch]
                nxt = max(r["timestamp"] for r in batch) + 1
                if nxt <= cursor:
                    break
                cursor = nxt
            if not out:
                # many exchanges only keep the last months: take whatever is recent
                batch = client._call_one(name, "fetch_funding_rate_history", symbol, None, 100) or []
                out = [(r["timestamp"], r["fundingRate"]) for r in batch
                       if r.get("timestamp") and r.get("fundingRate") is not None]
            return ex.id, sorted(set(out))
        except Exception as e:                       # try the next exchange
            log.warning("funding %s on %s failed: %s", symbol, name, e)
    return None, []


def fetch_history(cfg: dict, repo, client, market, cg, now_ms: int) -> dict:
    b = cfg["backtest"]
    start = now_ms - b["days"] * DAY
    bases, _ = market.perp_bases_and_spreads()
    raw, rows = load_universe(cg, bases, cfg)
    if b["max_coins"]:
        rows = rows[:b["max_coins"]]
    targets = sorted({r["symbol"].upper() for r in rows} | {"BTC", "ETH"})
    window = cfg["analysis"]["window_bars"] + cfg["storage"]["warmup_bars"]
    stats = {"coins": len(targets), "candles": 0, "funding": 0}
    for i, base in enumerate(targets):
        symbol = perp_symbol(base, cfg["exchange"]["quote"])
        for tf in b["timeframes"]:
            since = start - window * TF_MS[tf]
            try:
                name, candles = client.fetch_history(symbol, tf, since, now_ms)
            except AllExchangesFailed as e:
                log.warning("%s", e)
                continue
            repo.upsert_candles(name, symbol, tf, candles)
            stats["candles"] += len(candles)
        name, funding = fetch_funding(client, symbol, start - 7 * DAY, now_ms)
        if funding:
            repo.upsert_funding(name, symbol, funding)
            stats["funding"] += len(funding)
        log.info("[%d/%d] %s done", i + 1, len(targets), base)

    # CoinGecko: the raw top list (stablecoins included, for USDT.D) plus the universe
    ids = list(dict.fromkeys([m["id"] for m in raw[:cfg["universe"]["top_n"]]] + [r["id"] for r in rows]))
    charts = {}
    for cid in ids:
        try:
            data = cg.market_chart(cid, b["days"])
        except Exception as e:
            log.warning("market_chart %s failed: %s", cid, e)
            continue
        vols = {int(t) // DAY * DAY: v for t, v in data.get("total_volumes", [])}
        caps = [(int(t) // DAY * DAY, c, vols.get(int(t) // DAY * DAY)) for t, c in data.get("market_caps", [])]
        repo.upsert_coin_history(cid, caps)
        if cid in {m["id"] for m in raw[:cfg["universe"]["top_n"]]}:
            charts[cid] = data.get("market_caps", [])
    for r in reconstruct(charts).itertuples():
        repo.add_dominance(int(r.ts), "recon", r.total_mcap, r.btc_mcap, r.eth_mcap, r.usdt_mcap)

    repo.set_state("bt_universe", rows)
    repo.set_state("bt_categories", load_categories(cg, rows))
    repo.set_state("bt_meta", {"start": start, "end": now_ms, "exchange": client.names[0]})
    return stats
