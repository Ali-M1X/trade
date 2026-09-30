"""Layer 6: technical score (0..100), gates and grade for one coin and side."""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from ..analysis import candles as cd
from ..analysis.cycles import CycleScore, score_cycles
from ..analysis.frame import Frame
from ..analysis.patterns import find_patterns
from ..analysis.phase import Phase, climax, detect_phase, reversal_signs
from ..indicators.divergence import divergence
from .majors import Majors
from .regime import Regime
from .trade import Rejected, TradePlan, build_trade, collect_levels, size_position

TREND = {1: "TREND_UP", -1: "TREND_DOWN"}
RANGE_PHASE = {1: "ACC", -1: "DIST"}


def clamp(x: float, hi: float, lo: float = 0.0) -> float:
    return max(lo, min(hi, x))


# ------------------------------------------------------------- 1. phase
def phase_section(d: Frame, h4: Frame, side: int, cfg: dict) -> tuple[float, bool, Phase, Phase]:
    p = cfg["technical"]["phase"]
    pd_, p4 = detect_phase(d, cfg), detect_phase(h4, cfg)
    allowed = pd_.name in (TREND[side], RANGE_PHASE[side])
    if pd_.name == TREND[side] and \
            len(reversal_signs(d, side, cfg)) >= cfg["technical"]["patterns"]["reversal_signs_needed"]:
        allowed = False                                   # the trend itself is no longer valid
    if not allowed:
        return 0.0, False, pd_, p4
    if pd_.name == RANGE_PHASE[side] and pd_.event is None:
        pts = p["points_range_no_event"]
    elif p4.direction == side:
        pts = p["points_both"]
    else:
        pts = p["points_d_only"]
    return float(pts), True, pd_, p4


# --------------------------------------------------------------- 2. Dow
def dow_section(d: Frame, h4: Frame, side: int, cfg: dict) -> float:
    p = cfg["technical"]["dow"]
    ev = h4.last_event
    if ev and ev.kind == "CHoCH" and ev.direction == -side:
        return float(p["points_choch_against"])
    if d.dow == side and h4.dow == side:
        return float(p["points_both"])
    if h4.dow == side and d.dow == 0:
        return float(p["points_4h_only"])
    if d.dow == side and h4.dow == 0:
        return float(p["points_d_only"])
    return 0.0


# ------------------------------------------------------------- 3. levels
def levels_section(plan: TradePlan, cfg: dict) -> float:
    p = cfg["technical"]["levels"]
    if plan.support_kind == "cluster" and plan.support_tf in ("1d", "1w") and \
            plan.support_touches >= p["strong_min_touches"]:
        return float(p["points_strong"])
    if plan.support_kind in ("cluster", "prev_high", "prev_low", "flip"):
        return float(p["points_weak"])
    return 0.0                                            # round number only


# ------------------------------------------------------------- 4. volume
def volume_section(h4: Frame, side: int, cfg: dict) -> float:
    v = cfg["technical"]["volume"]
    rvol = h4.col("rvol")
    pts = 0.0
    ev = h4.last_event
    if ev and ev.direction == side and h4.bars_since(ev.idx) <= v["event_lookback"]:
        if rvol.iloc[ev.idx] >= v["breakout_rvol"]:
            pts += v["breakout_points"]
    elif rvol.iloc[-v["pullback_bars"]:].mean() < v["pullback_rvol_max"]:
        pts += v["breakout_points"]
    n = cfg["indicators"]["obv_lookback"]
    obv = h4.col("obv")
    if len(obv) > n and np.sign(obv.iloc[-1] - obv.iloc[-1 - n]) == side:
        pts += v["obv_points"]
    if climax(h4, side, cfg):
        pts += v["climax_penalty"]
    return clamp(pts, v["max_points"])


# ------------------------------------------------------------ 5. candles
def candles_section(h4: Frame, side: int, plan: TradePlan, cfg: dict) -> tuple[float, bool, str | None]:
    """Returns (points, chase, trigger name). A pin bar only counts if its wick reaches
    the entry zone of the level."""
    c = cfg["technical"]["candles"]
    i = h4.n - 1
    bar = h4.last
    trig = cd.trigger_candle(h4.df, i, side, cfg)
    if trig == "pin_bar":
        tip = bar["low"] if side == 1 else bar["high"]
        edge = plan.entry_high if side == 1 else plan.entry_low
        if (tip - edge) * side > 0:
            trig = None
    size = cd.size_class(bar, h4.atr, c["body_min_atr"], c["body_max_atr"], c["chase_atr"])
    pts = (c["trigger_points"] if trig else 0) + (c["size_points"] if size == "ok" else 0)
    if cd.weakening(h4.df, i, side, c["weakness_bars"]):
        pts += c["weakness_penalty"]
    return clamp(pts, c["max_points"]), size == "chase", trig


