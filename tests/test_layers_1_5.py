import numpy as np
import pandas as pd
import pytest

from agent.analysis.frame import make_frame
from agent.layers import scanners as sc
from agent.layers.funnel import run_funnel
from agent.layers.majors import compute_majors, weighted_score
from agent.layers.pairs import PairsResult, returns_corr_beta
from agent.layers.regime import classify, combine, compute_regime, make_regime
from agent.layers.scanners import CoinData, Label
from agent.layers.trade import flip_level, nearest_level_atr
from agent.layers.shortlist import (Candidate, label_points, label_sides, liquidity_points,
                                    regime_points, select)
from builders import from_closes, trend, waypoints

DAY = 86_400_000


def frame(cfg, closes, tf="1d", volumes=None):
    return make_frame(from_closes(closes, volumes, tf=tf), tf, cfg, with_indicators=True)


UP = dict(drift=0.5, start=100)
DOWN = dict(drift=-0.5, start=250)


# ------------------------------------------------------------------ L1
@pytest.mark.parametrize("u, b, t, name", [
    (-1, -1, 1, "alt_season"),
    (-1, 1, 1, "btc_led"), (-1, 1, 0, "btc_led"),
    (1, 1, -1, "risk_off"),
    (1, -1, -1, "capitulation"),
    (0, 0, 0, "neutral"), (-1, -1, 0, "neutral"), (1, 1, 1, "neutral"),
])
def test_regime_table(u, b, t, name):
    assert classify(u, b, t) == name


def test_combine_d_and_4h():
    assert (combine(1, 1), combine(1, 0), combine(0, -1), combine(1, -1)) == (1, 1, -1, 0)


def test_regime_bias_and_risk(cfg):
    alt = make_regime({"usdt_d": -1, "btc_d": -1, "total2": 1}, cfg)
    assert alt.allows(1) and not alt.allows(-1) and alt.risk_for(1) == 1.0
    led = make_regime({"usdt_d": -1, "btc_d": 1, "total2": 1}, cfg)
    assert led.risk_for(1, is_btc=True) == 1.0
    assert led.risk_for(1, btc_pair_up=True) == 1.0
    assert led.risk_for(1) == 0.5                       # other alts: half risk
    off = make_regime({"usdt_d": 1, "btc_d": 1, "total2": -1}, cfg)
    assert off.allows(-1) and not off.allows(1) and off.risk_for(1) == 0
    neutral = make_regime({"usdt_d": 0, "btc_d": 0, "total2": 0}, cfg)
    assert neutral.allows(1) and neutral.allows(-1)
    assert (neutral.risk, neutral.min_grade) == (0.5, "A")


def dominance_series(usdt, btc, total2, days=300):
    """4H snapshots where each index follows a zigzag trend (+1 up, -1 down)."""
    n = days * 6
    i = np.arange(n)

    def path(d, base):
        return base * (1 + d * 0.0006 * i + 0.02 * np.sin(i / 20))
    return pd.DataFrame({"ts": 1_690_000_000_000 + i * 4 * 3600 * 1000,
                         "usdt_d": path(usdt, 5), "btc_d": path(btc, 55),
                         "total2": path(total2, 1e12)})


@pytest.mark.parametrize("dirs, name", [((-1, -1, 1), "alt_season"), ((1, 1, -1), "risk_off"),
                                        ((-1, 1, 1), "btc_led"), ((1, -1, -1), "capitulation")])
def test_compute_regime_from_snapshots(cfg, dirs, name):
    r = compute_regime(dominance_series(*dirs), cfg)
    assert (r.usdt_d, r.btc_d, r.total2) == dirs and r.name == name


# ------------------------------------------------------------------ L2
@pytest.fixture
def up_frames(cfg):
    return {tf: frame(cfg, trend(**UP), tf) for tf in ("1w", "1d", "4h")}


@pytest.fixture
def down_frames(cfg):
    return {tf: frame(cfg, trend(**DOWN), tf) for tf in ("1w", "1d", "4h")}


def test_weighted_score(cfg, up_frames, down_frames):
    w = cfg["majors"]["weights"]
    assert weighted_score(up_frames, w) == 6 and weighted_score(down_frames, w) == -6
    mixed = {"1w": up_frames["1w"], "1d": down_frames["1d"], "4h": up_frames["4h"]}
    assert weighted_score(mixed, w) == 2


