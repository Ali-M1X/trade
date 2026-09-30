"""Config variants for the backtest, and a side-by-side comparison with a holdout.

Each variant overrides config keys (backtest.variants in config.yaml). Every variant
replays the same stored history; trades are then split by opening time into the tuning
period (everything before the last `holdout_days`) and the holdout period. Choices are
made on the tuning period only; the holdout is reported, not optimised.
"""
from __future__ import annotations

import copy
import json
from collections import Counter

from .report import CLOSED_TRADES, REGIME_NAMES, _date, build_report, stats
from .run import LIMITS, simulate

DAY = 86_400_000


def merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        out[k] = merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def variant_cfg(cfg: dict, name: str) -> dict:
    variants = cfg["backtest"]["variants"]
    if name not in variants:
        raise SystemExit(f"unknown variant {name!r}; known: {', '.join(variants)}")
    return merge(cfg, variants[name])


def run_variant(cfg: dict, repo, name: str, out_json: str) -> dict:
    res = simulate(variant_cfg(cfg, name), repo)
    res["variant"] = name
    with open(out_json, "w", encoding="utf-8") as f:
        json.dump(res, f)
    return res


# ---------------------------------------------------------------- comparison
def split(res: dict, holdout_days: int) -> tuple[list, list, int]:
    cut = res["meta"]["end"] - holdout_days * DAY
    trades = res["trades"]
    return [t for t in trades if t["created"] < cut], [t for t in trades if t["created"] >= cut], cut


def _cells(s: dict) -> str:
    if not s.get("trades"):
        return "0 | – | – | – | – | – | –"
    pf = "∞" if s["profit_factor"] == float("inf") else f"{s['profit_factor']:.2f}"
    return (f"{s['trades']} | {s['win_rate']:.0f}% | {s['avg_r']:+.2f} | {s['total_r']:+.1f} | {pf} | "
            f"{s['return_pct']:+.1f}% | {s['max_dd_pct']:.1f}%")


PERIOD_HEADER = ("| Variant | Trades | Win rate | Avg R | Total R | Profit factor | Return | Max DD |\n"
                 "|---|---|---|---|---|---|---|---|")


