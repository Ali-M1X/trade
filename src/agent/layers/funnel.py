"""Layers 1-5 in one pass: regime -> majors -> scanners -> pairs -> shortlist.

Pure function of already-loaded data, so live runs and the backtest share it.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from ..analysis.frame import Frame
from .majors import Majors, compute_majors
from .pairs import PairsResult, analyze_pairs
from .regime import Regime, compute_regime
from .scanners import (CoinData, filter_by_bias, hot_categories, liquid, rs_thresholds,
                       scan_coin)
from .shortlist import Candidate, label_sides, liquidity_points, score_candidate, select


@dataclass
class FunnelResult:
    regime: Regime
    majors: Majors
    watchlist: dict[str, list[str]] = field(default_factory=dict)     # base -> labels
    pairs: dict[str, PairsResult] = field(default_factory=dict)
    candidates: list[Candidate] = field(default_factory=list)
    shortlist: list[Candidate] = field(default_factory=list)
    hot: set[str] = field(default_factory=set)

    def to_dict(self) -> dict:
        return {
            "regime": self.regime.to_dict(),
            "majors": self.majors.to_dict(),
            "watchlist": self.watchlist,
            "hot_categories": sorted(self.hot),
            "shortlist": [{"base": c.base, "side": c.side, "score": round(c.score, 2),
                           "labels": c.labels, "parts": {k: round(v, 2) for k, v in c.parts.items()},
                           "pairs": self.pairs[c.base].to_dict()} for c in self.shortlist],
        }


def run_funnel(dominance: pd.DataFrame, major_frames: dict[str, dict[str, Frame]],
               coins: list[CoinData], markets: list[dict], coin_categories: dict[str, list[str]],
               cfg: dict) -> FunnelResult:
    """dominance: ts, usdt_d, btc_d, total2 snapshots. major_frames: BTC/ETH/ETHBTC frames."""
    regime = compute_regime(dominance, cfg)
    majors = compute_majors(major_frames, regime.total2, cfg)
    btc_d = major_frames["BTC"]["1d"]
    btc_dir = btc_d.direction
    total2_d = (dominance.assign(close=dominance["total2"])[["ts", "close"]]
                .pipe(_daily_close))
    rs_cut = rs_thresholds(coins, btc_d, cfg)
    hot = hot_categories(markets, coin_categories, cfg)
    out = FunnelResult(regime, majors, hot=hot)
    for coin in coins:
        if not liquid(coin.info, cfg):
            continue
        labels = filter_by_bias(scan_coin(coin, btc_d, rs_cut, hot, cfg), regime.bias)
        if not labels:
            continue
        out.watchlist[coin.base] = [l.name for l in labels]
        pairs = analyze_pairs(coin, btc_d.df, total2_d, cfg)
        out.pairs[coin.base] = pairs
        liq = liquidity_points(coin.info.get("total_volume") or 0, coin.spread_pct, cfg)
        for side in sorted(label_sides(labels)):
            if not regime.allows(side) or not pairs.allowed(side, btc_dir, cfg):
                continue
            out.candidates.append(score_candidate(coin.base, side, coin.is_btc, labels, pairs,
                                                  liq, regime, majors, cfg))
    out.shortlist = select(out.candidates, cfg)
    return out


def _daily_close(df: pd.DataFrame) -> pd.DataFrame:
    """Last value per UTC day, with ts at the day's start (matches exchange D candles)."""
    day = (df["ts"] // 86_400_000) * 86_400_000
    return df.assign(ts=day).groupby("ts", as_index=False)["close"].last()
