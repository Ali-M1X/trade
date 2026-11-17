"""System 3 test (2026-11-17, Ali): a pump-and-dump catcher on small and mid coins, 1H candles.

Two triggers, decided on the close of a 1H candle and entered at the next candle's open:
  pump (long)   the candle's volume is >= `vol_x` times the average of the last 7 days, it
                closes above the 24 h high and is up >= 2 % on the candle
  dump (short)  after a run of >= `run_pct` within ~1.5 days, the price makes a lower high,
                closes under the previous candle's low and the volume is below the peak's
Stop: pump = max(1 x ATR14, 1.5 %) under the entry; dump = just above the pump's high.
Target: `target_r` x the stop distance. Time stop: 24 candles (pump), 48 (dump).
A candle that touches the stop and the target counts as the stop.

Universe: the coins of the backtest database (bt_universe) from rank `min_rank` on (the
largest coins left out), with >= $3M daily volume on the day before. One trade per coin at a
time, at most 3 open at once, no new trade after 3 losses in a UTC day. Costs per fill: taker
fee + slippage (0.2 % by default, thin books), in R through the stop distance.

Usage: python scripts/s3_pump.py DB START END OUT.json
"""
from __future__ import annotations

import json
import sqlite3
import sys
from bisect import bisect_right
from datetime import datetime, timezone

H = 3_600_000
DAY = 24 * H
FEE = 0.05            # % per fill, taker
SLIP = 0.2            # % per fill, small coins
MIN_RANK = 20
MIN_VOL_USD = 3e6
MAX_OPEN = 3
DAY_LOSSES = 3

VARIANTS = {
    # name: (side, params)
    "pump_x3_2r": ("pump", {"vol_x": 3, "target_r": 2}),
    "pump_x3_3r": ("pump", {"vol_x": 3, "target_r": 3}),
    "pump_x5_2r": ("pump", {"vol_x": 5, "target_r": 2}),
    "pump_x5_3r": ("pump", {"vol_x": 5, "target_r": 3}),
    "dump_25_2r": ("dump", {"run_pct": 25, "target_r": 2}),
    "dump_25_3r": ("dump", {"run_pct": 25, "target_r": 3}),
    "dump_40_2r": ("dump", {"run_pct": 40, "target_r": 2}),
    "dump_40_3r": ("dump", {"run_pct": 40, "target_r": 3}),
}


def load(db: str, start: int, end: int):
    con = sqlite3.connect(db)
    rows = json.loads(con.execute("SELECT value FROM state WHERE key = 'bt_universe'").fetchone()[0])
    coins = {}
    for r in rows:
        if r.get("u_rank", 0) < MIN_RANK:
            continue
        base = r["symbol"].upper()
        sym = f"{base}/USDT:USDT"
        bars = con.execute("SELECT ts, open, high, low, close, volume FROM candles WHERE symbol = ? AND timeframe = '1h' "
                           "AND ts >= ? AND ts < ? ORDER BY exchange = 'gate', ts", (sym, start - 10 * DAY, end)).fetchall()
        by = {}
        for b in bars:                                  # gate rows come last and win
            if b[1] and b[4]:
                by[b[0]] = b
        bars = [by[t] for t in sorted(by)]
        if len(bars) < 200:
            continue
        vol = con.execute("SELECT ts, volume FROM coin_history WHERE id = ? ORDER BY ts", (r["id"],)).fetchall()
        coins[base] = (bars, [v[0] for v in vol], [v[1] or 0 for v in vol])
    return coins


def _atr(bars, i, n=14):
    s = 0.0
    for k in range(i - n + 1, i + 1):
        h, l, pc = bars[k][2], bars[k][3], bars[k - 1][4]
        s += max(h - l, abs(h - pc), abs(l - pc))
    return s / n


def _vol_ok(vts, vv, t):
    k = bisect_right(vts, t - DAY) - 1                  # the last full day before t
    return k >= 0 and vv[k] >= MIN_VOL_USD


