"""Layer 4: COIN/USDT, COIN/BTC, COIN/ETH structure plus correlation and beta."""
from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np
import pandas as pd

from .scanners import CoinData, period_return


def returns_corr_beta(coin: pd.DataFrame, ref: pd.DataFrame, days: int) -> tuple[float, float]:
    """Correlation and beta of daily returns over the last `days` days (aligned on ts).
    ref may be any (ts, close) frame, e.g. BTC candles or daily TOTAL2."""
    m = coin[["ts", "close"]].merge(ref[["ts", "close"]], on="ts", suffixes=("", "_r"))
    r = m[["close", "close_r"]].pct_change().dropna().iloc[-days:]
    if len(r) < max(5, days // 2) or r["close_r"].var() == 0:
        return float("nan"), float("nan")
    corr = float(r["close"].corr(r["close_r"]))
    beta = float(r["close"].cov(r["close_r"]) / r["close_r"].var())
    return corr, beta


@dataclass(frozen=True)
class PairsResult:
    dirs: dict                # {"USDT": {"1d": 1, "4h": 0}, "BTC": {...}, "ETH": {...}}
    corr_btc: float
    corr_total2: float
    beta: float
    rs: float                 # return vs BTC over rs_days
    high_beta: bool

    def strength(self, side: int, cfg: dict, neutral_regime: bool = False) -> float:
        """0..1: how well the three pairs line up with the side on D and 4H."""
        p = cfg["pairs"]
        qw, tw = p["quote_weights"], p["timeframe_weights"]
        total = 0.0
        for q, w in qw.items():
            per_tf = [(tw[tf], self.dirs.get(q, {}).get(tf, 0)) for tf in tw]
            align = sum(wt * (d * side + 1) / 2 for wt, d in per_tf) / sum(tw.values())
            total += w * align
        total /= sum(qw.values())
        if self.independent(side, cfg):
            total += p["independent_bonus_neutral"] if neutral_regime else p["independent_bonus"]
        return min(1.0, total)

    def independent(self, side: int, cfg: dict) -> bool:
        return (not np.isnan(self.corr_btc) and self.corr_btc <= cfg["pairs"]["corr_low"]
                and self.rs * side > 0)

    def allowed(self, side: int, btc_dir: int, cfg: dict) -> bool:
        """A coin that just follows BTC (corr >= corr_high) needs BTC moving the same way."""
        if not np.isnan(self.corr_btc) and self.corr_btc >= cfg["pairs"]["corr_high"]:
            return btc_dir == side
        return True

    def to_dict(self) -> dict:
        return asdict(self)


def analyze_pairs(coin: CoinData, btc_d: pd.DataFrame, total2_d: pd.DataFrame | None,
                  cfg: dict) -> PairsResult:
    p = cfg["pairs"]
    dirs = {"USDT": {tf: f.direction for tf, f in coin.frames.items() if tf in p["timeframes"]},
            "BTC": {tf: f.direction for tf, f in coin.btc_frames.items() if tf in p["timeframes"]},
            "ETH": {tf: f.direction for tf, f in coin.eth_frames.items() if tf in p["timeframes"]}}
    d = coin.frames["1d"].df
    if coin.is_btc:
        corr_btc, beta = 1.0, 1.0
    else:
        corr_btc, beta = returns_corr_beta(d, btc_d, p["corr_days"])
    corr_t2 = (returns_corr_beta(d, total2_d, p["corr_days"])[0]
               if total2_d is not None else float("nan"))
    rs_days = p["rs_days"]
    btc_ret = float(btc_d["close"].iloc[-1] / btc_d["close"].iloc[-1 - rs_days] - 1) \
        if len(btc_d) > rs_days else float("nan")
    rs = period_return(coin.frames["1d"], rs_days) - btc_ret
    return PairsResult(dirs, corr_btc, corr_t2, beta, rs,
                       not np.isnan(beta) and beta > p["beta_high"])
