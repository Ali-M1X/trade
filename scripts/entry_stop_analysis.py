"""Entry, stop and target rules for the L6 setups (analysis only).

Part A: why do strong D/W level setups (levels section = 15) all get stopped out?
Part B: re-build every deduplicated setup under entry x stop x target variants and simulate
each independently with the project's lifecycle and cost model (no signal-book caps).

Samples are the same as scripts/weight_analysis.py: every hourly L6 evaluation with a plan
(shortlist), plus BTC/ETH forced both sides every hour, de-duplicated to one per coin/side per
24h. The project is not modified; the engine is subclassed here.

    python scripts/entry_stop_analysis.py --db data/backtest.db --out-dir entry_stop
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import weight_analysis as wa  # noqa: E402

from agent.backtest.engine import Backtest  # noqa: E402
from agent.config import load_config  # noqa: E402
from agent.layers.majors import Majors  # noqa: E402
from agent.layers.regime import Regime  # noqa: E402
from agent.layers.technical import evaluate  # noqa: E402
from agent.layers.trade import collect_levels, flip_level, second_target  # noqa: E402
from agent.store.repository import Repository  # noqa: E402

log = logging.getLogger("entry_stop")
HOUR, H4, DAY = wa.HOUR, wa.H4, wa.DAY
GROUPS = ["ALL", "ALTS", "BTC", "ETH"]
MIN_TUNE = 20            # tuning trades needed to be ranked
RECLAIM_BARS = 6          # 4H bars after a stop in which a reclaim of the level is checked


# ============================================================ collection
@dataclass
class Lvl:
    price: float
    kind: str
    tf: str
    touches: int
    members: tuple


class Collector(Backtest):
    """Engine loop; L6 evaluates exactly like the engine and keeps, per (coin, side), the
    first sample with a plan and then the next one at least 24h later, with the context
    needed to rebuild the plan under other rules."""

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.samples: list[dict] = []
        self.raw = 0
        self._last: dict = {}

    def _keep(self, ev, t, frames, extra, source):
        self.raw += 1
        k = (ev.base, ev.side)
        if k in self._last and t - self._last[k] < DAY:
            return
        self._last[k] = t
        h4, h1 = frames["4h"], frames["1h"]
        levels = collect_levels(frames, h4.close, self.cfg) + list(extra)
        ma = f"ma{self.cfg['indicators']['ma_mid']}"
        p = ev.plan
        self.samples.append({
            "t": t, "base": ev.base, "group": wa.group_of(ev.base), "side": ev.side,
            "source": source, "score": ev.score, "grade": ev.grade or "",
            "gates_ok": all(ev.gates.get(g, False) for g in wa.GATES),
            "s_phase": ev.sections.get("phase", 0.0), "s_dow": ev.sections.get("dow", 0.0),
            "s_levels": ev.sections.get("levels", 0.0),
            "price": h1.close, "atr": h4.atr, "h4_close": h4.close, "ma25": float(h4.last[ma]),
            "atr_1d": frames["1d"].atr, "atr_1w": frames["1w"].atr,
            "levels": [Lvl(l.price, l.kind, l.timeframe, l.touches, tuple(l.members or [l.price]))
                       for l in levels],
            "swings": [(s.kind, s.price) for s in h4.swings[-40:]],
            "plan": {"order": p.order, "entry": p.entry, "sl": p.sl, "tp1": p.tp1, "tp2": p.tp2,
                     "entry_low": p.entry_low, "entry_high": p.entry_high, "support": p.support,
                     "support_kind": p.support_kind, "support_tf": p.support_tf,
                     "support_touches": p.support_touches, "tp1_r": p.tp1_r},
        })

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
            if ev.plan is not None:
                self._keep(ev, t, frames, extra, "shortlist")
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
                if ev.plan is not None:
                    self._keep(ev, t, frames, [], "forced")


# ============================================================ plan rebuild
def support_level(s: dict, cfg: dict) -> Lvl:
    """The level build_trade leans on: the closest one behind price within the limit."""
    lv = cfg["technical"]["levels"]
    side, price, atr = s["side"], s["price"], s["atr"]
    near = [l for l in s["levels"] if 0 <= (price - l.price) * side <= lv["max_entry_above_support_atr"] * atr]
    return max(near, key=lambda l: l.price * side)


def far_edge(lvl: Lvl, side: int) -> float:
    return min(lvl.members) if side == 1 else max(lvl.members)


def stop_for(rule: str, s: dict, lvl: Lvl, entry: float, cfg: dict) -> tuple[float, bool]:
    """Returns (stop, fell_back_to_S0)."""
    side, atr = s["side"], s["atr"]
    inv = far_edge(lvl, side)
    if rule == "S0":
        return inv - side * cfg["trade"]["sl_buffer_atr"] * atr, False
    if rule == "S1":
        return inv - side * 1.0 * atr, False
    if rule == "S3":
        return entry - side * 1.5 * atr, False
    if rule == "S2":
        kind = "L" if side == 1 else "H"
        behind = [p for k, p in s["swings"] if k == kind and (entry - p) * side > 0]
        if behind:
            return behind[-1] - side * 0.25 * atr, False
        return inv - side * cfg["trade"]["sl_buffer_atr"] * atr, True
    raise ValueError(rule)


def targets_for(rule: str, s: dict, lvl: Lvl, entry: float, sl: float, cfg: dict):
    """(tp1, tp2) or None when the R:R gate rejects."""
    t = cfg["trade"]
    side, atr = s["side"], s["atr"]
    risk = (entry - sl) * side
    if rule == "T1":
        return entry + side * 2 * risk, entry + side * t["tp2_r"] * risk
    half = t["entry_zone_atr"] * atr
    edge = lvl.price + side * half
    edge = max(edge, entry) if side == 1 else min(edge, entry)
    opposing = sorted((Lvl_to_level(l) for l in s["levels"] if (l.price - edge) * side > 0),
                      key=lambda l: l.price * side)
    if opposing:
        first = opposing[0]
        if (first.price - entry) * side < t["tp1_min_r"] * risk:
            return None
        tp1 = first.price
    else:
        tp1 = entry + side * t["tp1_min_r"] * risk
    if t["tp1_max_r"]:
        cap = entry + side * t["tp1_max_r"] * risk
        if (tp1 - cap) * side > 0:
            tp1 = cap
    tp2, _ = second_target(side, entry, risk, tp1, opposing, t["tp2_r"])
    return tp1, tp2


@dataclass
class _L:                     # the attributes second_target reads
    price: float
    timeframe: str


def Lvl_to_level(l: Lvl) -> _L:
    return _L(l.price, l.tf)


class Arrays:
    """Per-coin numpy views of the stored 1H/4H candles (with indicators)."""

    def __init__(self, bt: Backtest):
        self.bt, self.cache = bt, {}

    def get(self, base):
        if base not in self.cache:
            h = self.bt.h
            h1, h4 = h.series(base, "1h"), h.series(base, "4h")
            self.cache[base] = {
                "h1_ts": h._ts[(base, None, "1h")], "h1_hi": h1["high"].to_numpy(float),
                "h1_lo": h1["low"].to_numpy(float),
                "h4_ts": h._ts[(base, None, "4h")], "h4_open": h4["open"].to_numpy(float),
                "h4_close": h4["close"].to_numpy(float), "h4_hi": h4["high"].to_numpy(float),
                "h4_lo": h4["low"].to_numpy(float)}
        return self.cache[base]


def wait_entry(mode: str, s: dict, lvl: Lvl, base_plan: dict, A: dict, cfg: dict):
    """E1 / E2 triggers using only candles closed after the evaluation time t0.
    Returns (status, entry_time, entry_price, sweep_extreme)."""
    side, t0, atr = s["side"], s["t"], s["atr"]
    expiry = t0 + cfg["lifecycle"]["expiry_bars_4h"] * H4
    half = cfg["trade"]["entry_zone_atr"] * atr
    zone_edge = lvl.price + side * half                      # top of the zone for a long
    fe = far_edge(lvl, side)
    tp1, sl0 = base_plan["tp1"], base_plan["sl"]
    touched = mode == "E1" and (s["price"] - zone_edge) * side <= 0     # already in the zone
    swept, extreme = False, None
    i = int(np.searchsorted(A["h1_ts"], t0, side="left"))
    while i < len(A["h1_ts"]):
        ts = int(A["h1_ts"][i])
        if ts + HOUR > expiry:
            break
        hi, lo = A["h1_hi"][i], A["h1_lo"][i]
        adv, fav = (lo, hi) if side == 1 else (hi, lo)
        if mode == "E1":
            if not touched:
                if (fav - tp1) * side >= 0:
                    return "cancel_tp1_first", None, None, None
                if (adv - zone_edge) * side <= 0:
                    touched = True
        else:
            if not swept:
                if (fav - tp1) * side >= 0:
                    return "cancel_tp1_first", None, None, None
                if (adv - fe) * side < 0:
                    swept, extreme = True, adv
            else:
                extreme = min(extreme, adv) if side == 1 else max(extreme, adv)
        end = ts + HOUR
        if end % H4 == 0:                                   # a 4H candle closes now
            j = int(np.searchsorted(A["h4_ts"], end - H4, side="left"))
            if j < len(A["h4_ts"]) and int(A["h4_ts"][j]) == end - H4:
                c, o = A["h4_close"][j], A["h4_open"][j]
                if mode == "E1":
                    if touched and (c - lvl.price) * side > 0 and (c - o) * side > 0:
                        return "entered", end, float(c), None
                    if (c - sl0) * side < 0:
                        return "cancel_closed_beyond_sl", None, None, None
                elif swept and (c - fe) * side > 0:
                    return "entered", end, float(c), float(extreme)
        i += 1
    return "no_trigger", None, None, None


VARIANTS = [(e, st, tg) for e in ("E0", "E1", "E3") for st in ("S0", "S1", "S2", "S3")
            for tg in ("T0", "T1")] + [("E2", "SX", "T0"), ("E2", "SX", "T1")]


def build_variant(v, s: dict, A: dict, cfg: dict):
    """Returns (plan dict with created time, or None, reason)."""
    e, st, tg = v
    side, atr = s["side"], s["atr"]
    lvl = support_level(s, cfg)
    bp = s["plan"]
    if e == "E3":
        ext = (s["h4_close"] - s["ma25"]) * side > 2 * atr
        mature = s["s_phase"] >= 15 and s["s_dow"] >= 15
        if ext or mature:
            return None, "filtered_extended" if ext else "filtered_mature"
    if e in ("E0", "E3"):
        created, order, entry = s["t"], bp["order"], bp["entry"]
        sl, fb = stop_for(st, s, lvl, entry, cfg)
    else:
        status, created, entry, extreme = wait_entry(e, s, lvl, bp, A, cfg)
        if status != "entered":
            return None, status
        order = "market"
        if e == "E2":
            sl, fb = extreme - side * 0.25 * atr, False
        else:
            sl, fb = stop_for(st, s, lvl, entry, cfg)
    risk = (entry - sl) * side
    if risk <= 0:
        return None, "stop_on_wrong_side"
    if risk > cfg["trade"]["max_sl_atr"] * atr:
        return None, "sl_too_wide"
    tps = targets_for(tg, s, lvl, entry, sl, cfg)
    if tps is None:
        return None, "rr"
    return {"t": created, "base": s["base"], "side": side, "entry": entry, "sl": sl,
            "tp1": tps[0], "tp2": tps[1], "order": order, "s2_fallback": fb}, "ok"


# ============================================================ metrics
def max_dd(r: pd.Series) -> float:
    eq = np.r_[0.0, r.cumsum().to_numpy()]
    return float((np.maximum.accumulate(eq) - eq).max())


def stats(d: pd.DataFrame, n_setups: int) -> dict:
    f = d[d["filled"] & d["resolved"]].sort_values("fill_t")
    if not len(f):
        return {"n": 0, "setups": n_setups, "planned": len(d)}
    quick = ((f["status"] == "sl") & (f["closed_at"] - f["fill_t"] < 4 * HOUR)).mean()
    return {"n": len(f), "setups": n_setups, "planned": len(d),
            "fill": len(d[d["filled"]]) / len(d) if len(d) else np.nan,
            "win": (f["net_r"] > 0).mean(), "mean": f["net_r"].mean(), "total": f["net_r"].sum(),
            "stop": f["stop_pct"].median(), "q4h": quick, "dd": max_dd(f["net_r"])}


def cells(x: dict) -> str:
    if not x.get("n"):
        return f"0 | – | – | – | – | – | – | –"
    return (f"{x['n']} | {100 * x['fill']:.0f}% | {100 * x['win']:.0f}% | {x['mean']:+.3f} | "
            f"{x['total']:+.1f} | {x['stop']:.2f}% | {100 * x['q4h']:.0f}% | {x['dd']:.1f}")


def vname(v) -> str:
    return "E2·sweep-stop·" + v[2] if v[0] == "E2" else "·".join(v)


# ============================================================ Part A
def part_a(samples: list[dict], base_res: dict, arrays: Arrays, cfg: dict) -> list[str]:
    rows = []
    for k, s in enumerate(samples):
        r = base_res[k]
        if not (r["filled"] and r["resolved"]):
            continue
        lvl = support_level(s, cfg)
        side, atr, bp = s["side"], s["atr"], s["plan"]
        A = arrays.get(s["base"])
        risk = abs(bp["entry"] - bp["sl"])
        fill_t, close_t = r["fill_t"], r["closed_at"]
        i0 = int(np.searchsorted(A["h1_ts"], fill_t, side="left"))
        i1 = int(np.searchsorted(A["h1_ts"], close_t, side="right"))
        fav = A["h1_hi"][i0:i1] if side == 1 else A["h1_lo"][i0:i1]
        mfe = float(((fav - bp["entry"]) * side).max() / risk) if len(fav) and risk else np.nan
        reclaim_edge = reclaim_mid = tp1_after = False
        if r["status"] == "sl":
            fe = far_edge(lvl, side)
            j0 = int(np.searchsorted(A["h4_ts"], close_t, side="right"))
            closes = A["h4_close"][j0:j0 + RECLAIM_BARS]
            reclaim_edge = bool(((closes - fe) * side > 0).any())
            reclaim_mid = bool(((closes - lvl.price) * side > 0).any())
            k1 = int(np.searchsorted(A["h1_ts"], close_t, side="right"))
            fav2 = A["h1_hi"][k1:k1 + 120] if side == 1 else A["h1_lo"][k1:k1 + 120]
            tp1_after = bool(((fav2 - bp["tp1"]) * side >= 0).any())
        rows.append({
            "date": wa._date(s["t"]), "base": s["base"], "side": "long" if side == 1 else "short",
            "levels": s["s_levels"], "tf": lvl.tf, "kind": lvl.kind, "touches": lvl.touches,
            "spread_atr": (max(lvl.members) - min(lvl.members)) / atr,
            "spread_lvl_atr": (max(lvl.members) - min(lvl.members)) /
                              (s["atr_1d"] if lvl.tf == "1d" else s["atr_1w"] if lvl.tf == "1w" else atr),
            "entry_vs_mid_atr": (bp["entry"] - lvl.price) * side / atr,
            "entry_vs_edge_atr": (bp["entry"] - far_edge(lvl, side)) * side / atr,
            "price_vs_mid_atr": (s["price"] - lvl.price) * side / atr,
            "stop_atr": risk / atr, "stop_pct": risk / bp["entry"] * 100, "tp1_r": bp["tp1_r"],
            "order": bp["order"], "status": r["status"],
            "hours": (close_t - fill_t) / HOUR if close_t else np.nan, "mfe_r": mfe,
            "reclaim_edge": reclaim_edge, "reclaim_mid": reclaim_mid, "tp1_within_5d_after_stop": tp1_after,
            "net_r": r["net_r"]})
    df = pd.DataFrame(rows)
    L = ["## Part A: strong D/W level setups (levels section = 15)", ""]
    strong = df[df["levels"] == 15]

    def agg(d, name):
        if not len(d):
            return f"| {name} | 0 |" + " – |" * 12
        sl = d[d["status"] == "sl"]
        return (f"| {name} | {len(d)} | {100 * (d['net_r'] > 0).mean():.0f}% | {d['net_r'].mean():+.2f} | "
                f"{d['spread_atr'].median():.2f} | {d['entry_vs_mid_atr'].median():+.2f} | "
                f"{d['entry_vs_edge_atr'].median():+.2f} | {d['stop_atr'].median():.2f} | "
                f"{d['stop_pct'].median():.2f}% | {d['tp1_r'].median():.2f} | "
                f"{sl['hours'].median() if len(sl) else float('nan'):.0f} | {d['mfe_r'].median():.2f} | "
                f"{100 * sl['reclaim_edge'].mean() if len(sl) else float('nan'):.0f}% / "
                f"{100 * sl['tp1_within_5d_after_stop'].mean() if len(sl) else float('nan'):.0f}% | "
                f"{100 * (d['order'] == 'market').mean():.0f}% |")
    L += ["Medians. Spread = max − min cluster member in ATR(4H). Entry vs mid / edge = entry minus the "
          "level price / its far member (in the trade direction) in ATR(4H). Hours = fill → stop. "
          f"MFE = best favourable excursion between fill and exit, in R. Reclaim = a 4H close back beyond "
          f"the level's far edge within {RECLAIM_BARS} bars after the stop; TP1 after = price reached the "
          "original TP1 within 5 days after the stop.", "",
          "| Set | n | Win | Mean R | Spread ATR | Entry vs mid | Entry vs edge | Stop ATR | Stop % | "
          "TP1 R | Hours to stop | MFE R | Reclaim / TP1 after (stopped) | Market |",
          "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for v, name in ((15, "levels = 15"), (8, "levels = 8"), (0, "levels = 0")):
        L.append(agg(df[df["levels"] == v], name))
    for side in ("long", "short"):
        L.append(agg(strong[strong["side"] == side], f"levels = 15, {side}"))
    for tf in ("1d", "1w"):
        L.append(agg(strong[strong["tf"] == tf], f"levels = 15, {tf} cluster"))
    L += ["", f"Strong-level samples: {len(strong)} trades on {strong['base'].nunique()} coins, "
          f"{strong['date'].nunique()} distinct days. Status: {strong['status'].value_counts().to_dict()}.", "",
          "<details><summary>Every strong-level sample</summary>", "",
          "| Date | Coin | Side | TF | Touches | Spread ATR(4H) | Spread ATR(level TF) | Price vs mid | Entry vs mid | "
          "Entry vs edge | Stop ATR | Stop % | TP1 R | Order | Status | Hours | MFE R | Reclaim | TP1 after | Net R |",
          "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in strong.sort_values("date").itertuples():
        L.append(f"| {r.date} | {r.base} | {r.side} | {r.tf} | {r.touches} | {r.spread_atr:.2f} | "
                 f"{r.spread_lvl_atr:.2f} | {r.price_vs_mid_atr:+.2f} | {r.entry_vs_mid_atr:+.2f} | "
                 f"{r.entry_vs_edge_atr:+.2f} | {r.stop_atr:.2f} | {r.stop_pct:.2f}% | {r.tp1_r:.2f} | {r.order} | "
                 f"{r.status} | {r.hours:.0f} | {r.mfe_r:.2f} | {'y' if r.reclaim_edge else 'n'} | "
                 f"{'y' if r.tp1_within_5d_after_stop else 'n'} | {r.net_r:+.2f} |")
    L += ["", "</details>", ""]
    return L, df


# ============================================================ main
def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=None)
    ap.add_argument("--days", type=int, default=None)
    ap.add_argument("--holdout-days", type=int, default=None)
    ap.add_argument("--out-dir", default="entry_stop")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    cfg = load_config()
    if args.holdout_days:
        cfg["backtest"]["holdout_days"] = args.holdout_days
    repo = Repository(args.db or cfg["backtest"]["db_path"])
    meta = repo.get_state("bt_meta")
    days = args.days or cfg["backtest"]["days"]
    end = meta["end"]
    start = end - days * DAY
    cut = end - cfg["backtest"]["holdout_days"] * DAY
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)

    t0 = time.time()
    bt = Collector(cfg, repo, start, end)
    bt.run(progress_every=24 * 30)
    t_collect = time.time() - t0
    samples = bt.samples
    log.info("raw %d, kept %d in %.0fs", bt.raw, len(samples), t_collect)
    arrays = Arrays(bt)

    # ---- simulate every variant
    t1 = time.time()
    recs, base_res, mismatch, s2_fb = [], {}, 0, 0
    for k, s in enumerate(samples):
        A = arrays.get(s["base"])
        for v in VARIANTS:
            plan, why = build_variant(v, s, A, cfg)
            rec = {"k": k, "variant": vname(v), "E": v[0], "S": v[1], "T": v[2], "t": s["t"],
                   "base": s["base"], "group": s["group"], "side": s["side"],
                   "sel": s["gates_ok"] and s["score"] >= 65, "reason": why}
            if plan is None:
                recs.append({**rec, "planned": False})
                continue
            if v == ("E0", "S0", "T0"):
                bp = s["plan"]
                if any(abs(plan[x] - bp[x]) > 1e-9 * max(1.0, abs(bp[x])) for x in ("entry", "sl", "tp1", "tp2")) \
                        or plan["order"] != bp["order"]:
                    mismatch += 1
            s2_fb += plan["s2_fallback"]
            r = wa.simulate_one(bt, plan, cfg, end)
            fill_t = plan["t"] if plan["order"] == "market" else None
            if r["filled"] and fill_t is None:
                # limit fill time: the first 1H candle at/after creation touching entry
                i = int(np.searchsorted(A["h1_ts"], plan["t"], side="left"))
                adv = A["h1_lo"][i:] if s["side"] == 1 else A["h1_hi"][i:]
                hit = np.nonzero((adv - plan["entry"]) * s["side"] <= 0)[0]
                fill_t = int(A["h1_ts"][i + hit[0]]) if len(hit) else plan["t"]
            rec.update({"planned": True, **r, "fill_t": fill_t, "entry": plan["entry"], "sl": plan["sl"],
                        "tp1": plan["tp1"], "order": plan["order"], "created": plan["t"],
                        "stop_pct": abs(plan["entry"] - plan["sl"]) / plan["entry"] * 100})
            recs.append(rec)
            if v == ("E0", "S0", "T0"):
                base_res[k] = {**r, "fill_t": fill_t}
    t_sim = time.time() - t1
    df = pd.DataFrame(recs)
    log.info("simulated %d variant-samples in %.0fs", len(df), t_sim)

    # ---- report
    L = ["# Entry, stop and target rules for L6 setups", "",
         f"History {wa._date(start)} → {wa._date(end)} ({days} days, {len(bt.rows)} coins). **Tuning** "
         f"{wa._date(start)} → {wa._date(cut)}, **holdout** {wa._date(cut)} → {wa._date(end)}. Samples: "
         f"{bt.raw} hourly L6 evaluations with a plan (shortlist + BTC/ETH forced both sides) → "
         f"{len(samples)} after one-per-coin/side-per-24h dedup. Each variant rebuilds the plan from the "
         "same setup and is simulated on its own with the project lifecycle (1H candles, 4H-close rules, "
         "6×4H expiry) and cost model → net R. No book caps, cooldowns or correlation limits.", "",
         f"Check: E0·S0·T0 reproduces the engine's plan for every sample: **{len(samples) - mismatch}/"
         f"{len(samples)} identical**. S2 fell back to S0 (no swing behind entry) {s2_fb} times.",
         f"Runtime: collection {t_collect:.0f}s, simulation {t_sim:.0f}s.", ""]
    la, adf = part_a(samples, base_res, arrays, cfg)
    L += la

    L += ["## Part B: variants", "",
          "- **E0** current entry (market inside ±0.2 ATR of the level, else limit at the level).",
          "- **E1** 4H confirmation: after price touches the entry zone (1H candles after the evaluation), "
          "the first 4H candle that closes beyond the level price in the trade direction *and* is a "
          "trade-direction candle (close > open for a long) → market entry at that close. Cancelled if "
          "TP1 is hit before the touch, a 4H close goes beyond the original stop, or nothing within the "
          "6×4H expiry.",
          "- **E2** sweep and reclaim: a 1H candle trades beyond the level's far member, then the first 4H "
          "close back beyond that far member → market entry at that close; stop = sweep extreme ∓ 0.25 ATR. "
          "Same TP1-first cancel and expiry.",
          "- **E3** E0, but skip if the 4H close is > 2 ATR beyond MA25 in the trade direction, or phase = 15 "
          "and dow = 15 (mature D trend).",
          "- **S0** far member ∓ 0.5 ATR (current) · **S1** far member ∓ 1.0 ATR · **S2** last 4H swing "
          "behind entry ∓ 0.25 ATR (S0 if none) · **S3** entry ∓ 1.5 ATR.",
          "- **T0** current (first opposing level, must be ≥ 2R, else rejected) · **T1** TP1 = 2R, TP2 = 3R, "
          "no level R:R gate.",
          "- All: max_sl_atr (3 ATR) cap kept; ATR = ATR(4H) at evaluation. E1/E2 only use 1H/4H candles that "
          "have closed by the decision time (entry = the close of the 4H candle that just closed).", ""]

    # rejections
    rej = df[~df["planned"]].groupby(["variant", "reason"]).size().unstack(fill_value=0)
    L += ["### Setups rejected or not triggered per variant (all groups, both periods)", "",
          "| Variant | Planned | " + " | ".join(rej.columns) + " |", "|---|---|" + "---|" * len(rej.columns)]
    planned = df[df["planned"]].groupby("variant").size()
    for v in VARIANTS:
        n = vname(v)
        row = rej.loc[n] if n in rej.index else pd.Series(0, index=rej.columns)
        L.append(f"| {n} | {planned.get(n, 0)} | " + " | ".join(str(int(row[c])) for c in rej.columns) + " |")
    L.append("")

    head = ("| Variant | Tune n | fill | win | mean R | total R | med stop | stop<4h | maxDD R | "
            "Hold n | fill | win | mean R | total R | med stop | stop<4h | maxDD R |")
    sep = "|---|" + "---|" * 16
    ranking = {}
    for view, vlabel in (("all", "View 1: all setups with a plan"),
                         ("sel", "View 2: setups passing all four gates with score ≥ 65 (current weights)")):
        L += [f"### {vlabel}", "",
              "Cells: n filled & resolved · fill rate (filled / planned) · win rate · mean net R · total net R · "
              "median stop % · share stopped within 4h of fill · max drawdown (R) of the equal-weight sequence.", ""]
        dv = df if view == "all" else df[df["sel"]]
        for g in GROUPS:
            dg = dv if g == "ALL" else dv[dv["group"] == g]
            L += [f"#### {g} ({vlabel.split(':')[0]})", "", head, sep]
            for v in VARIANTS:
                n = vname(v)
                d = dg[(dg["variant"] == n) & dg["planned"]]
                st = {p: stats(d[(d["t"] < cut) if p == "tune" else (d["t"] >= cut)],
                               int(((dg["variant"] == n) & ((dg["t"] < cut) if p == "tune" else (dg["t"] >= cut))).sum()))
                      for p in ("tune", "hold")}
                ranking[(view, g, n)] = st
                L.append(f"| {n} | {cells(st['tune'])} | {cells(st['hold'])} |")
            L.append("")

    # ranking
    L += ["## Ranking", "",
          f"Eligible = at least {MIN_TUNE} tuning trades and tuning mean net R ≥ 0, or the best tuning mean "
          "among those. Ranked by holdout mean net R. "
          "⚠ = fewer than 30 holdout trades (unreliable).", ""]
    for view in ("all", "sel"):
        for g in GROUPS:
            items = [(n, ranking[(view, g, n)]) for n in map(vname, VARIANTS)]
            tun = [x for x in items if x[1]["tune"].get("n", 0) >= MIN_TUNE]
            if not tun:
                continue
            best_t = max(tun, key=lambda x: x[1]["tune"]["mean"])[0]
            elig = [x for x in tun if (x[1]["tune"]["mean"] >= 0 or x[0] == best_t) and x[1]["hold"].get("n")]
            elig.sort(key=lambda x: -x[1]["hold"]["mean"])
            L += [f"**{'View 1 (all)' if view == 'all' else 'View 2 (gates + score ≥ 65)'} · {g}** "
                  f"(best tuning: {best_t})", "",
                  "| # | Variant | Tune n | Tune mean R | Hold n | Hold mean R | Hold total R | Hold maxDD |",
                  "|---|---|---|---|---|---|---|---|"]
            for i, (n, st) in enumerate(elig[:8], 1):
                warn = " ⚠" if st["hold"]["n"] < 30 else ""
                L.append(f"| {i} | {n}{warn} | {st['tune']['n']} | {st['tune']['mean']:+.3f} | {st['hold']['n']} | "
                         f"{st['hold']['mean']:+.3f} | {st['hold']['total']:+.1f} | {st['hold']['dd']:.1f} |")
            if not elig:
                L.append("| – | none eligible | | | | | | |")
            L.append("")

    # too-good check
    good = [(k, v) for k, v in ranking.items() if k[0] == "all"
            for p in ("tune", "hold") if v[p].get("n", 0) > 50 and v[p]["mean"] > 0.5]
    L += ["## Sanity", "", "Variants with > +0.5 R/trade on > 50 trades (view 1): "
          + (", ".join(sorted({f'{k[2]} ({k[1]})' for k, _ in good})) if good else "none") + ".", ""]

    report = "\n".join(L) + "\n"
    (out / "ENTRY_STOP.md").write_text(report, encoding="utf-8")
    df.to_csv(out / "variant_samples.csv.gz", index=False, compression="gzip")
    adf.to_csv(out / "part_a_samples.csv", index=False)
    print(report)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as f:
            f.write(report)
    repo.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
