"""Review of every Telegram signal since a date (analysis only, 2026-10-16).

Green signals come from the live databases (system 1 and system 2). Yellow (Watch) alerts are
rebuilt from the agent run logs: a Watch with a trade plan was sent at most once per coin/side
per watch_alerts.repeat_hours. For each message the plan is rebuilt from exchange candles as
they were at that moment, and two outcomes are simulated on 1H candles up to now:

  now    entered at market the moment the message arrived, with the message's SL / TP1 / TP2
  plan   the system's own plan (limit at the level, 24h expiry), as the live book would run it

Both use the live lifecycle rules (50% at TP1 with SL to entry, 30% at TP2); what is still open
is valued at the last close. Fees and funding are left out. Prints a markdown report and
writes JSON next to it. Also summarises the one-year backtest trades per coin.

    python scripts/signal_review.py --since 2026-10-09T06:49:42Z --repo owner/name \
        --db1 state.db --db2 state_s2.db --backtest results/ph_stage.json results/s2_trend.json
"""
from __future__ import annotations

import argparse
import io
import json
import os
import re
import sqlite3
import zipfile
from collections import defaultdict
from datetime import datetime, timedelta, timezone

import pandas as pd
import requests

from agent.analysis.frame import make_frame
from agent.config import live_config, load_config
from agent.data.exchange import ExchangeClient, perp_symbol
from agent.layers.trade import Rejected, build_trade, collect_levels
from agent.signals.lifecycle import CLOSED, new_signal, on_candle, on_time, r_at

HOUR = 3_600_000
TF_MS = {"1h": HOUR, "4h": 4 * HOUR, "1d": 24 * HOUR, "1w": 7 * 24 * HOUR}
LINE = re.compile(r"^(\S+Z) (\S+)\s+(LONG|SHORT)\s+score\s+([\d.]+) grade (\S+)\s*(.*)$")
REGIME = re.compile(r"^(\S+Z) regime: (\S+)")


def ms(iso: str) -> int:
    return int(datetime.fromisoformat(iso[:19] + "+00:00").timestamp() * 1000)


def iran(t: int) -> str:
    return (datetime.fromtimestamp(t / 1000, timezone.utc) + timedelta(hours=3, minutes=30)).strftime("%m-%d %H:%M")


# ------------------------------------------------------------ 1. messages
def run_logs(repo: str, since: int, token: str):
    h = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}
    page, runs = 1, []
    while True:
        r = requests.get(f"https://api.github.com/repos/{repo}/actions/workflows/agent.yml/runs",
                         headers=h, params={"per_page": 100, "page": page}, timeout=60).json()
        batch = [x for x in r.get("workflow_runs", []) if ms(x["created_at"]) >= since - 2 * HOUR]
        runs += batch
        if len(r.get("workflow_runs", [])) < 100 or len(batch) < len(r["workflow_runs"]):
            break
        page += 1
    for run in sorted(runs, key=lambda x: x["created_at"]):
        z = requests.get(f"https://api.github.com/repos/{repo}/actions/runs/{run['id']}/logs",
                         headers=h, timeout=120)
        if z.status_code != 200:
            print(f"logs of run {run['id']}: HTTP {z.status_code}")
            continue
        with zipfile.ZipFile(io.BytesIO(z.content)) as zf:
            name = max((n for n in zf.namelist() if n.endswith(".txt")), key=lambda n: zf.getinfo(n).file_size)
            yield run["id"], zf.read(name).decode("utf-8", "replace").splitlines()


def watch_alerts(repo: str, since: int, token: str, repeat_h: int):
    evals, regime = [], None
    for run_id, lines in run_logs(repo, since, token):
        for line in lines:
            m = REGIME.match(line)
            if m:
                regime = m.group(2)
            m = LINE.match(line)
            if m and "system2" not in line:
                ts, base, side, score, grade, rest = m.groups()
                evals.append({"t": ms(ts), "base": base,
                              "side": 1 if side == "LONG" else -1, "score": float(score),
                              "grade": grade, "flags": re.findall(r"flags:(\S+)", rest),
                              "rejected": "rejected:" in rest, "regime": regime, "run": run_id})
    sent, last = [], {}
    for e in sorted(evals, key=lambda e: e["t"]):
        if e["t"] < since or e["grade"] != "Watch" or e["rejected"]:
            continue
        key = (e["base"], e["side"])
        if e["t"] - last.get(key, 0) >= repeat_h * HOUR:
            last[key] = e["t"]
            e["flags"] = e["flags"][0].split(",") if e["flags"] else []
            sent.append({**e, "kind": "yellow", "system": 1})
    return sent, evals


