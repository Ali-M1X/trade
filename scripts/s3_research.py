"""System 3 research (2026-11-07): well-known short-timeframe strategies on real 5m data.

Reads the scaled backtest database (scripts/scaled_history.py: its "1h" candles are real 5m
candles) and tests published intraday systems on real 15m bars, each with a fixed grid of
textbook parameters. The year is split by real time: the first 8 months choose, the last 4
months are only reported (out of sample). Costs per round trip: taker 0.20% (fees 0.05% +
slippage 0.05% each side) and mixed 0.14% (limit entry, market exit).

Strategies:
- donchian:  Turtle-style channel breakout with trend filter, ATR stop, half-channel exit
- rsi2:      Connors RSI(2) pullback in the direction of the 200-bar average
- flush:     long after a liquidation flush (big range, high volume, close near the low)
- xsmom:     cross-sectional momentum: every 4h long the top 5 / short the bottom 5 by 24h return
- xsrev:     the same, reversed (short-term reversal)

    python scripts/s3_research.py --db data/backtest.db --meta data/s3_meta.json
"""
from __future__ import annotations

import argparse
import itertools
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from agent.store.repository import Repository  # noqa: E402

COSTS = {"taker": 0.0020, "mixed": 0.0014}
BAR = 3                      # real 5m candles per 15m bar
TRAIN_FRAC = 8 / 12


