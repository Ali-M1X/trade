"""Market regime of the last days, for the panel's regime page (2026-11-16, Ali: "when results
turn bad I want one overall reason, e.g. the market changed regime, so the last N positions
went like this").

The market is a basket of large coins (BTC, ETH, SOL, ...) on 4H candles: the average move of
each bar (log returns) and the average bar size (high/low). Every bar gets one label:
  drop / jump_up   the basket moved >= jump_pct within two bars (8 h)
  trend_down / up  it moved >= trend_pct within six bars (24 h), mostly one way (efficiency),
                   or >= 1.5 x trend_pct within three days and still that way in the last 24 h
  whipsaw          a bar >= whipsaw_ratio x the usual size that ends near where it started:
                   stops on both sides get taken
  range_quiet      no direction and bars smaller than quiet_ratio x the days before
  range            no direction
Regimes (segments) are the families: trend_down (drop too), trend_up (jump_up too) and range
(range_quiet and whipsaw too). A family that lasts less than min_bars is folded into the one
before it. The bar labels stay as events on the page.
"""
from __future__ import annotations

import math
import sqlite3

H = 3_600_000
BAR = 4 * H
DAY = 24 * H

BASKET = ("BTC", "ETH", "SOL", "BNB", "XRP", "DOGE", "ADA", "LINK", "AVAX", "SUI")
DEFAULTS = {"days": 7, "jump_pct": 4.0, "trend_pct": 4.0, "min_efficiency": 0.4,
            "quiet_ratio": 0.75, "whipsaw_ratio": 1.8, "min_bars": 2}
FAMILY = {"drop": "trend_down", "trend_down": "trend_down", "jump_up": "trend_up",
          "trend_up": "trend_up", "range": "range", "range_quiet": "range", "whipsaw": "range"}


def _bars(repo, since: int) -> tuple[list[str], dict[str, dict[int, tuple]]]:
    out = {}
    for b in BASKET:
        try:
            rows = repo.conn.execute(
                "SELECT ts, open, high, low, close FROM candles WHERE symbol LIKE ? AND timeframe = '4h' "
                "AND ts >= ? ORDER BY exchange = 'gate', ts", (f"{b}/%", since)).fetchall()
        except sqlite3.Error:
            continue
        d = {}
        for ts, o, hi, lo, c in rows:                  # gate rows come last and win
            if o and lo and c:
                d[ts] = (o, hi, lo, c)
        if d:
            out[b] = d
    return list(out), out


def _dominance(repo, now: int) -> dict:
    try:
        rows = repo.conn.execute(
            "SELECT ts, total_mcap, btc_mcap, usdt_mcap FROM dominance WHERE source = 'global' "
            "AND ts >= ? ORDER BY ts", (now - 2 * DAY,)).fetchall()
    except sqlite3.Error:
        return {}
    rows = [r for r in rows if r[1] and r[2]]
    if not rows:
        return {}
    last = rows[-1]
    ago = [r for r in rows if r[0] <= last[0] - DAY]
    first = ago[-1] if ago else rows[0]

    def view(r):
        tot, btc, usdt = r[1], r[2], r[3] or 0
        return {"t2": (tot - btc) / 1e9, "btc_d": btc / tot * 100, "usdt_d": usdt / tot * 100}
    a, b = view(first), view(last)
    return {"ts": last[0], "from": first[0], "t2": round(b["t2"], 1),
            "t2_chg": round((b["t2"] / a["t2"] - 1) * 100, 2),
            "btc_d": round(b["btc_d"], 2), "btc_d_chg": round(b["btc_d"] - a["btc_d"], 2),
            "usdt_d": round(b["usdt_d"], 2), "usdt_d_chg": round(b["usdt_d"] - a["usdt_d"], 2)}


def label_bars(moves: list[float], sizes: list[float], c: dict) -> list[str | None]:
    """moves: basket % move of each bar; sizes: average high/low % of each bar."""
    out: list[str | None] = []
    for i in range(len(moves)):
        if i < 6:
            out.append(None)
            continue
        r2 = moves[i] + moves[i - 1]
        r6 = sum(moves[i - 5:i + 1])
        path = sum(abs(x) for x in moves[i - 5:i + 1])
        eff = abs(r6) / path if path else 0.0
        before = sizes[max(0, i - 35):i - 5] or sizes[:i]
        usual = sum(before) / len(before) if before else None
        recent = sum(sizes[i - 5:i + 1]) / 6
        if r2 >= c["jump_pct"]:
            lab = "jump_up"
        elif r2 <= -c["jump_pct"]:
            lab = "drop"
        elif abs(r6) >= c["trend_pct"] and eff >= c["min_efficiency"]:
            lab = "trend_up" if r6 > 0 else "trend_down"
        elif i >= 18 and _slow(moves[i - 17:i + 1], c) and r6 * sum(moves[i - 17:i + 1]) > 0 \
                and abs(r6) >= c["trend_pct"] / 4:            # still moving that way today
            lab = "trend_up" if sum(moves[i - 17:i + 1]) > 0 else "trend_down"
        elif usual and sizes[i] >= c["whipsaw_ratio"] * usual and abs(moves[i]) < sizes[i] / 3:
            lab = "whipsaw"
        elif usual and recent < c["quiet_ratio"] * usual:
            lab = "range_quiet"
        else:
            lab = "range"
        out.append(lab)
    return out


