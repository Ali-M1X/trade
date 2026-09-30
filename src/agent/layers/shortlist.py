"""Layer 5: shortlist score (0..100) and selection."""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from .majors import Majors
from .pairs import PairsResult
from .regime import Regime
from .scanners import Label


def liquidity_points(volume_usd: float, spread_pct: float | None, cfg: dict) -> float:
    s = cfg["shortlist"]
    lo, hi = cfg["universe"]["min_volume_24h_usd"], s["volume_full_usd"]
    v = (math.log(volume_usd / lo) / math.log(hi / lo)) if volume_usd and volume_usd > 0 else 0
    vol_pts = s["liquidity_volume_points"] * min(1.0, max(0.0, v))
    if spread_pct is None:
        return vol_pts
    sp = (s["spread_zero_pct"] - spread_pct) / (s["spread_zero_pct"] - s["spread_full_pct"])
    return vol_pts + s["liquidity_spread_points"] * min(1.0, max(0.0, sp))


def regime_points(side: int, regime: Regime, majors: Majors, cfg: dict) -> float:
    """Half from the regime bias (1 same side, 0.5 'both'), half from the BTC score."""
    if not regime.allows(side):
        return 0.0
    regime_part = 0.5 if regime.bias == "both" else 1.0
    btc_part = (majors.btc * side + 6) / 12
    return cfg["shortlist"]["weights"]["regime_alignment"] * (0.5 * regime_part + 0.5 * btc_part)


def label_points(labels: list[Label], cfg: dict) -> float:
    q = cfg["scanners"]["label_quality"]
    total = sum(q[l.name] for l in labels)
    return cfg["shortlist"]["weights"]["scanner_labels"] * min(1.0, total / cfg["shortlist"]["labels_full_at"])


def label_sides(labels: list[Label]) -> set[int]:
    sides: set[int] = set()
    for l in labels:
        sides |= {1, -1} if l.direction == 0 else {l.direction}
    return sides


@dataclass
class Candidate:
    base: str
    side: int
    score: float
    labels: list[str]
    parts: dict = field(default_factory=dict)
    level_atr: float | None = None      # distance to the nearest usable level, in ATR(4H)
    flip_level: float | None = None

    def near_level(self, cfg: dict) -> bool:
        return self.level_atr is not None and self.level_atr <= cfg["shortlist"]["near_level_atr"]


def score_candidate(base: str, side: int, is_btc: bool, labels: list[Label], pairs: PairsResult,
                    liquidity: float, regime: Regime, majors: Majors, cfg: dict) -> Candidate:
    w = cfg["shortlist"]["weights"]
    parts = {
        "regime": regime_points(side, regime, majors, cfg),
        "pairs": w["pair_strength"] * pairs.strength(side, cfg, regime.name == "neutral"),
        "labels": label_points([l for l in labels if l.direction in (0, side)], cfg),
        "liquidity": liquidity,
        "ethbtc": cfg["majors"]["ethbtc_up_bonus"] if majors.ethbtc_up_d and not is_btc else 0,
    }
    return Candidate(base, side, min(100.0, sum(parts.values())),
                     [l.name for l in labels if l.direction in (0, side)], parts)


def select(candidates: list[Candidate], cfg: dict) -> list[Candidate]:
    """Best side per coin, score >= min_score, at most max_coins. Coins within
    near_level_atr of a usable level rank ahead of the rest, then by score."""
    s = cfg["shortlist"]
    best: dict[str, Candidate] = {}
    for c in candidates:
        if c.base not in best or c.score > best[c.base].score:
            best[c.base] = c
    ok = [c for c in best.values() if c.score >= s["min_score"]]
    ok.sort(key=lambda c: (not c.near_level(cfg), -c.score, c.base))
    return ok[:s["max_coins"]]
