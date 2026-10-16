"""Robustness check for backtest variants (2026-10-16).

The 9/3-month split (variants.py) has been read in every comparison since 2026-10-06, so the
last 3 months no longer judge a new idea cleanly. This report judges a variant against a
baseline on the same stored history in three ways that don't depend on one holdout:

1. Quarters: net R in each of `QUARTERS` equal periods. A change is consistent only when it
   beats the baseline in at least `MIN_QUARTERS` of them.
2. Paired weekly bootstrap: weeks are resampled with replacement (both variants keep the same
   weeks), giving the chance that the variant's total beats the baseline's, and the same for
   the variant alone being above zero.
3. Coin dependence: total R without the variant's single best coin.

Closed trades only, by opening time, net of costs (as report.stats).
"""
from __future__ import annotations

import random
from collections import defaultdict

from .report import CLOSED_TRADES, _date

WEEK = 7 * 86_400_000
QUARTERS = 4
MIN_QUARTERS = 3
MIN_PROB = 0.9
SAMPLES = 5000


def closed(res: dict) -> list[dict]:
    return [t for t in res["trades"] if t["status"] in CLOSED_TRADES]


def quarter_totals(res: dict, n: int = QUARTERS) -> list[float]:
    start, end = res["meta"]["start"], res["meta"]["end"]
    step = (end - start) / n
    out = [0.0] * n
    for t in closed(res):
        out[min(n - 1, max(0, int((t["created"] - start) // step)))] += t["net_r"]
    return out


def weekly(res: dict) -> list[float]:
    start, end = res["meta"]["start"], res["meta"]["end"]
    out = [0.0] * (int((end - start) // WEEK) + 1)
    for t in closed(res):
        out[max(0, min(len(out) - 1, int((t["created"] - start) // WEEK)))] += t["net_r"]
    return out


def bootstrap(diff: list[float], samples: int = SAMPLES, seed: int = 0) -> tuple[float, float, float]:
    """P(sum > 0) and the 5%/95% bounds of the sum, resampling the weekly values."""
    rng, n = random.Random(seed), len(diff)
    sums = sorted(sum(diff[rng.randrange(n)] for _ in range(n)) for _ in range(samples))
    return (sum(s > 0 for s in sums) / samples, sums[int(0.05 * samples)], sums[int(0.95 * samples) - 1])


def by_coin(res: dict) -> dict[str, float]:
    out: dict[str, float] = defaultdict(float)
    for t in closed(res):
        out[t["base"]] += t["net_r"]
    return dict(out)


def without_best_coin(res: dict) -> tuple[str, float]:
    coins = by_coin(res)
    if not coins:
        return "-", 0.0
    best = max(coins, key=coins.get)
    return best, sum(coins.values()) - coins[best]


def judge(res: dict, base: dict) -> dict:
    q, qb = quarter_totals(res), quarter_totals(base)
    w, wb = weekly(res), weekly(base)
    p_better, lo, hi = bootstrap([a - b for a, b in zip(w, wb)])
    p_positive, _, _ = bootstrap(w)
    best, rest = without_best_coin(res)
    better_q = sum(a > b for a, b in zip(q, qb))
    return {
        "variant": res["variant"], "trades": len(closed(res)), "total_r": sum(w), "quarters": q,
        "better_quarters": better_q, "p_better": p_better, "diff_lo": lo, "diff_hi": hi,
        "p_positive": p_positive, "best_coin": best, "without_best": rest,
        "robust": better_q >= MIN_QUARTERS and p_better >= MIN_PROB,
    }


def robust_report(results: list[dict], baseline: str) -> str:
    base = next((r for r in results if r["variant"] == baseline), None)
    if base is None:
        raise SystemExit(f"baseline {baseline!r} is not among the inputs")
    meta = base["meta"]
    rows = sorted((judge(r, base) for r in results), key=lambda j: -j["total_r"])
    step = (meta["end"] - meta["start"]) / QUARTERS
    qnames = [f"Q{i + 1} ({_date(meta['start'] + i * step)})" for i in range(QUARTERS)]
    lines = [
        "# Robustness check", "",
        f"Same stored history for every variant: {_date(meta['start'])} → {_date(meta['end'])}, "
        f"{meta.get('coins', '?')} coins, closed trades net of fees, slippage and funding. "
        f"Baseline: `{baseline}`.", "",
        f"**Rule:** a variant counts as better only if it beats the baseline in at least "
        f"{MIN_QUARTERS} of {QUARTERS} quarters **and** the weekly bootstrap gives it at least "
        f"{MIN_PROB:.0%} chance of a higher total.", "",
        "| Variant | Trades | Total R | " + " | ".join(qnames) + " | Quarters better | "
        "P(better) | Difference 90% range | P(total > 0) | Without best coin |",
        "|---|---|---|" + "---|" * QUARTERS + "---|---|---|---|---|",
    ]
    for j in rows:
        mark = " ✅" if j["robust"] and j["variant"] != baseline else ""
        qs = " | ".join(f"{x:+.1f}" for x in j["quarters"])
        same = j["variant"] == baseline
        lines.append(
            f"| `{j['variant']}`{mark} | {j['trades']} | {j['total_r']:+.1f} | {qs} | "
            + ("– | – | – | " if same else
               f"{j['better_quarters']}/{QUARTERS} | {j['p_better']:.0%} | "
               f"{j['diff_lo']:+.1f} … {j['diff_hi']:+.1f} | ")
            + f"{j['p_positive']:.0%} | {j['without_best']:+.1f} (no {j['best_coin']}) |")
    winners = [j["variant"] for j in rows if j["robust"] and j["variant"] != baseline]
    lines += ["", "**Passed the rule:** " + (", ".join(f"`{v}`" for v in winners) or "none") + "."]
    return "\n".join(lines) + "\n"
