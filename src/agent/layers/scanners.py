"""Layer 3: scanners that tag coins with setup labels."""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from ..analysis.frame import Frame
from ..indicators.core import percentile_rank
from ..indicators.divergence import divergence

LONG, SHORT, BOTH = 1, -1, 0


@dataclass(frozen=True)
class Label:
    name: str
    direction: int          # +1 long, -1 short, 0 either


@dataclass
class CoinData:
    base: str
    info: dict                                        # CoinGecko market row
    frames: dict[str, Frame]                          # COIN/USDT: 1d, 4h (+1w, 1h for L6)
    btc_frames: dict[str, Frame] = field(default_factory=dict)   # COIN/BTC
    eth_frames: dict[str, Frame] = field(default_factory=dict)   # COIN/ETH
    funding: list[float] = field(default_factory=list)            # rates as fractions, oldest first
    oi: pd.DataFrame | None = None                    # ts, oi (daily)
    categories: list[str] = field(default_factory=list)
    spread_pct: float | None = None

    @property
    def is_btc(self) -> bool:
        return self.base == "BTC"


def period_return(f: Frame, bars: int) -> float:
    c = f.df["close"]
    if len(c) <= bars:
        return float("nan")
    return float(c.iloc[-1] / c.iloc[-1 - bars] - 1)


# ---------------------------------------------------------------- scanners
def squeeze(d: Frame, s: dict) -> bool:
    pr = percentile_rank(d.col("bb_width"), s["lookback"]).iloc[-1]
    atr = d.col("atr")
    n = s["atr_falling_bars"]
    return bool(len(atr) > n and pr <= s["bb_width_percentile"] and atr.iloc[-1] < atr.iloc[-1 - n])


def early_trend(d: Frame, s: dict) -> bool:
    n = s["min_range_bars"]
    if d.n <= n:
        return False
    prior_high = d.df["high"].iloc[-n - 1:-1].max()
    return bool(d.close > prior_high and d.last["rvol"] >= s["min_rvol"])


def pullback(d: Frame, s: dict, cfg: dict) -> bool:
    """D up-trend (Dow up and price above MA99; a pullback often dips under MA25);
    price has pulled back into 0.382-0.618 of the last up leg or to MA25/MA99,
    on falling volume."""
    ma_slow = f"ma{cfg['indicators']['ma_slow']}"
    if d.dow != 1 or not d.close > d.last[ma_slow]:
        return False
    if len(d.swings) < 2 or d.swings[-1].kind != "H":
        return False
    low, high = d.swings[-2].price, d.swings[-1].price
    retr = (high - d.close) / (high - low) if high > low else float("nan")
    in_fib = s["fib_min"] <= retr <= s["fib_max"]
    ind = cfg["indicators"]
    near_ma = any(abs(d.close - d.last[f"ma{ind[k]}"]) <= s["ma_touch_atr"] * d.atr
                  for k in ("ma_mid", "ma_slow"))
    falling = d.col("rvol").iloc[-s["volume_falling_bars"]:].mean() < 1
    return bool((in_fib or near_ma) and falling)


def exhaustion(d: Frame, h4: Frame, s: dict, cfg: dict) -> int:
    """Direction of the expected counter-move, or 0."""
    mid = f"ma{cfg['indicators']['ma_mid']}"
    stretch = (d.close - d.last[mid]) / d.atr
    rsi = d.last["rsi"]
    divs = {divergence(h4.swings, h4.col("rsi")), divergence(h4.swings, h4.col("macd_hist"))}
    if stretch > s["ma_distance_atr"] and rsi > s["rsi_high"] and SHORT in divs:
        return SHORT
    if stretch < -s["ma_distance_atr"] and rsi < s["rsi_low"] and LONG in divs:
        return LONG
    return 0


def oi_buildup(oi: pd.DataFrame | None, d: Frame, s: dict) -> bool:
    n = s["days"]
    if oi is None or len(oi) <= n:
        return False
    v = oi["oi"].to_numpy()
    oi_change = (v[-1] / v[-1 - n] - 1) * 100
    price_change = abs(period_return(d, n)) * 100
    return bool(oi_change >= s["min_oi_change_pct"] and price_change < s["max_price_change_pct"])


def funding_extreme(funding: list[float], s: dict) -> int:
    """Against the crowd: persistently high funding -> short, persistently negative -> long."""
    n = s["consecutive"]
    last = [r * 100 for r in funding[-n:]]
    if len(last) < n:
        return 0
    if all(r > s["high_pct"] for r in last):
        return SHORT
    if all(r < s["low_pct"] for r in last):
        return LONG
    return 0


