"""Entry timing for the live rules (analysis only): limit at the level vs entering earlier.

The live settings (`live.variant: atr_2r`: 1.5 ATR(4H) stop, fixed 2R / 3R targets) place a limit order at
the support level unless price is already within ±0.2 ATR of it. Many setups never fill because price
runs away first (lifecycle: `cancelled`, `tp1_before_entry`, or `expired`). This script measures what
those missed setups would have done, and compares entry rules on the same setups:

- L   limit at the level, as live (market only when price is within 0.2 ATR)
- M   market at signal time (stop = price ∓ 1.5 ATR, TP1 = 2R, TP2 = 3R from that entry)
- Z   limit at the near edge of the entry zone (market if price is already inside the zone)
- H05 market when price is within 0.5 ATR of the level, else L
- H10 market when price is within 1.0 ATR of the level, else L
- S   split: half size M, half size L (R is the average of the two halves; an unfilled half counts 0)

Samples are the same as scripts/entry_stop_analysis.py (every hourly L6 evaluation with a plan, BTC/ETH
forced both sides, one per coin/side per 24h), planned under the live variant. Simulation uses the project
lifecycle and cost model. No book caps, cooldowns or correlation limits.

    python scripts/entry_timing_analysis.py --db data/backtest.db --out-dir entry_timing
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import entry_stop_analysis as es  # noqa: E402
import weight_analysis as wa  # noqa: E402

from agent.backtest.variants import merge  # noqa: E402
from agent.config import load_config  # noqa: E402
from agent.store.repository import Repository  # noqa: E402

log = logging.getLogger("entry_timing")
DAY = wa.DAY
GROUPS = ["ALL", "ALTS", "BTC", "ETH"]
VIEWS = {"all": "all setups with a plan",
         "ab": "setups passing all four gates with score ≥ 65 (grade A or B)",
         "a": "setups passing all four gates with score ≥ 75 (grade A)"}
SINGLE = ["L", "M", "Z", "H05", "H10"]
NAMES = {"L": "L limit at level (live)", "M": "M market now", "Z": "Z limit at zone edge",
         "H05": "H05 market if ≤ 0.5 ATR", "H10": "H10 market if ≤ 1.0 ATR", "S": "S half M + half L"}
MIN_N = 30


def plan_row(s: dict, entry: float, order: str, cfg: dict) -> dict:
    """The live atr_2r plan around a chosen entry: stop 1.5 ATR(4H) behind it, TP1 2R, TP2 3R."""
    side, atr, t = s["side"], s["atr"], cfg["trade"]
    risk = t["atr_stop_mult"] * atr
    sl = entry - side * risk
    return {"t": s["t"], "base": s["base"], "side": side, "entry": entry, "sl": sl, "order": order,
            "tp1": entry + side * t["tp1_fixed_r"] * risk, "tp2": entry + side * t["tp2_r"] * risk}


def variant_rows(s: dict, cfg: dict) -> dict:
    side, atr, price, bp = s["side"], s["atr"], s["price"], s["plan"]
    level_entry = bp["entry"]
    out = {"L": {"t": s["t"], "base": s["base"], "side": side, "entry": level_entry, "sl": bp["sl"],
                 "tp1": bp["tp1"], "tp2": bp["tp2"], "order": bp["order"]}}
    out["M"] = plan_row(s, price, "market", cfg)
    if bp["order"] == "market":                       # price already at the level: nothing to change
        for k in ("Z", "H05", "H10"):
            out[k] = out["L"]
    else:
        edge = bp["entry_high"] if side == 1 else bp["entry_low"]
        inside = (price - edge) * side <= 0           # price already at or past the zone edge
        out["Z"] = out["M"] if inside else plan_row(s, edge, "limit", cfg)
        dist = (price - level_entry) * side / atr
        out["H05"] = out["M"] if dist <= 0.5 else out["L"]
        out["H10"] = out["M"] if dist <= 1.0 else out["L"]
    return out


def maxdd(r: pd.Series) -> float:
    eq = np.r_[0.0, r.cumsum().to_numpy()]
    return float((np.maximum.accumulate(eq) - eq).max())


def summary(d: pd.DataFrame) -> dict:
    """d: one row per planned setup with filled / resolved / net_r. Unresolved fills are dropped."""
    if not len(d):
        return {"planned": 0}
    f = d[d["filled"] & d["resolved"]].sort_values("t")
    out = {"planned": len(d), "fill": d["filled"].mean(), "n": len(f)}
    if len(f):
        out.update(win=(f["net_r"] > 0).mean(), mean=f["net_r"].mean(), total=f["net_r"].sum(),
                   dd=maxdd(f["net_r"]))
    return out


def cell(x: dict) -> str:
    if not x.get("n"):
        return f"{x.get('planned', 0)} | – | 0 | – | – | – | –"
    w = " ⚠" if x["n"] < MIN_N else ""
    return (f"{x['planned']} | {100 * x['fill']:.0f}% | {x['n']}{w} | {100 * x['win']:.0f}% | "
            f"{x['mean']:+.3f} | {x['total']:+.1f} | {x['dd']:.1f}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=None)
    ap.add_argument("--days", type=int, default=None)
    ap.add_argument("--holdout-days", type=int, default=None)
    ap.add_argument("--out-dir", default="entry_timing")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    cfg = load_config()
    if args.holdout_days:
        cfg["backtest"]["holdout_days"] = args.holdout_days
    live_name = (cfg.get("live") or {}).get("variant") or "atr_2r"
    cfg = merge(cfg, cfg["backtest"]["variants"][live_name])      # plans exactly as the live runs make them
    repo = Repository(args.db or cfg["backtest"]["db_path"])
    meta = repo.get_state("bt_meta")
    days = args.days or cfg["backtest"]["days"]
    end = meta["end"]
    start = end - days * DAY
    cut = end - cfg["backtest"]["holdout_days"] * DAY
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)

    t0 = time.time()
    bt = es.Collector(cfg, repo, start, end)
    bt.run(progress_every=24 * 30)
    samples = bt.samples
    log.info("raw %d, kept %d in %.0fs", bt.raw, len(samples), time.time() - t0)

    t1 = time.time()
    recs = []
    for k, s in enumerate(samples):
        rows = variant_rows(s, cfg)
        cache: dict = {}
        res = {}
        for name in SINGLE:
            key = tuple(sorted((x, round(float(y), 12) if not isinstance(y, str) else y)
                               for x, y in rows[name].items()))
            if key not in cache:
                cache[key] = wa.simulate_one(bt, rows[name], cfg, end)
            res[name] = cache[key]
        sel = s["gates_ok"] and s["score"] >= 65
        view_a = s["gates_ok"] and s["score"] >= 75
        # split: half market, half the limit; an unfilled half contributes 0, unresolved fills drop it
        m, l = res["M"], res["L"]
        s_ok = m["filled"] and m["resolved"] and (not l["filled"] or l["resolved"])
        s_r = 0.5 * m["net_r"] + 0.5 * (l["net_r"] if l["filled"] else 0.0)
        for name in SINGLE:
            r = res[name]
            recs.append({"k": k, "var": name, "t": s["t"], "base": s["base"], "group": s["group"],
                         "side": s["side"], "ab": sel, "a": view_a, "order": rows[name]["order"],
                         "dist_atr": (s["price"] - s["plan"]["entry"]) * s["side"] / s["atr"],
                         "status": r["status"], "filled": r["filled"], "resolved": r["resolved"],
                         "net_r": r["net_r"]})
        recs.append({"k": k, "var": "S", "t": s["t"], "base": s["base"], "group": s["group"],
                     "side": s["side"], "ab": sel, "a": view_a, "order": "split",
                     "dist_atr": (s["price"] - s["plan"]["entry"]) * s["side"] / s["atr"],
                     "status": "split", "filled": bool(m["filled"]), "resolved": bool(s_ok), "net_r": s_r})
    df = pd.DataFrame(recs)
    log.info("simulated %d samples in %.0fs", len(samples), time.time() - t1)

    def sub(view, g, var, period):
        d = df[df["var"] == var]
        if view != "all":
            d = d[d[view]]
        if g != "ALL":
            d = d[d["group"] == g]
        return d[d["t"] < cut] if period == "tune" else d[d["t"] >= cut]

    L = ["# Entry timing for the live rules (limit at the level vs entering earlier)", "",
         f"Live variant `{live_name}`. History {wa._date(start)} → {wa._date(end)} ({days} days, {len(bt.rows)} coins). "
         f"**Tuning** {wa._date(start)} → {wa._date(cut)}, **holdout** {wa._date(cut)} → {wa._date(end)}. "
         f"{bt.raw} hourly L6 evaluations with a plan → {len(samples)} setups after one-per-coin/side-per-24h dedup. "
         "Every variant is simulated on the same setups with the project lifecycle (1H candles, 4H-close rules, "
         "6×4H expiry) and cost model, net R. No book caps, cooldowns or correlation limits.", "",
         "- **L** limit at the level, as live (market only inside ±0.2 ATR).",
         "- **M** market at signal time: stop = price ∓ 1.5 ATR(4H), TP1 2R, TP2 3R from that entry.",
         "- **Z** limit at the near edge of the entry zone (market when price is already inside it).",
         "- **H05 / H10** market when price is within 0.5 / 1.0 ATR(4H) of the level, otherwise L.",
         "- **S** split: half size M, half size L. R is the average of the halves; an unfilled half counts 0.", ""]

    # ---- the missed setups
    L += ["## 1. What the limit orders that never filled would have done", "",
          "Setups where L did not fill (price ran away → `cancelled` before the level was touched, or `expired`), "
          "and M (market now) on exactly those setups. Filled setups for comparison.", "",
          "| View | Group | Period | Setups | L filled | L never filled | of which cancelled (TP1 first) | "
          "M on the never-filled: n | win | mean R | total R | M on the filled: n | mean R | L on the filled: mean R |",
          "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for view in ("all", "ab", "a"):
        for g in GROUPS:
            for period in ("tune", "hold"):
                lrows = sub(view, g, "L", period)
                mrows = sub(view, g, "M", period).set_index("k")
                lrows = lrows.set_index("k")
                if not len(lrows):
                    continue
                nf = lrows[~lrows["filled"]]
                fl = lrows[lrows["filled"] & lrows["resolved"]]
                mm = mrows.loc[mrows.index.intersection(nf.index)]
                mm = mm[mm["filled"] & mm["resolved"]]
                mf = mrows.loc[mrows.index.intersection(fl.index)]
                mf = mf[mf["filled"] & mf["resolved"]]
                canc = int((nf["status"] == "cancelled").sum())
                L.append(f"| {view} | {g} | {period} | {len(lrows)} | {int(lrows['filled'].sum())} | {len(nf)} | {canc} | "
                         f"{len(mm)} | {(100 * (mm['net_r'] > 0).mean() if len(mm) else float('nan')):.0f}% | "
                         f"{(mm['net_r'].mean() if len(mm) else float('nan')):+.3f} | {mm['net_r'].sum():+.1f} | "
                         f"{len(mf)} | {(mf['net_r'].mean() if len(mf) else float('nan')):+.3f} | "
                         f"{(fl.loc[fl.index.intersection(mf.index), 'net_r'].mean() if len(mf) else float('nan')):+.3f} |")
    L.append("")

    # ---- the comparison
    head = ("| Rule | Planned | Fill | n | win | mean R | total R | maxDD R | Planned | Fill | n | win | mean R | total R | maxDD R |")
    sep = "|---|" + "---|" * 14
    L += ["## 2. Entry rules compared", "",
          "Cells (tuning, then holdout): setups planned · fill rate · trades filled and resolved · win rate · "
          "mean net R · total net R · max drawdown (R) of the equal-weight sequence. ⚠ = fewer than 30 trades.", ""]
    for view, label in VIEWS.items():
        for g in GROUPS:
            L += [f"### {g}: {label}", "", head, sep]
            for var in SINGLE + ["S"]:
                tu = summary(sub(view, g, var, "tune"))
                ho = summary(sub(view, g, var, "hold"))
                L.append(f"| {NAMES[var]} | {cell(tu)} | {cell(ho)} |")
            L.append("")

    # ---- by distance
    L += ["## 3. By how far price already was from the level at signal time", "",
          "Distance = (price − level entry) in the trade direction, in ATR(4H). Setups whose L plan is already a "
          "market order are in the first row. Mean net R of L and M on the same setups (trades filled and resolved; "
          "L fill rate shown). Whole year.", "",
          "| Distance | Setups | L fill | L n | L mean R | M n | M mean R | M win |", "|---|---|---|---|---|---|---|---|"]
    lall = df[df["var"] == "L"].set_index("k")
    mall = df[df["var"] == "M"].set_index("k")
    bins = [(-99, 0.2, "≤ 0.2 ATR"), (0.2, 0.5, "0.2 – 0.5"), (0.5, 1.0, "0.5 – 1.0"), (1.0, 2.0, "1.0 – 2.0"),
            (2.0, 99, "> 2.0")]
    for lo, hi, name in bins:
        ks = lall.index[(lall["dist_atr"] > lo) & (lall["dist_atr"] <= hi)]
        if not len(ks):
            continue
        lf = lall.loc[ks]
        lr = lf[lf["filled"] & lf["resolved"]]
        mf = mall.loc[ks]
        mr = mf[mf["filled"] & mf["resolved"]]
        L.append(f"| {name} | {len(ks)} | {100 * lf['filled'].mean():.0f}% | {len(lr)} | "
                 f"{(lr['net_r'].mean() if len(lr) else float('nan')):+.3f} | {len(mr)} | "
                 f"{(mr['net_r'].mean() if len(mr) else float('nan')):+.3f} | "
                 f"{(100 * (mr['net_r'] > 0).mean() if len(mr) else float('nan')):.0f}% |")
    L.append("")

    L += ["## Notes", "",
          "- Market entries pay taker fee and slippage; limit fills pay them too (conservative for limits), "
          "like the engine.",
          "- Setups are deduplicated to one per coin and side per 24h, so counts are lower than in the full "
          "engine backtest and the sample is not the signal book.",
          "- A rule that adds trades also adds correlated trades (same coins, same days); in a real book the "
          "5-signal cap and the loss brake would cut some of them."]
    report = "\n".join(L) + "\n"
    (out / "ENTRY_TIMING.md").write_text(report, encoding="utf-8")
    df.to_csv(out / "entry_timing_samples.csv.gz", index=False, compression="gzip")
    print(report)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as f:
            f.write(report)
    repo.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
