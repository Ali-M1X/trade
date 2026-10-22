"""One system per market regime (2026-10-22): which config trades in which regime.

With `regime.source: price` the regime is bull / range / bear (layers/price_regime.py) and
`regime.price.systems` names the system each regime runs: a backtest variant name, a dict of
overrides, or "off" (no new trades). A regime without an entry runs the config as it is.
The backtest (backtest/engine.py) and the live runs (runs.py) share these helpers.
"""
from __future__ import annotations

import pandas as pd

from .layers.price_regime import alt_index, market_states
from .layers.regime import Regime, make_price_regime


def price_source(cfg: dict) -> bool:
    return cfg["regime"].get("source") == "price"


def system_cfg(cfg: dict, regime_name: str | None) -> dict | None:
    """The config of the regime's system; None when the regime is "off"."""
    from .backtest.variants import merge
    if not price_source(cfg):
        return cfg
    sys = (cfg["regime"]["price"].get("systems") or {}).get(regime_name)
    if sys is None:
        return cfg
    if sys == "off":
        return None
    over = cfg["backtest"]["variants"][sys] if isinstance(sys, str) else sys
    return merge(cfg, {k: v for k, v in over.items() if k != "regime"})


def daily_ohlc(df: pd.DataFrame) -> pd.DataFrame:
    return df.set_index("ts")[["high", "low", "close"]].astype(float)


def price_regime_from(btc: pd.DataFrame, alts: dict[str, pd.DataFrame], cfg: dict) -> Regime | None:
    """Regime at the last closed daily candle. btc/alts: daily candles (ts, ohlc)."""
    p = cfg["regime"]["price"]
    b = daily_ohlc(btc)
    idx = alt_index({k: daily_ohlc(f) for k, f in alts.items() if len(f)}).reindex(b.index).ffill()
    st = market_states(b, idx, p).dropna()
    if st.empty:
        return None
    return make_price_regime(int(st["state"].iloc[-1]), int(st["alts"].iloc[-1]), cfg)


def live_price_regime(market, cfg: dict) -> Regime | None:
    alts = {b: market.candles(b, "1d") for b in cfg["regime"]["price"]["basket"]}
    return price_regime_from(market.candles("BTC", "1d"), alts, cfg)