# ------------------------------------------------ 7. patterns and health
def health_section(h4: Frame, side: int, cfg: dict) -> tuple[float, list[str]]:
    p = cfg["technical"]["patterns"]
    ind = cfg["indicators"]
    fast, mid, slow = (f"ma{ind[k]}" for k in ("ma_fast", "ma_mid", "ma_slow"))
    last = h4.last
    notes = []
    pts = 0.0
    rsi = h4.col("rsi")
    recent = rsi.iloc[-p["rsi_lookback"]:]
    lo, hi = (p["rsi_healthy_low"], p["rsi_healthy_high"]) if side == 1 else \
        (100 - p["rsi_healthy_high"], 100 - p["rsi_healthy_low"])
    held = recent.min() >= lo if side == 1 else recent.max() <= hi
    if lo <= rsi.iloc[-1] <= hi and held:
        pts += p["rsi_points"]
        notes.append("rsi_healthy")
    if divergence(h4.swings, rsi) == -side:
        pts += p["rsi_divergence_penalty"]
        notes.append("rsi_divergence_against")
    hist = h4.col("macd_hist")
    if last["macd"] * side > 0 and len(hist) > 1 and (hist.iloc[-1] - hist.iloc[-2]) * side > 0:
        pts += p["macd_points"]
        notes.append("macd")
    ordered = (last[fast] - last[mid]) * side > 0 and (last[mid] - last[slow]) * side > 0
    if ordered and (h4.close - last[mid]) * side > 0:
        pts += p["sma_points"]
        notes.append("sma_order")
    if (h4.close - last[mid]) * side > p["overextended_atr"] * h4.atr:
        pts += p["overextended_penalty"]
        notes.append("overextended")
    pats = [x.name for x in find_patterns(h4, cfg) if x.direction == side]
    if pats:
        pts += p["pattern_points"]
        notes += [f"pattern:{n}" for n in pats]
    return clamp(pts, p["max_points"]), notes


# ------------------------------------------------------- 8. confirmation
def confirmations(h1: Frame, side: int, plan: TradePlan, cfg: dict) -> list[str]:
    c = cfg["technical"]["confirmation"]
    n = c["lookback_bars"]
    df = h1.df
    recent = df.iloc[-n:]
    zone = plan.entry_high - plan.support if side == 1 else plan.support - plan.entry_low
    out = []
    if side == 1:
        touched = (recent["low"] <= plan.support + zone).any()
    else:
        touched = (recent["high"] >= plan.support - zone).any()
    if touched and (h1.close - plan.support) * side > 0:
        out.append("retest")
    if any(e.kind == "CHoCH" and e.direction == side and h1.bars_since(e.idx) < n for e in h1.events):
        out.append("choch")
    if cd.trigger_candle(df, h1.n - 1, side, cfg):
        out.append("trigger_candle")
    rsi, hist = h1.col("rsi"), h1.col("macd_hist")
    mid = c["rsi_mid"]
    rsi_cross = (rsi.iloc[-1] - mid) * side > 0 and ((rsi.iloc[-n:-1] - mid) * side <= 0).any()
    macd_flip = hist.iloc[-1] * side > 0 and ((hist.iloc[-3:-1]) * side <= 0).any()
    if rsi_cross or macd_flip:
        out.append("rsi_macd")
    if h1.last["rvol"] >= c["trigger_rvol"]:
        out.append("rvol")
    return out


def confirmation_points(count: int, cfg: dict) -> float:
    c = cfg["technical"]["confirmation"]
    return min(c["max_points"], c["points_per_extra"] * count) if count >= c["min_confirmations"] else 0.0


# ---------------------------------------------------------------- grade
def grade_for(score: float, cfg: dict) -> str | None:
    g = cfg["grades"]
    if score >= g["A"]:
        return "A"
    if score >= g["B"]:
        return "B"
    if score >= g["watch"]:
        return "Watch"
    return None


