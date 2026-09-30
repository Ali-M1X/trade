"""Weekly spot HOLD ideas: coins meeting at least 4 of the 6 conditions in STRATEGY.md."""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from ..analysis.frame import Frame
from ..analysis.phase import detect_phase
from ..indicators.levels import Level, cluster_levels, previous_period_levels
from ..layers.scanners import period_return

CONDITIONS = ("w_cycle", "btc_pair_hl", "ma99_d", "rs_90d", "hot_category", "ath_acc")


@dataclass
class HoldIdea:
    base: str
    met: dict[str, bool]
    buy_steps: list[float] = field(default_factory=list)
    invalidation: float | None = None
    targets: list[float] = field(default_factory=list)

    @property
    def count(self) -> int:
        return sum(self.met.values())


def w_cycle(w: Frame, cfg: dict) -> bool:
    """1: Higher Wave Cycle (W) up, or an accumulation range broken on W with RVOL >= 1.5."""
    if w.direction == 1:
        return True
    ph = detect_phase(w, cfg)
    return ph.name == "ACC" and ph.event == "breakout" and w.last["rvol"] >= cfg["hold"]["breakout_rvol"]


def btc_pair_higher_low(w_btc: Frame | None) -> bool:
    """2: COIN/BTC on W made a higher low."""
    if w_btc is None:
        return False
    lows = [s.price for s in w_btc.swings if s.kind == "L"][-2:]
    return len(lows) == 2 and lows[1] > lows[0]


def ma99_rising(d: Frame, cfg: dict) -> bool:
    """3: MA99 on D slopes up and price is above it."""
    ma = d.col(f"ma{cfg['indicators']['ma_slow']}")
    n = cfg["technical"]["phase"]["ma_slope_bars"]
    if len(ma) <= n or np.isnan(ma.iloc[-1]) or np.isnan(ma.iloc[-1 - n]):
        return False
    return bool(ma.iloc[-1] > ma.iloc[-1 - n] and d.close > ma.iloc[-1])


def ath_accumulation(info: dict, w: Frame, cfg: dict) -> bool:
    """6: more than 60% below the all-time high and in ACC on W."""
    ath = info.get("ath_change_percentage")
    return ath is not None and ath <= -cfg["hold"]["ath_drawdown_pct"] and detect_phase(w, cfg).name == "ACC"


def plan_levels(price: float, d: Frame, w: Frame, cfg: dict) -> tuple[list[float], float | None, list[float]]:
    """Buy steps on D/W supports below price, invalidation under the deepest step, and
    targets on W resistances (D if W has none)."""
    sw, weights = cfg["swings"], cfg["technical"]["levels"]["timeframe_weight"]
    lv: list[Level] = []
    for f in (d, w):
        lv += cluster_levels(f.swings, f.atr, sw["level_cluster_atr"], sw["level_min_touches"], f.tf, weights)
        lv += previous_period_levels(f.df, f.tf, weights)
    gap = sw["level_cluster_atr"] * d.atr                   # levels closer than this are one
    swing_lows = [s.price for f in (d, w) for s in f.swings if s.kind == "L"]
    supports = spaced(sorted({l.price for l in lv if l.price < price} |
                             {p for p in swing_lows if p < price}, reverse=True), gap)
    steps = supports[:cfg["hold"]["buy_steps"]]
    res = sorted({l.price for l in lv if l.timeframe == "1w" and l.price > price} |
                 {s.price for s in w.swings if s.kind == "H" and s.price > price})
    if not res:
        res = sorted({l.price for l in lv if l.price > price} |
                     {s.price for s in d.swings if s.kind == "H" and s.price > price})
    targets = spaced(res, gap)[:3]
    return steps, (steps[-1] if steps else None), targets


def spaced(prices: list[float], gap: float) -> list[float]:
    """Keep prices in the given order, skipping any within `gap` of the last kept one."""
    out: list[float] = []
    for p in prices:
        if not out or abs(p - out[-1]) >= gap:
            out.append(p)
    return out


def scan_hold(coins: list[dict], btc_d: Frame, hot30: set[str], cfg: dict) -> list[HoldIdea]:
    """coins: [{base, info, d, w, w_btc, categories}]. Returns ideas meeting the minimum,
    most conditions first."""
    h = cfg["hold"]
    rs = {c["base"]: period_return(c["d"], h["rs_days"]) - period_return(btc_d, h["rs_days"])
          for c in coins}
    vals = [v for v in rs.values() if not np.isnan(v)]
    cut = np.percentile(vals, 100 - h["rs_top_percentile"]) if vals else np.inf
    ideas = []
    for c in coins:
        d, w = c["d"], c["w"]
        met = {
            "w_cycle": w_cycle(w, cfg),
            "btc_pair_hl": btc_pair_higher_low(c.get("w_btc")),
            "ma99_d": ma99_rising(d, cfg),
            "rs_90d": bool(not np.isnan(rs[c["base"]]) and rs[c["base"]] >= cut),
            "hot_category": bool(hot30 & set(c.get("categories", []))),
            "ath_acc": ath_accumulation(c["info"], w, cfg),
        }
        if sum(met.values()) < h["min_conditions"]:
            continue
        steps, inval, targets = plan_levels(d.close, d, w, cfg)
        ideas.append(HoldIdea(c["base"], met, steps, inval, targets))
    ideas.sort(key=lambda i: (-i.count, i.base))
    return ideas
