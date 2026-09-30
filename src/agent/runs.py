"""Scheduled runs. Loading data lives here; the layers stay pure functions."""
from __future__ import annotations

import logging

from .analysis.frame import make_frame
from .data.dominance import backfill, dominance_series, merged_history, snapshot_global
from .data.market import LiveMarket
from .layers.funnel import FunnelResult, run_funnel
from .layers.majors import Majors
from .layers.regime import Regime
from .layers.scanners import CoinData, hot_categories
from .layers.technical import Evaluation, evaluate

log = logging.getLogger(__name__)
DAY = 86_400_000


def frames_for(market, base: str, tfs, cfg, quote_base: str | None = None) -> dict:
    out = {}
    for tf in tfs:
        df = market.ratio(base, quote_base, tf) if quote_base else market.candles(base, tf)
        if len(df) >= 2:
            out[tf] = make_frame(df, tf, cfg, with_indicators=True)
    return out


def load_universe(cg, market_bases: set[str], cfg: dict) -> tuple[list[dict], list[dict]]:
    """Returns (raw top markets incl. stablecoins, tradable universe rows).
    Universe = top N without stables/wrapped + CoinGecko trending + coins of the hot
    categories, limited to coins with a USDT perpetual on the exchange."""
    u = cfg["universe"]
    raw = cg.markets()
    rows = cg.top_universe()
    ids = {r["id"] for r in rows}
    extra = []
    if u["add_exchange_trending"]:
        try:
            extra += [i for i in cg.trending_ids() if i not in ids]
        except Exception as e:
            log.warning("trending failed: %s", e)
    rows += cg.markets_by_ids(extra) if extra else []
    tradable = [r for r in rows if r["symbol"].upper() in market_bases]
    return raw, tradable


def ensure_dominance_history(cg, repo, raw_markets: list[dict], cfg: dict) -> None:
    if not repo.get_dominance("recon").empty:
        return
    ids = [m["id"] for m in raw_markets[:cfg["universe"]["top_n"]]]
    n = backfill(cg, repo, ids, cfg["regime"]["backfill_days"])
    log.info("dominance backfill: %d days", n)


def load_categories(cg, rows: list[dict]) -> dict[str, list[str]]:
    out = {}
    for r in rows:
        try:
            out[r["id"]] = cg.coin_categories(r["id"])
        except Exception as e:
            log.warning("categories %s failed: %s", r["id"], e)
            out[r["id"]] = []
    return out


def hot_category_coins(cg, hot: set[str], bases: set[str], known: set[str], cfg: dict) -> list[dict]:
    """Tradable coins of the hot categories that are not in the universe yet."""
    if not hot or not cfg["universe"]["add_hot_category_coins"]:
        return []
    try:
        ids = {c["name"]: c["category_id"] for c in cg.categories_list()}
    except Exception as e:
        log.warning("categories list failed: %s", e)
        return []
    out: dict[str, dict] = {}
    for name in sorted(hot):
        if name not in ids:
            continue
        try:
            rows = cg.markets(per_page=cfg["universe"]["hot_category_coins"], category=ids[name])
        except Exception as e:
            log.warning("category %s markets failed: %s", name, e)
            continue
        for r in rows:
            if r["id"] in known or r["symbol"].upper() not in bases:
                continue
            out.setdefault(r["id"], {**r, "_hot": []})["_hot"].append(name)
    return list(out.values())