@dataclass
class Evaluation:
    base: str
    side: int
    score: float = 0.0
    grade: str | None = None
    a_plus: bool = False
    sections: dict = field(default_factory=dict)
    gates: dict = field(default_factory=dict)
    flags: list[str] = field(default_factory=list)
    confirmations: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    phase_d: str = ""
    phase_4h: str = ""
    cycle: str = ""
    trigger: str | None = None
    plan: TradePlan | None = None
    rejected: str | None = None
    funding_pct: float | None = None

    @property
    def is_signal(self) -> bool:
        return self.grade in ("A", "B")

    def to_dict(self) -> dict:
        d = {k: v for k, v in self.__dict__.items() if k != "plan"}
        d["plan"] = self.plan.to_dict() if self.plan else None
        return d


def evaluate(base: str, side: int, frames: dict[str, Frame], regime: Regime, majors: Majors,
             cfg: dict, funding: float | None = None, btc_pair_up: bool = False,
             extra_levels: list | None = None) -> Evaluation:
    """frames: 1w, 1d, 4h, 1h for COIN/USDT. funding: latest rate as a fraction.
    extra_levels: e.g. the flip level of a coin on the breakout watch."""
    w, d, h4, h1 = frames["1w"], frames["1d"], frames["4h"], frames["1h"]
    ev = Evaluation(base, side)
    ev.gates["regime"] = regime.allows(side)

    pts, ok, pd_, p4 = phase_section(d, h4, side, cfg)
    ev.sections["phase"], ev.gates["phase"] = pts, ok
    ev.phase_d = pd_.name + (f":{pd_.event}" if pd_.event else "")
    ev.phase_4h = p4.name + (f":{p4.event}" if p4.event else "")
    ev.sections["dow"] = dow_section(d, h4, side, cfg)

    levels = collect_levels(frames, h4.close, cfg) + list(extra_levels or [])
    plan = build_trade(side, h4, levels, cfg)
    if isinstance(plan, Rejected):
        ev.rejected = plan.reason
        ev.gates["rr"] = False
        ev.score = sum(ev.sections.values())
        return ev
    ev.plan = plan
    ev.gates["rr"] = True
    ev.sections["levels"] = levels_section(plan, cfg)
    ev.sections["volume"] = volume_section(h4, side, cfg)
    ev.sections["candles"], chase, ev.trigger = candles_section(h4, side, plan, cfg)
    cyc: CycleScore = score_cycles(w, d, h4, side, cfg)
    ev.sections["cycles"], ev.cycle = float(cyc.points), cyc.note
    ev.sections["patterns"], ev.notes = health_section(h4, side, cfg)
    ev.confirmations = confirmations(h1, side, plan, cfg)
    ev.sections["confirmation"] = confirmation_points(len(ev.confirmations), cfg)
    ev.gates["confirmation"] = len(ev.confirmations) >= cfg["technical"]["confirmation"]["min_confirmations"]

    score = sum(ev.sections.values())
    t = cfg["trade"]
    if funding is not None:
        ev.funding_pct = funding * 100
        if ev.funding_pct * side > t["funding_crowded_pct"]:
            score += t["funding_penalty"]
            ev.flags.append("funding_crowded")
    ev.score = max(0.0, score)
    grade = grade_for(ev.score, cfg)

    # downgrades to a "near setup" alert
    if chase:
        ev.flags.append("chase")
    if cyc.watch_only:
        ev.flags.append("lower_cycle_correcting")
    if not ev.gates["confirmation"]:
        ev.flags.append("not_confirmed")
    if majors.btc_weak and side == 1 and base != "BTC" and \
            ev.score < cfg["majors"]["btc_weak_min_score"]:
        ev.flags.append("btc_weak")
    if regime.min_grade == "A" and grade == "B":
        ev.flags.append("neutral_regime_needs_A")
    hard_fail = not (ev.gates["regime"] and ev.gates["phase"])
    risk_mult = regime.risk_for(side, base == "BTC", btc_pair_up) * majors.risk_multiplier * \
        cyc.risk_multiplier
    if grade in ("A", "B") and (set(ev.flags) - {"funding_crowded"} or risk_mult == 0):
        grade = "Watch"
    ev.grade = None if hard_fail else grade
    ev.a_plus = ev.grade == "A" and ev.score >= cfg["grades"]["a_plus"]
    if ev.grade in ("A", "B"):
        risk = t["base_risk_pct"] * cfg["grades"]["risk_share"][ev.grade] * risk_mult
        size_position(plan, risk, cfg)
    return ev
