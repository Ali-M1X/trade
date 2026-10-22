"""Multi-year backtest by price regime (2026-10-22).

Joins the per-year replays of each variant (`backtest --variant X --start --end`) and splits
the closed trades by year, by regime (bull / range / bear, the price regime the trade was
opened in) and side, by grade and by plan type. Writes LONG_BACKTEST.md and one joined
JSON per variant.

    python scripts/long_report.py results/*.json
"""
from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone

from agent.backtest.report import CLOSED_TRADES

HOUR = 3_600_000
REGIMES = ("bull", "range", "bear")
SIDES = {1: "long", -1: "short"}


def _y(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y")


def _d(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d")


def join(parts: list[dict]) -> dict:
    parts = sorted(parts, key=lambda r: r["meta"]["start"])
    hours = Counter()
    for r in parts:
        hours.update(r["regime_hours"])
    return {"variant": parts[0]["variant"], "trades": [t for r in parts for t in r["trades"]],
            "regime_hours": dict(hours),
            "meta": {**parts[0]["meta"], "start": parts[0]["meta"]["start"],
                     "end": parts[-1]["meta"]["end"],
                     "slices": [[r["meta"]["start"], r["meta"]["end"]] for r in parts]}}


def cell(v: list[float]) -> str:
    if not v:
        return "–"
    return f"{len(v)} / {sum(x > 0 for x in v) / len(v) * 100:.0f}% / {sum(v):+.1f}"


def table(rows: dict, years: list[str], title: str) -> list[str]:
    out = [f"\n#### {title}\n", "| Group | All years | " + " | ".join(years) + " | Years > 0 |",
           "|---|---|" + "---|" * len(years) + "---|"]
    for key, by_year in rows.items():
        allv = [x for v in by_year.values() for x in v]
        if not allv:
            continue
        pos = sum(sum(by_year.get(y, [])) > 0 for y in years if by_year.get(y))
        seen = sum(1 for y in years if by_year.get(y))
        out.append(f"| {key} | {cell(allv)} | " + " | ".join(
            f"{sum(by_year[y]):+.1f} ({len(by_year[y])})" if by_year.get(y) else "–" for y in years)
            + f" | {pos}/{seen} |")
    return out


def report(res: dict) -> list[str]:
    tr = [t for t in res["trades"] if t["status"] in CLOSED_TRADES]
    years = sorted({_y(t["created"]) for t in tr})
    m = res["meta"]
    hours = res["regime_hours"]
    tot = sum(hours.values()) or 1
    out = [f"\n### `{res['variant']}`: {_d(m['start'])} → {_d(m['end'])}, {m.get('coins', '?')} coins, "
           f"{len(tr)} closed trades, {sum(t['net_r'] for t in tr):+.1f}R net\n",
           "Time in each regime: " + ", ".join(f"{r} {hours.get(r, 0) / tot * 100:.0f}%" for r in REGIMES)
           + ". Cells: trades / win rate / net R; per year: net R (trades)."]

    def group(key):
        g = defaultdict(lambda: defaultdict(list))
        for t in tr:
            k = key(t)
            if k:
                g[k][_y(t["created"])].append(t["net_r"])
        return g

    out += table({"all": group(lambda t: "all")["all"]}, years, "Total")
    rs = group(lambda t: f"{t['regime']} {SIDES[t['side']]}")
    out += table({k: rs[k] for r in REGIMES for k in (f"{r} long", f"{r} short") if k in rs},
                 years, "Regime × side")
    gr = group(lambda t: f"{t['regime']} {SIDES[t['side']]} {t['grade']}")
    out += table({k: gr[k] for k in sorted(gr, key=lambda k: (REGIMES.index(k.split()[0]), k))},
                 years, "Regime × side × grade")
    od = group(lambda t: f"{t['regime']} {t['order']}")
    out += table({k: od[k] for k in sorted(od, key=lambda k: (REGIMES.index(k.split()[0]), k))},
                 years, "Regime × plan type (limit: wait for the level; market: price already in the zone)")
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("inputs", nargs="+")
    a = ap.parse_args()
    by_var = defaultdict(list)
    for p in a.inputs:
        r = json.load(open(p))
        by_var[r["variant"]].append(r)
    lines = ["# Multi-year backtest by price regime", "",
             "Live systems replayed year by year on stored exchange history with the price regime "
             "(BTC + alt index, trend_adx) and every side open, so each trade carries the regime "
             "it was opened in. Net R after fees, slippage and funding. Today's top-100 coins over "
             "the whole period (survivorship bias); market caps are close × today's supply."]
    for name, parts in sorted(by_var.items()):
        res = join(parts)
        json.dump(res, open(f"{name}_joined.json", "w"))
        lines += report(res)
    text = "\n".join(lines) + "\n"
    print(text)
    open("LONG_BACKTEST.md", "w").write(text)


if __name__ == "__main__":
    main()
