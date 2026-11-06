"""Where system 3's trades lose (2026-11-06): breakdowns of the joined scaled replays.

Each table: trades, winners, net R, R before costs, average net R. Times are converted back
to real time with the s3_meta.json that scaled_history.py writes.

    python scripts/s3_review.py meta/s3_meta.json s3_trend_joined.json s3_combo_joined.json
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from datetime import datetime, timezone

CLOSED = ("sl", "breakeven", "tp3")


def table(title, trades, key):
    by = defaultdict(list)
    for t in trades:
        by[key(t)].append(t)
    print(f"\n  {title}")
    for k in sorted(by, key=str):
        ts = by[k]
        net = sum(t["net_r"] for t in ts)
        gross = sum(t.get("gross_r", t["net_r"]) for t in ts)
        wins = sum(t["net_r"] > 0 for t in ts)
        print(f"    {str(k):<22} {len(ts):>4} trades {wins:>4} won  net {net:+7.1f}R  "
              f"before costs {gross:+7.1f}R  avg {net / len(ts):+.2f}R")


def bucket(x, edges):
    if x is None:
        return "?"
    for e in edges:
        if x < e:
            return f"<{e}"
    return f">={edges[-1]}"


def main() -> int:
    meta = json.load(open(sys.argv[1]))
    real = lambda t: meta["real_start"] + (t - meta["scaled_t0"]) // meta["scale"]
    for path in sys.argv[2:]:
        d = json.load(open(path))
        ts = [t for t in d["trades"] if t["status"] in CLOSED]
        if not ts:
            print(f"\n{d['variant']}: no closed trades")
            continue
        cost = sum(t.get("gross_r", t["net_r"]) - t["net_r"] for t in ts) / len(ts)
        print(f"\n{d['variant']}: {len(ts)} closed trades, average cost {cost:.2f}R per trade")
        trend = {"bull": 1, "bear": -1}
        table("side vs the market trend", ts, lambda t: (
            "range " + ("long" if t["side"] == 1 else "short") if t["regime"] not in trend
            else ("with trend" if t["side"] == trend[t["regime"]] else "against trend") + " " + t["regime"]))
        table("grade", ts, lambda t: t["grade"])
        table("system", ts, lambda t: t.get("system", "house"))
        table("stop distance % of price", ts, lambda t: bucket((t.get("ctx") or {}).get("sl_pct"),
                                                               [0.4, 0.6, 0.8, 1.2, 2.0]))
        table("how it ended", ts, lambda t: t["status"])
        table("real hour of entry (UTC)", ts, lambda t: f"{datetime.fromtimestamp(real(t['created']) / 1000, timezone.utc).hour // 4 * 4:02d}-{datetime.fromtimestamp(real(t['created']) / 1000, timezone.utc).hour // 4 * 4 + 4:02d}h")
        table("real minutes held", ts, lambda t: bucket(
            (t["closed_at"] - t["filled_at"]) / 3_600_000 * 5 if t.get("filled_at") and t.get("closed_at") else None,
            [30, 60, 120, 240, 480]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