def green_signals(db: str, since: int, system: int):
    out = []
    c = sqlite3.connect(db)
    for sid, t, base, side, grade, score, status, payload in c.execute(
            "SELECT id, created_at, symbol, side, grade, score, status, payload FROM signals "
            "WHERE created_at >= ? ORDER BY created_at", (since,)):
        p = json.loads(payload)
        ev = p.get("evaluation") or {}
        out.append({"t": t, "base": base, "side": 1 if side == "long" else -1, "score": score,
                    "grade": grade, "flags": ev.get("flags", []), "regime": p["regime"]["name"],
                    "sections": ev.get("sections"), "kind": "green", "system": system,
                    "plan": p["plan"], "live_status": status,
                    "live_r": p["lifecycle"]["realized_r"], "live_open": p["lifecycle"]["open_frac"],
                    "live_filled": p["lifecycle"]["filled_at"]})
    return out


# ------------------------------------------------------------ 2. candles and plans
class Candles:
    def __init__(self, cfg):
        self.cfg, self.client, self.cache = cfg, ExchangeClient(cfg), {}

    def get(self, base: str, tf: str, since: int) -> pd.DataFrame:
        key = (base, tf)
        if key not in self.cache:
            start = since - 460 * TF_MS[tf]
            try:
                _, rows = self.client.fetch_history(perp_symbol(base), tf, start)
            except Exception as e:                       # delisted / renamed coin
                print(f"{base} {tf}: {e}")
                rows = []
            self.cache[key] = pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close", "volume"])
        return self.cache[key]

    def frames(self, base: str, t: int) -> dict:
        out = {}
        for tf in ("1w", "1d", "4h", "1h"):
            df = self.get(base, tf, t)
            df = df[df["ts"] + TF_MS[tf] <= t].tail(450).reset_index(drop=True)
            if len(df) < 30:
                return {}
            out[tf] = make_frame(df, tf, self.cfg, with_indicators=True)
        return out


def rebuild_plan(candles: Candles, m: dict, cfg: dict):
    frames = candles.frames(m["base"], m["t"])
    if not frames:
        return None, None
    price = frames["1h"].close
    plan = build_trade(m["side"], frames["4h"], collect_levels(frames, frames["4h"].close, cfg), cfg, price=price)
    return (None if isinstance(plan, Rejected) else plan.to_dict()), price


def simulate(plan: dict, t: int, h1: pd.DataFrame, cfg: dict, market_price: float | None = None):
    plan = dict(plan)
    if market_price is not None:                         # entered the moment the message came
        if (plan["tp1"] - market_price) * plan["side"] <= 0 or (market_price - plan["sl"]) * plan["side"] <= 0:
            return {"status": "skip", "r": 0.0, "note": "price already past TP1 or SL"}
        plan.update(order="market", entry=market_price)
    s = new_signal(plan, t, cfg)
    events, mfe = [], 0.0
    last = None
    for r in h1[h1["ts"] >= t].itertuples():
        events += on_candle(s, int(r.ts), float(r.high), float(r.low), cfg)
        events += on_time(s, int(r.ts) + HOUR)
        last = float(r.close)
        if s["filled_at"]:
            best = r.high if s["side"] == 1 else r.low
            mfe = max(mfe, r_at(s, float(best)))
        if s["status"] in CLOSED:
            break
    open_r = s["open_frac"] * r_at(s, last) if s["status"] not in CLOSED and s["filled_at"] and last else 0.0
    return {"status": s["status"], "r": round(s["realized_r"] + open_r, 2),
            "closed_r": round(s["realized_r"], 2), "open_r": round(open_r, 2), "mfe": round(mfe, 2),
            "filled": bool(s["filled_at"]), "events": [(e.kind, iran(e.ts)) for e in events]}


# ------------------------------------------------------------ 3. report
def summary(rows, key):
    g = defaultdict(list)
    for r in rows:
        if r.get(key) is not None and r["now"]["status"] != "skip":
            g[r[key]].append(r["now"]["r"])
    return {k: (len(v), sum(x > 0 for x in v), round(sum(v), 1)) for k, v in sorted(g.items(), key=lambda kv: -sum(kv[1]))}


