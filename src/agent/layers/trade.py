"""Trade builder: entry zone, stop, targets, position size and leverage.

`side` is +1 long / -1 short. Everything is mirrored through `side`, so "support" means
the level behind the entry (below for a long, above for a short).
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass

from ..analysis.frame import Frame
from ..indicators.levels import Level, cluster_levels, previous_period_levels, round_levels


def flip_level(price: float, cfg: dict) -> Level:
    """A broken D level kept as support while the coin is on the breakout watch."""
    w = cfg["technical"]["levels"]["timeframe_weight"]["1d"]
    return Level(price, 1, "1d", "flip", 1 + w, [price])


def nearest_level_atr(side: int, price: float, atr: float, levels: list[Level]) -> float | None:
    """Distance (in ATR) from price back to the closest level behind it, if any."""
    behind = [(price - l.price) * side for l in levels if (price - l.price) * side >= 0]
    return min(behind) / atr if behind and atr and atr == atr else None


def collect_levels(frames: dict[str, Frame], price: float, cfg: dict) -> list[Level]:
    """Pivot clusters on 4H/D/W, the previous D and W high/low, and round numbers."""
    sw, lv = cfg["swings"], cfg["technical"]["levels"]
    weights = lv["timeframe_weight"]
    out: list[Level] = []
    for tf in ("4h", "1d", "1w"):
        f = frames.get(tf)
        if f is None:
            continue
        out += cluster_levels(f.swings, f.atr, sw["level_cluster_atr"], sw["level_min_touches"],
                              tf, weights)
        if tf in ("1d", "1w"):
            out += previous_period_levels(f.df, tf, weights)
    if sw["round_number_levels"]:
        out += round_levels(price, cfg["structure"]["round_levels_each_side"])
    return out


@dataclass
class TradePlan:
    side: int
    order: str                  # limit | market
    entry: float                # reference price used for R
    entry_low: float
    entry_high: float
    sl: float
    tp1: float
    tp2: float
    tp1_r: float
    tp2_r: float
    tp2_from_level: bool
    support: float              # the level the setup leans on
    support_kind: str
    support_tf: str
    support_touches: int
    atr: float
    risk_pct: float = 0.0
    sl_pct: float = 0.0
    size_pct: float = 0.0
    leverage: int = 0
    margin_pct: float = 0.0

    @property
    def risk(self) -> float:
        return abs(self.entry - self.sl)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class Rejected:
    reason: str                 # no_level | sl_too_wide | rr


def build_trade(side: int, h4: Frame, levels: list[Level], cfg: dict,
                price: float | None = None) -> TradePlan | Rejected:
    """price: the current price (the 1H trigger close); defaults to the 4H close.
    Distances use ATR(4H)."""
    t, lv = cfg["trade"], cfg["technical"]["levels"]
    atr = h4.atr
    price = h4.close if price is None else price
    near = [l for l in levels
            if 0 <= (price - l.price) * side <= lv["max_entry_above_support_atr"] * atr]
    if not near:
        return Rejected("no_level")
    s = max(near, key=lambda l: l.price * side)              # the closest one behind price
    half = t["entry_zone_atr"] * atr
    low, high = s.price - half, s.price + half
    if low <= price <= high:
        order, entry = "market", price
    else:
        order, entry = "limit", s.price
    members = s.members or [s.price]
    invalid = min(members) if side == 1 else max(members)
    sl = invalid - side * t["sl_buffer_atr"] * atr
    risk = (entry - sl) * side
    if risk > t["max_sl_atr"] * atr:
        return Rejected("sl_too_wide")
    far_edge = high if side == 1 else low
    opposing = sorted((l for l in levels if (l.price - far_edge) * side > 0),
                      key=lambda l: l.price * side)
    if opposing:
        first = opposing[0]
        if (first.price - entry) * side < t["tp1_min_r"] * risk:
            return Rejected("rr")
        tp1 = first.price
    else:
        tp1 = entry + side * t["tp1_min_r"] * risk
    tp2, tp2_level = second_target(side, entry, risk, tp1, opposing, t["tp2_r"])
    return TradePlan(side, order, entry, low, high, sl, tp1, tp2,
                     (tp1 - entry) * side / risk, (tp2 - entry) * side / risk, tp2_level,
                     s.price, s.kind, s.timeframe, s.touches, atr)


def second_target(side: int, entry: float, risk: float, tp1: float, opposing: list[Level],
                  tp2_r: float) -> tuple[float, bool]:
    """TP2 is always beyond TP1: the next D/W level, else tp2_r x R, else (TP1 is already
    past tp2_r x R) the next level of any timeframe, else TP1 + 1R."""
    beyond = [l for l in opposing if (l.price - tp1) * side > 0]
    higher_tf = [l for l in beyond if l.timeframe in ("1d", "1w")]
    if higher_tf:
        return higher_tf[0].price, True
    r_target = entry + side * tp2_r * risk
    if (r_target - tp1) * side > 0:
        return r_target, False
    if beyond:
        return beyond[0].price, True
    return tp1 + side * risk, False


def size_position(plan: TradePlan, risk_pct: float, cfg: dict) -> TradePlan:
    """Position as % of balance = risk % / stop distance %. Isolated leverage keeps the
    liquidation price at least liquidation_sl_multiple x the stop distance away."""
    t = cfg["trade"]
    plan.risk_pct = risk_pct
    plan.sl_pct = plan.risk / plan.entry * 100
    plan.size_pct = risk_pct / plan.sl_pct * 100 if plan.sl_pct else 0.0
    max_lev = 1 / (t["liquidation_sl_multiple"] * plan.sl_pct / 100) if plan.sl_pct else 1
    plan.leverage = max(1, min(t["leverage_cap"], math.floor(max_lev)))
    if plan.size_pct > plan.leverage * 100:
        # a very tight stop would need more than the whole balance as margin: cap the
        # position at what the leverage allows and state the smaller risk
        plan.size_pct = plan.leverage * 100.0
        plan.risk_pct = plan.size_pct * plan.sl_pct / 100
    plan.margin_pct = plan.size_pct / plan.leverage
    return plan
