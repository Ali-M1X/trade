"""A synthetic backtest database: consistent 1h/4h/1d/1w candles, CoinGecko-like daily
history, dominance and universe state."""
import numpy as np
import pandas as pd

from agent.data.dominance import reconstruct
from agent.store.repository import Repository

HOUR = 3_600_000
DAY = 24 * HOUR


def hourly_path(n, seed, drift=0.0, vol=0.006, start=100.0):
    rng = np.random.default_rng(seed)
    # regime-switching drift so trends, pullbacks and ranges all occur
    blocks = np.repeat(rng.choice([-1.0, 0.0, 1.0], size=n // 240 + 1), 240)[:n]
    rets = rng.normal(drift + blocks * vol * 0.12, vol, n)
    return start * np.exp(np.cumsum(rets))


def candles_from_hourly(closes, t0, seed):
    rng = np.random.default_rng(seed + 1000)
    opens = np.r_[closes[0], closes[:-1]]
    wick = np.abs(rng.normal(0, 0.003, len(closes))) * closes
    df = pd.DataFrame({"ts": t0 + np.arange(len(closes)) * HOUR, "open": opens,
                       "high": np.maximum(opens, closes) + wick,
                       "low": np.minimum(opens, closes) - wick, "close": closes,
                       "volume": rng.uniform(800, 1200, len(closes)) * (1 + 3 * (rng.random(len(closes)) > 0.98))})
    return df


def resample(df, rule):
    x = df.set_index(pd.to_datetime(df["ts"], unit="ms"))
    o = x.resample(rule, label="left", closed="left").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}).dropna()
    o = o.reset_index(names="dt")
    o.insert(0, "ts", o.pop("dt").astype("datetime64[ms]").astype("int64"))
    return o


def build(days=200, coins=("SOL", "ADA", "LINK", "DOT"), seed=7, path=":memory:"):
    repo = Repository(path)
    end = 1_760_000_000_000 // DAY * DAY
    t0 = end - days * DAY
    n = days * 24
    bases = ["BTC", "ETH", *coins]
    rows, charts = [], {}
    for i, base in enumerate(bases):
        closes = hourly_path(n, seed + i, drift=0.00004 * (i % 3 - 1), start=[60000, 3000, 150, 0.5, 20, 7][i % 6])
        h1 = candles_from_hourly(closes, t0, seed + i)
        sym = f"{base}/USDT:USDT"
        for tf, rule in (("1h", None), ("4h", "4h"), ("1d", "1D"), ("1w", "7D")):
            df = h1 if rule is None else resample(h1, rule)
            repo.upsert_candles("okx", sym, tf, df[["ts", "open", "high", "low", "close", "volume"]].values.tolist())
        # funding for the last 60 days only, as exchanges keep little history
        fts = np.arange(end - 60 * DAY, end, 8 * HOUR)
        repo.upsert_funding("okx", sym, [(int(t), 0.0001) for t in fts])
        cid = base.lower()
        d = resample(h1, "1D")
        supply = 1e7 / (i + 1)
        hist = [(int(t), float(c) * supply, float(c) * supply * 0.05) for t, c in zip(d["ts"], d["close"])]
        repo.upsert_coin_history(cid, hist)
        charts[cid] = [[t, m] for t, m, _ in hist]
        rows.append({"id": cid, "symbol": cid, "market_cap": hist[-1][1], "total_volume": hist[-1][2]})
    # a stablecoin for USDT.D
    charts["tether"] = [[t, 1.5e11] for t, _ in charts["btc"]]
    for r in reconstruct({**charts, "bitcoin": charts["btc"], "ethereum": charts["eth"]}).itertuples():
        repo.add_dominance(int(r.ts), "recon", r.total_mcap, r.btc_mcap, r.eth_mcap, r.usdt_mcap)
    repo.set_state("bt_universe", rows)
    repo.set_state("bt_categories", {r["id"]: ["Layer 1"] if i % 2 else ["AI"] for i, r in enumerate(rows)})
    repo.set_state("bt_meta", {"start": t0, "end": end, "exchange": "okx"})
    return repo
