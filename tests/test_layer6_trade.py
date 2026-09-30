import numpy as np
import pandas as pd
import pytest

from agent.analysis.frame import Frame, make_frame
from agent.analysis.structure import StructureEvent
from agent.indicators.levels import Level
from agent.layers.majors import compute_majors
from agent.layers.regime import make_regime
from agent.layers.technical import (confirmation_points, dow_section, evaluate, grade_for,
                                    levels_section)
from agent.layers.trade import Rejected, build_trade, collect_levels, size_position
from builders import from_closes, trend, waypoints


# ------------------------------------------------------------ trade builder
def fake_h4(close, atr):
    df = pd.DataFrame({"close": [close], "atr": [atr]})
    return Frame("4h", df, [], [], 0, 0)


def lvl(price, tf="4h", touches=2, members=None, kind="cluster"):
    return Level(price, touches, tf, kind, touches, members or [price])


def test_long_market_entry_inside_zone():
    # ATR 2 -> zone +-0.4 around 142.7; price 143.0 is inside -> market
    levels = [lvl(142.7, members=[142.5, 142.9]), lvl(152.0), lvl(158.5, tf="1d")]
    p = build_trade(1, fake_h4(143.0, 2.0), levels, _cfg())
    assert p.order == "market" and p.entry == 143.0
    assert p.sl == pytest.approx(142.5 - 1.0)                 # lowest member - 0.5 ATR
    assert (p.tp1, p.tp2, p.tp2_from_level) == (152.0, 158.5, True)
    assert p.tp1_r == pytest.approx(9 / 1.5) and p.tp2_r == pytest.approx(15.5 / 1.5)


def test_long_limit_entry_above_zone():
    p = build_trade(1, fake_h4(144.5, 2.0), [lvl(142.7), lvl(160.0)], _cfg())
    assert p.order == "limit" and p.entry == 142.7
    assert (p.entry_low, p.entry_high) == pytest.approx((142.3, 143.1))


def test_short_mirror():
    levels = [lvl(100.0, members=[99.8, 100.3]), lvl(90.0), lvl(85.0, tf="1w")]
    p = build_trade(-1, fake_h4(99.5, 1.0), levels, _cfg())
    assert p.side == -1 and p.order == "limit" and p.entry == 100.0
    assert p.sl == pytest.approx(100.3 + 0.5)
    assert (p.tp1, p.tp2) == (90.0, 85.0)


@pytest.mark.parametrize("levels, price, reason", [
    ([lvl(140.0), lvl(160.0)], 145.5, "no_level"),              # support 2.75 ATR below
    ([lvl(142.7, members=[136.0, 142.9]), lvl(170.0)], 143.0, "sl_too_wide"),
    ([lvl(142.7), lvl(145.0)], 143.0, "rr"),                     # 2.0 away, risk 1.3 -> 1.5R
])
def test_rejections(levels, price, reason):
    assert build_trade(1, fake_h4(price, 2.0), levels, _cfg()) == Rejected(reason)


def test_tp2_is_always_beyond_tp1():
    # TP1 (152) is already 6R away, no D level beyond: next level of any timeframe
    p = build_trade(1, fake_h4(143.0, 2.0), [lvl(142.7, members=[142.5]), lvl(152.0), lvl(155.0)], _cfg())
    assert (p.tp1, p.tp2, p.tp2_from_level) == (152.0, 155.0, True)
    # nothing beyond TP1 either: TP1 + 1R
    p = build_trade(1, fake_h4(143.0, 2.0), [lvl(142.7, members=[142.5]), lvl(152.0)], _cfg())
    assert p.tp2 == pytest.approx(152.0 + 1.5) and not p.tp2_from_level
    # short side
    p = build_trade(-1, fake_h4(100.0, 1.0), [lvl(100.2, members=[100.2]), lvl(80.0)], _cfg())
    assert p.tp2 < p.tp1 < p.entry


def test_targets_default_to_r_multiples_without_opposing_levels():
    p = build_trade(1, fake_h4(143.0, 2.0), [lvl(142.7)], _cfg())
    risk = 143.0 - (142.7 - 1.0)
    assert p.tp1 == pytest.approx(143.0 + 2 * risk) and p.tp2 == pytest.approx(143.0 + 3 * risk)


@pytest.mark.parametrize("sl_pct, size, lev", [(3.4, 29.41, 10), (5.0, 20.0, 8), (20.0, 5.0, 2),
                                               (50.0, 2.0, 1)])