def compare(results: list[dict], cfg: dict) -> str:
    order = list(cfg["backtest"]["variants"])
    results = sorted(results, key=lambda r: order.index(r["variant"]) if r["variant"] in order else 99)
    hd = cfg["backtest"]["holdout_days"]
    parts = {r["variant"]: split(r, hd) for r in results}
    meta = results[0]["meta"]
    cut = next(iter(parts.values()))[2]
    lines = [
        "## Variant comparison",
        "",
        f"Same stored history for every variant: {_date(meta['start'])} → {_date(meta['end'])}, "
        f"{meta.get('coins', '?')} coins. **Tuning period:** {_date(meta['start'])} → {_date(cut)} "
        f"(used to choose). **Holdout:** {_date(cut)} → {_date(meta['end'])} (last {hd} days, "
        "reported only). A trade belongs to the period in which it was opened. All R figures are "
        "net of fees, slippage and funding.",
        "",
        "| Variant | Changes |",
        "|---|---|",
    ]
    for r in results:
        over = cfg["backtest"]["variants"].get(r["variant"], {})
        desc = "; ".join(f"{sec}.{k} = {v}" for sec, kv in over.items() for k, v in kv.items()) or "STRATEGY.md as built"
        lines.append(f"| {r['variant']} | {desc} |")
    tune_days = meta.get("days", cfg["backtest"]["days"]) - hd
    for title, idx in ((f"Tuning period (first {tune_days} days)", 0), (f"Holdout (last {hd} days)", 1)):
        lines += ["", f"### {title}", "", PERIOD_HEADER]
        for r in results:
            lines.append(f"| {r['variant']} | {_cells(stats(parts[r['variant']][idx]))} |")
    lines += ["", "### By grade", "",
              "| Variant | Grade | Tuning: trades | Tuning: avg R | Tuning: total R | Holdout: trades | Holdout: avg R | Holdout: total R |",
              "|---|---|---|---|---|---|---|---|"]
    for r in results:
        tune, hold, _ = parts[r["variant"]]
        for g in ("A", "B"):
            a, b = stats([t for t in tune if t["grade"] == g]), stats([t for t in hold if t["grade"] == g])
            lines.append(f"| {r['variant']} | {g} | {a['trades']} | {a.get('avg_r', 0):+.2f} | "
                         f"{a.get('total_r', 0):+.1f} | {b['trades']} | {b.get('avg_r', 0):+.2f} | "
                         f"{b.get('total_r', 0):+.1f} |")
    for title, idx in (("By regime, tuning period (total R, trades)", 0),
                       ("By regime, holdout (total R, trades)", 1)):
        lines += ["", f"### {title}", "",
                  "| Regime | " + " | ".join(r["variant"] for r in results) + " |",
                  "|---|" + "---|" * len(results)]
        for name in REGIME_NAMES:
            cells = []
            for r in results:
                s = stats([t for t in parts[r["variant"]][idx] if t["regime"] == name])
                cells.append(f"{s.get('total_r', 0):+.1f} ({s['trades']})")
            lines.append(f"| {name} | " + " | ".join(cells) + " |")
    lines += ["", "### Outcomes and blocks (whole year)", "",
              "| Variant | tp3 | breakeven | sl | expired | cancelled | L6 rejects: tight stop | blocked: cooldown | blocked: duplicate |",
              "|---|---|---|---|---|---|---|---|---|"]
    for r in results:
        o = Counter(t["status"] for t in r["trades"])
        st = r["stats"]
        lines.append(f"| {r['variant']} | {o['tp3']} | {o['breakeven']} | {o['sl']} | {o['expired']} | "
                     f"{o['cancelled']} | {st.get('rejected_sl_too_tight', 0)} | {st.get('blocked_cooldown', 0)} | "
                     f"{st.get('blocked_duplicate', 0)} |")
    lines += ["", verdict(results, parts, cfg)]
    return "\n".join(lines) + "\n"


def verdict(results, parts, cfg) -> str:
    """Pick on the tuning period only, then state how that choice did in the holdout."""
    scored = []
    for r in results:
        s = stats(parts[r["variant"]][0])
        if s.get("trades", 0) >= cfg["backtest"]["min_trades_to_choose"]:
            scored.append((s["total_r"], r["variant"], s))
    if not scored:
        return "**Choice:** no variant had enough trades in the tuning period to choose from."
    total, name, s = max(scored)
    h = stats(parts[name][1])
    base = stats(parts["baseline"][0]) if "baseline" in parts else {}
    hold = (f"In the holdout it made {h['total_r']:+.1f}R over {h['trades']} trades "
            f"({h['avg_r']:+.2f}R per trade, {h['return_pct']:+.1f}%)." if h.get("trades")
            else "It had no trades in the holdout.")
    return (f"**Choice on the tuning period:** `{name}` had the best total net R "
            f"({total:+.1f}R over {s['trades']} trades, {s['avg_r']:+.2f}R per trade"
            + (f"; baseline {base.get('total_r', 0):+.1f}R" if base else "") + "). " + hold)


def comparison_report(results: list[dict], cfg: dict, detail: str = "combined") -> str:
    """The comparison, followed by the full report of one variant (default: combined)."""
    out = ["# Backtest", "", compare(results, cfg)]
    pick = next((r for r in results if r["variant"] == detail), results[0])
    vcfg = variant_cfg(cfg, pick["variant"])
    body = build_report(pick["trades"], pick["stats"], pick["regime_hours"], pick["meta"], vcfg)
    out += ["", f"## Details: `{pick['variant']}` (whole year)", "",
            body.replace("# Backtest\n\n", "", 1).replace("\n## ", "\n### ")]
    return "\n".join(out) + LIMITS
