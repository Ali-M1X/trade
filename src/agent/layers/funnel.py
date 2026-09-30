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
from .scanners import (LONG, CoinData, Label, breakout_level, filter_by_bias, hot_categories,
                       liquid, rs_thresholds, scan_coin)
from .shortlist import Candidate, label_sides, liquidity_points, score_candidate, select
from .trade import collect_levels, flip_level, nearest_level_atr

DAY = 86_400_000


@dataclass
class FunnelResult:
    regime: Regime
    majors: Majors
    watchlist: dict[str, list[str]] = field(default_factory=dict)     # base -> labels
    pairs: dict[str, PairsResult] = field(default_factory=dict)
    candidates: list[Candidate] = field(default_factory=list)
    shortlist: list[Candidate] = field(default_factory=list)
    hot: set[str] = field(default_factory=set)
    breakout_watch: dict = field(default_factory=dict)   # base -> {since, level, labels}

    def to_dict(self) -> dict:
        return {
            "regime": self.regime.to_dict(),
            "majors": self.majors.to_dict(),
            "watchlist": self.watchlist,
            "hot_categories": sorted(self.hot),
            "breakout_watch": self.breakout_watch,
            "shortlist": [{"base": c.base, "side": c.side, "score": round(c.score, 2),
                           "labels": c.labels, "parts": {k: round(v, 2) for k, v in c.parts.items()},
                           "level_atr": None if c.level_atr is None else round(c.level_atr, 2),
                           "flip_level": c.flip_level,
                           "pairs": self.pairs[c.base].to_dict()} for c in self.shortlist],
        }


def run_funnel(dominance: pd.DataFrame, major_frames: dict[str, dict[str, Frame]],
               coins: list[CoinData], markets: list[dict], coin_categories: dict[str, list[str]],
               cfg: dict, hot: set[str] | None = None, breakout_watch: dict | None = None,
               now_ms: int | None = None) -> FunnelResult:
    """dominance: ts, usdt_d, btc_d, total2 snapshots. major_frames: BTC/ETH/ETHBTC frames.
    hot: precomputed hot categories (default: ranked from `markets`).
    breakout_watch: the previous run's watch; coins stay on it for breakout_watch.days."""
    regime = compute_regime(dominance, cfg)
    majors = compute_majors(major_frames, regime.total2, cfg)
    btc_d = major_frames["BTC"]["1d"]
    btc_dir = btc_d.direction
    total2_d = (dominance.assign(close=dominance["total2"])[["ts", "close"]]
                .pipe(_daily_close))
    rs_cut = rs_thresholds(coins, btc_d, cfg)
    hot = hot_categories(markets, coin_categories, cfg) if hot is None else hot
    bw = cfg["breakout_watch"]
    now_ms = int(btc_d.df["ts"].iloc[-1]) + DAY if now_ms is None else now_ms
    watch = {b: e for b, e in (breakout_watch or {}).items()
             if now_ms - e["since"] <= bw["days"] * DAY}
    out = FunnelResult(regime, majors, hot=hot, breakout_watch=watch)
    for coin in coins:
        if not liquid(coin.info, cfg):
            continue
        labels = scan_coin(coin, btc_d, rs_cut, hot, cfg)
        breakout = sorted({l.name for l in labels} & set(bw["labels"]))
        if breakout and coin.base not in watch:
            watch[coin.base] = {"since": now_ms, "labels": breakout,
                                "level": breakout_level(coin.frames["1d"], bw["lookback_bars"])}
        if coin.base in watch and not breakout:
            labels.append(Label("BREAKOUT_WATCH", LONG))
        labels = filter_by_bias(labels, regime.bias)
        if not labels:
            continue
        out.watchlist[coin.base] = [l.name for l in labels]
        pairs = analyze_pairs(coin, btc_d.df, total2_d, cfg)
        out.pairs[coin.base] = pairs
        liq = liquidity_points(coin.info.get("total_volume") or 0, coin.spread_pct, cfg)
        flip = watch.get(coin.base, {}).get("level")
        h4 = coin.frames["4h"]
        levels = collect_levels(coin.frames, h4.close, cfg) + ([flip_level(flip, cfg)] if flip else [])
        for side in sorted(label_sides(labels)):
            if not regime.allows(side) or not pairs.allowed(side, btc_dir, cfg):
                continue
            c = score_candidate(coin.base, side, coin.is_btc, labels, pairs, liq, regime, majors, cfg)
            c.level_atr = nearest_level_atr(side, h4.close, h4.atr, levels)
            c.flip_level = flip if side == LONG else None
            out.candidates.append(c)
    out.shortlist = select(out.candidates, cfg)
    return out


def _daily_close(df: pd.DataFrame) -> pd.DataFrame:
    """Last value per UTC day, with ts at the day's start (matches exchange D candles)."""
    day = (df["ts"] // 86_400_000) * 86_400_000
    return df.assign(ts=day).groupby("ts", as_index=False)["close"].last()
