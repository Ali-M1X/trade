"""System 3, round 6 (2026-11-19, Ali: «سیستم ۳ رو راه اندازی کنیم... با این روندی که گفتی اوکی ام»).

Short the exhaustion of a pump on small and mid coins, 1H candles. Round 5 (s3_pump.py) found the
plain «short after a big pump» positive before costs in both periods but not after them. This round
adds what was proposed to Ali:

  setup      a run of >= `run` % within ~1.5 days, then a lower high and a close under the
             previous candle's low on lighter volume than the peak's (round 5's dump trigger)
  funding    any | pos (the last funding rate >= 0: no shorts into crowded shorts) | hot (>= 0.03 %)
  wick       a climax candle near the peak: upper wick >= 40 % of its range on >= 3x the week's volume
  entry      taker (next open, fee + slippage) | maker (a sell limit halfway back up the signal
             candle, good for 6 candles, cancelled if price reaches the target first)
  stop       above the pump's high, at least 3 % from the entry (costs stay under ~0.2R)
  target     a `tp` retracement (38 / 50 / 62 %) of the whole pump, as a limit order (maker);
             skipped when it is under 1R away
  never      in a coin's first 48 h of trading
Open interest has no usable history, so it is not tested.

Costs per fill: taker 0.05 % + 0.2 % slippage, maker 0.02 %; funding received or paid while open.
Portfolio: one trade per coin, at most 3 open, no new trade after 3 losses in a UTC day.

Usage: python scripts/s3_exhaust.py DB START END TEST_FROM OUT.json REPORT.md
"""
from __future__ import annotations

import itertools
import json
import os
import sqlite3
import sys
from bisect import bisect_right
from collections import defaultdict

sys.path.insert(0, os.path.dirname(__file__))
from s3_pump import DAY, H, _atr, _ms, _vol_ok, load  # noqa: E402
from s3_report import HEAD, row, stats  # noqa: E402

TAKER, SLIP, MAKER = 0.05, 0.2, 0.02          # % per fill
MAX_OPEN, DAY_LOSSES = 3, 3
MIN_STOP, MAX_STOP = 0.03, 0.20
FILL_BARS, LIMIT_BARS = 6, 48
GRID = {"run": (25, 40), "funding": ("any", "pos", "hot"), "wick": (0, 1), "entry": ("taker", "maker"),
        "tp": (38, 50, 62)}
FUND_FA = {"any": "بی‌شرط", "pos": "فاندینگ ≥ ۰", "hot": "فاندینگ ≥ ۰٫۰۳٪"}


def funding(db: str, bases) -> dict[str, tuple[list[int], list[float]]]:
    con = sqlite3.connect(db)
    out = {}
    for b in bases:
        rows = con.execute("SELECT ts, rate FROM funding WHERE symbol = ? ORDER BY ts", (f"{b}/USDT:USDT",)).fetchall()
        if rows:
            out[b] = ([r[0] for r in rows], [r[1] or 0.0 for r in rows])
    return out


def _rate_at(f, t):
    if not f:
        return None
    k = bisect_right(f[0], t) - 1
    return f[1][k] if k >= 0 else None


def setups(coins, fund, run_pct: float, start: int, end: int, db_start: int) -> list[dict]:
    """Every dump trigger with what the filters need (funding, climax wick)."""
    out = []
    for base, (bars, vts, vv) in coins.items():
        n = len(bars)
        cum = [0.0]
        for b in bars:
            cum.append(cum[-1] + (b[5] or 0))
        first = bars[0][0]
        for i in range(170, n - 1):
            t = bars[i][0]
            if t < start or t >= end:
                continue
            o, h, l, c, v = bars[i][1:6]
            if c >= bars[i - 1][3]:
                continue
            peak_k = max(range(i - 12, i), key=lambda k: bars[k][2])
            peak = bars[peak_k][2]
            base_low = min(b[3] for b in bars[i - 36:i - 6])
            if peak / base_low - 1 < run_pct / 100 or h >= peak or (v or 0) >= (bars[peak_k][5] or 0):
                continue
            if first > db_start + 2 * DAY and t - first < 48 * H:       # a new listing: never
                continue
            if not _vol_ok(vts, vv, t):
                continue
            avg = (cum[i] - cum[i - 168]) / 168 or 0
            wick = False
            for k in range(max(peak_k - 2, i - 12), min(peak_k + 3, i + 1)):
                bo, bh, bl, bc, bv = bars[k][1:6]
                rng = bh - bl
                if rng > 0 and (bh - max(bo, bc)) / rng >= 0.4 and avg and (bv or 0) >= 3 * avg:
                    wick = True
                    break
            out.append({"t": t, "base": base, "i": i, "peak": peak, "base_low": base_low, "close": c, "high": h,
                        "rate": _rate_at(fund.get(base), t), "wick": wick, "atr": _atr(bars, i)})
    return sorted(out, key=lambda s: s["t"])


