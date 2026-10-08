"""Do the trend-quality checks (Ali's notes, 2026-10-08) predict trade outcomes? (analysis only)

Same setup replay as scripts/score_analysis_live.py: every hourly L6 evaluation with a plan under
the live rules (atr_2r), BTC/ETH forced on both sides, one per coin/side per 24h, each simulated
alone with the project lifecycle and costs. Trend quality runs in `observe` mode, so the scores
and grades are the live ones and every setup also carries the 15 checks and their points.

Report: per check (-1 / 0 / +1) mean net R, the points vs net R, and three selection rules on the
eligible pool (tuning = first 9 months, test = last 3):
  CURRENT   score >= 65
  SCORE     score + trend-quality points >= 65
  FILTER    score >= 65 and trend-quality points > filter_max

    python scripts/trend_quality_analysis.py --db data/backtest.db --out-dir tq_analysis
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import score_analysis_live as sal  # noqa: E402
import weight_analysis as wa  # noqa: E402

from agent.analysis.trend_quality import CHECKS  # noqa: E402
from agent.backtest.variants import merge  # noqa: E402
from agent.config import load_config  # noqa: E402
from agent.store.repository import Repository  # noqa: E402

log = logging.getLogger("tq_analysis")
DAY = wa.DAY
RNG = np.random.default_rng(11)


class Collector(sal.Collector):
    def _record(self, ev, t, source):
        super()._record(ev, t, source)
        row = self.samples[-1]
        row["tq_points"] = ev.tq_points if ev.tq_points is not None else 0.0
        for c in CHECKS:
            row[f"tq_{c}"] = int(ev.trend_quality.get(c, 0))


def rho_ci(x: pd.Series, y: pd.Series) -> tuple[float, float, float]:
    rho = wa.spearman(x, y)
    idx = np.arange(len(x))
    bs = [wa.spearman(x.iloc[j], y.iloc[j]) for j in (RNG.choice(idx, len(idx)) for _ in range(400))]
    return rho, float(np.nanpercentile(bs, 5)), float(np.nanpercentile(bs, 95))


def cell(g: pd.DataFrame) -> str:
    if len(g) < 5:
        return f"{len(g)} · –"
    lo, hi = sal.boot_ci(g.net_r.to_numpy())
    return f"{len(g)} · {100 * (g.net_r > 0).mean():.0f}% · {g.net_r.mean():+.2f} ({lo:+.2f}…{hi:+.2f})"


def check_lines(d: pd.DataFrame, title: str) -> list[str]:
    out = [f"### {title} (n={len(d)}, mean {d.net_r.mean():+.3f}R)", "",
           "| check | −1: n · win · mean R (90% CI) | 0 | +1 | ρ vs net R |", "|---|---|---|---|---|"]
    for c in CHECKS:
        col = d[f"tq_{c}"]
        cells = [cell(d[col == v]) for v in (-1, 0, 1)]
        out.append(f"| {c} | " + " | ".join(cells) + f" | {wa.spearman(col, d.net_r):+.3f} |")
    return out + [""]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=None)
    ap.add_argument("--out-dir", default="tq_analysis")
    ap.add_argument("--days", type=int, default=None)
    ap.add_argument("-v", "--verbose", action="store_true")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO if a.verbose else logging.WARNING,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    cfg = load_config()
    live = (cfg.get("live") or {}).get("variant") or "atr_2r"
    cfg = merge(cfg, cfg["backtest"]["variants"][live])
    cfg = merge(cfg, {"technical": {"trend_quality": {"mode": "observe"}}})
    repo = Repository(a.db or cfg["backtest"]["db_path"])
    meta = repo.get_state("bt_meta")
    end = meta["end"]
    start = end - (a.days or cfg["backtest"]["days"]) * DAY
    cut = end - cfg["backtest"]["holdout_days"] * DAY
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
    L = analyse(df, cfg, live, len(raw), start, end, cut)
    (out / "report.md").write_text("\n".join(L), encoding="utf-8")
    print("\n".join(L))
    return 0


def analyse(df, cfg, live, n_raw, start, end, cut):
    df = df.copy()
    df["period"] = np.where(df.t < cut, "tune", "test")
    f = df[df.filled & df.resolved].copy()
    e = f[f.eligible].copy()
    fmax = cfg["technical"]["trend_quality"]["filter_max"]
    B = cfg["grades"]["B"]

    L = [f"# Trend quality under the live rules ({live})", "",
         f"Replay {wa._date(start)} → {wa._date(end)}, tuning until {wa._date(cut)}. "
         f"{n_raw} hourly samples → {len(df)} setups → {len(f)} filled and closed, "
         f"{len(e)} of them eligible (gates pass, no blocking flag). Costs included. No book caps.", ""]

    L += ["## 1. Trend-quality points vs result", "",
          "| pool | ρ (90% CI) | ≤−6 | −5…−1 | 0 | 1…5 | ≥6 |", "|---|---|---|---|---|---|---|"]
    bands = [(-99, -6), (-5, -1), (0, 0), (1, 5), (6, 99)]
    for name, d in (("all filled", f), ("eligible", e), ("eligible alts", e[e.group == "ALTS"]),
                    ("eligible, tuning", e[e.period == "tune"]), ("eligible, test", e[e.period == "test"])):
        if len(d) < 10:
            continue
        rho, lo, hi = rho_ci(d.tq_points, d.net_r)
        cells = [cell(d[(d.tq_points >= a) & (d.tq_points <= b)]) for a, b in bands]
        L.append(f"| {name} | {rho:+.3f} ({lo:+.2f}…{hi:+.2f}) | " + " | ".join(cells) + " |")
    L += ["", "## 2. Each check", ""]
    L += check_lines(f, "All filled setups")
    L += check_lines(e, "Eligible setups")
    L += check_lines(e[e.period == "tune"], "Eligible, tuning period")
    L += check_lines(e[e.period == "test"], "Eligible, test period")

    L += ["## 3. Selection rules on the eligible pool", "",
          "| rule | tuning: n · win · mean · total | test: n · win · mean · total | test 90% CI mean |",
          "|---|---|---|---|"]
    rules = {
        "CURRENT (score ≥ 65)": lambda d: d.score >= B,
        "SCORE (score + TQ ≥ 65)": lambda d: (d.score + d.tq_points) >= B,
        f"FILTER (score ≥ 65 and TQ > {fmax})": lambda d: (d.score >= B) & (d.tq_points > fmax),
        "FILTER0 (score ≥ 65 and TQ ≥ 0)": lambda d: (d.score >= B) & (d.tq_points >= 0),
    }
    tune, test = e[e.period == "tune"], e[e.period == "test"]
    for name, rule in rules.items():
        a_, b_ = tune[rule(tune)], test[rule(test)]
        lo, hi = sal.boot_ci(b_.net_r.to_numpy())
        L.append(f"| {name} | {sal.st(a_)} | {sal.st(b_)} | {lo:+.2f} … {hi:+.2f} |")
    # the same number of trades as CURRENT, picked by score + TQ instead of score
    k = int((tune.score >= B).sum())
    s_tune = tune.score + tune.tq_points
    thr = sal.top_by_count(tune, s_tune, k)
    a_, b_ = tune[s_tune >= thr], test[(test.score + test.tq_points) >= thr]
    lo, hi = sal.boot_ci(b_.net_r.to_numpy())
    L.append(f"| SCORE, same count as CURRENT (threshold {thr:g}) | {sal.st(a_)} | {sal.st(b_)} | {lo:+.2f} … {hi:+.2f} |")
    L.append("")
    return L


if __name__ == "__main__":
    sys.exit(main())
