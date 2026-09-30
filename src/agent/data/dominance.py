"""USDT.D, BTC.D and TOTAL2 built from CoinGecko market caps."""
from __future__ import annotations

import pandas as pd


def snapshot_global(cg, repo, ts_ms: int) -> dict:
    """Store one /global snapshot. Market caps in USD are derived from the dominance %."""
    g = cg.global_data()
    total = float(g["total_market_cap"]["usd"])
    pct = g["market_cap_percentage"]
    row = {
        "total_mcap": total,
        "btc_mcap": total * pct.get("btc", 0.0) / 100,
        "eth_mcap": total * pct.get("eth", 0.0) / 100,
        "usdt_mcap": total * pct.get("usdt", 0.0) / 100,
    }
    repo.add_dominance(ts_ms, "global", **row)
    return row


def dominance_series(df: pd.DataFrame) -> pd.DataFrame:
    """From a dominance table to the three indices the regime layer reads."""
    out = pd.DataFrame({"ts": df["ts"]})
    out["usdt_d"] = df["usdt_mcap"] / df["total_mcap"] * 100
    out["btc_d"] = df["btc_mcap"] / df["total_mcap"] * 100
    out["total2"] = df["total_mcap"] - df["btc_mcap"]
    return out.reset_index(drop=True)
