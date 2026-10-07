"""Scheduled runs. Loading data lives here; the layers stay pure functions."""
from __future__ import annotations

import logging

from .analysis.frame import make_frame
from .data.dominance import backfill, dominance_series, merged_history, snapshot_global
from .data.market import LiveMarket
from .layers.funnel import FunnelResult, run_funnel
from .layers.majors import Majors
from .layers.regime import Regime
from .hold.scanner import scan_hold
from .layers.scanners import CoinData, category_growth, hot_categories
from .layers.technical import Evaluation, evaluate
from .layers.pairs import returns_corr_beta
from .layers.trade import flip_level
from .notify import formatter as fmt
from .signals.lifecycle import H4, on_4h_close, on_candle, on_time
from .signals.manager import SignalBook

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
    result = run_funnel(dom, majors, coins, rows, categories, cfg, hot=hot,
                        breakout_watch=repo.get_state("breakout_watch", {}), now_ms=now_ms)
    repo.set_state("funnel", {**result.to_dict(), "ts": now_ms})
    repo.set_state("breakout_watch", result.breakout_watch)
    return result


def run_1h(cfg: dict, repo, market: LiveMarket, now_ms: int, notifier=None) -> list[Evaluation]:
    state = repo.get_state("funnel")
    if not state:
        log.warning("no funnel state yet: run-4h first")
        return []
    regime = Regime(**state["regime"])
    majors = Majors(**state["majors"])
    out = []
    for item in state["shortlist"]:
        base, side = item["base"], item["side"]
        tfs = ["1w", "1d", "4h", "1h"]
        frames = frames_for(market, base, tfs, cfg)
        if len(frames) < len(tfs):
            log.warning("%s skipped: not enough candles for %s", base,
                        ", ".join(tf for tf in tfs if tf not in frames))
            continue
        btc_pair_up = item["pairs"]["dirs"].get("BTC", {}).get("1d") == 1
        extra = [flip_level(item["flip_level"], cfg)] if item.get("flip_level") else []
        ev = evaluate(base, side, frames, regime, majors, cfg,
                      funding=market.funding_now(base), btc_pair_up=btc_pair_up, extra_levels=extra,
                      labels=item["labels"])
        out.append(ev)
    repo.set_state("evaluations", {"ts": now_ms, "items": [e.to_dict() for e in out]})
    if notifier is not None:
        publish(cfg, repo, market, notifier, out, now_ms)
    return out


def summarize_funnel(r: FunnelResult) -> str:
    lines = [f"regime: {r.regime.name} (bias {r.regime.bias}, risk x{r.regime.risk}) "
             f"USDT.D {r.regime.usdt_d:+d} BTC.D {r.regime.btc_d:+d} TOTAL2 {r.regime.total2:+d}",
             f"majors: BTC {r.majors.btc:+d} ETH {r.majors.eth:+d} ETHBTC {r.majors.ethbtc:+d}"
             f"{' | BTC weak' if r.majors.btc_weak else ''}{' | BTC/TOTAL2 divergence' if r.majors.divergence else ''}",
             f"hot categories: {', '.join(sorted(r.hot)) or '-'}",
             f"watchlist: {len(r.watchlist)} coins, shortlist:"]
    for c in r.shortlist:
        # no funnel score here: the only score users see is the L6 score (DECISIONS #120)
        lvl = "-" if c.level_atr is None else f"{c.level_atr:.1f} ATR"
        lines.append(f"  {c.base:<8} {'LONG ' if c.side == 1 else 'SHORT'} "
                     f"level {lvl:<8} {'+'.join(c.labels)}")
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


# ------------------------------------------------------------------ signals
def publish(cfg: dict, repo, market, notifier, evs: list[Evaluation], now_ms: int) -> list[int]:
    """Turn A/B evaluations into signals (subject to the book's rules) and send Watch
    alerts. Returns the new signal ids."""
    state = repo.get_state("funnel")
    book = SignalBook(repo, cfg)

    def corr(a: str, b: str):
        return returns_corr_beta(market.candles(a, "1d"), market.candles(b, "1d"),
                                 cfg["pairs"]["corr_days"])[0]

    created = []
    watch_sent = repo.get_state("watch_sent", {})
    for ev in sorted(evs, key=lambda e: -e.score):
        if ev.is_signal:
            adm = book.admit(ev.base, ev.side, ev.score, now_ms, corr)
            if not adm.ok:
                log.info("%s %s not sent: %s", ev.base, ev.grade, adm.reason)
                continue
            meta = {"regime": state["regime"], "majors": state["majors"],
                    "evaluation": ev.to_dict(), "out_of_cap": adm.out_of_cap}
            sid, _ = book.create(ev, now_ms, meta)
            created.append(sid)
            notifier.send(fmt.signal_message(ev, state["regime"], cfg, adm.out_of_cap))
        elif ev.grade == "Watch" and ev.plan is not None and cfg["watch_alerts"]["enabled"]:
            key = f"{ev.base}:{ev.side}"
            if now_ms - watch_sent.get(key, 0) >= cfg["watch_alerts"]["repeat_hours"] * 3_600_000:
                watch_sent[key] = now_ms
                notifier.send(fmt.watch_message(ev, state["regime"], cfg))
    repo.set_state("watch_sent", watch_sent)
    return created


