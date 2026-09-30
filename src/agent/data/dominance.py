"""USDT.D, BTC.D and TOTAL2 built from CoinGecko market caps.

Going forward: a /global snapshot every 4H (source "global").
History: reconstructed daily from the top coins' market-cap history (source "recon"),
scaled at the join so the two sources form one continuous series.
"""
from __future__ import annotations

import logging

import numpy as np
import pandas as pd

log = logging.getLogger(__name__)
DAY = 86_400_000


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


def reconstruct(charts: dict[str, list], btc_id="bitcoin", eth_id="ethereum",
                usdt_id="tether") -> pd.DataFrame:
    """charts: coin id -> CoinGecko market_caps [[ts, cap], ...] (daily).
    Total = sum of all given coins per day."""
    frames = []
    for cid, caps in charts.items():
        if not caps:
            continue
        df = pd.DataFrame(caps, columns=["ts", cid])
        df["ts"] = (df["ts"] // DAY) * DAY
        frames.append(df.groupby("ts").last())
    if not frames:
        return pd.DataFrame(columns=["ts", "total_mcap", "btc_mcap", "eth_mcap", "usdt_mcap"])
    wide = pd.concat(frames, axis=1).sort_index()
    wide = wide.dropna(subset=[c for c in (btc_id, usdt_id) if c in wide])
    out = pd.DataFrame({
        "ts": wide.index.astype("int64"),
        "total_mcap": wide.sum(axis=1, skipna=True),
        "btc_mcap": wide.get(btc_id, 0.0),
        "eth_mcap": wide.get(eth_id, 0.0),
        "usdt_mcap": wide.get(usdt_id, 0.0),
    })
    return out.reset_index(drop=True)


def backfill(cg, repo, coin_ids: list[str], days: int) -> int:
    """Rebuild daily history from market caps (one CoinGecko call per coin)."""
    charts = {}
    for cid in coin_ids:
        try:
            charts[cid] = cg.market_chart(cid, days)["market_caps"]
        except Exception as e:                      # one missing coin should not stop it
            log.warning("market_chart %s failed: %s", cid, e)
    rec = reconstruct(charts)
    for r in rec.itertuples():
        repo.add_dominance(int(r.ts), "recon", r.total_mcap, r.btc_mcap, r.eth_mcap, r.usdt_mcap)
    return len(rec)


COLS = ["total_mcap", "btc_mcap", "eth_mcap", "usdt_mcap"]


def merged_history(repo) -> pd.DataFrame:
    """Global snapshots, preceded by reconstructed days scaled to match at the join."""
    glob = repo.get_dominance("global")
    rec = repo.get_dominance("recon")
    if rec.empty:
        return glob
    if glob.empty:
        return rec
    first = glob.iloc[0]
    before = rec[rec["ts"] <= first["ts"]]
    if before.empty:
        return glob
    anchor = before.iloc[-1]
    scaled = before.copy()
    for c in COLS:
        scaled[c] = scaled[c] * (first[c] / anchor[c] if anchor[c] else np.nan)
    scaled = scaled[scaled["ts"] < first["ts"]]
    return pd.concat([scaled, glob], ignore_index=True)


def dominance_series(df: pd.DataFrame) -> pd.DataFrame:
    """From a dominance table to the three indices the regime layer reads."""
    out = pd.DataFrame({"ts": df["ts"]})
    out["usdt_d"] = df["usdt_mcap"] / df["total_mcap"] * 100
    out["btc_d"] = df["btc_mcap"] / df["total_mcap"] * 100
    out["total2"] = df["total_mcap"] - df["btc_mcap"]
    if "source" in df:
        out["source"] = df["source"].to_numpy()
    return out.reset_index(drop=True)
