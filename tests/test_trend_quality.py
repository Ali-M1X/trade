import numpy as np
import pandas as pd

from agent.analysis.frame import make_frame
from agent.analysis.trend_quality import CHECKS, trend_quality


def path(legs, start=100.0, vol_up=2.0, vol_down=1.0, wick=0.1):
    """legs: [(bars, move per bar)] -> 4H candles walking that path (up legs on vol_up)."""
    rows, p = [], start
    for bars, step in legs:
        for _ in range(bars):
            o, c = p, p + step
            hi, lo = max(o, c) + wick, min(o, c) - wick
            rows.append((o, hi, lo, c, vol_up if step > 0 else vol_down))
            p = c
    df = pd.DataFrame(rows, columns=["open", "high", "low", "close", "volume"])
    df.insert(0, "ts", np.arange(len(df)) * 4 * 3_600_000)
    return df


def frame(df, cfg):
    return make_frame(df, "4h", cfg, with_indicators=True)


def tq_cfg(cfg):
    cfg["technical"]["trend_quality"]["mode"] = "observe"
    return cfg


def test_strong_uptrend_scores_positive_and_short_negative(cfg):
    cfg = tq_cfg(cfg)
    # impulses growing (10, 12, 14, 16 bars of +1..), corrections shrinking and quiet
    legs = [(40, 0.3)]
    for up, down in [(10, 8), (11, 6), (12, 5), (13, 4)]:
        legs += [(up, 1.0 + up / 20), (down, -0.6)]
    legs += [(8, 1.8), (3, -0.5)]
    f = frame(path(legs), cfg)
    long_ = trend_quality(f, 1, cfg)
    assert set(long_.checks) == set(CHECKS)
    assert long_.checks["amplitude"] == 1
    assert long_.checks["volume_legs"] == 1
    assert long_.checks["correction_bars"] == 1
    assert long_.points > 0
    assert trend_quality(f, -1, cfg).points <= 0


def test_fading_uptrend_scores_negative(cfg):
    cfg = tq_cfg(cfg)
    # impulses shrinking and slowing, corrections as big and louder than the impulses
    legs = [(40, 0.3)]
    for up, step in [(14, 1.4), (12, 1.0), (10, 0.7), (8, 0.5)]:
        legs += [(up, step), (8, -0.9)]
    f = frame(path(legs, vol_up=1.0, vol_down=2.0), cfg)
    tq = trend_quality(f, 1, cfg)
    assert tq.checks["impulse_size"] == -1
    assert tq.checks["impulse_speed"] == -1
    assert tq.checks["volume_legs"] == -1
    assert tq.points < 0


def test_opposite_burst_after_small_candles(cfg):
    cfg = tq_cfg(cfg)
    legs = [(60, 0.5), (4, 0.02), (1, -3.0)]
    f = frame(path(legs), cfg)
    assert trend_quality(f, 1, cfg).checks["opposite_burst"] == -1


def test_rsi_stuck_overbought_is_a_warning_for_longs(cfg):
    cfg = tq_cfg(cfg)
    f = frame(path([(80, 1.0)]), cfg)
    assert trend_quality(f, 1, cfg).checks["rsi_persistence"] == -1
    assert trend_quality(f, -1, cfg).checks["rsi_persistence"] == 0


def test_points_are_clamped(cfg):
    cfg = tq_cfg(cfg)
    cfg["technical"]["trend_quality"]["points"] = {k: 50 for k in CHECKS}
    f = frame(path([(80, 1.0)]), cfg)
    assert abs(trend_quality(f, 1, cfg).points) <= cfg["technical"]["trend_quality"]["max_points"]


def test_live_config_keeps_trend_quality_off(cfg):
    from agent.backtest.variants import variant_cfg
    assert cfg["technical"]["trend_quality"]["mode"] == "off"
    live = variant_cfg(cfg, cfg["live"]["variant"])
    assert live["technical"]["trend_quality"]["mode"] == "off"
    assert variant_cfg(cfg, "tq_score")["technical"]["trend_quality"]["mode"] == "score"