def setups(coins, side, p, start, end):
    """Every trigger: (signal ts, base, entry index, side, stop, target, time limit)."""
    out = []
    for base, (bars, vts, vv) in coins.items():
        n = len(bars)
        cum = [0.0]
        for b in bars:
            cum.append(cum[-1] + (b[5] or 0))
        for i in range(170, n - 1):
            t = bars[i][0]
            if t < start or t >= end:
                continue
            o, h, l, c, v = bars[i][1:6]
            if side == "pump":
                avg = (cum[i] - cum[i - 168]) / 168
                if not avg or (v or 0) < p["vol_x"] * avg or c / o - 1 < 0.02:
                    continue
                if c <= max(b[2] for b in bars[i - 24:i]) or c / o - 1 < 0.02:
                    continue
                if not _vol_ok(vts, vv, t):
                    continue
                entry = bars[i + 1][1]
                d = max(_atr(bars, i), 0.015 * entry)
                if d > 0.12 * entry:
                    continue
                out.append((t, base, i + 1, 1, entry - d, entry + p["target_r"] * d, 24))
            else:
                if c >= bars[i - 1][3]:
                    continue
                peak_k = max(range(i - 12, i), key=lambda k: bars[k][2])
                peak = bars[peak_k][2]
                base_low = min(b[3] for b in bars[i - 36:i - 6])
                if peak / base_low - 1 < p["run_pct"] / 100:
                    continue
                if h >= peak or c >= bars[i - 1][3] or (v or 0) >= (bars[peak_k][5] or 0):
                    continue
                if not _vol_ok(vts, vv, t):
                    continue
                entry = bars[i + 1][1]
                stop = peak * 1.005
                d = stop - entry
                if d < 0.015 * entry or d > 0.15 * entry:
                    continue
                out.append((t, base, i + 1, -1, stop, entry - p["target_r"] * d, 48))
    return sorted(out)


def run(coins, side, p, start, end):
    trades, open_until, busy_until = [], [], {}
    day_losses = {}
    for t, base, j, s, stop, target, limit in setups(coins, side, p, start, end):
        if busy_until.get(base, 0) > t:
            continue
        open_until = [x for x in open_until if x > t]
        if len(open_until) >= MAX_OPEN:
            continue
        dkey = t // DAY
        if day_losses.get(dkey, 0) >= DAY_LOSSES:
            continue
        bars = coins[base][0]
        entry = bars[j][1]
        d = abs(entry - stop)
        exit_px, exit_ts, why = None, None, "time"
        last = min(len(bars) - 1, j + limit - 1)
        for k in range(j, last + 1):
            o, h, l, c = bars[k][1:5]
            if s > 0:
                if l <= stop:
                    exit_px, why = min(o, stop) if k > j else stop, "sl"
                elif h >= target:
                    exit_px, why = max(o, target) if k > j else target, "tp"
            else:
                if h >= stop:
                    exit_px, why = max(o, stop) if k > j else stop, "sl"
                elif l <= target:
                    exit_px, why = min(o, target) if k > j else target, "tp"
            if exit_px is not None:
                exit_ts = bars[k][0] + H
                break
        if exit_px is None:
            exit_px, exit_ts = bars[last][4], bars[last][0] + H
        gross = s * (exit_px - entry) / d
        cost = (2 * (FEE + SLIP) / 100) * entry / d
        net = gross - cost
        trades.append({"base": base, "side": s, "created": t, "filled_at": bars[j][0], "closed_at": exit_ts,
                       "entry": entry, "stop": stop, "stop_pct": round(d / entry * 100, 2), "exit": exit_px,
                       "why": why, "gross_r": round(gross, 3), "cost_r": round(cost, 3), "net_r": round(net, 3)})
        open_until.append(exit_ts)
        busy_until[base] = exit_ts + 6 * H
        if net < 0:
            day_losses[exit_ts // DAY] = day_losses.get(exit_ts // DAY, 0) + 1
    return trades


def _ms(day: str) -> int:
    return int(datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp() * 1000)


def main(db: str, start: str, end: str, out: str) -> None:
    s, e = _ms(start), _ms(end)
    coins = load(db, s, e)
    print(f"{len(coins)} coins with 1H candles from rank {MIN_RANK}")
    res = {"start": s, "end": e, "coins": len(coins), "fee_pct": FEE, "slippage_pct": SLIP, "variants": {}}
    for name, (side, p) in VARIANTS.items():
        tr = run(coins, side, p, s, e)
        res["variants"][name] = {"side": side, "params": p, "trades": tr}
        tot = sum(x["net_r"] for x in tr)
        print(f"{name}: {len(tr)} trades, net {tot:+.1f}R, before costs {sum(x['gross_r'] for x in tr):+.1f}R")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(res, f)


if __name__ == "__main__":
    main(*sys.argv[1:5])
