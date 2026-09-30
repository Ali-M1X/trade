import pytest

from agent.analysis.cycles import cycle_state, fresh_turn, score_cycles
from agent.analysis.frame import make_frame
from agent.analysis.patterns import find_patterns
from agent.analysis.phase import Phase, detect_phase, reversal_signs
from builders import accumulation, from_closes, trend, waypoints, with_last_volume


def frame(cfg, closes, tf="4h", volumes=None):
    return make_frame(from_closes(closes, volumes, tf=tf), tf, cfg, with_indicators=True)


# ------------------------------------------------------------------ phase
def test_trend_phases(cfg):
    assert detect_phase(frame(cfg, trend()), cfg) == Phase("TREND_UP")
    assert detect_phase(frame(cfg, trend(drift=-0.5, start=250)), cfg) == Phase("TREND_DOWN")


@pytest.mark.parametrize("kw, event", [({}, None), ({"spring": True}, "spring"),
                                       ({"breakout": True}, "breakout")])
def test_accumulation_and_events(cfg, kw, event):
    closes, vols = accumulation(**kw)
    ph = detect_phase(frame(cfg, closes, volumes=vols), cfg)
    assert ph == Phase("ACC", event) and ph.direction == 1


def test_distribution_mirrors_accumulation(cfg):
    closes, vols = accumulation()
    # a rally, then a range above MA99; the heavy candles are now the red ones
    ph = detect_phase(frame(cfg, 200 - closes, volumes=vols), cfg)
    assert ph == Phase("DIST")


def test_range_without_volume_edge_is_plain_range(cfg):
    closes, _ = accumulation()
    assert detect_phase(frame(cfg, closes), cfg) == Phase("RANGE")   # equal volume


def test_reversal_signs(cfg):
    down = frame(cfg, trend(drift=-0.5, start=250))
    assert "ma99" in reversal_signs(down, 1, cfg)
    assert reversal_signs(frame(cfg, trend()), 1, cfg) == ["divergence"]


# ------------------------------------------------------------------ cycles
FRESH = [(0, 50), (20, 60), (30, 55), (40, 65), (48, 60), (52, 62)]
CORRECTING = [(0, 50), (20, 60), (30, 55), (40, 65), (46, 61)]
LATE = [(0, 50), (12, 60), (18, 56), (30, 66), (36, 62), (90, 90)]


@pytest.fixture
def up_wd(cfg):
    return frame(cfg, trend(), "1w"), frame(cfg, trend(), "1d")


@pytest.mark.parametrize("path, points, watch, note", [
    (FRESH, 10, False, "fresh_turn"),
    (CORRECTING, 5, True, "lower_correcting"),
    (LATE, 0, False, "late"),
])
def test_cycle_scores(cfg, up_wd, path, points, watch, note):
    w, d = up_wd
    s = score_cycles(w, d, frame(cfg, waypoints(path)), 1, cfg)
    assert (s.points, s.watch_only, s.note, s.risk_multiplier) == (points, watch, note, 1.0)


def test_counter_trend_cycle_halves_risk(cfg, up_wd):
    w, _ = up_wd
    d_down = frame(cfg, trend(drift=-0.5, start=250), "1d")
    s = score_cycles(w, d_down, frame(cfg, waypoints(FRESH)), 1, cfg)
    assert (s.points, s.risk_multiplier) == (3, 0.5)


def test_higher_ranging_with_medium_and_lower_aligned(cfg, up_wd):
    from dataclasses import replace
    w, d = up_wd
    w_range = replace(w, direction=0)                     # weekly structure ranging
    s = score_cycles(w_range, d, frame(cfg, waypoints(FRESH)), 1, cfg)
    assert (s.points, s.note) == (5, "higher_ranging")
    s = score_cycles(w_range, d, frame(cfg, waypoints(CORRECTING)), 1, cfg)
    assert s.points == 0                                  # 4H not aligned


def test_cycles_not_aligned_with_side(cfg, up_wd):
    w, d = up_wd
    assert score_cycles(w, d, frame(cfg, waypoints(FRESH)), -1, cfg).points == 0


def test_cycle_state_and_fresh_turn(cfg):
    f = frame(cfg, waypoints(FRESH))
    st = cycle_state(f, cfg)
    assert (st.major, st.leg, st.bars_in_leg) == (1, 1, 4) and not st.late
    assert fresh_turn(f, 1, cfg) and not fresh_turn(f, -1, cfg)


# ------------------------------------------------------------------ patterns
PATTERNS = {
    "double_bottom": ([(0, 60), (10, 50), (18, 55), (26, 50.2), (33, 54.6), (34, 56)], 1),
    "double_top": ([(0, 40), (10, 50), (18, 45), (26, 49.8), (33, 45.4), (34, 44)], -1),
    "inverse_head_shoulders": ([(0, 60), (8, 52), (14, 56), (22, 48), (30, 56.2), (38, 52.2),
                                (45, 55.8), (46, 57.5)], 1),
    "triangle": ([(0, 40), (8, 56), (14, 46), (20, 54), (26, 48), (32, 53), (33, 57)], 1),
    "bull_flag": ([(0, 50), (10, 51), (20, 62), (26, 59), (31, 61.5), (32, 63)], 1),
    "rising_wedge": ([(0, 40), (6, 50), (12, 46), (18, 52), (24, 50), (29, 51.5), (30, 48.5)], -1),
}


@pytest.mark.parametrize("name", PATTERNS)
def test_pattern_confirmed_by_neckline_break_with_volume(cfg, name):
    path, direction = PATTERNS[name]
    closes = waypoints(path)
    found = {p.name: p for p in find_patterns(frame(cfg, closes, volumes=with_last_volume(closes)), cfg)}
    assert name in found and found[name].direction == direction


def test_pattern_needs_volume_on_the_break(cfg):
    closes = waypoints(PATTERNS["double_bottom"][0])
    assert find_patterns(frame(cfg, closes, volumes=with_last_volume(closes, 1000)), cfg) == []


def test_pattern_needs_a_break(cfg):
    closes = waypoints(PATTERNS["double_bottom"][0][:-1])          # stops under the neckline
    assert find_patterns(frame(cfg, closes, volumes=with_last_volume(closes)), cfg) == []