def test_majors_flags(cfg, up_frames, down_frames):
    m = compute_majors({"BTC": down_frames, "ETH": up_frames, "ETHBTC": up_frames}, 1, cfg)
    assert m.btc == -6 and m.btc_weak and m.ethbtc_up_d
    assert m.divergence and m.risk_multiplier == 0.5      # BTC down, TOTAL2 up
    m = compute_majors({"BTC": up_frames, "ETH": up_frames, "ETHBTC": down_frames}, 1, cfg)
    assert not (m.btc_weak or m.divergence or m.ethbtc_up_d) and m.risk_multiplier == 1.0


# ------------------------------------------------------------------ L3
def test_squeeze(cfg):
    i = np.arange(200)
    tight = np.r_[100 + 5 * np.sin(i[:170] / 2), 100 + 0.1 * np.sin(i[170:] / 2)]
    s = cfg["scanners"]["squeeze"]
    assert sc.squeeze(frame(cfg, tight), s)
    assert not sc.squeeze(frame(cfg, 100 + 5 * np.sin(i / 2)), s)


def test_early_trend_needs_volume(cfg):
    closes = np.r_[100 + 0.5 * np.sin(np.arange(199)), 104]
    vols = np.r_[np.full(199, 1000.0), 3000]
    s = cfg["scanners"]["early_trend"]
    assert sc.early_trend(frame(cfg, closes, volumes=vols), s)
    assert not sc.early_trend(frame(cfg, closes), s)          # RVOL 1


def test_pullback_needs_falling_volume(cfg):
    closes = waypoints([(0, 50), (20, 60), (30, 56), (50, 70), (56, 64)], prefix=120, level=50)
    vols = np.full(len(closes), 1000.0)
    vols[-6:] = 600
    s = cfg["scanners"]["pullback"]
    assert sc.pullback(frame(cfg, closes, volumes=vols), s, cfg)   # 43% retracement of 56->70
    assert not sc.pullback(frame(cfg, closes), s, cfg)


def test_exhaustion_short_after_parabolic_move(cfg):
    closes = np.r_[50 + 0.3 * np.sin(np.arange(150)), np.linspace(50, 90, 20)]
    h4 = frame(cfg, waypoints([(0, 50), (8, 60), (14, 56), (22, 61), (26, 59)]), "4h")
    assert sc.exhaustion(frame(cfg, closes), h4, cfg["scanners"]["exhaustion"], cfg) == -1
    assert sc.exhaustion(frame(cfg, trend(**UP)), h4, cfg["scanners"]["exhaustion"], cfg) == 0


def test_oi_buildup(cfg):
    d = frame(cfg, np.full(60, 100.0) + 0.1 * np.sin(np.arange(60)))
    s = cfg["scanners"]["oi_buildup"]
    oi = pd.DataFrame({"ts": range(5), "oi": [100, 100, 101, 110, 118]})
    assert sc.oi_buildup(oi, d, s)                         # +16.8% OI in 3 days, flat price
    assert not sc.oi_buildup(oi.assign(oi=[100, 100, 101, 105, 110]), d, s)
    assert not sc.oi_buildup(None, d, s)


def test_funding_extreme(cfg):
    s = cfg["scanners"]["funding_extreme"]
    assert sc.funding_extreme([0.0001, 0.0006, 0.0007, 0.0008], s) == -1   # crowd long
    assert sc.funding_extreme([-0.0004, -0.0005, -0.0004], s) == 1
    assert sc.funding_extreme([0.0006, 0.0001, 0.0007], s) == 0
    assert sc.funding_extreme([0.0006], s) == 0


def test_volume_anomaly(cfg):
    closes = np.full(40, 100.0) + 0.1 * np.sin(np.arange(40))
    vols = np.r_[np.full(39, 1000.0), 3500]
    s = cfg["scanners"]["volume_anomaly"]
    assert sc.volume_anomaly(frame(cfg, closes, volumes=vols), s)
    moved = closes.copy()
    moved[-1] = 110                                        # +10%: not an anomaly
    assert not sc.volume_anomaly(frame(cfg, moved, volumes=vols), s)


def test_category_growth():
    markets = [
        {"id": "a", "market_cap": 110, "price_change_percentage_7d_in_currency": 10},
        {"id": "b", "market_cap": 90, "price_change_percentage_7d_in_currency": -10},
        {"id": "c", "market_cap": 200, "price_change_percentage_7d_in_currency": 100},
        {"id": "d", "market_cap": 50, "price_change_percentage_7d_in_currency": None},
    ]
    cats = {"a": ["AI", "L2"], "b": ["L2"], "c": ["AI", "Meme"], "d": ["AI"]}
    g = dict(sc.category_growth(markets, cats, "price_change_percentage_7d_in_currency", 2, []))
    assert set(g) == {"AI", "L2"}                          # Meme has one coin
    assert g["AI"] == pytest.approx(310 / 200 - 1)         # then: 100 + 100
    assert g["L2"] == pytest.approx(200 / 200 - 1)