def volume_anomaly(d: Frame, s: dict) -> bool:
    n = s["avg_days"]
    vol = d.df["volume"]
    if len(vol) <= n:
        return False
    avg = vol.iloc[-n - 1:-1].mean()
    return bool(avg > 0 and vol.iloc[-1] > s["multiple"] * avg
                and abs(period_return(d, 1)) * 100 < s["max_price_change_pct"])


# ------------------------------------------------------------- universe-wide
def rs_values(coin: CoinData, btc_d: Frame, days: int) -> float:
    return period_return(coin.frames["1d"], days) - period_return(btc_d, days)


def rs_thresholds(coins: list[CoinData], btc_d: Frame, cfg: dict) -> dict[int, float]:
    """RS value at the top-percentile cut-off for each period."""
    s = cfg["scanners"]["rs_leader"]
    out = {}
    for days in s["periods_days"]:
        vals = [v for v in (rs_values(c, btc_d, days) for c in coins) if not np.isnan(v)]
        out[days] = float(np.percentile(vals, 100 - s["top_percentile"])) if vals else float("inf")
    return out


def rs_leader(coin: CoinData, btc_d: Frame, thresholds: dict[int, float], s: dict) -> bool:
    if coin.is_btc or "1d" not in coin.btc_frames:
        return False
    if not all(rs_values(coin, btc_d, days) >= t for days, t in thresholds.items()):
        return False
    ratio = coin.btc_frames["1d"].df["close"]
    return bool(ratio.iloc[-1] >= ratio.iloc[-s["new_high_lookback"]:].max())


def category_growth(markets: list[dict], coin_categories: dict[str, list[str]], pct_key: str,
                    min_coins: int, ignore: list[str]) -> list[tuple[str, float]]:
    """Market-cap growth of each category over the period, from the universe coins in it:
    sum(cap now) / sum(cap then) - 1, where cap then = cap now / (1 + pct/100)."""
    now: dict[str, float] = {}
    then: dict[str, float] = {}
    count: dict[str, int] = {}
    for m in markets:
        pct, cap = m.get(pct_key), m.get("market_cap")
        if pct is None or not cap:
            continue
        for cat in coin_categories.get(m["id"], []):
            if cat in ignore:
                continue
            now[cat] = now.get(cat, 0) + cap
            then[cat] = then.get(cat, 0) + cap / (1 + pct / 100)
            count[cat] = count.get(cat, 0) + 1
    growth = [(c, now[c] / then[c] - 1) for c in now if count[c] >= min_coins]
    return sorted(growth, key=lambda x: (-x[1], x[0]))


def hot_categories(markets, coin_categories, cfg) -> set[str]:
    h = cfg["scanners"]["hot_sector"]
    pct_key = f"price_change_percentage_{h['period_days']}d_in_currency"
    ranked = category_growth(markets, coin_categories, pct_key, h["min_coins"], h["ignore_categories"])
    return {c for c, _ in ranked[:h["top_categories"]]}


# ------------------------------------------------------------------ labels
def scan_coin(coin: CoinData, btc_d: Frame, rs_cut: dict[int, float], hot: set[str],
              cfg: dict) -> list[Label]:
    s = cfg["scanners"]
    d, h4 = coin.frames["1d"], coin.frames["4h"]
    labels = []
    if squeeze(d, s["squeeze"]):
        labels.append(Label("SQUEEZE", BOTH))
    if early_trend(d, s["early_trend"]):
        labels.append(Label("EARLY_TREND", LONG))
    if rs_leader(coin, btc_d, rs_cut, s["rs_leader"]):
        labels.append(Label("RS_LEADER", LONG))
    if pullback(d, s["pullback"], cfg):
        labels.append(Label("PULLBACK", LONG))
    ex = exhaustion(d, h4, s["exhaustion"], cfg)
    if ex:
        labels.append(Label("EXHAUSTION", ex))
    if oi_buildup(coin.oi, d, s["oi_buildup"]):
        labels.append(Label("OI_BUILDUP", BOTH))
    fe = funding_extreme(coin.funding, s["funding_extreme"])
    if fe:
        labels.append(Label("FUNDING_EXTREME", fe))
    if volume_anomaly(d, s["volume_anomaly"]):
        labels.append(Label("VOLUME_ANOMALY", BOTH))
    if hot & set(coin.categories):
        labels.append(Label("HOT_SECTOR", LONG))
    return labels


def filter_by_bias(labels: list[Label], bias: str) -> list[Label]:
    """Drop labels whose direction the regime forbids (e.g. EARLY_TREND in risk-off)."""
    if bias == "none":
        return []
    if bias == "long":
        return [l for l in labels if l.direction != SHORT]
    if bias == "short":
        return [l for l in labels if l.direction != LONG]
    return labels


def liquid(info: dict, cfg: dict) -> bool:
    return (info.get("total_volume") or 0) >= cfg["universe"]["min_volume_24h_usd"]