def test_size_and_leverage(sl_pct, size, lev):
    p = build_trade(1, fake_h4(100.0, 100.0), [lvl(100.0)], _cfg())
    p.entry, p.sl = 100.0, 100.0 - sl_pct
    size_position(p, 1.0, _cfg())
    assert p.size_pct == pytest.approx(size, abs=0.01) and p.leverage == lev
    assert p.margin_pct == pytest.approx(size / lev, abs=0.01)
    # liquidation (~1/leverage away) stays >= 2.5x the stop distance
    assert 100 / p.leverage >= 2.5 * sl_pct or p.leverage == 1


def test_position_never_needs_more_than_the_balance_as_margin():
    p = build_trade(1, fake_h4(100.0, 100.0), [lvl(100.0)], _cfg())
    p.entry, p.sl = 100.0, 99.95                                  # 0.05% stop
    size_position(p, 1.0, _cfg())
    assert p.leverage == 10 and p.size_pct == 1000 and p.margin_pct == 100
    assert p.risk_pct == pytest.approx(0.5)                       # what is actually at risk


def test_strategy_example_numbers():
    """STRATEGY.md: risk 1%, stop 3.4% -> position ~29%, max 11x -> 10x, margin ~2.9%."""
    p = build_trade(1, fake_h4(100.0, 100.0), [lvl(100.0)], _cfg())
    p.entry, p.sl = 142.7, 142.7 * (1 - 0.034)
    size_position(p, 1.0, _cfg())
    assert round(p.size_pct) == 29 and p.leverage == 10 and round(p.margin_pct, 1) == 2.9


def test_collect_levels_sources(cfg):
    ranging = 100 + 5 * np.sin(np.arange(220) / 3)          # repeated highs/lows -> clusters
    frames = {tf: make_frame(from_closes(ranging, tf=tf), tf, cfg, True) for tf in ("4h", "1d", "1w")}
    kinds = {(l.kind, l.timeframe) for l in collect_levels(frames, 100.0, cfg)}
    assert {("prev_high", "1d"), ("prev_low", "1w"), ("round", "round")} <= kinds
    assert any(k == "cluster" for k, _ in kinds)


_CFG = None


def _cfg():
    global _CFG
    if _CFG is None:
        from agent.config import load_config
        _CFG = load_config()
    return _CFG


# ---------------------------------------------------------------- sections
def frame_with(dow, events=()):
    return Frame("x", pd.DataFrame({"close": [1.0] * 10}), [], list(events), dow, dow)


def test_dow_section(cfg):
    assert dow_section(frame_with(1), frame_with(1), 1, cfg) == 15
    assert dow_section(frame_with(0), frame_with(1), 1, cfg) == 8
    assert dow_section(frame_with(1), frame_with(0), 1, cfg) == 8     # D aligned, 4H ranging
    assert dow_section(frame_with(1), frame_with(-1), 1, cfg) == 0
    choch = frame_with(1, [StructureEvent(9, "CHoCH", -1, 1.0)])
    assert dow_section(frame_with(1), choch, 1, cfg) == 0
    assert dow_section(frame_with(-1), frame_with(-1), -1, cfg) == 15


def test_levels_section(cfg):
    p = build_trade(1, fake_h4(143.0, 2.0), [lvl(142.7)], cfg)
    assert levels_section(p, cfg) == 8
    p.support_tf, p.support_touches = "1d", 3
    assert levels_section(p, cfg) == 15
    p.support_kind = "round"
    assert levels_section(p, cfg) == 0


def test_confirmation_points_and_grades(cfg):
    assert [confirmation_points(n, cfg) for n in range(6)] == [0, 0, 5, 7.5, 10, 10]
    assert [grade_for(s, cfg) for s in (85, 75, 74.9, 65, 64, 55, 54.9)] == \
        ["A", "A", "B", "B", "Watch", "Watch", None]


# ------------------------------------------------------------ full L6 run
def fr(cfg, closes, tf, vols=None):
    return make_frame(from_closes(closes, vols, tf=tf), tf, cfg, True)


@pytest.fixture
def setup(cfg):
    """Up-trend on W/D; on 4H a pullback retests the broken high at ~118.2 (flip) and
    turns up with a fresh higher low; on 1H a CHoCH up with a volume trigger candle."""
    up = trend(start=9.7)                                  # ends near the 4H price
    w, d = fr(cfg, up, "1w"), fr(cfg, up, "1d")
    h4p = waypoints([(0, 100), (12, 110), (18, 106), (30, 118), (36, 113), (48, 128),
                     (58, 118.3), (61, 119.2)], prefix=150, level=100)
    v4 = np.full(len(h4p), 1000.0)
    v4[-16:-6] = 700
    h1p = waypoints([(0, 119.5), (10, 118.3), (14, 119.0), (17, 118.6), (22, 119.3)],
                    prefix=150, level=119.5)
    v1 = np.full(len(h1p), 1000.0)
    v1[-1] = 2500
    frames = {"1w": w, "1d": d, "4h": fr(cfg, h4p, "4h", v4), "1h": fr(cfg, h1p, "1h", v1)}
    majors = compute_majors({"BTC": {"1w": w, "1d": d, "4h": w},
                             "ETH": {"1w": w, "1d": d}, "ETHBTC": {"1d": d}}, 1, cfg)
    alt_season = make_regime({"usdt_d": -1, "btc_d": -1, "total2": 1}, cfg)
    return frames, majors, alt_season