def test_filter_by_bias():
    labels = [Label("EARLY_TREND", 1), Label("EXHAUSTION", -1), Label("SQUEEZE", 0)]
    assert [l.name for l in sc.filter_by_bias(labels, "short")] == ["EXHAUSTION", "SQUEEZE"]
    assert [l.name for l in sc.filter_by_bias(labels, "long")] == ["EARLY_TREND", "SQUEEZE"]
    assert sc.filter_by_bias(labels, "both") == labels and sc.filter_by_bias(labels, "none") == []


# ------------------------------------------------------------------ L4
def test_corr_and_beta():
    ts = np.arange(40) * DAY
    btc = pd.DataFrame({"ts": ts, "close": 100 * np.cumprod(1 + 0.01 * np.sin(np.arange(40)))})
    r = btc["close"].pct_change().fillna(0)
    coin = pd.DataFrame({"ts": ts, "close": 10 * np.cumprod(1 + 2 * r)})
    corr, beta = returns_corr_beta(coin, btc, 30)
    assert corr == pytest.approx(1) and beta == pytest.approx(2)


def pairs(dirs, corr=0.6, rs=0.05):
    return PairsResult(dirs, corr, float("nan"), 1.0, rs, False)


def test_pair_strength(cfg):
    all_up = {q: {"1d": 1, "4h": 1} for q in ("USDT", "BTC", "ETH")}
    assert pairs(all_up).strength(1, cfg) == 1 and pairs(all_up).strength(-1, cfg) == 0
    riding_btc = {"USDT": {"1d": 1, "4h": 1}, "BTC": {"1d": -1, "4h": -1}, "ETH": {"1d": 0, "4h": 0}}
    assert pairs(riding_btc).strength(1, cfg) == pytest.approx(0.5 + 0.15 * 0.5)
    independent = pairs(riding_btc, corr=0.3, rs=0.1)
    assert independent.strength(1, cfg) == pytest.approx(0.575 + 0.1)
    assert independent.strength(1, cfg, neutral_regime=True) == pytest.approx(0.575 + 0.2)


def test_high_correlation_needs_btc_alignment(cfg):
    p = pairs({}, corr=0.9)
    assert p.allowed(1, 1, cfg) and not p.allowed(1, 0, cfg) and not p.allowed(-1, 1, cfg)
    assert pairs({}, corr=0.7).allowed(1, -1, cfg)


# ------------------------------------------------------------------ L5
def test_liquidity_points(cfg):
    assert liquidity_points(10e6, 0.05, cfg) == pytest.approx(5)          # min volume, best spread
    assert liquidity_points(500e6, 0.05, cfg) == pytest.approx(15)
    assert liquidity_points(5e9, 0.5, cfg) == pytest.approx(10)
    assert 10 < liquidity_points(500e6, 0.175, cfg) < 15


def test_regime_and_label_points(cfg, up_frames):
    majors = compute_majors({"BTC": up_frames, "ETH": up_frames, "ETHBTC": up_frames}, 1, cfg)
    alt = make_regime({"usdt_d": -1, "btc_d": -1, "total2": 1}, cfg)
    neutral = make_regime({"usdt_d": 0, "btc_d": 0, "total2": 0}, cfg)
    assert regime_points(1, alt, majors, cfg) == 30
    assert regime_points(1, neutral, majors, cfg) == pytest.approx(22.5)
    assert regime_points(-1, alt, majors, cfg) == 0
    assert label_points([Label("EARLY_TREND", 1)], cfg) == 12.5
    assert label_points([Label("EARLY_TREND", 1), Label("PULLBACK", 1), Label("SQUEEZE", 0)], cfg) == 25
    assert label_sides([Label("SQUEEZE", 0)]) == {1, -1}


def test_select_best_side_threshold_and_cap(cfg):
    cands = [Candidate(f"C{i}", 1, 60 + i, []) for i in range(12)]
    cands += [Candidate("C11", -1, 50, []), Candidate("LOW", 1, 59.9, [])]
    out = select(cands, cfg)
    assert [c.base for c in out] == [f"C{i}" for i in range(11, 3, -1)]   # top 8, >= 60
    assert out[0].side == 1