def _fund_ok(mode: str, rate) -> bool:
    if mode == "any":
        return True
    if rate is None:
        return False
    return rate >= 0 if mode == "pos" else rate >= 0.0003


def simulate(coins, fund, sets: list[dict], p: dict) -> list[dict]:
    trades, open_until, busy, day_losses = [], [], {}, defaultdict(int)
    for s in sets:
        if not _fund_ok(p["funding"], s["rate"]) or (p["wick"] and not s["wick"]):
            continue
        t, base = s["t"], s["base"]
        if busy.get(base, 0) > t:
            continue
        open_until = [x for x in open_until if x > t]
        if len(open_until) >= MAX_OPEN or day_losses[t // DAY] >= DAY_LOSSES:
            continue
        bars = coins[base][0]
        target = s["peak"] - p["tp"] / 100 * (s["peak"] - s["base_low"])
        j = s["i"] + 1
        if p["entry"] == "taker":
            entry, fill_k, fee_in = bars[j][1], j, TAKER + SLIP
        else:
            limit = (s["close"] + s["high"]) / 2
            entry = fill_k = None
            for k in range(j, min(len(bars), j + FILL_BARS)):
                bo, bh, bl = bars[k][1], bars[k][2], bars[k][3]
                if bo >= limit or bh >= limit:
                    entry, fill_k = max(bo, limit), k
                    break
                if bl <= target:                                  # gone without us
                    break
            if entry is None:
                continue
            fee_in = MAKER
        stop = max(s["peak"] * 1.005, entry * (1 + MIN_STOP))
        d = stop - entry
        if d > MAX_STOP * entry or (entry - target) < d:
            continue
        exit_px = exit_ts = None
        why, fee_out = "time", TAKER + SLIP
        last = min(len(bars) - 1, fill_k + LIMIT_BARS - 1)
        for k in range(fill_k, last + 1):
            bo, bh, bl, bc = bars[k][1:5]
            if bh >= stop:                                        # a candle touching both counts as the stop
                exit_px, why, fee_out = (max(bo, stop) if k > fill_k else stop), "sl", TAKER + SLIP
            elif bl <= target and (k > fill_k or p["entry"] == "taker"):   # a limit fill: the low may come first
                exit_px, why, fee_out = (min(bo, target) if k > fill_k else target), "tp", MAKER
            if exit_px is not None:
                exit_ts = bars[k][0] + H
                break
        if exit_px is None:
            exit_px, exit_ts = bars[last][4], bars[last][0] + H
        gross = (entry - exit_px) / d
        cost = (fee_in + fee_out) / 100 * entry / d
        f = fund.get(base)
        fr = 0.0
        if f:                                                     # shorts receive a positive rate
            a, b = bisect_right(f[0], bars[fill_k][0]), bisect_right(f[0], exit_ts)
            fr = sum(f[1][a:b]) * entry / d
        net = gross - cost + fr
        trades.append({"base": base, "side": -1, "created": t, "filled_at": bars[fill_k][0], "closed_at": exit_ts,
                       "entry": entry, "stop": stop, "target": target, "stop_pct": round(d / entry * 100, 2),
                       "exit": exit_px, "why": why, "rate": s["rate"], "gross_r": round(gross, 3),
                       "cost_r": round(cost, 3), "funding_r": round(fr, 3), "net_r": round(net, 3)})
        open_until.append(exit_ts)
        busy[base] = exit_ts + 6 * H
        if net < 0:
            day_losses[exit_ts // DAY] += 1
    return trades


def name_of(p: dict) -> str:
    return f"r{p['run']}·{p['funding']}·{'wick' if p['wick'] else 'nowick'}·{p['entry']}·tp{p['tp']}"


def label(p: dict) -> str:
    return (f"پامپ ≥{p['run']}٪ · {FUND_FA[p['funding']]} · {'با کندل اوج' if p['wick'] else 'بدون شرط کندل'} · "
            f"{'لیمیت' if p['entry'] == 'maker' else 'مارکت'} · تارگت {p['tp']}٪")


def report(res: dict, cut: int) -> str:
    var = res["variants"]
    split = {k: ([t for t in v["trades"] if t["closed_at"] < cut], [t for t in v["trades"] if t["created"] >= cut])
             for k, v in var.items()}
    st = {k: (stats(a), stats(b)) for k, (a, b) in split.items()}
    out = [f"# سیستم ۳، دور ۶: شورت در خستگی پامپ ({res['coins']} کوین، کندل ۱ ساعته)", "",
           "کارمزد مارکت ۰٫۰۵٪ + لغزش ۰٫۲٪، لیمیت ۰٫۰۲٪، فاندینگ دریافتی/پرداختی حساب شده. "
           "آموزش تا تاریخ برش، آزمون بعد از آن. انتخاب فقط روی آموزش.", ""]
    ok = [k for k in var if st[k][0].get("n", 0) >= 30]
    best = sorted(ok, key=lambda k: -st[k][0]["tot"])[:8]
    out += ["## بهترین‌ها در آموزش (دست‌کم ۳۰ معامله) و همان‌ها در آزمون", "", HEAD.replace("| گزینه", "| گزینه · دوره")]
    for k in best:
        lb = label(var[k]["params"])
        out += [row(lb + " · آموزش", st[k][0]), row(lb + " · آزمون", st[k][1])]
    pos_test = [k for k in var if st[k][1].get("n", 0) >= 20 and st[k][1]["tot"] > 0]
    out += ["", f"گزینه‌های مثبت بعد از هزینه در آزمون (دست‌کم ۲۰ معامله): {len(pos_test)} از {len(var)}", ""]
    out += ["## اثر هر فیلتر (میانگین R خالص هر معامله، همه‌ی ترکیب‌های دیگر با هم)", "",
            "| فیلتر | مقدار | آموزش | آزمون |", "|---|---|---|---|"]
    for key, vals in GRID.items():
        for v in vals:
            ks = [k for k in var if var[k]["params"][key] == v]
            a = [t["net_r"] for k in ks for t in split[k][0]]
            b = [t["net_r"] for k in ks for t in split[k][1]]
            fa = lambda xs: f"{sum(xs) / len(xs):+.3f} ({len(xs)})" if xs else "–"
            out.append(f"| {key} | {v} | {fa(a)} | {fa(b)} |")
    base = res.get("baseline")
    if base:
        sa = stats([t for t in base if t["closed_at"] < cut])
        sb = stats([t for t in base if t["created"] >= cut])
        out += ["", "## مرجع: شکار دامپ دور ۵ (dump_25_3r)", "", HEAD, row("آموزش", sa), row("آزمون", sb)]
    return "\n".join(out) + "\n"


def main(db: str, start: str, end: str, test_from: str, out: str, rep: str) -> None:
    s, e, cut = _ms(start), _ms(end), _ms(test_from)
    coins = load(db, s, e)
    fund = funding(db, coins)
    db_start = min(b[0][0] for b, _, _ in coins.values()) if coins else s
    print(f"{len(coins)} coins, funding for {len(fund)}")
    res = {"start": s, "end": e, "coins": len(coins), "variants": {}}
    cache = {}
    for vals in itertools.product(*GRID.values()):
        p = dict(zip(GRID, vals))
        if p["run"] not in cache:
            cache[p["run"]] = setups(coins, fund, p["run"], s, e, db_start)
        tr = simulate(coins, fund, cache[p["run"]], p)
        res["variants"][name_of(p)] = {"params": p, "trades": tr}
        print(f"{name_of(p)}: {len(tr)} trades, net {sum(t['net_r'] for t in tr):+.1f}R")
    try:
        from s3_pump import run
        res["baseline"] = run(coins, "dump", {"run_pct": 25, "target_r": 3}, s, e)
    except Exception as ex:                                      # noqa: BLE001  (the reference is optional)
        print("baseline skipped:", ex)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(res, f)
    with open(rep, "w", encoding="utf-8") as f:
        f.write(report(res, cut))


if __name__ == "__main__":
    main(*sys.argv[1:7])
