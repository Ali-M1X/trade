"""Extra panel data (Ali, 2026-11-15): how far each trade went for and against us (MAE/MFE),
how much the open positions move with BTC, system health, and free market data
(Gate futures stats, the Fear & Greed index, the economic calendar).

Everything here is best effort: a missing candle, a slow API or a changed field only leaves a
gap in the panel and never stops the build. Market data is cached next to the database so a
panel built every minute asks each API only now and then.
"""
from __future__ import annotations

import json
import logging
import math
import os
import re
import sqlite3
import time
from datetime import datetime
from pathlib import Path

log = logging.getLogger(__name__)

HOUR = 3_600_000
TF_MS = {"5m": 300_000, "15m": 900_000, "1h": HOUR, "4h": 4 * HOUR}
GATE = "https://api.gateio.ws/api/v4/futures/usdt"
FNG = "https://api.alternative.me/fng/?limit=30"
CALENDAR = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
CALENDAR_NEXT = "https://nfs.faireconomy.media/ff_calendar_nextweek.json"
TTL_S = {"tickers": 55, "stats": 300, "fng": 1800, "calendar": 1800, "calendar_next": 6 * 3600}   # panel: every minute
MAX_STATS_PER_BUILD = 8                # contract stats refreshed per build (each is one request)


# ------------------------------------------------------------------ trades
def _sym(base: str) -> str:
    return base.replace("%", "").replace("_", r"\_") + "/USDT%"


def _first_ts(repo, base: str, tf: str, memo: dict) -> int | None:
    key = (base, tf)
    if key not in memo:
        row = repo.conn.execute(r"SELECT MIN(ts) FROM candles WHERE symbol LIKE ? ESCAPE '\' AND timeframe=?",
                                (_sym(base), tf)).fetchone()
        memo[key] = row[0] if row else None
    return memo[key]


