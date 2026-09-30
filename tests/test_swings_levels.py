import pandas as pd
import pytest

from agent.indicators.divergence import divergence
from agent.indicators.levels import (cluster_levels, nearest, previous_period_levels,
                                     round_levels, round_step)
from agent.indicators.swings import Swing, alternate, known_at, legs, mean_leg_bars, pivots
from conftest import candles


def test_pivots_on_zigzag(zigzag):
    p = pivots(zigzag, 2, 2)
    assert [(s.idx, s.kind, s.price) for s in p] == [
        (3, "H", 13.2), (5, "L", 10.8), (9, "H", 15.2), (11, "L", 12.8), (15, "H", 17.2)]
    assert all(s.confirmed == s.idx + 2 for s in p)


def test_equal_high_plateau_gives_one_pivot():
    # bars 3 and 4 share the same high: only bar 3 is the pivot
    assert [s.idx for s in pivots(pd.DataFrame({"high": [1, 2, 3, 5, 5, 3, 2, 1],
                                                 "low": [0] * 8}), 2, 2) if s.kind == "H"] == [3]


def test_known_at_prevents_lookahead(zigzag):
    p = pivots(zigzag, 2, 2)
    assert [s.idx for s in known_at(p, 10)] == [3, 5]
    assert [s.idx for s in known_at(p, 11)] == [3, 5, 9]


def test_alternate_keeps_extreme():
    s = [Swing(1, 2, 10, "H"), Swing(3, 4, 12, "H"), Swing(5, 6, 8, "L"),
         Swing(7, 8, 7, "L"), Swing(9, 10, 11, "H")]
    assert [(x.idx, x.price) for x in alternate(s)] == [(3, 12), (7, 7), (9, 11)]


def test_legs_and_mean_length(zigzag):
    lg = legs(alternate(pivots(zigzag, 2, 2)))
    assert [(l.direction, l.bars) for l in lg] == [(-1, 2), (1, 4), (-1, 2), (1, 4)]
    assert lg[1].size == pytest.approx(4.4)
    assert mean_leg_bars(lg, 2) == 3
    assert mean_leg_bars([], 5) != mean_leg_bars([], 5)  # nan


def test_cluster_levels():
    sw = [Swing(i, i, p, "H") for i, p in enumerate([100, 100.3, 105, 105.2, 104.9, 110])]
    lv = cluster_levels(sw, atr_value=1.0, tol_atr=0.5, min_touches=2, timeframe="1d",
                        tf_weight={"1d": 2})
    assert [(round(l.price, 4), l.touches, l.strength) for l in lv] == [
        (100.15, 2, 4), (105.0333, 3, 5)]
    assert cluster_levels(sw, float("nan"), 0.5, 2, "1d", {}) == []


def test_previous_period_and_round_levels():
    d = candles([(1, 5, 0.5, 2), (2, 6, 1.5, 3), (3, 3.5, 2.5, 3)])
    hi, lo = previous_period_levels(d, "1d", {"1d": 2})
    assert (hi.price, hi.kind, hi.strength, lo.price, lo.kind) == (6, "prev_high", 3, 1.5, "prev_low")
    assert round_step(142) == 50 and round_step(0.53) == 0.05 and round_step(83700) == 5000
    assert [l.price for l in round_levels(142, 2)] == [50, 100, 150, 200]


def test_nearest_levels():
    from agent.indicators.levels import Level
    lv = [Level(p, 2, "4h") for p in (90, 95, 105, 120)]
    s, r = nearest(lv, 100)
    assert (s.price, r.price) == (95, 105)
    assert nearest(lv, 130)[1] is None


def test_divergence():
    osc = pd.Series([50.0] * 10)
    osc[2], osc[6] = 80, 70                          # lower high on the oscillator
    sw = [Swing(2, 4, 100, "H"), Swing(4, 5, 95, "L"), Swing(6, 8, 105, "H")]
    assert divergence(sw, osc) == -1
    osc[4], osc[8] = 20, 30                          # higher low while price makes a lower low
    sw += [Swing(8, 9, 90, "L")]
    assert divergence(sw, osc) == 1                  # the most recent pair wins
    assert divergence(sw[:1], osc) == 0


def test_vectorised_pivots_match_the_plain_definition():
    """pivots() is vectorised; compare it with a direct loop over random data with ties."""
    import numpy as np

    def plain(df, left, right):
        high, low = df["high"].to_numpy(), df["low"].to_numpy()
        out = []
        for i in range(left, len(df) - right):
            if high[i] > high[i - left:i].max() and high[i] >= high[i + 1:i + right + 1].max():
                out.append(Swing(i, i + right, float(high[i]), "H"))
            if low[i] < low[i - left:i].min() and low[i] <= low[i + 1:i + right + 1].min():
                out.append(Swing(i, i + right, float(low[i]), "L"))
        return sorted(out, key=lambda s: (s.idx, s.kind))

    rng = np.random.default_rng(0)
    for _ in range(200):
        n = int(rng.integers(1, 60))
        h = np.round(rng.normal(0, 1, n).cumsum(), 1)          # rounding creates ties
        df = pd.DataFrame({"high": h, "low": h - np.round(rng.random(n), 1)})
        for lr in ((2, 2), (3, 3), (5, 5), (3, 2)):
            assert pivots(df, *lr) == plain(df, *lr)
