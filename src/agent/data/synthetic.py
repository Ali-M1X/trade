"""Synthetic cross pairs (COIN/BTC, COIN/ETH) from two USDT candle series.

Each field is divided separately (open/open, high/high, ...), as TradingView does for
spread symbols; high/low are then widened to contain open and close. Volume is the
numerator's volume. Only timestamps present in both series are kept.
"""
from __future__ import annotations

import pandas as pd


def ratio_candles(num: pd.DataFrame, den: pd.DataFrame) -> pd.DataFrame:
    cols = ["ts", "open", "high", "low", "close"]
    m = num[cols + ["volume"]].merge(den[cols], on="ts", suffixes=("", "_d"))
    out = pd.DataFrame({"ts": m["ts"]})
    for c in ("open", "high", "low", "close"):
        out[c] = m[c] / m[f"{c}_d"]
    out["high"] = out[["open", "high", "low", "close"]].max(axis=1)
    out["low"] = out[["open", "high", "low", "close"]].min(axis=1)
    out["volume"] = m["volume"]
    return out.reset_index(drop=True)
