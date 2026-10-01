"""How much does each L6 score section predict trade outcomes? (analysis only)

Replays the backtest year hour by hour with the engine's own funnel and data access, records
every L6 evaluation that produced a trade plan (whatever its grade or gates), plus BTC and ETH
force-evaluated every hour on both sides. Each sample (one per coin/side per 24h) is then
simulated on its own with the project's lifecycle functions and cost model, exactly as the
engine manages trades, but without signal-book caps, cooldowns or correlation limits.

The report measures, per group (BTC, ETH, ALTS, ALL), how each section and gate relates to
net R, and compares weight sets on the tuning period and the holdout.

Nothing in the project is changed: the engine is subclassed here.

    python scripts/weight_analysis.py --db data/backtest.db --out-dir weight_analysis
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from agent.backtest.costs import trade_costs
from agent.backtest.engine import Backtest
from agent.config import load_config
from agent.layers.majors import Majors
from agent.layers.regime import Regime
from agent.layers.technical import evaluate
from agent.layers.trade import flip_level
from agent.signals.lifecycle import CLOSED, OPEN, new_signal, on_4h_close, on_candle, on_time
from agent.store.repository import Repository

log = logging.getLogger("weight_analysis")
HOUR = 3_600_000
H4 = 4 * HOUR
DAY = 24 * HOUR

SECTIONS = ["phase", "dow", "levels", "volume", "candles", "cycles", "patterns", "confirmation"]
GATES = ["regime", "phase", "rr", "confirmation"]
MAXP = {"phase": 15, "dow": 15, "levels": 15, "volume": 10, "candles": 10, "cycles": 10,
        "patterns": 15, "confirmation": 10}
WEIGHTS = {
    "CURRENT": {"phase": 15, "dow": 15, "levels": 15, "volume": 10, "candles": 10, "cycles": 10,
                "patterns": 15, "confirmation": 10},
    "RESEARCH": {"phase": 15, "dow": 20, "levels": 15, "volume": 15, "candles": 5, "cycles": 5,
                 "patterns": 10, "confirmation": 15},
}
GROUPS = ["BTC", "ETH", "ALTS", "ALL"]


def _date(ms) -> str:
    return datetime.fromtimestamp(int(ms) / 1000, tz=timezone.utc).strftime("%Y-%m-%d")


def group_of(base: str) -> str:
    return base if base in ("BTC", "ETH") else "ALTS"


# ============================================================ 1. sample collection
class Collector(Backtest):
    """The engine's hourly loop, with L6 recording every evaluation that has a plan and no
    signal book (the book is never filled, so manage() is a no-op)."""

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.samples: list[dict] = []

    def _record(self, ev, t: int, source: str) -> None:
        p = ev.plan
        row = {"t": t, "date": _date(t), "base": ev.base, "group": group_of(ev.base),
               "side": ev.side, "source": source, "score": ev.score, "grade": ev.grade or "",
               "rejected": ev.rejected or "", "flags": ",".join(ev.flags),
               "regime_name": self.state["regime"]["name"],
               "order": p.order, "entry": p.entry, "sl": p.sl, "tp1": p.tp1, "tp2": p.tp2,
               "tp1_r": p.tp1_r, "tp2_r": p.tp2_r}
        for s in SECTIONS:
            row[f"s_{s}"] = float(ev.sections.get(s, 0.0))
        for g in GATES:
            row[f"g_{g}"] = bool(ev.gates.get(g, False))
        row["funding_crowded"] = "funding_crowded" in ev.flags
        self.samples.append(row)

    def l6(self, t: int) -> None:
        regime, majors = Regime(**self.state["regime"]), Majors(**self.state["majors"])
        seen = set()
        for item in self.state["shortlist"]:
            base, side = item["base"], item["side"]
            frames = self._frames(base, ["1w", "1d", "4h", "1h"], t)
            if len(frames) < 4:
                continue
            extra = [flip_level(item["flip_level"], self.cfg)] if item.get("flip_level") else []
            funding = self.h.funding_until(base, t, 1)
            ev = evaluate(base, side, frames, regime, majors, self.cfg,
                          funding=funding[0] if funding else None,
                          btc_pair_up=item["pairs"]["dirs"].get("BTC", {}).get("1d") == 1,
                          extra_levels=extra, labels=item["labels"])
            seen.add((base, side))
            self.stats["evaluations"] += 1
            if ev.plan is not None:
                self._record(ev, t, "shortlist")
        # majors: force both sides every hour with this hour's regime/majors
        eth_btc = self.h.frame("ETH", "1d", t, "BTC")
        for base in ("BTC", "ETH"):
            frames = self._frames(base, ["1w", "1d", "4h", "1h"], t)
            if len(frames) < 4:
                continue
            funding = self.h.funding_until(base, t, 1)
            for side in (1, -1):
                if (base, side) in seen:
                    continue
                ev = evaluate(base, side, frames, regime, majors, self.cfg,
                              funding=funding[0] if funding else None,
                              btc_pair_up=base == "ETH" and eth_btc is not None and eth_btc.direction == 1)
                self.stats["forced_evaluations"] += 1
                if ev.plan is not None:
                    self._record(ev, t, "forced")


# ============================================================ 2. dedup
def dedup(df: pd.DataFrame) -> pd.DataFrame:
    """At most one sample per (coin, side) per 24h: keep the first, then the next one at
    least 24h after the last kept one."""
    keep = []
    last: dict = {}
    for i, r in df.sort_values(["t", "base", "side"]).iterrows():
        k = (r["base"], r["side"])
        if k not in last or r["t"] - last[k] >= DAY:
            keep.append(i)
            last[k] = r["t"]
    return df.loc[keep].sort_values("t").reset_index(drop=True)


# ============================================================ 3. outcomes
def simulate_one(bt: Backtest, row: dict, cfg: dict, end: int) -> dict:
    """Manage one plan created at row['t'] exactly as Backtest.manage does, hour by hour."""
    ma = f"ma{cfg['indicators']['ma_mid']}"
    base = row["base"]
    plan = {"side": int(row["side"]), "entry": row["entry"], "sl": row["sl"], "tp1": row["tp1"],
            "tp2": row["tp2"], "order": row["order"]}
    lc = new_signal(plan, int(row["t"]), cfg)
    h1 = bt.h.series(base, "1h")
    h1_ts = bt.h._ts[(base, None, "1h")]
    h1_hi, h1_lo = h1["high"].to_numpy(float), h1["low"].to_numpy(float)
    h4 = bt.h.series(base, "4h")
    h4_ts = bt.h._ts[(base, None, "4h")]
    h4_close, h4_ma = h4["close"].to_numpy(float), h4[ma].to_numpy(float)
    events = []
    t = int(row["t"]) + HOUR
    i = int(np.searchsorted(h1_ts, lc["last_candle_ts"], side="right"))
    while t <= end and lc["status"] in OPEN:
        # 1H candles closed by t (open ts <= t - 1h)
        while i < len(h1_ts) and h1_ts[i] <= t - HOUR:
            if h1_ts[i] > lc["last_candle_ts"]:
                events += on_candle(lc, int(h1_ts[i]), float(h1_hi[i]), float(h1_lo[i]), cfg)
            i += 1
        if t % H4 == 0 and lc["status"] in OPEN:
            j = int(np.searchsorted(h4_ts, t - H4, side="right")) - 1   # last closed 4H bar
            if j >= 1 and int(h4_ts[j]) == t - H4:
                choch = False
                if lc["status"] == "tp2":          # the only state that reads the CHoCH
                    f = bt.h.frame(base, "4h", t)
                    choch = any(e.idx == f.n - 1 and e.kind == "CHoCH" and e.direction == -lc["side"]
                                for e in f.events)
                events += on_4h_close(lc, t - H4, float(h4_close[j]), float(h4_ma[j]), choch)
        events += on_time(lc, t)
        t += HOUR
    trade = {"base": base, "side": lc["side"], "entry": lc["entry"], "sl": plan["sl"],
             "gross_r": lc["realized_r"], "filled_at": lc["filled_at"], "closed_at": lc["closed_at"],
             "events": [(e.kind, e.ts, e.price) for e in events if e.kind != "created"]}
    c = trade_costs(trade, cfg, bt.h.funding_rate_at)
    return {"status": lc["status"], "filled": lc["filled_at"] is not None,
            "resolved": lc["status"] in CLOSED, "gross_r": c["gross_r"], "net_r": c["net_r"],
            "fee_r": c["fee_r"], "slippage_r": c["slippage_r"], "funding_r": c["funding_r"],
            "closed_at": lc["closed_at"]}


# ============================================================ stats helpers
def spearman(x, y) -> float:
    x, y = pd.Series(x).rank().to_numpy(float), pd.Series(y).rank().to_numpy(float)
    if len(x) < 3 or x.std() == 0 or y.std() == 0:
        return float("nan")
    return float(np.corrcoef(x, y)[0, 1])


def nnls(A: np.ndarray, b: np.ndarray) -> np.ndarray:
    try:
        from scipy.optimize import nnls as _nnls
        return _nnls(A, b)[0]
    except ImportError:
        pass
    # projected gradient fallback
    x = np.zeros(A.shape[1])
    L = np.linalg.norm(A, 2) ** 2 or 1.0
    for _ in range(20000):
        g = A.T @ (A @ x - b)
        nx = np.maximum(0.0, x - g / L)
        if np.max(np.abs(nx - x)) < 1e-10:
            x = nx
            break
        x = nx
    return x


def fmt(x, nd=2, sign=True) -> str:
    if x is None or (isinstance(x, float) and np.isnan(x)):
        return "–"
    return f"{x:+.{nd}f}" if sign else f"{x:.{nd}f}"


def basic(d: pd.DataFrame) -> str:
    if not len(d):
        return "0 | – | –"
    return f"{len(d)} | {100 * (d['net_r'] > 0).mean():.0f}% | {d['net_r'].mean():+.3f}"


def norm(df: pd.DataFrame) -> pd.DataFrame:
    return pd.DataFrame({s: df[f"s_{s}"] / MAXP[s] for s in SECTIONS})


def rescore(df: pd.DataFrame, w: dict, cfg: dict) -> pd.Series:
    n = norm(df)
    score = sum(n[s] * w[s] for s in SECTIONS)
    pen = cfg["trade"]["funding_penalty"]
    score = score + df["funding_crowded"].astype(float) * pen
    return score.clip(lower=0)


def fit_weights(df: pd.DataFrame) -> tuple[dict | None, dict | None, dict]:
    """NNLS of net R on the normalised sections (free intercept by centring), and the
    Spearman alternative. Returns (nnls_weights, spearman_weights, raw info)."""
    X = norm(df).to_numpy(float)
    y = df["net_r"].to_numpy(float)
    Xc, yc = X - X.mean(0), y - y.mean()
    beta = nnls(Xc, yc)
    w_nnls = None
    if beta.sum() > 0:
        w_nnls = {s: 100 * b / beta.sum() for s, b in zip(SECTIONS, beta)}
    rho = {s: spearman(df[f"s_{s}"], df["net_r"]) for s in SECTIONS}
    pos = {s: max(0.0, v) if not np.isnan(v) else 0.0 for s, v in rho.items()}
    w_sp = {s: 100 * v / sum(pos.values()) for s, v in pos.items()} if sum(pos.values()) > 0 else None
    return w_nnls, w_sp, {"beta": dict(zip(SECTIONS, beta)), "rho": rho, "n": len(df)}


# ============================================================ report
def section_table(d: pd.DataFrame) -> list[str]:
    out = ["| Section (max) | Spearman ρ vs net R | Low tercile: range · n · win · mean R | "
           "Mid tercile | High tercile |", "|---|---|---|---|---|"]
    for s in SECTIONS:
        col = d[f"s_{s}"]
        cells = []
        try:
            bins = pd.qcut(col, 3, duplicates="drop")
        except ValueError:
            bins = pd.Series(["all"] * len(col), index=col.index)
        cats = list(bins.cat.categories) if hasattr(bins, "cat") else ["all"]
        for c in cats:
            sub = d[bins == c]
            lo, hi = sub[f"s_{s}"].min(), sub[f"s_{s}"].max()
            rng = f"{lo:g}" if lo == hi else f"{lo:g}–{hi:g}"
            cells.append(f"{rng} · {len(sub)} · {100 * (sub['net_r'] > 0).mean():.0f}% · "
                         f"{sub['net_r'].mean():+.3f}")
        while len(cells) < 3:
            cells.insert(1 if len(cells) == 2 else len(cells), "(tied)")
        out.append(f"| {s} ({MAXP[s]}) | {fmt(spearman(col, d['net_r']))} | " + " | ".join(cells) + " |")
    return out


def gate_table(d: pd.DataFrame) -> list[str]:
    out = ["| Gate | Passed: n · win · mean R | Failed: n · win · mean R |", "|---|---|---|"]
    for g in GATES:
        p, f = d[d[f"g_{g}"]], d[~d[f"g_{g}"]]
        out.append(f"| {g} | {basic(p).replace(' | ', ' · ')} | {basic(f).replace(' | ', ' · ')} |")
    allp = d[d[[f"g_{g}" for g in GATES]].all(axis=1)]
    rest = d[~d[[f"g_{g}" for g in GATES]].all(axis=1)]
    out.append(f"| **all four** | {basic(allp).replace(' | ', ' · ')} | {basic(rest).replace(' | ', ' · ')} |")
    return out


def thr_cells(d: pd.DataFrame, score: pd.Series, thr: float) -> str:
    gates = d[[f"g_{g}" for g in GATES]].all(axis=1)
    sub = d[gates & (score >= thr)]
    if not len(sub):
        return "0 | – | – | –"
    return (f"{len(sub)} | {100 * (sub['net_r'] > 0).mean():.0f}% | {sub['net_r'].mean():+.3f} | "
            f"{sub['net_r'].sum():+.1f}")


def build_report(samples: pd.DataFrame, raw_n: int, raw_counts: dict, meta: dict, cut: int,
                 cfg: dict, elapsed: dict) -> str:
    L = []
    L += ["# L6 score-section weight analysis", "",
          f"History: {_date(meta['start'])} → {_date(meta['end'])} ({meta['days']} days, "
          f"{meta['coins']} coins). **Tuning:** {_date(meta['start'])} → {_date(cut)}; "
          f"**holdout:** {_date(cut)} → {_date(meta['end'])} (last {cfg['backtest']['holdout_days']} days). "
          "A sample belongs to the period in which it was created.", "",
          "Samples: every hourly L6 evaluation of a shortlisted coin that produced a trade plan "
          "(any grade, any gates), plus BTC and ETH force-evaluated every hour on both sides. "
          "Each sample is simulated on its own with the project's lifecycle (1H candles, 4H-close "
          "handling, expiry) and cost model (fees, slippage, funding) → net R. No signal-book caps, "
          "cooldowns or correlation limits. Statistics use **filled and resolved** samples; "
          "unfilled samples (expired/cancelled) are 0 R and are counted separately.", "",
          f"Runtime: collection {elapsed['collect']:.0f}s, simulation {elapsed['simulate']:.0f}s.", ""]

    L += ["## Sample counts", "",
          "| Group | Raw samples (hourly) | After 24h dedup | Filled | Filled & resolved | Unfilled | Still open at end |",
          "|---|---|---|---|---|---|---|"]
    for g in GROUPS:
        d = samples if g == "ALL" else samples[samples["group"] == g]
        L.append(f"| {g} | {raw_counts.get(g, raw_n) if g != 'ALL' else raw_n} | {len(d)} | "
                 f"{int(d['filled'].sum())} | {int((d['filled'] & d['resolved']).sum())} | "
                 f"{int((~d['filled']).sum())} | {int((~d['resolved']).sum())} |")
    src = samples.groupby("source").size().to_dict()
    L += ["", f"After dedup, by source: {src}. Engine grade distribution after dedup: "
          f"{samples['grade'].replace('', 'none').value_counts().to_dict()}.", ""]

    used = samples[samples["filled"] & samples["resolved"]].copy()

    L += ["## Overall outcome by group (filled & resolved)", "",
          "| Group | n | Win rate | Mean net R | Total net R | Engine A/B only: n · win · mean R |",
          "|---|---|---|---|---|---|"]
    for g in GROUPS:
        d = used if g == "ALL" else used[used["group"] == g]
        ab = d[d["grade"].isin(["A", "B"])]
        tot = f"{d['net_r'].sum():+.1f}" if len(d) else "–"
        L.append(f"| {g} | {basic(d)} | {tot} | {basic(ab).replace(' | ', ' · ')} |")
    L.append("")

    for g in GROUPS:
        d = used if g == "ALL" else used[used["group"] == g]
        L += [f"## {g}: sections and gates", "", f"n = {len(d)} filled & resolved samples, "
              f"win rate {100 * (d['net_r'] > 0).mean():.0f}%, mean net R {d['net_r'].mean():+.3f}."
              if len(d) else "no samples", ""]
        if len(d) < 5:
            continue
        L += ["Terciles split the section's score at its 33rd/67th percentiles; with few distinct "
              "values, ties collapse terciles (shown as the actual score range).", ""]
        L += section_table(d) + [""] + gate_table(d) + [""]

    # ------------------------------------------------------------------ weights
    tune, hold = used[used["t"] < cut], used[used["t"] >= cut]
    weights = dict(WEIGHTS)
    fit_info = []
    alts_t = tune[tune["group"] == "ALTS"]
    btc_t = tune[tune["group"] == "BTC"]
    maj_t = btc_t if len(btc_t) >= 100 else tune[tune["group"].isin(["BTC", "ETH"])]
    maj_label = "BTC only" if len(btc_t) >= 100 else "BTC+ETH pooled (BTC < 100 tuning samples)"
    for name, d, label in (("ALTS", alts_t, "ALTS"), ("MAJORS", maj_t, maj_label)):
        if len(d) < 20:
            fit_info.append(f"- FITTED_{name}: not enough tuning samples ({len(d)}).")
            continue
        w_n, w_s, info = fit_weights(d)
        if w_n:
            weights[f"FITTED_{name}"] = w_n
        if w_s:
            weights[f"FITTED_{name}_SPEARMAN"] = w_s
        fit_info.append(f"- FITTED_{name}: fitted on {label}, {info['n']} tuning samples. NNLS "
                        "coefficients (net R per unit of normalised section, intercept free): "
                        + ", ".join(f"{s} {info['beta'][s]:+.3f}" for s in SECTIONS)
                        + (". All zero → no NNLS weight set." if not w_n else "."))
    L += ["## Weight sets", "", "Each section is normalised to 0..1 by its max points, multiplied by the "
          "weight, summed; the funding-crowded penalty is kept. Fitted sets use the **tuning period only**.", ""]
    L += fit_info + [""]
    L += ["| Set | " + " | ".join(SECTIONS) + " |", "|---|" + "---|" * len(SECTIONS)]
    for n, w in weights.items():
        L.append(f"| {n} | " + " | ".join(f"{w[s]:.1f}" for s in SECTIONS) + " |")
    L.append("")

    L += ["## Weight-set results", "",
          "Samples passing all four gates (regime, phase, rr, confirmation) with rescored score ≥ 75 "
          "(A) or ≥ 65 (A+B). Thresholds unchanged. Engine downgrade flags (chase, not_confirmed beyond "
          "the gate, btc_weak, neutral regime, lower cycle correcting) are **not** applied here, so the "
          "CURRENT row is wider than the engine's actual A/B signals; the 'ENGINE A/B' row shows those "
          "for reference. Columns: n | win | mean R | total R.", ""]
    for g in GROUPS:
        L += [f"### {g}", "",
              "| Set | Tuning ≥75 n | win | mean R | total R | Tuning ≥65 n | win | mean R | total R "
              "| Holdout ≥75 n | win | mean R | total R | Holdout ≥65 n | win | mean R | total R |",
              "|---|" + "---|" * 16]
        parts = {}
        for per, dd in (("tune", tune), ("hold", hold)):
            parts[per] = dd if g == "ALL" else dd[dd["group"] == g]
        rows = list(weights.items())
        if g == "ALL" and "FITTED_ALTS" in weights and "FITTED_MAJORS" in weights:
            rows.append(("FITTED_PER_GROUP", None))
        for n, w in rows:
            cells = []
            for per in ("tune", "hold"):
                d = parts[per]
                if w is None:
                    sc = pd.Series(np.where(d["group"] == "ALTS",
                                            rescore(d, weights["FITTED_ALTS"], cfg),
                                            rescore(d, weights["FITTED_MAJORS"], cfg)), index=d.index)
                else:
                    sc = rescore(d, w, cfg)
                cells += [thr_cells(d, sc, 75), thr_cells(d, sc, 65)]
            L.append(f"| {n} | " + " | ".join(cells) + " |")
        # engine's actual A/B for reference
        cells = []
        for per in ("tune", "hold"):
            d = parts[per]
            for grades in (["A"], ["A", "B"]):
                sub = d[d["grade"].isin(grades)]
                cells.append("0 | – | – | –" if not len(sub) else
                             f"{len(sub)} | {100 * (sub['net_r'] > 0).mean():.0f}% | "
                             f"{sub['net_r'].mean():+.3f} | {sub['net_r'].sum():+.1f}")
        L.append("| ENGINE A/B (ref) | " + " | ".join(cells) + " |")
        L.append("")
    return "\n".join(L) + "\n"


# ============================================================ main
def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=None)
    ap.add_argument("--config", default=None)
    ap.add_argument("--days", type=int, default=None)
    ap.add_argument("--holdout-days", type=int, default=None)
    ap.add_argument("--out-dir", default="weight_analysis")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    cfg = load_config(args.config)
    if args.holdout_days:
        cfg["backtest"]["holdout_days"] = args.holdout_days
    for s, m in MAXP.items():
        cm = cfg["technical"][s].get("max_points")
        if cm is not None and cm != m:
            log.warning("config max_points for %s is %s, analysis uses %s", s, cm, m)
    repo = Repository(args.db or cfg["backtest"]["db_path"])
    meta = repo.get_state("bt_meta")
    if not meta:
        raise SystemExit("no backtest data in the database")
    days = args.days or cfg["backtest"]["days"]
    end = meta["end"]
    start = end - days * DAY
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)

    t0 = time.time()
    bt = Collector(cfg, repo, start, end)
    bt.run(progress_every=24 * 15)
    t_collect = time.time() - t0
    raw = pd.DataFrame(bt.samples)
    log.info("collected %d raw samples in %.0fs; stats %s", len(raw), t_collect, dict(bt.stats))
    if raw.empty:
        raise SystemExit("no samples collected")
    raw_counts = raw.groupby("group").size().to_dict()
    samples = dedup(raw)
    log.info("after dedup: %d samples", len(samples))

    t1 = time.time()
    res = [simulate_one(bt, r, cfg, end) for r in samples.to_dict("records")]
    samples = pd.concat([samples, pd.DataFrame(res)], axis=1)
    t_sim = time.time() - t1
    log.info("simulated in %.0fs", t_sim)

    cut = end - cfg["backtest"]["holdout_days"] * DAY
    meta_r = {**meta, "start": start, "end": end, "days": days, "coins": len(bt.rows)}
    report = build_report(samples, len(raw), raw_counts, meta_r, cut, cfg,
                          {"collect": t_collect, "simulate": t_sim})
    report += ("\n<details><summary>Engine counters during collection</summary>\n\n```\n"
               f"{dict(bt.stats)}\n```\n</details>\n")
    (out / "WEIGHT_ANALYSIS.md").write_text(report, encoding="utf-8")
    samples.to_csv(out / "samples.csv", index=False)
    raw.to_csv(out / "raw_samples.csv.gz", index=False, compression="gzip")
    print(report)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as f:
            f.write(report)
    repo.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
