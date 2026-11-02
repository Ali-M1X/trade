"""Why do trades lose? (2026-11-01, Ali's question)

Reads the joined multi-year replays (scripts/long_report.py output, trades with `ctx`) and the
backtest database, follows every filled trade on 1H candles and writes LOSS_REVIEW.md:

- the price path: best point before the exit (MFE), worst point (MAE), hours held
- what happened after a stop: did price then reach the target anyway (stop too tight, a
  shakeout) or keep going against the trade (the idea was wrong)
- the market: what BTC did during the trade (market-driven loss or coin-specific)
- what the signal knew: labels, phases, cycle, trigger, score sections, plan type, stop size,
  BTC score, etc. — each compared between winners and losers

    python scripts/loss_review.py --db data/backtest.db combo_top100_joined.json s2_top100_joined.json
"""
from __future__ import annotations

import argparse
import json
import statistics as st
from collections import defaultdict
from datetime import datetime, timezone

from agent.backtest.history import History
from agent.backtest.report import CLOSED_TRADES
from agent.config import load_config
from agent.store.repository import Repository

H = 3_600_000
AFTER_H = 7 * 24            # how long to follow price after a stop


def _y(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y")


class Paths:
    def __init__(self, cfg: dict, repo):
        self.h = History(cfg, repo)
        self.cache: dict = {}

    def candles(self, base: str):
        if base not in self.cache:
            df = self.h.raw(base, "1h")
            self.cache[base] = df.set_index("ts")[["high", "low", "close"]].astype(float) if len(df) else None
        return self.cache[base]

    def window(self, base: str, a: int, b: int):
        df = self.candles(base)
        return None if df is None else df[(df.index >= a) & (df.index < b)]


def follow(t: dict, paths: Paths) -> dict | None:
    side, entry, sl = t["side"], t["entry"], t["sl"]
    risk = abs(entry - sl)
    if not risk or not t.get("filled_at") or not t.get("closed_at"):
        return None
    a, b = t["filled_at"], t["closed_at"]
    w = paths.window(t["base"], a, b + H)
    if w is None or w.empty:
        return None
    fav = (w["high"] - entry) / risk if side == 1 else (entry - w["low"]) / risk
    adv = (w["low"] - entry) / risk if side == 1 else (entry - w["high"]) / risk
    out = {"mfe": float(fav.max()), "mae": float(adv.min()), "hours": (b - a) / H}
    last = w.iloc[-1]
    # the stop candle: closed back on the trade's side of the stop (a wick took it out)
    out["wick_stop"] = bool((last["close"] - sl) * side > 0)
    after = paths.window(t["base"], b + H, b + (AFTER_H + 1) * H)
    if after is not None and len(after):
        fav_a = (after["high"] - entry) / risk if side == 1 else (entry - after["low"]) / risk
        adv_a = (after["low"] - entry) / risk if side == 1 else (entry - after["high"]) / risk
        out["after_best"] = float(fav_a.max())
        out["after_worst"] = float(adv_a.min())
        hit_tp = fav_a[fav_a >= t.get("tp1_r", 2.0)]
        hit_more = adv_a[adv_a <= -2.0]
        out["after_tp_first"] = bool(len(hit_tp) and (not len(hit_more) or hit_tp.index[0] < hit_more.index[0]))
    btc = paths.window("BTC", a, b + H)
    if btc is not None and len(btc) >= 2:
        out["btc_move"] = float((btc["close"].iloc[-1] / btc["close"].iloc[0] - 1) * 100 * side)
    coin = w["close"]
    out["coin_move"] = float((coin.iloc[-1] / coin.iloc[0] - 1) * 100 * side) if len(coin) >= 2 else 0.0
    return out


def row(vals: list[float]) -> str:
    if not vals:
        return "– | – | –"
    win = sum(v > 0 for v in vals) / len(vals) * 100
    return f"{len(vals)} | {win:.0f}% | {sum(vals):+.1f}"


def group_table(trades: list[dict], key, title: str, min_n: int = 8, sort=True) -> list[str]:
    g: dict = defaultdict(list)
    for t in trades:
        ks = key(t)
        for k in (ks if isinstance(ks, (list, tuple, set)) else [ks]):
            g[k].append(t["net_r"])
    items = [(k, v) for k, v in g.items() if len(v) >= min_n]
    if sort:
        items.sort(key=lambda kv: sum(kv[1]) / len(kv[1]))
    else:
        items.sort(key=lambda kv: str(kv[0]))
    out = [f"\n#### {title}\n", "| Group | Trades | Win rate | Net R | Avg R |", "|---|---|---|---|---|"]
    for k, v in items:
        out.append(f"| {k} | {row(v)} | {sum(v) / len(v):+.2f} |")
    return out


def bucket(x, edges: list[float], fmt="{:g}") -> str:
    if x is None:
        return "n/a"
    for lo, hi in zip(edges, edges[1:]):
        if lo <= x < hi:
            return f"{fmt.format(lo)}–{fmt.format(hi)}"
    return f">= {fmt.format(edges[-1])}" if x >= edges[-1] else f"< {fmt.format(edges[0])}"


def section_compare(trades: list[dict]) -> list[str]:
    wins = [t for t in trades if t["net_r"] > 0]
    loss = [t for t in trades if t["net_r"] <= 0]
    keys = sorted({k for t in trades for k in (t["ctx"].get("sections") or {})})
    out = ["\n#### Score sections: average points, winners against losers\n",
           "| Section | Winners | Losers | Difference |", "|---|---|---|---|"]
    for k in keys:
        w = [t["ctx"]["sections"].get(k, 0) for t in wins if t["ctx"].get("sections")]
        l_ = [t["ctx"]["sections"].get(k, 0) for t in loss if t["ctx"].get("sections")]
        if w and l_:
            out.append(f"| {k} | {st.mean(w):.1f} | {st.mean(l_):.1f} | {st.mean(w) - st.mean(l_):+.1f} |")
    w = [t["score"] for t in wins]
    l_ = [t["score"] for t in loss]
    if w and l_:
        out.append(f"| **total score** | {st.mean(w):.1f} | {st.mean(l_):.1f} | {st.mean(w) - st.mean(l_):+.1f} |")
    return out


def stops_section(trades: list[dict]) -> list[str]:
    stops = [t for t in trades if t["status"] == "sl" and t.get("path")]
    if not stops:
        return []
    n = len(stops)
    p = [t["path"] for t in stops]

    def share(cond) -> str:
        k = sum(1 for x in p if cond(x))
        return f"{k} ({k / n * 100:.0f}%)"
    quick = share(lambda x: x["hours"] < 12)
    day = share(lambda x: 12 <= x["hours"] < 48)
    slow = share(lambda x: x["hours"] >= 48)
    never = share(lambda x: x["mfe"] < 0.3)
    half = share(lambda x: 0.5 <= x["mfe"] < 1)
    one = share(lambda x: 1 <= x["mfe"] < 2)
    wick = share(lambda x: x["wick_stop"])
    tp_after = share(lambda x: x.get("after_tp_first"))
    worse = share(lambda x: x.get("after_worst", 0) <= -2 and not x.get("after_tp_first"))
    btc_against = share(lambda x: x.get("btc_move", 0) <= -2)
    btc_with = share(lambda x: x.get("btc_move", 0) >= 0)
    lines = [f"\n#### The {n} full stops (status sl)\n",
             "| Question | Stops |", "|---|---|",
             f"| Stopped within 12 hours of the fill | {quick} |",
             f"| Stopped after 12-48 hours | {day} |",
             f"| Stopped after 48 hours or more | {slow} |",
             f"| Never went 0.3R in profit (wrong from the start) | {never} |",
             f"| Was 0.5-1R in profit before the stop | {half} |",
             f"| Was 1-2R in profit before the stop (gave it back) | {one} |",
             f"| Stop candle closed back on the trade's side (a wick took the stop) | {wick} |",
             f"| Within 7 days after the stop, price reached TP1 before going 2R further against | {tp_after} |",
             f"| Within 7 days after the stop, price went 2R further against (the idea was wrong) | {worse} |",
             f"| BTC moved 2% or more against the trade while it was open | {btc_against} |",
             f"| BTC moved with the trade (or flat) while it was open | {btc_with} |",
             f"| Median hours to the stop | {st.median(x['hours'] for x in p):.0f} |",
             f"| Median best point before the stop | {st.median(x['mfe'] for x in p):+.2f}R |"]
    return lines


def review(name: str, trades: list[dict], paths: Paths) -> list[str]:
    closed = [t for t in trades if t["status"] in CLOSED_TRADES and t.get("filled_at")]
    for t in closed:
        t["path"] = follow(t, paths)
        t["ctx"] = t.get("ctx") or {}
    lines = [f"\n## `{name}`: {len(closed)} closed trades, {sum(t['net_r'] for t in closed):+.1f}R\n"]
    outcome = defaultdict(list)
    for t in closed:
        outcome[t["status"]].append(t["net_r"])
    lines += ["| Exit | Trades | Net R |", "|---|---|---|"]
    for k, v in sorted(outcome.items(), key=lambda kv: sum(kv[1])):
        lines.append(f"| {k} | {len(v)} | {sum(v):+.1f} |")
    lines += stops_section(closed)
    lines += section_compare(closed)
    c = lambda k: (lambda t: t["ctx"].get(k) if t["ctx"].get(k) not in (None, "") else "n/a")
    side = lambda t: "long" if t["side"] == 1 else "short"
    lines += group_table(closed, lambda t: f"{t['regime']} {side(t)} {_y(t['filled_at'])}", "Regime × side × year", 5, sort=False)
    lines += group_table(closed, lambda t: t["ctx"].get("labels") or ["none"], "Scanner label (a trade counts under each of its labels)")
    lines += group_table(closed, c("phase_d"), "Daily phase at the signal")
    lines += group_table(closed, c("phase_4h"), "4H phase at the signal")
    lines += group_table(closed, c("cycle"), "Cycle note")
    lines += group_table(closed, c("trigger"), "4H trigger candle")
    lines += group_table(closed, lambda t: f"{t['order']}", "Plan type")
    lines += group_table(closed, lambda t: f"{t['ctx'].get('support_kind')} {t['ctx'].get('support_tf')}", "Level the plan leans on")
    lines += group_table(closed, lambda t: bucket(t["ctx"].get("support_touches"), [1, 2, 3, 4, 6]), "Touches of that level", sort=False)
    lines += group_table(closed, lambda t: len(t["ctx"].get("confirmations") or []), "Number of 1H confirmations", sort=False)
    lines += group_table(closed, lambda t: t["ctx"].get("confirmations") or ["none"], "1H confirmation (each one)")
    lines += group_table(closed, lambda t: bucket(t["score"], [65, 70, 75, 80, 85, 90]), "Score", sort=False)
    lines += group_table(closed, lambda t: t["grade"], "Grade", sort=False)
    lines += group_table(closed, lambda t: bucket(t["ctx"].get("sl_pct"), [0, 2, 3, 4, 6, 8, 12]), "Stop distance (% of price)", sort=False)
    lines += group_table(closed, lambda t: bucket((t["ctx"].get("majors") or {}).get("btc", 0) * t["side"], [-6, -3, 0, 3, 6]), "BTC score in the trade's direction (-6..+6)", sort=False)
    lines += group_table(closed, lambda t: "divergence" if (t["ctx"].get("majors") or {}).get("divergence") else "no divergence", "BTC and TOTAL2 in opposite directions")
    lines += group_table(closed, lambda t: bucket(t["ctx"].get("cycle_d"), [-1, 0, 1, 2]), "Daily cycle direction", sort=False)
    lines += group_table(closed, lambda t: bucket((t["ctx"].get("funding_pct") or 0) * t["side"], [-0.05, 0, 0.01, 0.03, 0.05]), "Funding in the trade's direction (%)", sort=False)
    lines += group_table(closed, lambda t: t["ctx"].get("flags") or ["no flag"], "Flags")
    lines += group_table(closed, lambda t: bucket((t["filled_at"] - t["created"]) / H, [0, 1, 6, 12, 24]), "Hours from signal to fill", sort=False)
    lines += group_table(closed, lambda t: datetime.fromtimestamp(t["filled_at"] / 1000, tz=timezone.utc).strftime("%a"), "Weekday of the fill", sort=False)
    lines += group_table([t for t in closed if t.get("path")], lambda t: bucket(t["path"].get("btc_move"), [-5, -2, 0, 2, 5]), "BTC move during the trade, in the trade's direction (%)", sort=False)
    lines += group_table(closed, lambda t: t["base"], "Coin (worst first, 8+ trades)")
    return lines


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--out", default="LOSS_REVIEW.md")
    ap.add_argument("inputs", nargs="+")
    a = ap.parse_args()
    cfg = load_config()
    paths = Paths(cfg, Repository(a.db))
    lines = ["# Loss review (2021 - 2026)",
             "\nEvery filled trade of the live systems, followed on 1H candles. R: multiples of the "
             "trade's own risk, after costs. Groups with fewer than 8 trades are left out.\n"]
    for p in a.inputs:
        d = json.load(open(p))
        lines += review(d["variant"], d["trades"], paths)
    open(a.out, "w", encoding="utf-8").write("\n".join(lines) + "\n")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
