"""Price-based market regime: bull / bear / range (analysis only, 2026-10-22).

The live regime (layers/regime.py) comes from USDT.D, BTC.D and TOTAL2, which only have one
year of history and call ~68% of the time "neutral". This script builds a regime from prices
that exist for many years, compares a few definitions over that history, and labels the
backtest trades with each so we can see which one separates good trades from bad ones.

Market = BTC and an equal-weight basket of large alts (daily log returns averaged over the
coins that exist that day). The market is bull/bear only when BTC and the basket agree;
otherwise range. A state must hold `HOLD_DAYS` closes before the regime switches.

Definitions (each on daily closes, no look-ahead: the label of day d uses closes up to d-1):
  ma200      bull: close > MA200 and MA50 > MA200; bear: both below; else range
  weinstein  weekly close vs the 30-week MA and its 4-week slope (Stage 2 / Stage 4)
  trend_adx  close vs MA100 for the side, ADX(14) >= 20 for a trend; else range

    python scripts/price_regime.py --since 2019-01-01 --trades results/*.json
"""
from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from agent.backtest.report import CLOSED_TRADES
from agent.config import load_config
from agent.data.exchange import ExchangeClient, perp_symbol

DAY = 86_400_000
BASKET = ["ETH", "SOL", "XRP", "BNB", "ADA", "DOGE", "LINK", "AVAX", "DOT", "LTC", "BCH", "TRX"]
HOLD_DAYS = 3
FWD = 30
NAMES = ("bull", "range", "bear")


def daily(client, base: str, since: int) -> pd.Series:
    try:
        _, rows = client.fetch_history(perp_symbol(base), "1d", since)
    except Exception as e:
        print(f"{base}: {e}")
        return pd.Series(dtype=float)
    df = pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close", "volume"])
    return df.set_index("ts")[["high", "low", "close"]].astype(float)