def run_4h(cfg: dict, repo, market: LiveMarket, cg, now_ms: int) -> FunnelResult:
    bases, spreads = market.perp_bases_and_spreads()
    raw, rows = load_universe(cg, bases, cfg)
    snapshot_global(cg, repo, now_ms)
    ensure_dominance_history(cg, repo, raw, cfg)
    dom = dominance_series(merged_history(repo))

    major_tfs = list(cfg["majors"]["weights"])
    majors = {"BTC": frames_for(market, "BTC", major_tfs, cfg),
              "ETH": frames_for(market, "ETH", major_tfs, cfg),
              "ETHBTC": frames_for(market, "ETH", major_tfs, cfg, quote_base="BTC")}
    min_vol = cfg["universe"]["min_volume_24h_usd"]
    liquid_rows = [r for r in rows if (r.get("total_volume") or 0) >= min_vol]
    categories = load_categories(cg, liquid_rows)
    hot = hot_categories(rows, categories, cfg)
    for r in hot_category_coins(cg, hot, bases, {r["id"] for r in rows}, cfg):
        if (r.get("total_volume") or 0) >= min_vol:
            liquid_rows.append(r)
            categories[r["id"]] = sorted(set(categories.get(r["id"], [])) | set(r["_hot"]))

    coins = []
    for r in liquid_rows:
        base = r["symbol"].upper()
        frames = frames_for(market, base, ["1d", "4h"], cfg)
        if "1d" not in frames or "4h" not in frames:
            continue
        coins.append(CoinData(
            base=base, info=r, frames=frames,
            btc_frames=frames_for(market, base, ["1d", "4h"], cfg, "BTC") if base != "BTC" else {},
            eth_frames=frames_for(market, base, ["1d", "4h"], cfg, "ETH") if base != "ETH" else {},
            funding=market.funding_history(base),
            oi=market.open_interest(base),
            categories=categories.get(r["id"], []),
            spread_pct=spreads.get(base)))
    result = run_funnel(dom, majors, coins, rows, categories, cfg, hot=hot)
    repo.set_state("funnel", {**result.to_dict(), "ts": now_ms})
    return result


def run_1h(cfg: dict, repo, market: LiveMarket, now_ms: int) -> list[Evaluation]:
    state = repo.get_state("funnel")
    if not state:
        log.warning("no funnel state yet: run-4h first")
        return []
    regime = Regime(**state["regime"])
    majors = Majors(**state["majors"])
    out = []
    for item in state["shortlist"]:
        base, side = item["base"], item["side"]
        frames = frames_for(market, base, ["1w", "1d", "4h", "1h"], cfg)
        if len(frames) < 4:
            continue
        btc_pair_up = item["pairs"]["dirs"].get("BTC", {}).get("1d") == 1
        ev = evaluate(base, side, frames, regime, majors, cfg,
                      funding=market.funding_now(base), btc_pair_up=btc_pair_up)
        ev.notes = item["labels"] + ev.notes
        out.append(ev)
    repo.set_state("evaluations", {"ts": now_ms, "items": [e.to_dict() for e in out]})
    return out


def summarize_funnel(r: FunnelResult) -> str:
    lines = [f"regime: {r.regime.name} (bias {r.regime.bias}, risk x{r.regime.risk}) "
             f"USDT.D {r.regime.usdt_d:+d} BTC.D {r.regime.btc_d:+d} TOTAL2 {r.regime.total2:+d}",
             f"majors: BTC {r.majors.btc:+d} ETH {r.majors.eth:+d} ETHBTC {r.majors.ethbtc:+d}"
             f"{' | BTC weak' if r.majors.btc_weak else ''}{' | BTC/TOTAL2 divergence' if r.majors.divergence else ''}",
             f"hot categories: {', '.join(sorted(r.hot)) or '-'}",
             f"watchlist: {len(r.watchlist)} coins, shortlist:"]
    for c in r.shortlist:
        lines.append(f"  {c.base:<8} {'LONG ' if c.side == 1 else 'SHORT'} {c.score:5.1f}  {'+'.join(c.labels)}")
    return "\n".join(lines)


def summarize_evaluations(evs: list[Evaluation]) -> str:
    lines = []
    for e in evs:
        side = "LONG " if e.side == 1 else "SHORT"
        gates = " ".join(f"{k}{'✓' if v else '✗'}" for k, v in e.gates.items())
        lines.append(f"{e.base:<8} {side} score {e.score:5.1f} grade {e.grade or '-':<5} {gates}"
                     f"{' rejected:' + e.rejected if e.rejected else ''}"
                     f"{' flags:' + ','.join(e.flags) if e.flags else ''}")
    return "\n".join(lines) or "no shortlist coins evaluated"
