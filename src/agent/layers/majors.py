"""Layer 2: BTCUSDT, ETHUSDT and ETHBTC on W, D and 4H."""
from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np

from ..analysis.frame import Frame


def weighted_score(frames: dict[str, Frame], weights: dict[str, int]) -> int:
    """Sum of direction x weight over W/D/4H: -6..+6 with weights 3/2/1."""
    return int(sum(weights[tf] * frames[tf].direction for tf in weights if tf in frames))


@dataclass(frozen=True)
class Majors:
    btc: int
    eth: int
    ethbtc: int
    ethbtc_up_d: bool
    ethbtc_d: int           # ETHBTC structure direction on D (for messages)
    btc_weak: bool          # alt longs then need a technical score >= btc_weak_min_score
    divergence: bool        # BTC and TOTAL2 pointing opposite ways: risk x0.5
    risk_multiplier: float

    def to_dict(self) -> dict:
        return asdict(self)


def compute_majors(frames: dict[str, dict[str, Frame]], total2_dir: int, cfg: dict) -> Majors:
    """frames: {"BTC": {tf: Frame}, "ETH": {...}, "ETHBTC": {...}}."""
    m = cfg["majors"]
    w = m["weights"]
    btc = weighted_score(frames["BTC"], w)
    eth = weighted_score(frames["ETH"], w)
    ethbtc = weighted_score(frames["ETHBTC"], w)
    divergence = bool(btc and total2_dir and np.sign(btc) == -total2_dir)
    return Majors(
        btc=btc, eth=eth, ethbtc=ethbtc,
        ethbtc_up_d=frames["ETHBTC"]["1d"].direction == 1 if "1d" in frames["ETHBTC"] else False,
        ethbtc_d=frames["ETHBTC"]["1d"].direction if "1d" in frames["ETHBTC"] else 0,
        btc_weak=btc <= m["btc_weak_threshold"],
        divergence=divergence,
        risk_multiplier=m["divergence_risk_multiplier"] if divergence else 1.0,
    )