def basket_index(frames: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Equal-weight index; high/low approximated from the average relative range."""
    rets = pd.DataFrame({b: np.log(f["close"]).diff() for b, f in frames.items()})
    rng_hi = pd.DataFrame({b: np.log(f["high"] / f["close"]) for b, f in frames.items()})
    rng_lo = pd.DataFrame({b: np.log(f["low"] / f["close"]) for b, f in frames.items()})
    close = np.exp(rets.mean(axis=1, skipna=True).fillna(0).cumsum()) * 100
    return pd.DataFrame({"close": close, "high": close * np.exp(rng_hi.mean(axis=1)),
                         "low": close * np.exp(rng_lo.mean(axis=1))}).sort_index()


def adx(df: pd.DataFrame, n: int = 14) -> pd.Series:
    up, dn = df["high"].diff(), -df["low"].diff()
    plus = np.where((up > dn) & (up > 0), up, 0.0)
    minus = np.where((dn > up) & (dn > 0), dn, 0.0)
    tr = pd.concat([df["high"] - df["low"], (df["high"] - df["close"].shift()).abs(),
                    (df["low"] - df["close"].shift()).abs()], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1 / n, adjust=False).mean()
    pdi = 100 * pd.Series(plus, index=df.index).ewm(alpha=1 / n, adjust=False).mean() / atr
    mdi = 100 * pd.Series(minus, index=df.index).ewm(alpha=1 / n, adjust=False).mean() / atr
    dx = 100 * (pdi - mdi).abs() / (pdi + mdi)
    return dx.ewm(alpha=1 / n, adjust=False).mean()


def raw_state(df: pd.DataFrame, how: str) -> pd.Series:
    c = df["close"]
    if how == "ma200":
        m50, m200 = c.rolling(50).mean(), c.rolling(200).mean()
        s = np.where((c > m200) & (m50 > m200), 1, np.where((c < m200) & (m50 < m200), -1, 0))
        ok = m200.notna()
    elif how == "weinstein":
        w = c.groupby(c.index // (7 * DAY)).last()
        ma = w.rolling(30).mean()
        slope = ma - ma.shift(4)
        ws = pd.Series(np.where((w > ma) & (slope > 0), 1, np.where((w < ma) & (slope < 0), -1, 0)),
                       index=w.index).where(ma.notna() & slope.notna())
        # a weekly state is known after the week closed: apply it from the next week
        s = (c.index // (7 * DAY) - 1).map(ws)
        return pd.Series(s, index=c.index)
    elif how == "trend_adx":
        m100, a = c.rolling(100).mean(), adx(df)
        s = np.where(a >= 20, np.where(c > m100, 1, -1), 0)
        ok = m100.notna()
    else:
        raise ValueError(how)
    return pd.Series(s, index=c.index).where(ok)


def market_regime(btc: pd.DataFrame, alts: pd.DataFrame, how: str) -> pd.Series:
    b, a = raw_state(btc, how), raw_state(alts, how).reindex(btc.index)
    both = pd.Series(np.where(b == a, b, 0), index=btc.index).where(b.notna() & a.notna())
    out, cur, pending, count = [], None, None, 0
    for v in both:
        if np.isnan(v):
            out.append(np.nan)
            continue
        if cur is None:
            cur = v
        elif v != cur:
            pending, count = (v, count + 1) if v == pending else (v, 1)
            if count >= HOLD_DAYS:
                cur, count = v, 0
        else:
            count = 0
        out.append(cur)
    # label day d with the state known at the close of d-1 (no look-ahead)
    return pd.Series(out, index=btc.index).shift(1)


def name(v) -> str | None:
    return None if v is None or (isinstance(v, float) and np.isnan(v)) else NAMES[1 - int(v)]


def describe(reg: pd.Series, btc: pd.DataFrame, alts: pd.DataFrame, title: str) -> list[str]:
    r = reg.dropna()
    lab = r.map(name)
    days = len(r)
    switches = int((r != r.shift()).sum()) - 1
    runs = (r != r.shift()).cumsum()
    run_len = r.groupby(runs).size()
    fwd_b = np.log(btc["close"].shift(-FWD) / btc["close"]).reindex(r.index)
    fwd_a = np.log(alts["close"].shift(-FWD) / alts["close"]).reindex(r.index)
    out = [f"\n### {title}\n",
           f"{days} days from {_d(r.index[0])}, {switches} switches ({switches / days * 365:.1f} a year), "
           f"median spell {run_len.median():.0f} days.\n",
           f"| Regime | Share of time | BTC next {FWD}d (avg) | Alts next {FWD}d (avg) | Alts up after {FWD}d |",
           "|---|---|---|---|---|"]
    for n in NAMES:
        m = lab == n
        if not m.any():
            continue
        fa = fwd_a[m].dropna()
        out.append(f"| {n} | {m.mean() * 100:.0f}% | {fwd_b[m].mean() * 100:+.1f}% | "
                   f"{fa.mean() * 100:+.1f}% | {(fa > 0).mean() * 100:.0f}% |")
    by_year = Counter()
    for t, n in lab.items():
        by_year[(_d(t)[:4], n)] += 1
    years = sorted({y for y, _ in by_year})
    out += ["", "| Year | " + " | ".join(NAMES) + " |", "|---|---|---|---|"]
    for y in years:
        tot = sum(by_year[(y, n)] for n in NAMES) or 1
        out.append(f"| {y} | " + " | ".join(f"{by_year[(y, n)] / tot * 100:.0f}%" for n in NAMES) + " |")
    return out


def _d(ms) -> str:
    return datetime.fromtimestamp(int(ms) / 1000, tz=timezone.utc).strftime("%Y-%m-%d")


def label_trades(paths: list[str], regimes: dict[str, pd.Series]) -> list[str]:
    out = ["\n## Backtest trades by price regime\n",
           "Closed trades, net R. Rows: definition × regime × side.\n"]
    for p in paths:
        res = json.load(open(p))
        tr = [t for t in res["trades"] if t["status"] in CLOSED_TRADES]
        out += [f"\n### `{res['variant']}` ({_d(res['meta']['start'])} → {_d(res['meta']['end'])}, "
                f"{len(tr)} trades, {sum(t['net_r'] for t in tr):+.1f}R)\n",
                "| Definition | Regime | Long: n / win / R | Short: n / win / R |", "|---|---|---|---|"]
        for how, reg in regimes.items():
            g = defaultdict(list)
            for t in tr:
                day = t["created"] // DAY * DAY
                n = name(reg.get(day, np.nan))
                g[(n, t["side"])].append(t["net_r"])
            for n in NAMES:
                cells = []
                for side in (1, -1):
                    v = g.get((n, side), [])
                    cells.append(f"{len(v)} / {sum(x > 0 for x in v) / len(v) * 100:.0f}% / {sum(v):+.1f}"
                                 if v else "0")
                out.append(f"| {how} | {n} | {cells[0]} | {cells[1]} |")
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default="2019-01-01")
    ap.add_argument("--trades", nargs="*", default=[])
    a = ap.parse_args()
    since = int(datetime.fromisoformat(a.since + "T00:00:00+00:00").timestamp() * 1000)
    client = ExchangeClient(load_config())
    btc = daily(client, "BTC", since)
    alts = basket_index({b: f for b in BASKET if len(f := daily(client, b, since))})
    alts = alts.reindex(btc.index).ffill()
    print(f"BTC {len(btc)} days from {_d(btc.index[0])}; basket from {_d(alts.dropna().index[0])}")
    lines = ["# Price regime: bull / bear / range", "",
             f"Market = BTC and an equal-weight basket of {', '.join(BASKET)}. Bull or bear only when "
             f"both agree; a new state must hold {HOLD_DAYS} daily closes. 'Next {FWD}d' is the log return "
             f"over the following {FWD} days from each day in that regime."]
    regimes = {}
    for how in ("ma200", "weinstein", "trend_adx"):
        regimes[how] = market_regime(btc, alts, how)
        lines += describe(regimes[how], btc, alts, how)
    cur = {how: name(r.dropna().iloc[-1]) for how, r in regimes.items()}
    lines += ["", "**Today:** " + ", ".join(f"{k} = {v}" for k, v in cur.items()) + "."]
    lines += label_trades(a.trades, regimes)
    text = "\n".join(lines)
    print(text)
    open("PRICE_REGIME.md", "w").write(text)
    pd.DataFrame({k: v.map(name) for k, v in regimes.items()}).to_csv("price_regime.csv")


if __name__ == "__main__":
    main()