def excursions(repo, trades: list[dict], now: int) -> None:
    """Adds mfe_r / mae_r (best and worst move while the trade was open, in R of the first stop)
    and exc_tf (the candles used: the finest timeframe still stored for the whole trade)."""
    if repo is None:
        return
    memo: dict = {}
    for t in trades:
        entry, sl, start = t.get("entry"), t.get("sl"), t.get("filled_at")
        if not (entry and sl and start) or entry == sl:
            continue
        end = t.get("closed_at") or now
        risk = abs(entry - sl)
        for tf, step in TF_MS.items():
            first = _first_ts(repo, t["base"], tf, memo)
            if first is None or first > start:
                continue
            row = repo.conn.execute(
                r"SELECT MAX(high), MIN(low), COUNT(*) FROM candles WHERE symbol LIKE ? ESCAPE '\' "
                "AND timeframe=? AND ts>=? AND ts<=?", (_sym(t["base"]), tf, start // step * step, end)).fetchone()
            if not row or not row[2]:
                continue
            hi, lo = row[0], row[1]
            fav, adv = (hi - entry, entry - lo) if t["side"] > 0 else (entry - lo, hi - entry)
            t["mfe_r"], t["mae_r"], t["exc_tf"] = round(max(fav, 0) / risk, 3), round(max(adv, 0) / risk, 3), tf
            break


def _closes(repo, base: str, since: int, tf: str = "1h") -> dict[int, float]:
    rows = repo.conn.execute(
        r"SELECT ts, close FROM candles WHERE symbol LIKE ? ESCAPE '\' AND timeframe=? AND ts>=? "
        "GROUP BY ts ORDER BY ts", (_sym(base), tf, since)).fetchall()
    return {r[0]: r[1] for r in rows if r[1]}


def beta_btc(repo, bases: list[str], now: int, days: int = 7) -> dict[str, dict]:
    """Correlation and beta of each coin's 1h returns with BTC's over the last week
    (4h returns over three weeks when the database keeps no 1h candles for the pair)."""
    out: dict[str, dict] = {}
    if repo is None:
        return out
    since = now - days * 24 * HOUR
    btc = {"1h": _closes(repo, "BTC", since), "4h": _closes(repo, "BTC", now - 3 * days * 24 * HOUR, "4h")}
    for base in bases:
        if base == "BTC":
            out[base] = {"corr": 1.0, "beta": 1.0}
            continue
        for tf, start in (("1h", since), ("4h", now - 3 * days * 24 * HOUR)):
            c, b0 = _closes(repo, base, start, tf), btc[tf]
            ts = sorted(set(c) & set(b0))
            x = [b0[b] / b0[a] - 1 for a, b in zip(ts, ts[1:])]
            y = [c[b] / c[a] - 1 for a, b in zip(ts, ts[1:])]
            if len(x) >= 48:
                break
        else:
            continue
        mx, my = sum(x) / len(x), sum(y) / len(y)
        vx = sum((v - mx) ** 2 for v in x)
        vy = sum((v - my) ** 2 for v in y)
        cov = sum((a - mx) * (b - my) for a, b in zip(x, y))
        if vx <= 0 or vy <= 0:
            continue
        out[base] = {"corr": round(cov / math.sqrt(vx * vy), 3), "beta": round(cov / vx, 3)}
    return out


# ------------------------------------------------------------------ health
def health(cfg: dict, repo, now: int) -> dict:
    data_dir = Path(cfg["storage"]["db_path"]).parent
    backup = None
    try:
        backup = int(float((data_dir / ".last_backup").read_text()) * 1000)
    except (OSError, ValueError):
        pass
    runs = (repo.get_state("run_log", []) or []) if repo is not None else []
    return {"runs": runs[-200:], "last_backup": backup,
            "storage": os.environ.get("AGENT_STORAGE") or cfg["storage"].get("backend"),
            "backup_min": cfg["storage"].get("backup_min")}


# ------------------------------------------------------------------ taken / not taken
MARKS_FILE = "panel_marks.json"
ID_RE = re.compile(r"^[A-Za-z0-9_.-]{1,80}$")


def marks_path(cfg: dict) -> Path:
    return Path(cfg["storage"]["db_path"]).parent / MARKS_FILE


def read_marks(cfg: dict) -> dict:
    try:
        data = json.loads(marks_path(cfg).read_text(encoding="utf-8"))
        return {k: v for k, v in data.items() if ID_RE.match(k) and isinstance(v, dict)}
    except (OSError, ValueError, AttributeError):
        return {}


# ------------------------------------------------------------------ market data
def _f(x) -> float | None:
    try:
        v = float(x)
        return v if math.isfinite(v) else None
    except (TypeError, ValueError):
        return None


class _Net:
    """GET with a short timeout; after the first network failure the rest of this build stays offline."""

    def __init__(self, timeout: float = 5.0, get=None):
        import requests
        self.get_fn = get or requests.get
        self.timeout = timeout
        self.down = False

    def json(self, url: str, **params):
        if self.down:
            return None
        try:
            r = self.get_fn(url, params=params or None, timeout=self.timeout,
                            headers={"User-Agent": "trade-panel/1.0"})
            if r.status_code != 200:
                log.info("panel data %s: HTTP %s", url, r.status_code)
                return None
            return r.json()
        except Exception as e:                    # noqa: BLE001  (network, JSON: all only leave a gap)
            log.info("panel data %s failed: %s", url, e)
            self.down = True
            return None


def _ticker(x: dict) -> dict:
    return {"funding": _f(x.get("funding_rate")), "funding_next": _f(x.get("funding_rate_indicative")),
            "change": _f(x.get("change_percentage")), "vol_usd": _f(x.get("volume_24h_quote")),
            "last": _f(x.get("last")), "high": _f(x.get("high_24h")), "low": _f(x.get("low_24h")),
            "mark": _f(x.get("mark_price"))}


def stats_summary(rows: list[dict]) -> dict | None:
    """Gate contract_stats (1h, oldest first) -> what the panel shows. Field names differ
    between API versions, so each value has a fallback."""
    rows = [r for r in rows or [] if isinstance(r, dict)]
    if not rows:
        return None
    rows.sort(key=lambda r: _f(r.get("time")) or 0)
    last, first = rows[-1], rows[0]

    def oi(r):
        v = _f(r.get("open_interest_usd"))
        if v is None and _f(r.get("open_interest")) is not None and _f(r.get("mark_price")):
            v = _f(r.get("open_interest")) * _f(r.get("mark_price"))
        return v

    def ratio(r, a, b, c=None):
        v = _f(r.get(a))
        if v is None and c:
            hi, lo = _f(r.get(c[0])), _f(r.get(c[1]))
            v = hi / lo if hi is not None and lo else None
        return v

    def liq(r, k):
        v = _f(r.get(f"{k}_liq_usd"))
        if v is None:
            v = _f(r.get(f"{k}_liq_usd_new"))
        if v is None and _f(r.get(f"{k}_liq_size")) is not None and _f(r.get("mark_price")):
            v = _f(r.get(f"{k}_liq_size")) * _f(r.get("mark_price"))
        return v or 0.0

    oi_now, oi_then = oi(last), oi(first)
    return {
        "oi_usd": oi_now,
        "oi_change": round((oi_now / oi_then - 1) * 100, 2) if oi_now and oi_then else None,
        "lsr_account": ratio(last, "lsr_account", None, ("long_users", "short_users")),
        "lsr_top": ratio(last, "top_lsr_size", None, ("top_long_size", "top_short_size")),
        "lsr_taker": ratio(last, "lsr_taker", None, ("long_taker_size", "short_taker_size")),
        "liq_long_usd": round(sum(liq(r, "long") for r in rows), 2),
        "liq_short_usd": round(sum(liq(r, "short") for r in rows), 2),
        "hours": len(rows),
    }


# what each kind of release is, for the calendar's explanations (texts in app.js CALKIND)
EVENT_KINDS = (
    ("fomc", r"FOMC|Fed Chair|Powell|Monetary Policy (Statement|Report)|Fed.*Minutes|Beige Book"),
    ("rate", r"Federal Funds Rate|Rate Decision|Cash Rate|Refinancing Rate|Policy Rate|Bank Rate|Interest Rate"),
    ("cpi", r"\bCPI\b|Consumer Price|Inflation"),
    ("ppi", r"\bPPI\b|Producer Price"),
    ("pce", r"\bPCE\b|Personal Consumption|Personal (Income|Spending)"),
    ("nfp", r"Non-Farm|\bNFP\b"),
    ("adp", r"\bADP\b"),
    ("claims", r"Jobless Claims|Unemployment Claims"),
    ("unemp", r"Unemployment Rate|Claimant Count|Employment Change"),
    ("jolts", r"JOLTS|Job Openings"),
    ("wages", r"Earnings|Wage|Labor Cost"),
    ("gdp", r"\bGDP\b"),
    ("retail", r"Retail Sales"),
    ("pmi", r"\bPMI\b|\bISM\b"),
    ("sentiment", r"Sentiment|Confidence|Expectations"),
    ("durable", r"Durable Goods|Factory Orders|Industrial Production"),
    ("housing", r"Home Sales|Housing|Building Permits|Pending Home"),
    ("trade", r"Trade Balance|Current Account"),
    ("auction", r"Auction"),
    ("speech", r"Speaks|Testifies|Speech"),
)
MAJOR_CCY = {"USD": 1, "EUR": 0, "CNY": 0, "JPY": 0, "GBP": 0}
TOP_US = {"fomc", "rate", "cpi", "nfp", "pce", "ppi"}


def event_kind(title: str, impact: str = "") -> str:
    if impact == "Holiday" or "Holiday" in title:
        return "holiday"
    for kind, rx in EVENT_KINDS:
        if re.search(rx, title, re.I):
            return kind
    return "other"


def crypto_weight(country: str, impact: str, kind: str) -> int:
    """0-5: how much a release usually moves crypto. US data moves it most (the dollar and
    the Fed set the price of risk), the big US releases most of all; holidays mean thin markets."""
    if impact == "Holiday":
        return 1 if country == "USD" else 0
    w = {"High": 3, "Medium": 2, "Low": 1}.get(impact, 0)
    w += MAJOR_CCY.get(country, -1)
    if country == "USD" and kind in TOP_US:
        w += 1
    return max(0, min(5, w))


def _calendar(data) -> list[dict]:
    """Every release of the week, all countries (2026-11-15, Ali: "a much fuller calendar")."""
    out = []
    for e in data or []:
        if not isinstance(e, dict):
            continue
        try:
            ts = int(datetime.fromisoformat(str(e["date"])).timestamp() * 1000)
        except (KeyError, ValueError):
            continue
        title, country = str(e.get("title", ""))[:120], str(e.get("country") or "")[:4]
        impact = str(e.get("impact") or "")
        kind = event_kind(title, impact)
        out.append({"ts": ts, "title": title, "country": country, "impact": impact, "kind": kind,
                    "w": crypto_weight(country, impact, kind),
                    "forecast": str(e.get("forecast") or "")[:20], "previous": str(e.get("previous") or "")[:20],
                    "actual": str(e.get("actual") or "")[:20]})
    return sorted(out, key=lambda e: (e["ts"], -e["w"]))


def _merge_calendar(cache: dict, now: int) -> list[dict]:
    """This week and next, from 2 days back (past releases stay, with their actual values)."""
    seen, out = set(), []
    for key in ("calendar", "calendar_next"):
        for e in (cache.get(key) or {}).get("v") or []:
            k = (e["ts"], e["title"], e.get("country"))
            if e["ts"] > now - 48 * HOUR and k not in seen:
                seen.add(k)
                out.append(e)
    return sorted(out, key=lambda e: e["ts"])


def market_data(cfg: dict, bases: list[str], now: int, net: _Net | None = None) -> dict:
    """{"deriv": {base: {...}}, "fng": [[ts, value, label]], "calendar": [...], "fetched": {...}}"""
    pcfg = cfg.get("panel") or {}
    if not pcfg.get("market_data"):
        return {}
    path = Path(cfg["storage"]["db_path"]).parent / "panel_cache.json"
    try:
        cache = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        cache = {}
    net = net or _Net()
    t = time.time()

    def fresh(key, ttl):
        return key in cache and t - cache[key].get("t", 0) < ttl

    if not fresh("tickers", TTL_S["tickers"]):
        data = net.json(f"{GATE}/tickers")
        if isinstance(data, list):
            cache["tickers"] = {"t": t, "v": {str(x.get("contract", "")).removesuffix("_USDT"): _ticker(x)
                                              for x in data if isinstance(x, dict)}}
    stats = cache.setdefault("stats", {})
    asked = 0
    for base in bases:
        if asked >= MAX_STATS_PER_BUILD or net.down:
            break
        if base in stats and t - stats[base].get("t", 0) < TTL_S["stats"]:
            continue
        asked += 1
        data = net.json(f"{GATE}/contract_stats", contract=f"{base}_USDT", interval="1h", limit=25)
        summary = stats_summary(data) if isinstance(data, list) else None
        stats[base] = {"t": t, "v": summary}
    for key in [k for k in stats if t - stats[k].get("t", 0) > 6 * 3600]:
        del stats[key]
    if not fresh("fng", TTL_S["fng"]):
        data = net.json(FNG)
        rows = (data or {}).get("data") if isinstance(data, dict) else None
        if rows:
            cache["fng"] = {"t": t, "v": [[int(_f(r.get("timestamp")) or 0) * 1000, int(_f(r.get("value")) or 0),
                                           str(r.get("value_classification", ""))] for r in rows][::-1]}
    for key, url in (("calendar", CALENDAR), ("calendar_next", CALENDAR_NEXT)):
        if not fresh(key, TTL_S[key]) and not net.down:
            data = net.json(url)
            if isinstance(data, list):
                cache[key] = {"t": t, "v": _calendar(data)}
            else:                                   # the site allows few requests: retry in 10 min
                cache[key] = {"t": t - TTL_S[key] + 600, "v": (cache.get(key) or {}).get("v") or []}
    try:
        tmp = path.with_name(f".{path.name}.tmp")
        tmp.write_text(json.dumps(cache), encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        pass
    tick = (cache.get("tickers") or {}).get("v") or {}
    deriv = {}
    for base in set(bases) | {"BTC", "ETH"}:
        d = dict(tick.get(base) or {})
        s = (stats.get(base) or {}).get("v")
        if s:
            d.update(s)
        if d:
            deriv[base] = d
    return {"deriv": deriv, "fng": (cache.get("fng") or {}).get("v") or [],
            "calendar": _merge_calendar(cache, now),
            "fetched": {k: int(cache[k]["t"] * 1000) for k in ("tickers", "fng", "calendar") if k in cache}}


# ------------------------------------------------------------------ news page (2026-11-15, Ali: "more news, stars that tell them apart, their impact")
NEWS_DAYS = 14
NEWS_MAX = 600
NEWS_STATS_DAYS = 30


def _moves(prices: dict) -> dict:
    """{coin: {"1h": %, "4h": %, "24h": %}} from the prices the news run recorded after the headline."""
    out = {}
    for coin, p in (prices or {}).items():
        p0 = _f((p or {}).get("p0"))
        if not p0:
            continue
        m = {k: round((_f(p[k]) / p0 - 1) * 100, 2) for k in ("1h", "4h", "24h") if _f(p.get(k))}
        if m:
            out[coin] = m
    return out


def news_stars(level: int, kind: str, moves: dict) -> int:
    """1..5: how much a headline matters. The rule's strength (|level| 0..2), +1 for an exchange's
    own announcement, then +1 / +2 when the coin actually moved 3% / 8% within 4 hours."""
    stars = 1 + abs(level or 0)
    if level and kind == "exchange":
        stars += 1
    big = max((abs(m.get(k, 0)) for m in moves.values() for k in ("1h", "4h")), default=0)
    stars += 2 if big >= 8 else 1 if big >= 3 else 0
    return max(1, min(5, stars))


def news_view(repos, now: int) -> dict:
    """{"items": [...], "rules": [{rule, fa, level, n, avg_1h, avg_4h, avg_24h, hit}]} from the news tables."""
    from .news.classify import load_rules
    try:
        fa = {r["id"]: r.get("fa", r["id"]) for r in load_rules()["rules"]}
    except (OSError, ValueError, KeyError):
        fa = {}
    seen, rows = set(), []
    for repo in repos:
        try:
            got = repo.get_news(now - NEWS_STATS_DAYS * 24 * HOUR)
        except sqlite3.Error:
            continue
        for n in got:
            key = n["id"] or n["title"]
            if key in seen or n.get("rule") == "ignored":
                continue
            seen.add(key)
            rows.append(n)
    rows.sort(key=lambda n: -n["seen_at"])
    items = []
    stats: dict[str, dict] = {}
    for n in rows:
        moves = _moves(n.get("prices"))
        level = int(n.get("level") or 0)
        if n.get("rule") and moves and level:
            s = stats.setdefault(n["rule"], {"rule": n["rule"], "fa": fa.get(n["rule"], n["rule"]), "level": level,
                                             "n": 0, "1h": [], "4h": [], "24h": []})
            s["n"] += 1
            sign = 1 if level > 0 else -1
            for m in moves.values():
                for k in ("1h", "4h", "24h"):
                    if k in m:
                        s[k].append(sign * m[k])
        if n["seen_at"] < now - NEWS_DAYS * 24 * HOUR or len(items) >= NEWS_MAX:
            continue
        url = n.get("url") or ""
        items.append({
            "ts": n["seen_at"], "pub": n.get("ts"), "coins": n["coins"][:6], "level": level,
            "rule": n.get("rule"), "rule_fa": fa.get(n.get("rule") or "", None), "title": n["title"],
            "url": url if url.startswith(("http://", "https://")) else None, "source": n["source"],
            "kind": n.get("kind"), "alerted": bool(n.get("alerted")), "moves": moves,
            "stars": news_stars(level, n.get("kind") or "", moves),
        })

    def avg(v):
        return round(sum(v) / len(v), 2) if v else None
    rules = []
    for s in stats.values():
        rules.append({"rule": s["rule"], "fa": s["fa"], "level": s["level"], "n": s["n"],
                      "avg_1h": avg(s["1h"]), "avg_4h": avg(s["4h"]), "avg_24h": avg(s["24h"]),
                      "hit": round(sum(1 for v in s["24h"] if v > 0) / len(s["24h"]) * 100) if s["24h"] else None})
    rules.sort(key=lambda r: -r["n"])
    return {"items": items, "rules": rules, "days": NEWS_DAYS, "stats_days": NEWS_STATS_DAYS}
