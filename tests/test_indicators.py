import numpy as np
import pandas as pd
import pytest

from agent.indicators.core import (add_indicators, atr, bollinger, ema, macd, obv,
                                   percentile_rank, rsi, rvol, slope_sign, sma, true_range)
from conftest import candles


def test_sma_needs_full_window():
    s = sma(pd.Series([1.0, 2, 3, 4]), 3)
    assert np.isnan(s[1]) and s[2] == 2 and s[3] == 3


def test_rsi_matches_wilder_reference(wilder):
    r = rsi(wilder["close"], 14)
    assert r[:14].isna().all()
    # exact seed: mean gain / mean loss of the first 14 changes
    d = wilder["close"].diff()[1:15]
    g, l = d.clip(lower=0).mean(), (-d.clip(upper=0)).mean()
    assert r[14] == pytest.approx(100 - 100 / (1 + g / l))
    # published values (their sheet rounds intermediate averages to 2 dp)
    exp = wilder["rsi14"].dropna()
    assert np.abs(r[exp.index] - exp).max() < 0.1


def test_rsi_extremes():
    assert rsi(pd.Series(np.arange(30.0)), 14).iloc[-1] == 100
    assert rsi(pd.Series(np.arange(30.0, 0, -1)), 14).iloc[-1] == 0


def test_true_range_and_wilder_atr():
    df = candles([(10, 12, 9, 11), (11, 13, 10, 12), (12, 12.5, 8, 9), (9, 11, 8.5, 10)])
    # TR: 3 (h-l first bar), 3, max(4.5, 0.5, 4)=4.5, max(2.5, 2, 0.5)=2.5
    assert list(true_range(df)) == [3, 3, 4.5, 2.5]
    a = atr(df, 3)
    assert np.isnan(a[1])
    assert a[2] == pytest.approx(3.5)                 # (3 + 3 + 4.5) / 3
    assert a[3] == pytest.approx((3.5 * 2 + 2.5) / 3)


def test_macd_on_constant_and_linear_series():
    flat = macd(pd.Series([5.0] * 60), 12, 26, 9)
    assert flat["macd"].dropna().abs().max() < 1e-12
    # EMA(n) of a line lags by slope*(n-1)/2, so MACD -> slope*(26-12)/2 = 7*slope
    line = macd(pd.Series(np.arange(400.0) * 2), 12, 26, 9)
    assert line["macd"].iloc[-1] == pytest.approx(14, rel=1e-6)
    assert line["hist"].iloc[-1] == pytest.approx(0, abs=1e-6)
    assert ema(pd.Series([1.0, 2, 3]), 3).isna().sum() == 2


def test_rvol_excludes_current_bar():
    v = pd.Series([10.0] * 20 + [30.0])
    r = rvol(v, 20)
    assert np.isnan(r[19]) and r[20] == 3.0


def test_obv():
    df = candles([(1, 1, 1, 10, 5), (1, 1, 1, 11, 7), (1, 1, 1, 9, 4), (1, 1, 1, 9, 9)])
    assert list(obv(df)) == [0, 7, 3, 3]


def test_bollinger_width():
    flat = bollinger(pd.Series([3.0] * 20), 20, 2)
    assert flat["width"].iloc[-1] == 0
    s = pd.Series(np.arange(1.0, 21))
    b = bollinger(s, 20, 2).iloc[-1]
    std = np.std(np.arange(1.0, 21))                  # population std, like TradingView
    assert b["upper"] == pytest.approx(10.5 + 2 * std)
    assert b["width"] == pytest.approx(4 * std / 10.5)


def test_percentile_rank_and_slope():
    s = pd.Series([5.0, 4, 3, 2, 1, 6])
    pr = percentile_rank(s, 5)
    assert pr[4] == 20 and pr[5] == 100
    assert list(slope_sign(pd.Series([1.0, 2, 2, 1]), 1)[1:]) == [1, 0, -1]


def test_add_indicators_columns(cfg):
    n = 150
    close = 100 + np.sin(np.arange(n) / 5) * 5 + np.arange(n) * 0.1
    df = pd.DataFrame({"ts": range(n), "open": close - 0.5, "high": close + 1,
                       "low": close - 1, "close": close, "volume": 1000.0})
    out = add_indicators(df, cfg)
    for col in ["ma7", "ma25", "ma99", "rsi", "macd", "macd_signal", "macd_hist", "atr",
                "rvol", "obv", "bb_width"]:
        assert col in out and not np.isnan(out[col].iloc[-1]), col
    assert "ma7" not in df                            # input untouched


def test_indicators_are_causal(cfg, zigzag):
    """Values up to bar k do not change when later bars are appended."""
    k = 15
    full = add_indicators(zigzag, cfg)
    cut = add_indicators(zigzag.iloc[:k + 1], cfg)
    pd.testing.assert_frame_equal(full.iloc[:k + 1], cut)