def test_full_long_signal(cfg, setup):
    frames, majors, regime = setup
    e = evaluate("SOL", 1, frames, regime, majors, cfg, funding=0.0001)
    assert all(e.gates.values()) and e.gates.keys() == {"regime", "phase", "rr", "confirmation"}
    assert e.phase_d == "TREND_UP" and e.cycle == "fresh_turn"
    assert {"choch", "trigger_candle", "rvol"} <= set(e.confirmations)
    assert e.plan.order == "limit" and e.plan.support == pytest.approx(118.15, abs=0.05)
    assert e.plan.tp1_r >= 2 and (e.plan.tp2 - e.plan.tp1) > 0
    assert e.score == pytest.approx(sum(e.sections.values()))
    assert e.grade in ("A", "B") and e.is_signal and not e.flags
    share = cfg["grades"]["risk_share"][e.grade]
    assert e.plan.risk_pct == pytest.approx(cfg["trade"]["base_risk_pct"] * share)
    assert e.plan.leverage <= cfg["trade"]["leverage_cap"]


def test_short_side_fails_phase_gate(cfg, setup):
    frames, majors, _ = setup
    both = make_regime({"usdt_d": 0, "btc_d": 0, "total2": 0}, cfg)
    e = evaluate("SOL", -1, frames, both, majors, cfg)
    assert e.gates["phase"] is False and e.grade is None


def test_regime_gate(cfg, setup):
    frames, majors, _ = setup
    risk_off = make_regime({"usdt_d": 1, "btc_d": 1, "total2": -1}, cfg)
    e = evaluate("SOL", 1, frames, risk_off, majors, cfg)
    assert e.gates["regime"] is False and e.grade is None


def test_funding_crowded_costs_five_points(cfg, setup):
    frames, majors, regime = setup
    base = evaluate("SOL", 1, frames, regime, majors, cfg, funding=0.0001)
    crowded = evaluate("SOL", 1, frames, regime, majors, cfg, funding=0.0006)   # 0.06%
    assert crowded.score == pytest.approx(base.score - 5) and "funding_crowded" in crowded.flags


def test_without_confirmation_it_is_only_a_watch(cfg, setup):
    frames, majors, regime = setup
    flat = 119.3 + 0.02 * np.sin(np.arange(200))
    frames = {**frames, "1h": fr(cfg, flat, "1h")}
    e = evaluate("SOL", 1, frames, regime, majors, cfg, funding=0.0001)
    assert e.gates["confirmation"] is False and "not_confirmed" in e.flags
    assert e.grade in ("Watch", None) and not e.is_signal


def test_neutral_regime_turns_b_into_watch(cfg, setup):
    frames, majors, _ = setup
    neutral = make_regime({"usdt_d": 0, "btc_d": 0, "total2": 0}, cfg)
    e = evaluate("SOL", 1, frames, neutral, majors, cfg, funding=0.0001)
    assert cfg["grades"]["B"] <= e.score < cfg["grades"]["A"]         # a B setup...
    assert e.grade == "Watch" and "neutral_regime_needs_A" in e.flags  # ...is only a watch


def test_btc_weak_blocks_alt_longs_under_80(cfg, setup):
    frames, _, regime = setup
    down = fr(cfg, trend(drift=-0.5, start=250), "1d")
    weak = compute_majors({"BTC": {"1w": down, "1d": down, "4h": down},
                           "ETH": {"1d": down}, "ETHBTC": {"1d": down}}, 1, cfg)
    assert weak.btc_weak
    e = evaluate("SOL", 1, frames, regime, weak, cfg, funding=0.0001)
    assert e.score < 80 and "btc_weak" in e.flags and e.grade == "Watch"
    # BTC itself is not an alt
    assert "btc_weak" not in evaluate("BTC", 1, frames, regime, weak, cfg).flags


def test_flip_level_is_a_usable_support(cfg):
    from agent.layers.trade import flip_level
    p = build_trade(1, fake_h4(101.0, 2.0), [flip_level(100.6, cfg), lvl(110.0)], cfg)
    assert p.support_kind == "flip" and p.support == 100.6 and p.order == "market"
    assert levels_section(p, cfg) == 8
