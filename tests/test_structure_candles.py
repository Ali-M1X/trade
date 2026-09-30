import pytest

from agent.analysis import candles as cd
from agent.analysis.structure import analyze, direction, dow_trend, structure_events
from agent.indicators.swings import Swing, alternate, known_at, pivots
from conftest import candles


def test_dow_trend():
    up = [Swing(0, 0, 10, "L"), Swing(1, 1, 12, "H"), Swing(2, 2, 11, "L"), Swing(3, 3, 13, "H")]
    down = [Swing(0, 0, 13, "H"), Swing(1, 1, 10, "L"), Swing(2, 2, 12, "H"), Swing(3, 3, 9, "L")]
    mixed = [Swing(0, 0, 13, "H"), Swing(1, 1, 10, "L"), Swing(2, 2, 14, "H"), Swing(3, 3, 9, "L")]
    assert (dow_trend(up), dow_trend(down), dow_trend(mixed), dow_trend(up[:3])) == (1, -1, 0, 0)


def test_bos_then_choch_on_zigzag(zigzag):
    sw = alternate(pivots(zigzag, 2, 2))
    ev = [(e.idx, e.kind, e.direction, e.level) for e in structure_events(zigzag, sw)]
    # high 13.2 (confirmed bar 5) broken by close 14 at bar 8; high 15.2 by close 16 at bar 14;
    # the decline closes 12 < HL 12.8 at bar 20: first break against the up-trend
    assert ev == [(8, "BOS", 1, 13.2), (14, "BOS", 1, 15.2), (20, "CHoCH", -1, 12.8)]


def test_structure_events_are_causal(zigzag):
    sw = alternate(pivots(zigzag, 2, 2))
    full = structure_events(zigzag, sw)
    for k in range(len(zigzag)):
        cut = zigzag.iloc[:k + 1]
        part = structure_events(cut, known_at(alternate(pivots(cut, 2, 2)), k))
        assert part == [e for e in full if e.idx <= k]


def test_direction_votes():
    assert direction(1, 10, 9, 8, 2) == 1
    assert direction(1, 10, 11, 12, 2) == 0          # structure up but below both MAs: conflict
    assert direction(0, 10, 11, 12, 2) == -1
    assert direction(0, 10, 9, 8, 2) == 1
    assert direction(1, 10, 11, 8, 2) == 0
    assert direction(-1, 10, float("nan"), 8, 2) == -1


def test_analyze_at_bar(cfg, zigzag):
    cfg["swings"]["pivot_lr"]["4h"] = 2
    at17 = analyze(zigzag, "4h", cfg, bar=17)
    assert at17.dow == 1 and at17.last_event.kind == "BOS"
    end = analyze(zigzag, "4h", cfg)
    assert end.last_event.kind == "CHoCH" and end.last_event.direction == -1


# ------------------------------------------------------------------ candles
def test_engulfing():
    prev, cur = candles([(10, 10.2, 9.4, 9.5), (9.4, 10.6, 9.3, 10.5)]).iloc
    assert cd.engulfing(prev, cur, 1) and not cd.engulfing(prev, cur, -1)
    small = candles([(9.6, 10.1, 9.5, 10.0)]).iloc[0]
    assert not cd.engulfing(prev, small, 1)


def test_pin_bar_and_strong_close(cfg):
    t = cfg["technical"]["candles"]
    hammer = candles([(10, 10.3, 8, 10.2)]).iloc[0]      # lower wick 2, body 0.2
    assert cd.pin_bar(hammer, 1, t) and not cd.pin_bar(hammer, -1, t)
    assert cd.strong_close(hammer, 1, 25)                 # close at 96% of range
    marubozu = candles([(10, 11, 10, 11)]).iloc[0]
    assert not cd.pin_bar(marubozu, 1, t)
    assert cd.strong_close(candles([(11, 11, 10, 10)]).iloc[0], -1, 25)


@pytest.mark.parametrize("o, c, ok, why", [
    (7.5, 8.5, True, "wick 75%, body 10%, opposite 15%"),
    (6.5, 7.5, False, "opposite wick 25% of range"),
    (5.8, 8.0, False, "wick 58% of range (< 60%) though >= 2x body"),
    (8.0, 10.0, True, "wick 80%, body 20%, no opposite wick"),
    (5.0, 5.2, False, "near-doji, wicks 50% / 48% (the old rule accepted it)"),
    (6.0, 9.2, False, "body 32%, wick 60% but < 2x body"),
])
def test_pin_bar_strict_rules(cfg, o, c, ok, why):
    """Range 0..10. Long pin bars: lower wick = min(o, c)."""
    bar = candles([(o, 10, 0, c)]).iloc[0]
    assert cd.pin_bar(bar, 1, cfg["technical"]["candles"]) is ok, why


def test_pin_bar_short_mirror(cfg):
    t = cfg["technical"]["candles"]
    shooting_star = candles([(2.5, 10, 0, 1.5)]).iloc[0]   # upper wick 75%, lower 15%
    assert cd.pin_bar(shooting_star, -1, t) and not cd.pin_bar(shooting_star, 1, t)


def test_size_class():
    c = candles([(10, 12, 9.5, 11.5)]).iloc[0]            # body 1.5
    assert cd.size_class(c, 1.0, 0.8, 2.0, 2.5) == "ok"
    assert cd.size_class(c, 0.7, 0.8, 2.0, 2.5) == "large"  # 2.14 ATR
    assert cd.size_class(c, 0.5, 0.8, 2.0, 2.5) == "chase"  # 3 ATR
    assert cd.size_class(c, 5.0, 0.8, 2.0, 2.5) == "small"


def test_trigger_candle(cfg):
    df = candles([(10, 10.2, 9.4, 9.5), (9.4, 10.6, 9.3, 10.5), (10, 10.3, 8, 10.2),
                  (10, 10.9, 9.9, 10.8), (10, 10.6, 9.9, 10.1)])
    assert cd.trigger_candle(df, 1, 1, cfg) == "engulfing"
    assert cd.trigger_candle(df, 2, 1, cfg) == "pin_bar"
    assert cd.trigger_candle(df, 3, 1, cfg) == "strong_close"
    assert cd.trigger_candle(df, 4, 1, cfg) is None


def test_weakening():
    # three green candles, bodies 1.0 -> 0.6 -> 0.3, upper wicks 0.1 -> 0.4 -> 0.8
    df = candles([(10, 11.1, 10, 11), (11, 12, 11, 11.6), (11.6, 12.7, 11.6, 11.9)])
    assert cd.weakening(df, 2, 1, 3)
    assert not cd.weakening(df, 2, -1, 3)
    assert not cd.weakening(df, 1, 1, 3)
