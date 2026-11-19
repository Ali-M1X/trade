"""System 3: short the exhaustion of a pump on small and mid coins (paper only, 2026-11-19).

Ali (2026-11-19): «سیستم ۱ و ۲ رو دست نزنیم. سیستم ۳ رو راه اندازی کنیم». The rules are the one
variant of the round 6 backtest that was positive after costs in both periods
(scripts/s3_exhaust.py, docs/system3-exhaust.md: r25 · funding any · climax wick · maker · tp62):

  coins     CoinGecko rank 20 and below (the top 20 left out), 24h volume >= $3M, a Gate USDT perpetual
  setup     on a closed 1H candle: the high of the last 12 candles is >= 25 % above the low of the
            candles 36 to 6 back, a climax candle near that high (upper wick >= 40 % of its range on
            >= 3x the week's average volume), and this candle closes under the previous one's low,
            makes a lower high and has less volume than the peak candle
  entry     a sell limit halfway between the close and the high of that candle, good for 6 hours,
            cancelled if price reaches the target first
  stop      0.5 % above the pump's high, at least 3 % from the entry (no trade if over 20 %)
  target    a 62 % retracement of the whole pump (limit), at least 1R away
  time      closed at market after 48 hours
  limits    at most 3 open, one trade per coin, 6 hours between trades on a coin, no new trade after
            3 losses in a UTC day; risk 0.5 % a trade

It has its own database (system3.db_path) and is kept out of systems 1 and 2 entirely: their
signals, accounts and the measurement period (scorecard.NOT_RULES holds `system3`).
"""
from __future__ import annotations

import logging
import math
from datetime import datetime, timezone

from .notify import formatter as fmt

log = logging.getLogger(__name__)
H = 3_600_000
DAY = 86_400_000
HEADER = "🧪 سیستم ۳ (آزمایشی · کاغذی)"
# 2026-11-19 (Ali: «هر ساعت دیره»): a fast copy next to the tested one, with its own account. It checks every
# minute on the 1H candle that is still forming (not backtested); the hourly one stays the reference.
HEADERS = {"hourly": HEADER, "fast": "⚡️ سیستم ۳ سریع (آزمایشی · کاغذی)"}
MODES = ("hourly", "fast")
OPEN = ("pending", "active")
CLOSED = ("tp", "sl", "time")
UNFILLED = ("expired", "cancelled")
STATUS_FA = {"pending": "سفارش در انتظار", "active": "باز", "tp": "تارگت", "sl": "استاپ",
             "time": "خروج زمانی", "expired": "منقضی", "cancelled": "لغو"}
WHY_FA = {"target_first": "قیمت قبل از ورود به تارگت رسید", "max_open": "سه معامله‌ی باز داشت",
          "bad_fill": "با قیمت پر شدن، استاپ یا تارگت دیگر جور نبود"}
DEFAULTS = {
    "enabled": False, "db_path": "data/state_s3.db", "telegram": True, "fast": False,
    "since": "2026-11-19", "review_days": 30, "risk_pct": 0.5, "balance_usd": 100,
    "min_rank": 20, "min_volume_usd": 3_000_000, "prefilter_range_pct": 12, "mover_hold_h": 48,
    "run_pct": 25, "peak_bars": 12, "base_from": 36, "base_to": 6,
    "wick_frac": 0.4, "wick_vol_x": 3, "avg_bars": 168,
    "fill_bars": 6, "tp_retrace": 0.62, "stop_above_peak": 0.005, "min_stop": 0.03, "max_stop": 0.20,
    "max_bars": 48, "max_open": 3, "day_losses": 3, "cooldown_h": 6,
    "fee_taker_pct": 0.05, "slippage_pct": 0.2, "fee_maker_pct": 0.02,
    "leverage_cap": 10, "liquidation_sl_multiple": 2.5, "scan_delay_s": 60,
    "live_json": None,
}


def settings(cfg: dict) -> dict:
    return {**DEFAULTS, **(cfg.get("system3") or {})}


def enabled(cfg: dict) -> bool:
    return bool((cfg.get("system3") or {}).get("enabled"))