def manage(cfg: dict, repo, market, notifier, now_ms: int) -> list:
    """run-15m: feed new closed 15m candles and 4H closes to every open signal, in time
    order, then check expiry. Sends a message per event."""
    book = SignalBook(repo, cfg)
    ma = f"ma{cfg['indicators']['ma_mid']}"
    all_events = []
    for sig in book.open_signals():
        lc = sig["payload"]["lifecycle"]
        base, side = sig["symbol"], lc["side"]
        steps = []
        m15 = market.candles(base, cfg["timeframes"]["fast_trigger"])
        for r in m15[m15["ts"] > lc["last_candle_ts"]].itertuples():
            steps.append((r.ts, 0, r))
        h4 = market.candles(base, "4h")
        if len(h4) >= 2:
            f = make_frame(h4, "4h", cfg, with_indicators=True)
            offset = len(h4) - f.n                   # frame keeps the last window only
            chochs = {e.idx + offset for e in f.events if e.kind == "CHoCH" and e.direction == -side}
            ma_values = f.df[ma].to_numpy()
            for i, r in enumerate(h4.itertuples()):
                if r.ts > lc["last_4h_ts"] and i >= offset:
                    steps.append((r.ts + H4, 1, (r, ma_values[i - offset], i in chochs)))
        events = []
        # a confirm_4h signal can fill at a 4H close: that close comes before the 15m candle
        # opening at the same time (other signals keep the original order)
        first = 1 if "confirm" in lc else 0
        for _, kind, item in sorted(steps, key=lambda x: (x[0], x[1] != first)):
            if kind == 0:
                events += on_candle(lc, int(item.ts), float(item.high), float(item.low), cfg)
            else:
                r, ma_v, choch = item
                events += on_4h_close(lc, int(r.ts), float(r.close), float(ma_v), choch,
                                      float(r.open))
        events += on_time(lc, now_ms)
        if not events:
            repo.update_signal(sig["id"], lc["status"], sig["payload"], sig["updated_at"])
            continue
        notice = book.save(sig, events, now_ms)
        for e in events:
            notifier.send(fmt.event_message(sig, e, cfg))
        if notice:
            notifier.send(fmt.pause_message(book.pause()["paused_until"]))
        all_events += events
    return all_events


def daily(cfg: dict, repo, notifier, now_ms: int) -> list[str] | None:
    """Sends the daily report (one or more messages, split between cards) and returns them."""
    state = repo.get_state("funnel")
    if not state:
        return None
    evaluations = repo.get_state("evaluations", {}).get("items", [])
    parts = fmt.daily_message(state, SignalBook(repo, cfg).open_signals(), evaluations, now_ms,
                              cfg)
    for text in parts:
        notifier.send(text)
    return parts


# --------------------------------------------------------------------- HOLD
def hold_ideas(cfg: dict, repo, market, cg) -> list:
    """Spot HOLD scan over the tradable universe (W and D candles, COIN/BTC on W)."""
    bases, _ = market.perp_bases_and_spreads()
    _, rows = load_universe(cg, bases, cfg)
    categories = load_categories(cg, rows)
    h = cfg["hold"]
    ranked = category_growth(rows, categories, f"price_change_percentage_{h['category_period_days']}d_in_currency",
                             cfg["scanners"]["hot_sector"]["min_coins"],
                             cfg["scanners"]["hot_sector"]["ignore_categories"])
    hot30 = {c for c, _ in ranked[:h["top_categories"]]}
    btc = frames_for(market, "BTC", ["1d"], cfg)
    if "1d" not in btc:
        return []
    coins = []
    for r in rows:
        base = r["symbol"].upper()
        f = frames_for(market, base, ["1d", "1w"], cfg)
        if len(f) < 2:
            continue
        w_btc = frames_for(market, base, ["1w"], cfg, "BTC").get("1w") if base != "BTC" else None
        coins.append({"base": base, "info": r, "d": f["1d"], "w": f["1w"], "w_btc": w_btc,
                      "categories": categories.get(r["id"], [])})
    return scan_hold(coins, btc["1d"], hot30, cfg)


def weekly(cfg: dict, repo, market, cg, notifier, now_ms: int) -> list:
    ideas = hold_ideas(cfg, repo, market, cg)
    repo.set_state("hold", sorted(i.base for i in ideas))
    notifier.send(fmt.hold_message(ideas, now_ms))
    try:
        notifier.send(fmt.performance_message(repo.get_signals(), now_ms))
    except Exception:                       # the HOLD report is already out; don't fail the task
        logging.getLogger(__name__).exception("performance report failed")
    return ideas


def hold_new_entrants(cfg: dict, repo, market, cg, notifier, now_ms: int) -> list:
    """Daily: announce coins that joined the HOLD list since the last scan."""
    ideas = hold_ideas(cfg, repo, market, cg)
    known = set(repo.get_state("hold", []))
    new = [i for i in ideas if i.base not in known]
    repo.set_state("hold", sorted(i.base for i in ideas))
    if new and known:            # the first scan is the weekly report's job
        notifier.send(fmt.hold_message(new, now_ms, new_only=True))
    return new