# ------------------------------------------------------------------ funnel
def test_funnel_end_to_end(cfg, up_frames, down_frames):
    dom = dominance_series(-1, -1, 1)                       # alt season: longs only
    majors = {"BTC": up_frames, "ETH": up_frames, "ETHBTC": up_frames}
    closes = np.r_[100 + 0.5 * np.sin(np.arange(199)), 104]
    vols = np.r_[np.full(199, 1000.0), 3000]
    breakout = {"1d": frame(cfg, closes, volumes=vols), "4h": frame(cfg, closes, "4h", vols)}
    coin = CoinData("SOL", {"id": "solana", "total_volume": 400e6, "market_cap": 1},
                    breakout, {"1d": up_frames["1d"], "4h": up_frames["4h"]},
                    {"1d": up_frames["1d"], "4h": up_frames["4h"]}, spread_pct=0.02)
    thin = CoinData("THIN", {"id": "thin", "total_volume": 1e6}, breakout)
    res = run_funnel(dom, majors, [coin, thin], [], {}, cfg)
    assert res.regime.name == "alt_season"
    assert res.watchlist == {"SOL": ["EARLY_TREND"]}        # THIN fails the liquidity filter
    [c] = res.shortlist
    assert c.base == "SOL" and c.side == 1 and c.score >= 60
    assert c.parts["ethbtc"] == 5 and c.parts["regime"] == 30
    assert res.to_dict()["shortlist"][0]["base"] == "SOL"


def test_select_ranks_near_level_first(cfg):
    far = Candidate("FAR", 1, 95, [], level_atr=4.0)
    near = Candidate("NEAR", 1, 70, [], level_atr=1.2)
    none = Candidate("NONE", 1, 99, [], level_atr=None)
    assert [c.base for c in select([far, near, none], cfg)] == ["NEAR", "NONE", "FAR"]


def test_nearest_level_atr():
    levels = [flip_level(100.0, _cfg()), flip_level(96.0, _cfg()), flip_level(110.0, _cfg())]
    assert nearest_level_atr(1, 103.0, 2.0, levels) == 1.5
    assert nearest_level_atr(-1, 103.0, 2.0, levels) == 3.5
    assert nearest_level_atr(1, 90.0, 2.0, levels) is None


def _cfg():
    from agent.config import load_config
    return load_config()


def test_breakout_watch_keeps_coin_for_five_days(cfg, up_frames):
    dom = dominance_series(-1, -1, 1)
    majors = {"BTC": up_frames, "ETH": up_frames, "ETHBTC": up_frames}
    base = np.r_[100 + 0.5 * np.sin(np.arange(199)), 104]
    vols = np.r_[np.full(199, 1000.0), 3000]

    def coin(closes, v=None):
        return CoinData("SOL", {"id": "solana", "total_volume": 400e6},
                        {"1d": frame(cfg, closes, volumes=v), "4h": frame(cfg, closes, "4h", v)})

    t0 = 1_760_000_000_000
    r1 = run_funnel(dom, majors, [coin(base, vols)], [], {}, cfg, now_ms=t0)
    entry = r1.breakout_watch["SOL"]
    assert entry["since"] == t0 and 100 < entry["level"] < 104
    # two days later the breakout candle is no longer the last one: no EARLY_TREND,
    # but the coin stays on the watch with its flip level
    retest = np.r_[base, 102.5, 101.2]
    r2 = run_funnel(dom, majors, [coin(retest)], [], {}, cfg, breakout_watch=r1.breakout_watch,
                    now_ms=t0 + 2 * DAY)
    assert r2.watchlist["SOL"] == ["BREAKOUT_WATCH"]
    assert r2.breakout_watch["SOL"] == entry
    [c] = [c for c in r2.candidates if c.base == "SOL"]
    assert c.flip_level == entry["level"] and c.level_atr is not None
    # after 5 days it is dropped
    r3 = run_funnel(dom, majors, [coin(retest)], [], {}, cfg, breakout_watch=r2.breakout_watch,
                    now_ms=t0 + 6 * DAY)
    assert "SOL" not in r3.breakout_watch and "SOL" not in r3.watchlist


def test_index_candles_keep_millisecond_timestamps():
    from agent.layers.regime import index_candles
    ts = 1_759_968_000_000 + np.arange(12) * 4 * 3600 * 1000       # two days of 4H snapshots
    c = index_candles(pd.DataFrame({"ts": ts, "btc_d": np.arange(12.0)}), "btc_d", "1d")
    assert c["ts"].tolist() == [1_759_968_000_000, 1_759_968_000_000 + DAY]
    assert c["open"].tolist() == [0, 6] and c["close"].tolist() == [5, 11]