def _ms(day: str) -> int:
    return int(datetime.strptime(str(day), "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp() * 1000)


# ------------------------------------------------------------------ the setup (pure)
def find_setup(bars: list, c: dict) -> dict | None:
    """bars: closed 1H candles [ts, open, high, low, close, volume], oldest first. A setup on the
    last one, as in scripts/s3_exhaust.py (setups + simulate), or None."""
    i = len(bars) - 1
    if i < max(170, c["avg_bars"] + 2):
        return None
    t, o, h, l, cl, v = bars[i][:6]
    if cl >= bars[i - 1][3]:
        return None
    peak_k = max(range(i - c["peak_bars"], i), key=lambda k: bars[k][2])
    peak = bars[peak_k][2]
    base_low = min(b[3] for b in bars[i - c["base_from"]:i - c["base_to"]])
    if base_low <= 0 or peak / base_low - 1 < c["run_pct"] / 100:
        return None
    if h >= peak or (v or 0) >= (bars[peak_k][5] or 0):
        return None
    avg = sum((b[5] or 0) for b in bars[i - c["avg_bars"]:i]) / c["avg_bars"]
    wick = None
    for k in range(max(peak_k - 2, i - c["peak_bars"]), min(peak_k + 3, i + 1)):
        bo, bh, bl, bc, bv = bars[k][1:6]
        rng = bh - bl
        if rng > 0 and (bh - max(bo, bc)) / rng >= c["wick_frac"] and avg and (bv or 0) >= c["wick_vol_x"] * avg:
            wick = k
            break
    if wick is None:
        return None
    limit = (cl + h) / 2
    target = peak - c["tp_retrace"] * (peak - base_low)
    plan = levels(limit, peak, target, c)
    if plan is None:
        return None
    return {"ts": t, "close_ts": t + H, "peak": peak, "peak_ts": bars[peak_k][0], "base_low": base_low,
            "run_pct": round((peak / base_low - 1) * 100, 1), "wick_ts": bars[wick][0],
            "wick_vol_x": round((bars[wick][5] or 0) / avg, 1) if avg else None,
            "limit": limit, **plan}


def levels(entry: float, peak: float, target: float, c: dict) -> dict | None:
    """Stop and leverage for a short at `entry`; None when the stop is too wide or the target under 1R."""
    stop = max(peak * (1 + c["stop_above_peak"]), entry * (1 + c["min_stop"]))
    d = stop - entry
    if d <= 0 or d > c["max_stop"] * entry or entry - target < d:
        return None
    sl_pct = d / entry * 100
    lev = max(1, min(int(c["leverage_cap"]), math.floor(1 / (c["liquidation_sl_multiple"] * sl_pct / 100))))
    return {"entry": entry, "sl": stop, "target": target, "sl_pct": round(sl_pct, 2),
            "rr": round((entry - target) / d, 2), "leverage": lev}


# ------------------------------------------------------------------ the trade (pure)
def new_trade(base: str, s: dict, now: int, c: dict, mode: str = "hourly") -> dict:
    expires = (now if mode == "fast" else s["close_ts"]) + c["fill_bars"] * H
    return {"base": base, "side": -1, "mode": mode, "status": "pending", "created": now, "signal_ts": s["ts"],
            "limit": s["limit"], "entry": s["entry"], "sl": s["sl"], "target": s["target"],
            "sl_pct": s["sl_pct"], "rr": s["rr"], "leverage": s["leverage"], "risk_pct": c["risk_pct"],
            "peak": s["peak"], "base_low": s["base_low"], "run_pct": s["run_pct"],
            "wick_ts": s["wick_ts"], "wick_vol_x": s["wick_vol_x"],
            "expires_at": expires, "filled_at": None, "closed_at": None,
            "exit": None, "why": None, "last_ts": now - 1, "events": [["created", now, None]]}


def _close(tr: dict, kind: str, ts: int, price: float | None, c: dict, why: str | None = None) -> None:
    tr["status"], tr["closed_at"], tr["exit"], tr["why"] = kind, ts, price, why
    tr["events"].append([kind, ts, price])
    if kind in CLOSED:
        d = tr["sl"] - tr["entry"]
        fee_in = c["fee_maker_pct"]
        fee_out = c["fee_maker_pct"] if kind == "tp" else c["fee_taker_pct"] + c["slippage_pct"]
        tr["gross_r"] = round((tr["entry"] - price) / d, 4)
        tr["cost_r"] = round((fee_in + fee_out) / 100 * tr["entry"] / d, 4)
        tr["net_r"] = round(tr["gross_r"] - tr["cost_r"], 4)


def step(tr: dict, candle, c: dict, active_others: int = 0) -> list[str]:
    """One closed candle [ts, open, high, low, close, ...] (5m live) for one trade, in time order.
    active_others: filled trades of the system other than this one (the max_open limit at the fill).
    Returns the new event kinds. Within a candle the stop comes before the target, like the backtest,
    and a fill candle is checked against the stop only."""
    ts, o, h, l, cl = candle[0], candle[1], candle[2], candle[3], candle[4]
    if tr["status"] not in OPEN or ts <= tr["last_ts"]:
        return []
    tr["last_ts"] = ts
    out = []
    step_ms = c.get("_candle_ms", 300_000)
    if tr["status"] == "pending":
        if ts >= tr["expires_at"]:
            _close(tr, "expired", tr["expires_at"], None, c)
            return ["expired"]
        if o >= tr["limit"] or h >= tr["limit"]:
            if active_others >= c["max_open"]:
                _close(tr, "cancelled", ts, None, c, "max_open")
                return ["cancelled"]
            entry = max(o, tr["limit"])
            plan = levels(entry, tr["peak"], tr["target"], c)
            if plan is None:
                _close(tr, "cancelled", ts, None, c, "bad_fill")
                return ["cancelled"]
            tr.update(entry=entry, sl=plan["sl"], sl_pct=plan["sl_pct"], rr=plan["rr"], leverage=plan["leverage"],
                      status="active", filled_at=ts)
            tr["events"].append(["filled", ts, entry])
            out.append("filled")
            if h >= tr["sl"]:
                _close(tr, "sl", ts, tr["sl"], c)
                out.append("sl")
            return out
        if l <= tr["target"]:
            _close(tr, "cancelled", ts, None, c, "target_first")
            return ["cancelled"]
        return []
    if h >= tr["sl"]:
        _close(tr, "sl", ts, max(o, tr["sl"]), c)
        return ["sl"]
    if l <= tr["target"]:
        _close(tr, "tp", ts, min(o, tr["target"]), c)
        return ["tp"]
    if ts + step_ms >= tr["filled_at"] + c["max_bars"] * H:
        _close(tr, "time", ts + step_ms, cl, c)
        return ["time"]
    return []


def open_r(tr: dict, price: float | None) -> float | None:
    if tr["status"] != "active" or not price:
        return None
    return (tr["entry"] - price) / (tr["sl"] - tr["entry"])


# ------------------------------------------------------------------ messages
def _p(x) -> str:
    return fmt.fmt_level(x) if x is not None else "—"


def _t(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%H:%M UTC")


def _head(tr: dict) -> str:
    return HEADERS.get(tr.get("mode") or "hourly", HEADER)


def new_message(tr: dict, cfg: dict) -> str:
    fast = tr.get("mode") == "fast"
    return "\n".join([
        _head(tr),
        f"🔻 {fmt.pair(tr['base'])} SHORT · سفارش لیمیت",
        f"ورود: {_p(tr['limit'])}",
        f"استاپ: {_p(tr['sl'])} ({fmt.fmt_num(tr['sl_pct'])}%)",
        f"تارگت: {_p(tr['target'])} ({fmt.fmt_num(tr['rr'])}R)",
        f"ریسک {fmt.fmt_num(tr['risk_pct'])}٪ · اهرم {tr['leverage']}x",
        f"چرا: پامپ {fmt.fmt_num(tr['run_pct'])}٪، کندل اوج با فتیله و حجم {fmt.fmt_num(tr['wick_vol_x'] or 0)} برابر، "
        + ("حالا در کندل ۱ ساعته‌ی جاری (هنوز باز) سقف پایین‌تر و قیمت زیر کف کندل قبل" if fast
           else "حالا سقف پایین‌تر و بسته شدن زیر کف کندل قبل"),
        f"سفارش تا {_t(tr['expires_at'])} معتبر است؛ خروج زمانی بعد از ۴۸ ساعت.",
    ]) + _link(cfg, tr.get("mode") or "hourly")


def _link(cfg: dict, mode: str = "hourly") -> str:
    url = ((cfg.get("panel") or {}).get("url") or "").rstrip("/")
    return f"\nپنل: {url}/#s3/{mode}" if url.startswith("https://") else ""


def event_message(tr: dict, kind: str) -> str:
    b = fmt.pair(tr["base"])
    if kind == "filled":
        return f"{_head(tr)}\n✅ {b} SHORT پر شد در {_p(tr['entry'])} · استاپ {_p(tr['sl'])} · تارگت {_p(tr['target'])}"
    if kind in CLOSED:
        icon = "🎯" if kind == "tp" else "🛑" if kind == "sl" else "⏱"
        return (f"{_head(tr)}\n{icon} {b} {STATUS_FA[kind]} در {_p(tr['exit'])} · "
                f"{tr['net_r']:+.2f}R خالص (بعد از کارمزد)")
    why = WHY_FA.get(tr.get("why") or "", "")
    return f"{_head(tr)}\n⚪️ {b} سفارش {STATUS_FA[kind]} شد" + (f" ({why})" if why else "")


# ------------------------------------------------------------------ live run
def _trades(repo) -> list[dict]:
    return [dict(s["payload"]["s3"], id=s["id"]) for s in repo.get_signals()]


def _save(repo, tr: dict, now: int) -> None:
    payload = {"s3": {k: v for k, v in tr.items() if k != "id"}}
    repo.update_signal(tr["id"], tr["status"], payload, now)


def _bars(df) -> list:
    return [[int(r.ts), float(r.open), float(r.high), float(r.low), float(r.close), float(r.volume or 0)]
            for r in df.itertuples()]


def manage(cfg: dict, repo, market, notifier, now: int) -> int:
    """Feed new closed 5m candles to every open trade. Returns the number of events."""
    c = {**settings(cfg), "_candle_ms": 300_000}
    trs = [t for t in _trades(repo) if t["status"] in OPEN]
    n = 0
    for tr in sorted(trs, key=lambda t: t["created"]):
        try:
            rows = _bars(market.candles(tr["base"], "5m"))
        except Exception:                                   # noqa: BLE001  one coin never blocks the others
            log.exception("system3 candles %s", tr["base"])
            continue
        evs = []
        mode = tr.get("mode") or "hourly"
        others = sum(1 for t in _trades(repo) if t["status"] == "active" and t["id"] != tr["id"]
                     and (t.get("mode") or "hourly") == mode)
        for r in rows:
            evs += step(tr, r, c, others)
            if tr["status"] not in OPEN:
                break
        if tr["status"] == "pending" and now >= tr["expires_at"]:
            _close(tr, "expired", tr["expires_at"], None, c)
            evs.append("expired")
        _save(repo, tr, now)
        for k in evs:
            repo.add_event(now, k, {"price": tr["exit"] if k in CLOSED else tr["entry"]}, tr["id"])
            if notifier is not None:
                notifier.send(event_message(tr, k))
        n += len(evs)
    return n


def universe(cfg: dict, cg) -> list[dict]:
    """CoinGecko top list (cached by the funnel), rank >= min_rank and 24h volume >= the floor."""
    c = settings(cfg)
    rows = cg.top_universe(cfg["universe"].get("coins_top_n"))
    out = []
    for i, r in enumerate(rows):
        if i < c["min_rank"] or (r.get("total_volume") or 0) < c["min_volume_usd"]:
            continue
        out.append({"base": r["symbol"].upper(), "high": r.get("high_24h"), "low": r.get("low_24h")})
    return out


def _blocked(trs: list[dict], base: str, now: int, c: dict, mode: str) -> str | None:
    """Why a new trade of this mode on `base` is not allowed now (each mode has its own limits)."""
    mine = [t for t in trs if (t.get("mode") or "hourly") == mode]
    if any(t["base"] == base and (t["status"] in OPEN or (t["filled_at"] and (t["closed_at"] or 0) + c["cooldown_h"] * H > now))
           for t in mine):
        return "busy"
    if sum(1 for t in mine if t["status"] == "active") >= c["max_open"]:
        return "max_open"
    day = now // DAY
    if sum(1 for t in mine if t["status"] in CLOSED and (t.get("net_r") or 0) < 0 and t["closed_at"] // DAY == day) >= c["day_losses"]:
        return "day_losses"
    return None


def _open_trade(cfg: dict, repo, base: str, s: dict, now: int, c: dict, mode: str, notifier) -> None:
    tr = new_trade(base, s, now, c, mode)
    sid = repo.add_signal(now, base, "short", "S3" if mode == "hourly" else "S3F", s["run_pct"], "pending", {"s3": tr})
    tr["id"] = sid
    repo.add_event(now, "created", {"run_pct": s["run_pct"], "mode": mode}, sid)
    if notifier is not None:
        notifier.send(new_message(tr, cfg))


def scan_due(repo, now: int, c: dict) -> bool:
    last = repo.get_state("s3_last_scan") or 0
    return now // H > last // H and now % H >= c["scan_delay_s"] * 1000


def scan(cfg: dict, repo, market, cg, notifier, now: int) -> list[dict]:
    """After each 1H close: the coins that moved >= prefilter in 24h (kept `mover_hold_h`), their
    1H candles, and a new pending short for each setup the limits allow."""
    c = settings(cfg)
    repo.set_state("s3_last_scan", now)
    movers = repo.get_state("s3_movers", {}) or {}
    rows = universe(cfg, cg)
    for r in rows:
        if r["high"] and r["low"] and r["low"] > 0 and (r["high"] / r["low"] - 1) * 100 >= c["prefilter_range_pct"]:
            movers[r["base"]] = now + c["mover_hold_h"] * H
    live = {r["base"] for r in rows}
    movers = {b: u for b, u in movers.items() if u > now and b in live}
    repo.set_state("s3_movers", movers)
    repo.set_state("s3_universe", sorted(live))
    found = []
    for base in sorted(movers):
        try:
            bars = _bars(market.candles(base, "1h"))
        except Exception:                                   # noqa: BLE001
            log.exception("system3 1h candles %s", base)
            continue
        s = find_setup(bars, c)
        if s is None or now - s["close_ts"] > 2 * H:
            continue
        found.append({"base": base, **{k: s[k] for k in ("ts", "run_pct", "entry", "sl", "target")}})
        trs = _trades(repo)
        if any(t["signal_ts"] == s["ts"] and t["base"] == base and (t.get("mode") or "hourly") == "hourly" for t in trs):
            continue
        why = _blocked(trs, base, now, c, "hourly")
        if why:
            found[-1]["skip"] = why
            continue
        _open_trade(cfg, repo, base, s, now, c, "hourly", notifier)
    repo.set_state("s3_scan", {"ts": now, "coins": len(rows), "movers": len(movers), "setups": found[-20:]})
    prune(repo, set(movers) | {t["base"] for t in _trades(repo) if t["status"] in OPEN}, now)
    return found


def _perp_tickers(client) -> dict[str, dict]:
    """base -> ticker (last, 24h high and low) of every USDT perpetual, one call."""
    name, tickers = client.call("fetch_tickers")
    ex = client.exchange(name)
    out = {}
    for sym, t in tickers.items():
        m = ex.markets.get(sym) if ex.markets else None
        if m and m.get("swap") and m.get("linear") and m.get("quote") == "USDT":
            out[m["base"]] = t
    return out


def _prev_low(repo, base: str, hour: int) -> float | None:
    row = repo.conn.execute("SELECT low FROM candles WHERE symbol = ? AND timeframe = '1h' AND ts = ? LIMIT 1",
                            (f"{base}/USDT:USDT", hour - H)).fetchone()
    return row[0] if row else None


def scan_fast(cfg: dict, repo, market_open, client, notifier, now: int) -> list[dict]:
    """Every minute: one ticker call. A coin of the universe that moved >= prefilter in 24h becomes a mover
    (at once, not only on the hour). A mover whose price is under the low of the last closed 1H candle gets
    its 1H candles with the forming one, and the setup is checked on that forming candle."""
    c = settings(cfg)
    uni = set(repo.get_state("s3_universe") or [])
    if not uni:
        return []
    try:
        tick = _perp_tickers(client)
    except Exception:                                       # noqa: BLE001
        log.exception("system3 tickers")
        return []
    movers = repo.get_state("s3_movers", {}) or {}
    for b, t in tick.items():
        hi, lo = t.get("high"), t.get("low")
        if b in uni and hi and lo and lo > 0 and (hi / lo - 1) * 100 >= c["prefilter_range_pct"]:
            movers[b] = now + c["mover_hold_h"] * H
    movers = {b: u for b, u in movers.items() if u > now}
    repo.set_state("s3_movers", movers)
    hour, found = now // H * H, []
    for base in sorted(movers):
        last = (tick.get(base) or {}).get("last")
        low = _prev_low(repo, base, hour)
        if last is None or (low is not None and last >= low):
            continue
        try:
            bars = _bars(market_open.candles(base, "1h"))
        except Exception:                                   # noqa: BLE001
            log.exception("system3 fast candles %s", base)
            continue
        if not bars or bars[-1][0] != hour:
            continue
        bars[-1][4] = last                                  # the forming candle closes at the price now
        s = find_setup(bars, c)
        if s is None:
            continue
        found.append({"base": base, "ts": s["ts"], "run_pct": s["run_pct"]})
        trs = _trades(repo)
        if any(t["signal_ts"] == s["ts"] and t["base"] == base and t.get("mode") == "fast" for t in trs):
            continue
        why = _blocked(trs, base, now, c, "fast")
        if why:
            found[-1]["skip"] = why
            continue
        _open_trade(cfg, repo, base, s, now, c, "fast", notifier)
    st = repo.get_state("s3_scan", {}) or {}
    repo.set_state("s3_scan", {**st, "fast_ts": now, "movers": len(movers)})
    return found


def prune(repo, keep: set[str], now: int) -> None:
    """Candles of coins that are no longer followed (the database is backed up every few minutes)."""
    try:
        for ex, sym in repo.candle_series("1h") + repo.candle_series("5m"):
            if sym.split("/")[0] not in keep:
                repo.conn.execute("DELETE FROM candles WHERE exchange = ? AND symbol = ?", (ex, sym))
        repo.conn.commit()
    except Exception:                                       # noqa: BLE001
        log.exception("system3 prune")


def run(cfg: dict, repo3, client, cg, notifier, now: int) -> None:
    """The minute run's system 3 part. repo3: its own database (candles, trades, state)."""
    from .data.market import LiveMarket
    c = settings(cfg)
    market = LiveMarket(cfg, repo3, client, now)
    out = notifier if c["telegram"] else None
    n = manage(cfg, repo3, market, out, now)
    if n:
        print(f"system3: {n} trade events")
    if scan_due(repo3, now, c):
        found = scan(cfg, repo3, market, cg, out, now)
        print(f"system3 scan: {len(found)} setups")
    if c["fast"]:
        fast = LiveMarket(cfg, repo3, client, now)
        fast.open_tfs.add("1h")
        found = scan_fast(cfg, repo3, fast, client, out, now)
        if found:
            print(f"system3 fast: {len(found)} setups")
    publish(cfg, now)


def publish(cfg: dict, now: int) -> str | None:
    """The panel's System 3 data, written by the run itself (2026-11-19, Ali: Telegram was minutes ahead of
    the panel, which is rebuilt only after the whole run). The page reads it every few seconds."""
    import json
    import os
    from pathlib import Path
    path = settings(cfg).get("live_json")
    if not path or not Path(path).parent.is_dir():
        return None
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(panel_view(cfg, now), f, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, path)
    return path


# ------------------------------------------------------------------ panel
def panel_view(cfg: dict, now: int) -> dict:
    """Trades, a $100 paper account at risk_pct a trade, and the last scan, for the panel's
    «سیستم ۳» page. Nothing here reaches systems 1 and 2's accounts or the scorecard."""
    from pathlib import Path

    from .store.repository import Repository
    c = settings(cfg)
    path = c["db_path"]
    out = {"enabled": enabled(cfg), "now": now, "since": _ms(c["since"]), "review_days": c["review_days"],
           "risk_pct": c["risk_pct"], "balance_usd": c["balance_usd"], "trades": [], "scan": {},
           "rules": {k: c[k] for k in ("run_pct", "tp_retrace", "min_stop", "max_stop", "fill_bars", "max_bars",
                                        "max_open", "day_losses", "min_rank", "min_volume_usd")}}
    if not Path(path).exists():
        return out
    repo = Repository.open_readonly(path)
    try:
        trs = _trades(repo)
        out["scan"] = repo.get_state("s3_scan", {}) or {}
        last = {}
        for t in trs:
            if t["status"] == "active":
                row = repo.conn.execute("SELECT close FROM candles WHERE symbol LIKE ? AND timeframe = '5m' "
                                        "ORDER BY ts DESC LIMIT 1", (f"{t['base']}/%",)).fetchone()
                last[t["id"]] = row[0] if row else None
    finally:
        repo.close()
    rows = []
    for t in sorted(trs, key=lambda x: x["created"]):
        r = {k: t.get(k) for k in ("base", "side", "status", "created", "filled_at", "closed_at", "entry", "sl",
                                   "target", "limit", "exit", "why", "sl_pct", "rr", "leverage", "risk_pct",
                                   "peak", "base_low", "run_pct", "wick_vol_x", "expires_at", "events",
                                   "gross_r", "cost_r", "net_r")}
        r.update(id=f"s3-{t['id']}", mode=t.get("mode") or "hourly", status_fa=STATUS_FA.get(t["status"], t["status"]),
                 why_fa=WHY_FA.get(t.get("why") or ""), price=last.get(t["id"]),
                 open_r=open_r(t, last.get(t["id"])), sl_now=t["sl"], tp1=t["target"], tp2=None)
        rows.append(r)
    out["trades"] = rows
    out["accounts"] = {m: _account([r for r in rows if r["mode"] == m], c, out["since"]) for m in MODES}
    out["account"] = out["accounts"]["hourly"]
    out["fast"] = bool(c["fast"])
    return out


def _account(rows: list[dict], c: dict, since: int) -> dict:
    """A $100 paper account: each closed trade risks risk_pct of the balance before it, in the order they closed.
    Sets risk_usd, pnl_usd and pnl_pct on the rows."""
    bal = float(c["balance_usd"])
    eq, peak, dd = [[since, bal]], bal, 0.0
    for r in sorted([r for r in rows if r["status"] in CLOSED], key=lambda r: r["closed_at"]):
        r["risk_usd"] = bal * c["risk_pct"] / 100
        r["pnl_usd"] = r["risk_usd"] * r["net_r"]
        bal += r["pnl_usd"]
        peak = max(peak, bal)
        dd = max(dd, (peak - bal) / peak * 100)
        eq.append([r["closed_at"], round(bal, 4)])
    for r in rows:
        if r["status"] == "active":
            r["risk_usd"] = bal * c["risk_pct"] / 100
            r["pnl_usd"] = None if r["open_r"] is None else r["risk_usd"] * r["open_r"]
        if r.get("net_r") is not None or r.get("open_r") is not None:
            x = r["net_r"] if r["status"] in CLOSED else r["open_r"]
            r["pnl_pct"] = x * r["sl_pct"] * (r["leverage"] or 1)
    closed = [r for r in rows if r["status"] in CLOSED]
    wins = [r for r in closed if r["net_r"] > 0]
    gain, loss = sum(r["net_r"] for r in wins), -sum(r["net_r"] for r in closed if r["net_r"] < 0)
    return {
        "balance": round(bal, 4), "start": float(c["balance_usd"]), "eq": eq, "max_dd_pct": round(dd, 2),
        "closed": len(closed), "wins": len(wins), "net_r": round(sum(r["net_r"] for r in closed), 3),
        "avg_r": round(sum(r["net_r"] for r in closed) / len(closed), 3) if closed else None,
        "pf": round(gain / loss, 2) if loss else None,
        "open": sum(1 for r in rows if r["status"] == "active"),
        "pending": sum(1 for r in rows if r["status"] == "pending"),
        "unfilled": sum(1 for r in rows if r["status"] in UNFILLED),
    }
