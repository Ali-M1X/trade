"""Do the L6 score sections predict outcomes under the LIVE rules? (analysis only)

Same replay as scripts/weight_analysis.py (every hourly L6 evaluation with a plan, BTC/ETH forced
both sides, one per coin/side per 24h, simulated alone with the project lifecycle and costs), but
planned with the live variant (`live.variant`, atr_2r: 1.5 ATR stop, TP1 2R, TP2 3R) and with the
market context recorded: regime, labels, warning notes, level quality and the 1H trigger.

"Eligible" = a setup that would be sent if its score were high enough: phase and regime gates pass,
all four gates pass, and no blocking flag (chase, not_confirmed, btc_weak, ...). Weight sets are
compared on the eligible pool: tuned on the first 9 months, checked on the last 3, with the
threshold set so each weight set sends as many tuning-period trades as the current one does.

    python scripts/score_analysis_live.py --db data/backtest.db --out-dir score_live
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import weight_analysis as wa  # noqa: E402

from agent.backtest.variants import merge  # noqa: E402
from agent.config import load_config  # noqa: E402
from agent.store.repository import Repository  # noqa: E402

log = logging.getLogger("score_live")
DAY = wa.DAY
S = wa.SECTIONS
NOTES = ["overextended", "rsi_divergence_against", "breakout_volume", "pullback_volume", "macd",
         "sma_order", "obv", "rsi_healthy"]
LABELS = ["HOT_SECTOR", "BREAKOUT_WATCH", "RS_LEADER", "EARLY_TREND", "VOLUME_ANOMALY",
          "FUNDING_EXTREME"]
RNG = np.random.default_rng(7)


class Collector(wa.Collector):
    def _record(self, ev, t, source):
        super()._record(ev, t, source)
        p, row = ev.plan, self.samples[-1]
        blocking = set(ev.flags) - {"funding_crowded"}
        row.update({
            "eligible": ev.grade is not None and all(ev.gates.get(g, False) for g in wa.GATES)
            and not blocking,
            "sent": ev.grade in ("A", "B"),
            "notes": ",".join(ev.notes), "labels": ",".join(ev.labels),
            "confs": ",".join(ev.confirmations), "n_conf": len(ev.confirmations),
            "trigger_1h": ev.trigger_1h or "", "cycle": ev.cycle,
            "phase_4h": ev.phase_4h, "phase_d": ev.phase_d,
            "lvl_tf": p.support_tf or "", "lvl_kind": p.support_kind or "",
            "lvl_touches": p.support_touches or 0,
            "btc": self.state["majors"]["btc"], "btc_weak": self.state["majors"]["btc_weak"],
            "dist_atr": abs(ev.price - p.entry) / max(1e-12, (p.entry - p.sl) * ev.side / 1.5),
        })


# ------------------------------------------------------------------ helpers
def st(d: pd.DataFrame) -> str:
    if not len(d):
        return "0 | – | – | –"
    return f"{len(d)} | {100 * (d.net_r > 0).mean():.0f}% | {d.net_r.mean():+.3f} | {d.net_r.sum():+.1f}"


def boot_ci(x: np.ndarray, n=2000) -> tuple[float, float]:
    if len(x) < 5:
        return float("nan"), float("nan")
    m = RNG.choice(x, size=(n, len(x)), replace=True).mean(1)
    return float(np.percentile(m, 5)), float(np.percentile(m, 95))


def score_with(d: pd.DataFrame, w: dict, cfg: dict) -> pd.Series:
    return wa.rescore(d, w, cfg)


def top_by_count(d: pd.DataFrame, s: pd.Series, k: int) -> float:
    """Threshold that selects k rows of d by score s (ties included)."""
    if k <= 0 or not len(d):
        return float("inf")
    return float(np.sort(s.to_numpy())[::-1][min(k, len(d)) - 1])


def section_lines(d: pd.DataFrame, title: str) -> list[str]:
    out = [f"### {title} (n={len(d)}, mean {d.net_r.mean():+.3f}R)" if len(d) else f"### {title} (n=0)",
           "", "| Section | Spearman ρ | 90% CI of ρ (bootstrap) | value: n · win · mean R |", "|---|---|---|---|"]
    if len(d) < 10:
        return out + [""]
    for s in S:
        col = d[f"s_{s}"]
        rho = wa.spearman(col, d.net_r)
        bs = []
        idx = np.arange(len(d))
        for _ in range(400):
            j = RNG.choice(idx, len(idx))
            bs.append(wa.spearman(col.iloc[j], d.net_r.iloc[j]))
        lo, hi = np.nanpercentile(bs, 5), np.nanpercentile(bs, 95)
        vals = []
        for v, g in d.groupby(col):
            if len(g) >= 5:
                vals.append(f"{v:g}: {len(g)} · {100 * (g.net_r > 0).mean():.0f}% · {g.net_r.mean():+.2f}")
        out.append(f"| {s} | {rho:+.3f} | {lo:+.2f} … {hi:+.2f} | {'; '.join(vals)} |")
    return out + [""]


def cat_lines(d: pd.DataFrame, title: str, key, values=None) -> list[str]:
    out = [f"### {title}", "", "| value | n | win | mean R | total R | 90% CI mean |", "|---|---|---|---|---|---|"]
    if callable(key):
        groups = [(v, d[key(d, v)]) for v in values]
    else:
        groups = list(d.groupby(d[key]))
    for v, g in groups:
        if len(g) < 5:
            continue
        lo, hi = boot_ci(g.net_r.to_numpy())
        out.append(f"| {v} | {len(g)} | {100 * (g.net_r > 0).mean():.0f}% | {g.net_r.mean():+.3f} | "
                   f"{g.net_r.sum():+.1f} | {lo:+.2f} … {hi:+.2f} |")
    return out + [""]


def has(col):
    return lambda d, v: d[col].str.split(",").apply(lambda xs: v in xs)


def lacks(col):
    return lambda d, v: ~d[col].str.split(",").apply(lambda xs: v in xs)


# ------------------------------------------------------------------ main
def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=None)
    ap.add_argument("--out-dir", default="score_live")
    ap.add_argument("--days", type=int, default=None)
    ap.add_argument("--holdout-days", type=int, default=None)
    ap.add_argument("-v", "--verbose", action="store_true")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO if a.verbose else logging.WARNING,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    cfg = load_config()
    live = (cfg.get("live") or {}).get("variant") or "atr_2r"
    cfg = merge(cfg, cfg["backtest"]["variants"][live])
    repo = Repository(a.db or cfg["backtest"]["db_path"])
    meta = repo.get_state("bt_meta")
    end = meta["end"]
    start = end - (a.days or cfg["backtest"]["days"]) * DAY
    cut = end - (a.holdout_days or cfg["backtest"]["holdout_days"]) * DAY
    out = Path(a.out_dir)
    out.mkdir(parents=True, exist_ok=True)

    t0 = time.time()
    bt = Collector(cfg, repo, start, end)
    bt.run(progress_every=24 * 30)
    raw = pd.DataFrame(bt.samples)
    log.info("raw %d samples in %.0fs", len(raw), time.time() - t0)
    df = wa.dedup(raw)
    res = [wa.simulate_one(bt, r, cfg, end) for r in df.to_dict("records")]
    df = pd.concat([df, pd.DataFrame(res)], axis=1)
    df.to_csv(out / "samples.csv", index=False)
    L, fits = analyse(df, cfg, live, len(raw), start, end, cut)
    (out / "report.md").write_text("\n".join(L), encoding="utf-8")
    (out / "fits.json").write_text(json.dumps(fits, default=float, indent=1))
    print("\n".join(L))
    return 0


def analyse(df, cfg, live, n_raw, start, end, cut):
    df = df.copy()
    df["period"] = np.where(df.t < cut, "tune", "test")
    f = df[df.filled & df.resolved].copy()

    L = [f"# L6 score sections under the live rules ({live})", "",
         f"Replay {wa._date(start)} → {wa._date(end)}, tuning until {wa._date(cut)}. "
         f"{n_raw} hourly samples → {len(df)} setups (one per coin/side per 24h) → {len(f)} filled and closed. "
         "Costs included. No book caps.", "",
         "## 1. Overall", "", "| pool | period | n | win | mean R | total R |", "|---|---|---|---|---|---|"]
    pools = {"all with plan": f, "eligible (would be sent at a high enough score)": f[f.eligible],
             "sent (grade A/B)": f[f.sent], "sent A": f[f.sent & (f.score >= cfg['grades']['A'])],
             "sent B": f[f.sent & (f.score < cfg['grades']['A'])]}
    for name, d in pools.items():
        for per in ("tune", "test"):
            L.append(f"| {name} | {per} | {st(d[d.period == per])} |")
    L += ["", "| group (eligible) | n | win | mean R | total R |", "|---|---|---|---|---|"]
    for g, d in f[f.eligible].groupby("group"):
        L.append(f"| {g} | {st(d)} |")
    L += ["", "## 2. Does each section predict the result?", ""]
    L += section_lines(f, "All filled setups")
    L += section_lines(f[f.eligible], "Eligible setups")
    L += section_lines(f[f.eligible & (f.group == "ALTS")], "Eligible alts")
    L += section_lines(f[f.sent], "Sent (A/B)")

    # score bands
    e = f[f.eligible].copy()
    e["band"] = pd.cut(e.score, [0, 55, 65, 75, 85, 101], right=False,
                       labels=["<55", "55-64", "65-74", "75-84", "85+"])
    L += ["## 3. Score band (eligible setups)", "", "| band | period | n | win | mean R | total R |", "|---|---|---|---|---|---|"]
    for b, g in e.groupby("band", observed=True):
        for per in ("tune", "test"):
            L.append(f"| {b} | {per} | {st(g[g.period == per])} |")
    L.append("")

    # weight sets: tuned on eligible tune period, evaluated on test with matched counts
    tune, test = e[e.period == "tune"], e[e.period == "test"]
    cur = wa.WEIGHTS["CURRENT"]
    w_nnls, w_sp, info = wa.fit_weights(tune)
    sets = {"CURRENT": cur, "EQUAL": {s: 100 / len(S) for s in S}}
    if w_nnls:
        sets["FIT_NNLS (tune only)"] = w_nnls
    if w_sp:
        sets["FIT_SPEARMAN (tune only)"] = w_sp
    for s in S:
        sets[f"drop {s}"] = {k: (0 if k == s else v) for k, v in cur.items()}
    k_tune = int((score_with(tune, cur, cfg) >= cfg["grades"]["B"]).sum())
    k_test = int((score_with(test, cur, cfg) >= cfg["grades"]["B"]).sum())
    L += ["## 4. Weight sets (eligible pool; each set sends the same NUMBER of trades as CURRENT ≥ 65)", "",
          f"CURRENT ≥ 65 sends {k_tune} tuning and {k_test} test trades. Other sets take their own top {k_tune} "
          "in tuning; the test threshold is the tuning threshold (no peeking).", "",
          "| weights | tune: n · win · mean · total | test: n · win · mean · total | test 90% CI mean |", "|---|---|---|---|"]
    wrows = {}
    for name, w in sets.items():
        s_tune, s_test = score_with(tune, w, cfg), score_with(test, w, cfg)
        thr = cfg["grades"]["B"] if name == "CURRENT" else top_by_count(tune, s_tune, k_tune)
        a_, b_ = tune[s_tune >= thr], test[s_test >= thr]
        lo, hi = boot_ci(b_.net_r.to_numpy())
        wrows[name] = (w, thr)
        L.append(f"| {name} | {st(a_)} | {st(b_)} | {lo:+.2f} … {hi:+.2f} |")
    # null check: refit on shuffled tuning results; how often does noise do as well on the test period?
    if w_nnls:
        real = test[score_with(test, w_nnls, cfg) >= wrows["FIT_NNLS (tune only)"][1]].net_r.sum()
        cur_t = test[score_with(test, cur, cfg) >= cfg["grades"]["B"]].net_r.sum()
        null = []
        for _ in range(300):
            sh = tune.copy()
            sh["net_r"] = RNG.permutation(sh.net_r.to_numpy())
            wn, _, _ = wa.fit_weights(sh)
            if not wn:
                continue
            thr = top_by_count(sh, score_with(sh, wn, cfg), k_tune)
            null.append(test[score_with(test, wn, cfg) >= thr].net_r.sum())
        null = np.array(null)
        L += ["", f"Noise check: refitting on 300 shuffled copies of the tuning results gives a test total of "
              f"{np.median(null):+.1f}R median (90% range {np.percentile(null, 5):+.1f} … {np.percentile(null, 95):+.1f}R). "
              f"The real fit gives {real:+.1f}R and CURRENT {cur_t:+.1f}R; the real fit beats "
              f"{100 * (null < real).mean():.0f}% of the noise fits."]
    L += ["", "Fitted weights (sum 100): " + "; ".join(
        f"{n}: " + ", ".join(f"{s} {w[s]:.0f}" for s in S) for n, w in sets.items() if n.startswith("FIT")), ""]
    # walk-forward: refit on rolling 6 months, apply next month
    L += ["### Walk-forward refit (fit NNLS on the previous 180 days, apply to the next 30, same count rule)", "",
          "| month | CURRENT: n · win · mean · total | REFIT: n · win · mean · total |", "|---|---|---|"]
    tot_c, tot_r = [], []
    m0 = start + min(180 * DAY, (cut - start))
    while m0 < end:
        tr = e[(e.t >= m0 - 180 * DAY) & (e.t < m0)]
        te = e[(e.t >= m0) & (e.t < m0 + 30 * DAY)]
        wn, _, _ = wa.fit_weights(tr) if len(tr) > 30 else (None, None, None)
        c_sel = te[score_with(te, cur, cfg) >= cfg["grades"]["B"]]
        if wn:
            k = int((score_with(tr, cur, cfg) >= cfg["grades"]["B"]).sum())
            thr = top_by_count(tr, score_with(tr, wn, cfg), k)
            r_sel = te[score_with(te, wn, cfg) >= thr]
        else:
            r_sel = c_sel
        tot_c.append(c_sel.net_r.to_numpy()); tot_r.append(r_sel.net_r.to_numpy())
        L.append(f"| {wa._date(m0)[:7]} | {st(c_sel)} | {st(r_sel)} |")
        m0 += 30 * DAY
    cc, rr = np.concatenate(tot_c) if tot_c else np.array([]), np.concatenate(tot_r) if tot_r else np.array([])
    L += [f"| total | {len(cc)} · {cc.sum():+.1f}R | {len(rr)} · {rr.sum():+.1f}R |", ""]

    # market context and warnings (eligible)
    L += ["## 5. Market conditions and warnings (eligible setups, whole year)", ""]
    L += cat_lines(e, "Regime", "regime_name")
    L += cat_lines(e, "Side", "side")
    L += cat_lines(e, "BTC 1D structure direction (majors.btc)", "btc")
    L += cat_lines(e, "4H phase", "phase_4h")
    L += cat_lines(e, "Cycle", "cycle")
    L += cat_lines(e, "Scanner label present", has("labels"), LABELS)
    L += cat_lines(e, "Note present", has("notes"), NOTES)
    L += cat_lines(e, "Note absent", lacks("notes"), ["overextended", "rsi_divergence_against"])
    L += cat_lines(e, "Level timeframe", "lvl_tf")
    L += cat_lines(e, "Level kind", "lvl_kind")
    e["touch_b"] = e.lvl_touches.clip(upper=4)
    L += cat_lines(e, "Level touches (4 = 4+)", "touch_b")
    e["trig"] = np.where(e.trigger_1h != "", "1H trigger", "no 1H trigger")
    L += cat_lines(e, "1H trigger candle", "trig")
    L += cat_lines(e, "Confirmations count", "n_conf")
    e["dist_b"] = pd.cut(e.dist_atr, [-1, 0.2, 0.5, 1, 99], labels=["≤0.2", "0.2-0.5", "0.5-1", ">1"])
    L += cat_lines(e, "Distance from level at signal (ATR)", "dist_b")
    L += cat_lines(e, "Group", "group")

    # unfilled share
    L += ["## 6. Fill rate (eligible, all periods)", "",
          f"planned {int(df.eligible.sum())}, filled {int((df.eligible & df.filled).sum())} "
          f"({100 * (df.eligible & df.filled).sum() / max(1, df.eligible.sum()):.0f}%)", ""]

    return L, {"info": info, "sets": {k: v[0] for k, v in wrows.items()}}


if __name__ == "__main__":
    sys.exit(main())
