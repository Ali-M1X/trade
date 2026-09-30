"""Three wave cycles: Higher (W), Medium (D), Lower (4H)."""
from __future__ import annotations

from dataclasses import dataclass

from ..indicators.swings import legs, mean_leg_bars
from .frame import Frame


@dataclass(frozen=True)
class CycleState:
    major: int          # structure direction of the timeframe (+1/0/-1)
    leg: int            # current leg: +1 moving up from the last swing low, -1 down from a high
    bars_in_leg: int
    mean_leg: float     # average leg length over the last N legs

    @property
    def impulse(self) -> bool:
        return self.major != 0 and self.leg == self.major

    @property
    def correction(self) -> bool:
        return self.major != 0 and self.leg == -self.major

    @property
    def late(self) -> bool:
        return self.impulse and self.bars_in_leg > self.mean_leg


def cycle_state(f: Frame, cfg: dict) -> CycleState:
    if not f.swings:
        return CycleState(f.direction, 0, 0, float("nan"))
    last = f.swings[-1]
    leg = 1 if last.kind == "L" else -1
    return CycleState(f.direction, leg, f.bars_since(last.idx),
                      mean_leg_bars(legs(f.swings), cfg["swings"]["wave_history_swings"]))


def fresh_turn(f: Frame, side: int, cfg: dict) -> bool:
    """The Lower cycle has just turned in `side` direction: a new higher low (long) /
    lower high (short) confirmed, or a BOS/CHoCH in that direction, within fresh_bars."""
    n = cfg["technical"]["cycles"]["fresh_bars"]
    kind = "L" if side == 1 else "H"
    same = [s for s in f.swings if s.kind == kind]
    if len(same) >= 2 and f.swings[-1].kind == kind:
        a, b = same[-2], same[-1]
        if (b.price - a.price) * side > 0 and f.bars_since(b.confirmed) <= n:
            return True
    ev = f.last_event
    return bool(ev and ev.direction == side and f.bars_since(ev.idx) <= n)


@dataclass(frozen=True)
class CycleScore:
    points: float
    risk_multiplier: float
    watch_only: bool
    note: str


def score_cycles(w: Frame, d: Frame, h4: Frame, side: int, cfg: dict) -> CycleScore:
    c = cfg["technical"]["cycles"]
    hw, mw, lw = cycle_state(w, cfg), cycle_state(d, cfg), cycle_state(h4, cfg)
    if hw.major and mw.major and mw.major == -hw.major:
        return CycleScore(c["points_counter_trend"], c["counter_trend_risk_multiplier"], False,
                          "counter_trend")
    if hw.major == mw.major == side:
        if lw.leg == side and lw.late:
            return CycleScore(c["points_late"], 1.0, False, "late")
        if lw.leg == side and fresh_turn(h4, side, cfg):
            return CycleScore(c["points_all_aligned_fresh"], 1.0, False, "fresh_turn")
        if lw.leg == -side:
            return CycleScore(c["points_lower_correcting"], 1.0, True, "lower_correcting")
        return CycleScore(0, 1.0, False, "lower_impulse")
    return CycleScore(0, 1.0, False, "not_aligned")