def _slow(m: list[float], c: dict) -> bool:
    """A slower trend: >= 1.5 x trend_pct over three days, mostly one way."""
    r, p = sum(m), sum(abs(x) for x in m)
    return abs(r) >= 1.5 * c["trend_pct"] and p > 0 and abs(r) / p >= c["min_efficiency"] - 0.1


def segments(ts: list[int], labels: list[str | None], moves: list[float], sizes: list[float],
             min_bars: int) -> list[dict]:
    fam = [FAMILY.get(x) if x else None for x in labels]
    # a family shorter than min_bars takes the one before it
    i = 0
    while i < len(fam):
        j = i
        while j + 1 < len(fam) and fam[j + 1] == fam[i]:
            j += 1
        if fam[i] and j - i + 1 < min_bars and i > 0 and fam[i - 1]:
            for k in range(i, j + 1):
                fam[k] = fam[i - 1]
        i = j + 1
    segs: list[dict] = []
    for k, f in enumerate(fam):
        if f is None:
            continue
        if not segs or segs[-1]["label"] != f:
            segs.append({"label": f, "from": ts[k], "to": ts[k] + BAR, "move": 0.0, "sizes": []})
        s = segs[-1]
        s["to"] = ts[k] + BAR
        s["move"] += moves[k]
        s["sizes"].append(sizes[k])
    for s in segs:
        s["move"] = round((math.exp(s["move"] / 100) - 1) * 100, 2)
        s["bar_size"] = round(sum(s["sizes"]) / len(s["sizes"]), 2)
        del s["sizes"]
    return segs


def regime_view(repo, now: int, cfg: dict | None = None) -> dict:
    c = {**DEFAULTS, **(((cfg or {}).get("panel") or {}).get("regime") or {})}
    start = now - c["days"] * DAY
    names, bars = _bars(repo, start - 7 * DAY)
    if not bars:
        return {}
    common = sorted(set.intersection(*[set(v) for v in bars.values()]))
    common = [t for t in common if t + BAR <= now]   # closed candles only: the stored forming one is a stale snapshot
    if len(common) < 8:
        return {}
    moves, sizes = [], []
    for t in common:
        moves.append(sum(math.log(bars[b][t][3] / bars[b][t][0]) for b in names) / len(names) * 100)
        sizes.append(sum((bars[b][t][1] / bars[b][t][2] - 1) * 100 for b in names) / len(names))
    labels = label_bars(moves, sizes, c)
    segs = [s for s in segments(common, labels, moves, sizes, c["min_bars"]) if s["to"] > start]
    keep = [k for k, t in enumerate(common) if t + BAR > start]
    cum, path = 0.0, []
    for k in keep:
        cum += moves[k]
        path.append([common[k], round((math.exp(cum / 100) - 1) * 100, 2), round(sizes[k], 2), labels[k]])
    events = [{"ts": common[k], "kind": labels[k], "move": round(moves[k] + (moves[k - 1] if k else 0), 2),
               "size": round(sizes[k], 2)}
              for k in keep if labels[k] in ("drop", "jump_up", "whipsaw")]
    i = len(common) - 1
    r6 = sum(moves[i - 5:i + 1])
    p6 = sum(abs(x) for x in moves[i - 5:i + 1])
    before = sizes[max(0, i - 35):i - 5]
    return {"basket": names, "bars": path, "segments": segs, "events": events,
            "metrics": {"move_24h": round(r6, 2), "efficiency": round(abs(r6) / p6, 2) if p6 else None,
                        "bar_size_24h": round(sum(sizes[i - 5:i + 1]) / 6, 2),
                        "bar_size_before": round(sum(before) / len(before), 2) if before else None},
            "dominance": _dominance(repo, now),
            "params": {k: c[k] for k in ("jump_pct", "trend_pct", "min_efficiency", "quiet_ratio",
                                         "whipsaw_ratio", "min_bars")}}
