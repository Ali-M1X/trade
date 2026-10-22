"""Backtest trades by market regime, and what simple filters would have changed (analysis only,
2026-10-22). Reads variant result JSON files (one history per --set) and prints markdown.

Filters drop trades after the fact, so a dropped trade's slot is not given to another signal:
an approximation of what the engine would do with the filter switched on.

    python scripts/regime_review.py --set top200 results200/*.json --set top100_oct09 results09/*.json
"""
from __future__ import annotations

import argparse
import json
from collections import defaultdict

from agent.backtest.report import CLOSED_TRADES, REGIME_NAMES, stats
from agent.backtest.robust import bootstrap, quarter_totals, weekly

KEEP = ("ph_stage", "ph_stage_top100", "s2_trend", "s2_trend_top100", "atr_2r")
FILTERS = {
    "all": lambda t: True,
    "no neutral": lambda t: t["regime"] != "neutral",
    "neutral only A": lambda t: t["regime"] != "neutral" or t["grade"] == "A",
    "A only": lambda t: t["grade"] == "A",
    "market entries only": lambda t: t["order"] == "market",
    "limit entries only": lambda t: t["order"] != "market",
    "long only": lambda t: t["side"] == 1,
    "score >= 75": lambda t: t["score"] >= 75,
    "no neutral + market only": lambda t: t["regime"] != "neutral" and t["order"] == "market",
}


def closed(trades):
    return [t for t in trades if t["status"] in CLOSED_TRADES]


def row(label, trades):
    s = stats(trades)
    if not s.get("trades"):
        return f"| {label} | 0 | – | – | – |"
    return f"| {label} | {s['trades']} | {s['win_rate']:.0f}% | {s['avg_r']:+.2f} | {s['total_r']:+.1f} |"


def report(name, res):
    tr = closed(res["trades"])
    hours = res.get("regime_hours", {})
    th = sum(hours.values()) or 1
    out = [f"\n## {name}: `{res['variant']}` ({res['meta'].get('coins', '?')} coins)\n",
           "| Group | Trades | Win | Avg R | Total R |", "|---|---|---|---|---|", row("**All**", tr)]
    for g in REGIME_NAMES:
        sel = [t for t in tr if t["regime"] == g]
        out.append(row(f"{g} ({hours.get(g, 0) / th * 100:.0f}% of time)", sel))
        for side, lab in ((1, "long"), (-1, "short")):
            s2 = [t for t in sel if t["side"] == side]
            if s2:
                out.append(row(f"  {g} {lab}", s2))
        for gr in ("A", "B"):
            s3 = [t for t in sel if t["grade"] == gr]
            if s3:
                out.append(row(f"  {g} grade {gr}", s3))
    for k, lab in (("order", "order"), ("grade", "grade")):
        for v in sorted({t[k] for t in tr}):
            out.append(row(f"{lab} {v}", [t for t in tr if t[k] == v]))
    for lo, hi in ((0, 70), (70, 75), (75, 80), (80, 101)):
        out.append(row(f"score {lo}-{hi}", [t for t in tr if lo <= t["score"] < hi]))
    # what-if filters
    base_q = quarter_totals(res)
    out += ["", "| Filter | Trades | Total R | Quarters | Quarters better than all | P(total > 0) |",
            "|---|---|---|---|---|---|"]
    for fname, f in FILTERS.items():
        sub = {**res, "trades": [t for t in res["trades"] if f(t)]}
        q = quarter_totals(sub)
        p, _, _ = bootstrap(weekly(sub))
        out.append(f"| {fname} | {len(closed(sub['trades']))} | {sum(q):+.1f} | "
                   + " / ".join(f"{x:+.1f}" for x in q)
                   + f" | {sum(a > b for a, b in zip(q, base_q))}/4 | {p:.0%} |")
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--set", nargs="+", action="append", required=True, metavar=("NAME", "FILE"))
    a = ap.parse_args()
    lines = ["# Backtest by regime", ""]
    pooled = defaultdict(list)
    for name, *files in a.set:
        for p in files:
            res = json.load(open(p))
            if res["variant"] not in KEEP:
                continue
            lines += report(name, res)
            fam = "system 2 (s2_trend)" if res["variant"].startswith("s2") else (
                "system 1 (ph_stage)" if res["variant"].startswith("ph") else res["variant"])
            pooled[fam] += closed(res["trades"])
    lines += ["\n## Pooled over all histories (same trades can repeat across histories)\n",
              "| System | Regime | Trades | Win | Avg R | Total R |", "|---|---|---|---|---|---|"]
    for fam, tr in pooled.items():
        for g in REGIME_NAMES:
            sel = [t for t in tr if t["regime"] == g]
            s = stats(sel)
            if s.get("trades"):
                lines.append(f"| {fam} | {g} | {s['trades']} | {s['win_rate']:.0f}% | {s['avg_r']:+.2f} | {s['total_r']:+.1f} |")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