def load(db: str, meta: dict) -> dict[str, pd.DataFrame]:
    repo = Repository(db)
    out = {}
    for ex, sym in repo.candle_series("1h"):
        df = repo.get_candles(ex, sym, "1h")
        if len(df) < 10_000:
            continue
        df["ts"] = meta["real_start"] + (df["ts"] - meta["scaled_t0"]) // meta["scale"]
        g = df.groupby(df["ts"] // (BAR * 300_000))
        b = pd.DataFrame({"ts": g["ts"].first(), "open": g["open"].first(), "high": g["high"].max(),
                          "low": g["low"].min(), "close": g["close"].last(), "volume": g["volume"].sum()})
        out[sym.split("/")[0]] = b.reset_index(drop=True)
    repo.close()
    return out


def atr(d: pd.DataFrame, n: int = 14) -> np.ndarray:
    pc = d["close"].shift()
    tr = pd.concat([d["high"] - d["low"], (d["high"] - pc).abs(), (d["low"] - pc).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / n, adjust=False).mean().to_numpy()


def rsi(c: pd.Series, n: int) -> np.ndarray:
    d = c.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    return (100 - 100 / (1 + up / dn.replace(0, np.nan))).fillna(50).to_numpy()


# ------------------------------------------------------------------ single-coin trade systems
def run_trades(d, entries, exit_fn, max_bars):
    """entries: list of (i, side, stop). Enter at close[i]; one trade at a time per coin.
    exit_fn(j, side, entry) -> exit price at bar j's close or None. Stop checked first (on
    the bar's range), then the rule exit, then the time stop. Returns (ts, side, R, ret)."""
    h, l, c, ts = (d[k].to_numpy() for k in ("high", "low", "close", "ts"))
    out, busy = [], -1
    for i, side, stop in entries:
        if i <= busy or i + 1 >= len(c):
            continue
        e, risk = c[i], abs(c[i] - stop)
        if risk <= 0 or risk / e < 0.002:
            continue
        x, j = None, i
        for j in range(i + 1, min(len(c), i + 1 + max_bars)):
            if (l[j] <= stop) if side == 1 else (h[j] >= stop):
                x = stop
                break
            x = exit_fn(j, side, e)
            if x is not None:
                break
        if x is None:
            x = c[j]
        busy = j
        out.append((ts[i], side, (x - e) * side / risk, (x - e) * side / e, risk / e))
    return out


def donchian(d, n, k, trend):
    c, a = d["close"], atr(d)
    hi, lo = d["high"].rolling(n).max().shift(), d["low"].rolling(n).min().shift()
    xh, xl = d["high"].rolling(n // 2).max().shift(), d["low"].rolling(n // 2).min().shift()
    ema = c.ewm(span=trend, adjust=False).mean()
    cv, hv, lv, ev = c.to_numpy(), hi.to_numpy(), lo.to_numpy(), ema.to_numpy()
    ent = []
    for i in range(trend, len(cv)):
        if cv[i] > hv[i] and cv[i - 1] <= hv[i - 1] and cv[i] > ev[i]:
            ent.append((i, 1, cv[i] - k * a[i]))
        elif cv[i] < lv[i] and cv[i - 1] >= lv[i - 1] and cv[i] < ev[i]:
            ent.append((i, -1, cv[i] + k * a[i]))
    xhv, xlv = xh.to_numpy(), xl.to_numpy()
    return run_trades(d, ent, lambda j, s, e: cv[j] if (s == 1 and cv[j] < xlv[j]) or
                      (s == -1 and cv[j] > xhv[j]) else None, 10_000)


def rsi2(d, th, trend, hold):
    c, a = d["close"], atr(d)
    r, sma, s5 = rsi(c, 2), c.rolling(trend).mean().to_numpy(), c.rolling(5).mean().to_numpy()
    cv = c.to_numpy()
    ent = []
    for i in range(trend, len(cv)):
        if cv[i] > sma[i] and r[i] < th:
            ent.append((i, 1, cv[i] - 3 * a[i]))
        elif cv[i] < sma[i] and r[i] > 100 - th:
            ent.append((i, -1, cv[i] + 3 * a[i]))
    return run_trades(d, ent, lambda j, s, e: cv[j] if (cv[j] - s5[j]) * s > 0 else None, hold)


def flush(d, rng_k, vol_k, hold):
    c, a = d["close"].to_numpy(), atr(d)
    h, l, v = d["high"].to_numpy(), d["low"].to_numpy(), d["volume"]
    vmed = v.rolling(96).median().to_numpy()
    v = v.to_numpy()
    ent = []
    for i in range(100, len(c)):
        rg = h[i] - l[i]
        if rg > rng_k * a[i - 1] and v[i] > vol_k * vmed[i] and (c[i] - l[i]) < 0.35 * rg:
            ent.append((i, 1, l[i] - 0.5 * a[i]))
    return run_trades(d, ent, lambda j, s, e: None, hold)


# ------------------------------------------------------------------ cross-sectional
def xsection(bars, look, hold, n, sign):
    closes = pd.DataFrame({b: d.set_index("ts")["close"] for b, d in bars.items()}).sort_index()
    past = closes / closes.shift(look) - 1
    fwd = closes.shift(-hold) / closes - 1
    out = []
    for i in range(look, len(closes) - hold, hold):
        p, f = past.iloc[i].dropna(), fwd.iloc[i]
        if len(p) < 2 * n + 5:
            continue
        r = p.sort_values()
        longs, shorts = (r.index[-n:], r.index[:n]) if sign > 0 else (r.index[:n], r.index[-n:])
        ret = (f[longs].mean() - f[shorts].mean()) / 2
        if not np.isnan(ret):
            out.append((int(closes.index[i]), 0, np.nan, ret, np.nan))
    return out


# ------------------------------------------------------------------ evaluation
def summarize(trades, cut, cost, cross=False):
    rows = {}
    for name, part in (("train", [t for t in trades if t[0] < cut]), ("test", [t for t in trades if t[0] >= cut])):
        if not part:
            rows[name] = None
            continue
        if cross:           # a rebalance turns the whole book over: 2 round trips per side pair
            net = np.array([t[3] - cost for t in part])
            rows[name] = {"n": len(part), "win": float((net > 0).mean()), "ret": float(net.sum() * 100),
                          "avg": float(net.mean() * 100), "pf": _pf(net), "r": None}
        else:
            net_r = np.array([t[2] - cost / t[4] for t in part])
            rows[name] = {"n": len(part), "win": float((net_r > 0).mean()), "r": float(net_r.sum()),
                          "avg": float(net_r.mean()), "pf": _pf(net_r), "ret": None}
    return rows


def _pf(x):
    g, l_ = x[x > 0].sum(), -x[x < 0].sum()
    return float(g / l_) if l_ > 0 else float("inf")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--meta", required=True)
    ap.add_argument("--out", default="S3_RESEARCH.md")
    a = ap.parse_args()
    meta = json.load(open(a.meta))
    bars = load(a.db, meta)
    t0 = min(int(d["ts"].iloc[0]) for d in bars.values())
    t1 = max(int(d["ts"].iloc[-1]) for d in bars.values())
    cut = t0 + int((t1 - t0) * TRAIN_FRAC)
    grid = []
    for n, k, tr in itertools.product([48, 96], [2.0, 3.0], [200, 400]):
        grid.append((f"donchian n={n} k={k} ema={tr}", lambda d, n=n, k=k, tr=tr: donchian(d, n, k, tr), False))
    for th, tr, hold in itertools.product([5, 10], [200, 400], [16, 48]):
        grid.append((f"rsi2 th={th} sma={tr} hold={hold}", lambda d, th=th, tr=tr, hold=hold: rsi2(d, th, tr, hold), False))
    for rk, vk, hold in itertools.product([3.0, 4.0], [3.0, 5.0], [4, 16]):
        grid.append((f"flush range={rk} vol={vk} hold={hold}", lambda d, rk=rk, vk=vk, hold=hold: flush(d, rk, vk, hold), False))
    lines = [f"# System 3 research: {len(bars)} coins, real 15m bars", "",
             f"Train: {pd.to_datetime(t0, unit='ms'):%Y-%m-%d} to {pd.to_datetime(cut, unit='ms'):%Y-%m-%d}; "
             f"test (out of sample): to {pd.to_datetime(t1, unit='ms'):%Y-%m-%d}.", "",
             "| Strategy | Costs | Train trades | Train win | Train total | Train PF | Test trades | Test win | Test total | Test PF |",
             "|---|---|---|---|---|---|---|---|---|---|"]
    results = []
    for name, fn, _ in grid:
        trades = [t for d in bars.values() for t in fn(d)]
        for cname, cost in COSTS.items():
            s = summarize(trades, cut, cost)
            results.append((name, cname, s, "R"))
        print(name, len(trades), flush=True)
    for look, hold, sign in itertools.product([16, 96], [16, 96], [1, -1]):
        name = f"{'xsmom' if sign > 0 else 'xsrev'} look={look} hold={hold}"
        trades = xsection(bars, look, hold, 5, sign)
        for cname, cost in COSTS.items():
            results.append((name, cname, summarize(trades, cut, cost, cross=True), "%"))
        print(name, len(trades), flush=True)
    for name, cname, s, unit in results:
        tr, te = s["train"], s["test"]
        cell = lambda x: "– | – | – | –" if x is None else (
            f"{x['n']} | {x['win'] * 100:.0f}% | {(x['r'] if unit == 'R' else x['ret']):+.1f}{unit} | {x['pf']:.2f}")
        lines.append(f"| {name} | {cname} | {cell(tr)} | {cell(te)} |")
    Path(a.out).write_text("\n".join(lines) + "\n")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
