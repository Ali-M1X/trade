import numpy as np
import pandas as pd
import pytest

from agent.analysis.classic import classic_flags, impulse, vcp
from agent.analysis.frame import make_frame
from agent.indicators.core import adx
from agent.signals.lifecycle import H4, new_signal, on_4h_close, on_candle

T0 = 1_760_000_000_000
M15 = 900_000


def path(legs, start=100.0, vol=lambda step, i: 1.0, wick=0.1):
    """legs: [(bars, move per bar)] -> 4H candles; vol(step, leg_index) gives the volume."""
    rows, p = [], start
    for k, (bars, step) in enumerate(legs):
        for _ in range(bars):
            o, c = p, p + step
            rows.append((o, max(o, c) + wick, min(o, c) - wick, c, vol(step, k)))
            p = c
    df = pd.DataFrame(rows, columns=["open", "high", "low", "close", "volume"])
    df.insert(0, "ts", np.arange(len(df)) * H4)
    return df


def frame(df, cfg):
    return make_frame(df, "4h", cfg, with_indicators=True)


# ------------------------------------------------------------------ ADX
def test_adx_high_in_a_trend_low_in_a_range():
    trend = path([(120, 1.0)])
    chop = path([(4, 1.0), (4, -1.0)] * 15)
    assert adx(trend, 14).iloc[-1] > 40
    assert adx(chop, 14).iloc[-1] < 20


# ------------------------------------------------------------------ Elder impulse
def test_impulse_colours(cfg):
    up = frame(path([(80, 1.0), (3, 2.0)]), cfg)
    down = frame(path([(80, 1.0), (12, -1.5)]), cfg)
    assert impulse(up, "ema13") == 1
    assert impulse(down, "ema13") == -1


# ------------------------------------------------------------------ VCP
def test_vcp_needs_shallower_and_quieter_corrections(cfg):
    p = cfg["technical"]["classic"]
    vol_quiet = lambda step, k: 1.0 if step > 0 else 3.0 / (k + 1)        # noqa: E731
    good = [(40, 0.5), (10, 1.0), (8, -1.5), (10, 1.0), (6, -1.0), (10, 1.0), (4, -0.6), (4, 1.0)]
    f = frame(path(good, vol=vol_quiet), cfg)
    assert vcp(f, 1, p)
    loud = lambda step, k: 1.0 if step > 0 else float(k)                  # noqa: E731
    assert not vcp(frame(path(good, vol=loud), cfg), 1, p)
    deeper = [(40, 0.5), (10, 1.0), (4, -0.6), (10, 1.0), (6, -1.0), (10, 1.0), (8, -1.5), (4, 1.0)]
    assert not vcp(frame(path(deeper, vol=vol_quiet), cfg), 1, p)


def test_flags_only_when_switched_on(cfg):
    f = frame(path([(80, 1.0), (12, -1.5)]), cfg)
    frames = {"4h": f, "1w": f}
    assert classic_flags(frames, 1, cfg) == []
    cfg["technical"]["classic"].update(impulse=True, adx_min=20, vcp=True)
    flags = classic_flags(frames, 1, cfg)
    assert "impulse_against_4h" in flags and "no_vcp" in flags


# ------------------------------------------------------------------ let winners run
def test_trail_from_tp1_keeps_the_rest_open(cfg):
    cfg["trade"].update(tp1_close_pct=25, tp2_close_pct=0, trail_from="tp1")
    s = new_signal({"side": 1, "order": "market", "entry": 100.0, "sl": 95.0, "tp1": 110.0,
                    "tp2": 115.0}, T0, cfg)
    on_candle(s, T0 + M15, 111, 104, cfg)                       # TP1: 25% off, stop to entry
    assert s["status"] == "tp1" and s["open_frac"] == pytest.approx(0.75)
    on_candle(s, T0 + 2 * M15, 130, 112, cfg)                    # TP2 hit, nothing closed
    assert s["open_frac"] == pytest.approx(0.75)
    ev = on_4h_close(s, T0 + 4 * H4, 128.0, 129.0, False)        # close under MA25
    assert [e.kind for e in ev] == ["tp3"]
    assert s["realized_r"] == pytest.approx(0.25 * 2 + 0.75 * 5.6)


def test_default_still_trails_only_after_tp2(cfg):
    s = new_signal({"side": 1, "order": "market", "entry": 100.0, "sl": 95.0, "tp1": 110.0,
                    "tp2": 115.0}, T0, cfg)
    on_candle(s, T0 + M15, 111, 104, cfg)
    assert on_4h_close(s, T0 + 4 * H4, 108.0, 109.0, False) == []


# ------------------------------------------------------------------ market-phase filters
def test_phase_filters(cfg):
    from agent.analysis.classic import efficiency, phase_flags, stage_ok
    up = frame(path([(220, 0.5)]), cfg)
    assert stage_ok(up, 1, 150, 20) is True and stage_ok(up, -1, 150, 20) is False
    assert efficiency(up, 20, 1) == pytest.approx(1.0)
    chop = frame(path([(220, 0.5)] + [(2, 1.0), (2, -1.0)] * 6), cfg)
    assert abs(efficiency(chop, 20, 1)) < 0.2
    frames = {"1d": up, "4h": up, "1w": up}
    assert phase_flags(frames, -1, cfg) == []                      # all off by default
    cfg["technical"]["classic"].update(weekly_tide=True, stage=True, dmi=True, er_min=0.2)
    assert phase_flags(frames, 1, cfg) == []
    assert set(phase_flags(frames, -1, cfg)) == {"weekly_tide_against", "stage_against",
                                                 "dmi_against", "choppy"}
