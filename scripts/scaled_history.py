"""Short-timeframe history for system 3 (2026-11-06, Ali): the engine's timeframes, scaled.

The backtest engine reasons in 1w / 1d / 4h / 1h candles and steps hour by hour. System 3
runs the same logic 12 times faster: its "1h" is a real 5m candle, "4h" a real 20m candle,
"1d" a real 2h candle and "1w" a real 14h candle. Rather than rewriting every timeframe
in the engine, this script builds a backtest database whose clock runs 12 times slower:
real 5m candles are stamped 1h apart from SCALED_T0 on, and the longer timeframes are
resampled from them. A replay of that database is a replay of system 3 on real 5m data;
scaled dates map back with `real_ms`.

Usage:
  python scripts/scaled_history.py fetch --coins 50 --days 365 --shard I/N --db part.db
  python scripts/scaled_history.py build --out data/backtest.db part0.db part1.db ...
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agent.config import load_config, load_secrets  # noqa: E402
from agent.data.exchange import ExchangeClient, perp_symbol  # noqa: E402
from agent.store.repository import Repository  # noqa: E402

log = logging.getLogger("scaled")
SCALE = 12
REAL_STEP = 5 * 60_000                       # one real 5m candle ...
SCALED_T0 = 946_857_600_000                  # ... becomes one hour from Mon 2000-01-03 00:00 UTC
DAY = 86_400_000
RESAMPLE = {"4h": "4h", "1d": "1D", "1w": "W-MON"}


def real_ms(scaled_ms: int, real_start: int) -> int:
    return real_start + (scaled_ms - SCALED_T0) // SCALE


def universe(cfg: dict, client, n: int) -> list[dict]:
    """The n coins with the largest 24h quote volume among OKX USDT perpetuals, plus BTC,
    ETH and the price-regime basket. Stablecoins and wrapped tokens are left out."""
    name, tickers = client.call("fetch_tickers")
    ex = client.exchange(name)
    skip = set(cfg["universe"]["exclude_symbols"])
    vols = {}
    for sym, t in tickers.items():
        m = ex.markets.get(sym)
        if not m or not m.get("swap") or not m.get("linear") or m.get("quote") != "USDT":
            continue
        if m["base"].lower() in skip:
            continue
        vols[m["base"]] = float(t.get("quoteVolume") or 0)
    ranked = sorted(vols, key=vols.get, reverse=True)
    keep = ranked[:n]
    for b in ["BTC", "ETH", *cfg["regime"]["price"]["basket"]]:
        if b not in keep and b in vols:
            keep.append(b)
    return [{"id": b.lower(), "symbol": b.lower(), "name": b, "u_rank": i,
             "circulating_supply": None} for i, b in enumerate(keep)]


def fetch(args) -> None:
    cfg = load_config()
    cfg["exchange"]["primary"], cfg["exchange"]["fallbacks"] = "okx", []
    cfg["exchange"]["ohlcv_limit"] = 300
    client = ExchangeClient(cfg)
    rows = universe(cfg, client, args.coins)
    i, n = (int(x) for x in args.shard.split("/"))
    now = int(time.time() * 1000) // REAL_STEP * REAL_STEP
    since = now - args.days * DAY
    repo = Repository(args.db)
    repo.set_state("s3_universe", rows)
    repo.set_state("s3_meta", {"real_start": since, "real_end": now})
    for k, r in enumerate(rows):
        if k % n != i:
            continue
        symbol = perp_symbol(r["symbol"].upper(), "USDT")
        t0 = time.time()
        try:
            candles = client.fetch_ohlcv_range("okx", symbol, "5m", since, now, skip_gaps=True)
        except Exception as e:
            log.warning("%s: %s", symbol, e)
            continue
        repo.upsert_candles("okx", symbol, "5m", candles)
        log.info("[%d/%d] %s: %d candles in %.0fs", k + 1, len(rows), symbol, len(candles),
                 time.time() - t0)
    repo.close()


def scale(df: pd.DataFrame, real_start: int) -> pd.DataFrame:
    out = df.copy()
    out["ts"] = SCALED_T0 + (out["ts"] - real_start) // REAL_STEP * 3_600_000
    return out


def resample(h1: pd.DataFrame, rule: str) -> pd.DataFrame:
    x = h1.set_index(pd.to_datetime(h1["ts"], unit="ms", utc=True))
    agg = x.resample(rule, label="left", closed="left").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}).dropna()
    agg["ts"] = (agg.index.view("int64") // 1_000_000).astype("int64")
    return agg.reset_index(drop=True)[["ts", "open", "high", "low", "close", "volume"]]


def build(args) -> None:
    out = Repository(args.out)
    meta = rows = None
    for p in args.parts:
        part = Repository(p)
        meta = meta or part.get_state("s3_meta")
        rows = rows or part.get_state("s3_universe")
        for ex, symbol in part.candle_series("5m"):
            df = part.get_candles(ex, symbol, "5m")
            if len(df) < 1000:
                continue
            h1 = scale(df, meta["real_start"])
            frames = {"1h": h1, **{tf: resample(h1, rule) for tf, rule in RESAMPLE.items()}}
            for tf, f in frames.items():
                out.upsert_candles(ex, symbol, tf, f[["ts", "open", "high", "low", "close", "volume"]]
                                   .values.tolist())
            d = frames["1d"]
            # synthetic daily market row (as fetch_long_history): no supply, so no market cap
            base = symbol.split("/")[0].lower()
            out.upsert_coin_history(base, [(int(t), None, float(c) * float(v))
                                           for t, c, v in zip(d["ts"], d["close"], d["volume"])])
            log.info("%s: %d scaled hours, %d scaled days", symbol, len(h1), len(d))
        part.close()
    have = {s.split("/")[0].lower() for _, s in out.candle_series("1d")}
    rows = [r for r in rows if r["id"] in have]
    out.set_state("bt_universe", rows)
    out.set_state("bt_categories", {})
    start = SCALED_T0
    end = SCALED_T0 + (meta["real_end"] - meta["real_start"]) // REAL_STEP * 3_600_000
    out.set_state("bt_meta", {"start": start, "end": end, "exchange": "okx", "long": True,
                              "scale": SCALE, "real_start": meta["real_start"]})
    Path(args.out).with_name("s3_meta.json").write_text(json.dumps({"real_start": meta["real_start"], "real_end": meta["real_end"], "scaled_t0": SCALED_T0, "scale": SCALE, "coins": [r["symbol"] for r in rows]}))
    print(f"coins: {len(rows)}; scaled {pd.to_datetime(start, unit='ms')} -> "
          f"{pd.to_datetime(end, unit='ms')}")
    out.close()


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("fetch")
    f.add_argument("--coins", type=int, default=50)
    f.add_argument("--days", type=int, default=365)
    f.add_argument("--shard", default="0/1")
    f.add_argument("--db", required=True)
    b = sub.add_parser("build")
    b.add_argument("--out", required=True)
    b.add_argument("parts", nargs="+")
    args = p.parse_args()
    {"fetch": fetch, "build": build}[args.cmd](args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