def table(title, d, head):
    lines = [f"\n### {title}\n", f"| {head} | پیام | سودده | جمع R |", "|---|---|---|---|"]
    lines += [f"| {k} | {n} | {w} | {r:+.1f} |" for k, (n, w, r) in d.items()]
    return lines


def backtest_by_coin(paths):
    out = []
    for p in paths:
        trades = [t for t in json.load(open(p))["trades"] if t.get("filled_at")]
        g = defaultdict(list)
        for t in trades:
            g[t["base"]].append(t.get("net_r", t.get("gross_r", 0)))
        rows = sorted(((b, len(v), sum(x > 0 for x in v), round(sum(v), 1)) for b, v in g.items()), key=lambda x: -x[3])
        out += [f"\n### بک‌تست یک‌ساله، {os.path.basename(p)} ({len(trades)} معامله)\n",
                "| کوین | معامله | سودده | جمع R |", "|---|---|---|---|"]
        out += [f"| {b} | {n} | {w} | {r:+.1f} |" for b, n, w, r in rows]
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", required=True)
    ap.add_argument("--repo", required=True)
    ap.add_argument("--db1", required=True)
    ap.add_argument("--db2")
    ap.add_argument("--since2", help="when system 2 started (default --since)")
    ap.add_argument("--backtest", nargs="*", default=[])
    ap.add_argument("--out", default="signal_review")
    a = ap.parse_args()
    since = ms(a.since)
    base_cfg = load_config()
    cfg = live_config(base_cfg)
    yellows, evals = watch_alerts(a.repo, since, os.environ["GITHUB_TOKEN"], cfg["watch_alerts"]["repeat_hours"])
    greens = green_signals(a.db1, since, 1) + (green_signals(a.db2, ms(a.since2 or a.since), 2) if a.db2 else [])
    print(f"{len(evals)} evaluations in the logs, {len(yellows)} yellow alerts, {len(greens)} green signals")
    candles = Candles(cfg)
    rows = []
    for m in sorted(greens + yellows, key=lambda m: m["t"]):
        plan, price = rebuild_plan(candles, m, cfg)
        if m["kind"] == "green":
            plan = m["plan"]
        if plan is None:
            print(f"{m['base']} {iran(m['t'])}: no plan rebuilt")
            continue
        h1 = candles.get(m["base"], "1h", m["t"])
        m["now"] = simulate(plan, m["t"], h1, cfg, market_price=price)
        m["plan_result"] = simulate(plan, m["t"], h1, cfg)
        m["entry_gap_atr"] = round(abs(price - plan["entry"]) / plan["atr"], 2) if plan.get("atr") and price else None
        m["order"] = plan["order"]
        m["tp1_r"] = plan.get("tp1_r")
        m["hour"] = int(iran(m["t"])[6:8])
        rows.append(m)

    lines = ["# بررسی همه پیام‌ها از زمان آپدیت", "",
             "| سیستم | نوع | زمان (ایران) | کوین | رده | امتیاز | فلگ‌ها | ورود همان لحظه (R) | MFE | طبق پلن سیستم (R) |",
             "|---|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        n, p = r["now"], r["plan_result"]
        lines.append(f"| {r['system']} | {r['kind']} | {iran(r['t'])} | {r['base']} {'L' if r['side'] == 1 else 'S'} | "
                     f"{r['grade']} | {r['score']:.1f} | {','.join(r['flags']) or '-'} | "
                     f"{n['r']:+.2f} ({n['status']}) | {n.get('mfe', 0):+.1f} | {p['r']:+.2f} ({p['status']}) |")
    for kind in ("green", "yellow"):
        sub = [r for r in rows if r["kind"] == kind and r["system"] == 1]
        lines += table(f"سیستم ۱، {kind}: بر اساس پرچم", summary(
            [{**r, "f": f} for r in sub for f in (r["flags"] or ["-"])], "f"), "فلگ")
    s1 = [r for r in rows if r["system"] == 1]
    for key, title in (("grade", "رده"), ("regime", "رژیم"), ("base", "کوین (زنده)"), ("order", "نوع ورود پلن"),
                       ("hour", "ساعت")):
        lines += table(f"سیستم ۱: بر اساس {title}", summary(s1, key), title)
    lines += backtest_by_coin(a.backtest)
    text = "\n".join(lines)
    print(text)
    with open(a.out + ".md", "w") as f:
        f.write(text)
    with open(a.out + ".json", "w") as f:
        json.dump(rows, f, default=str, indent=1)


if __name__ == "__main__":
    main()
