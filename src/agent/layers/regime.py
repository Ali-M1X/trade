"""Layer 1: market regime from USDT.D, BTC.D and TOTAL2."""
from __future__ import annotations

from dataclasses import asdict, dataclass

import pandas as pd

from ..analysis.frame import Frame, make_frame

INDICES = ("usdt_d", "btc_d", "total2")


@dataclass(frozen=True)
class Regime:
    name: str
    bias: str               # long | short | both | none
    risk: float
    usdt_d: int
    btc_d: int
    total2: int
    other_alts_risk: float | None = None
    min_grade: str | None = None

    def allows(self, side: int) -> bool:
        return self.bias == "both" or (self.bias == "long" and side == 1) or \
            (self.bias == "short" and side == -1)

    def risk_for(self, side: int, is_btc: bool = False, btc_pair_up: bool = False) -> float:
        """Risk multiplier for a trade. In a BTC-led market only BTC and alts whose COIN/BTC
        pair is rising on D get the full risk."""
        if not self.allows(side):
            return 0.0
        if self.other_alts_risk is not None and side == 1 and not (is_btc or btc_pair_up):
            return self.other_alts_risk
        return self.risk

    def to_dict(self) -> dict:
        return asdict(self)


def classify(usdt_d: int, btc_d: int, total2: int) -> str:
    if usdt_d == -1 and btc_d == -1 and total2 == 1:
        return "alt_season"
    if usdt_d == -1 and btc_d == 1 and total2 >= 0:
        return "btc_led"
    if usdt_d == 1 and btc_d == 1 and total2 == -1:
        return "risk_off"
    if usdt_d == 1 and btc_d == -1 and total2 == -1:
        return "capitulation"
    return "neutral"


def combine(d: int, h4: int) -> int:
    """Index direction from D and 4H: agreement or one of them ranging gives the other's
    direction; opposite directions give 0."""
    if d == h4 or h4 == 0:
        return d
    if d == 0:
        return h4
    return 0


def make_regime(dirs: dict[str, int], cfg: dict) -> Regime:
    name = classify(dirs["usdt_d"], dirs["btc_d"], dirs["total2"])
    r = cfg["regime"]["regimes"][name]
    return Regime(name, r["bias"], r["risk"], dirs["usdt_d"], dirs["btc_d"], dirs["total2"],
                  r.get("other_alts_risk"), r.get("min_grade"))


def index_candles(series: pd.DataFrame, column: str, tf: str) -> pd.DataFrame:
    """Snapshots (ts, value) -> OHLC candles of `tf` (volume is zero)."""
    s = series.set_index(pd.to_datetime(series["ts"], unit="ms"))[column].dropna()
    rule = {"4h": "4h", "1d": "1D"}[tf]
    o = s.resample(rule, label="left", closed="left").ohlc().dropna()
    out = o.reset_index(names="dt")
    # explicit unit: pandas may hold these as datetime64[ms] or [ns]
    out.insert(0, "ts", out.pop("dt").astype("datetime64[ms]").astype("int64"))
    out["volume"] = 0.0
    return out[["ts", "open", "high", "low", "close", "volume"]]


def index_frames(series: pd.DataFrame, cfg: dict) -> dict[str, dict[str, Frame]]:
    """series: ts, usdt_d, btc_d, total2 (see data.dominance.dominance_series). If it has a
    `source` column, the 4H candles use only real 4H snapshots ("global"), since the
    reconstructed history is daily."""
    out: dict[str, dict[str, Frame]] = {}
    for col in INDICES:
        out[col] = {}
        for tf in cfg["regime"]["timeframes"]:
            src = series
            if tf == "4h" and "source" in series:
                src = series[series["source"] == "global"]
            candles = index_candles(src, col, tf)
            if len(candles) >= 2:
                out[col][tf] = make_frame(candles, tf, cfg, with_indicators=True)
    return out


def compute_regime(series: pd.DataFrame, cfg: dict) -> Regime:
    frames = index_frames(series, cfg)
    dirs = {}
    for col in INDICES:
        f = frames[col]
        d = f["1d"].direction if "1d" in f else 0
        h4 = f["4h"].direction if "4h" in f else 0
        dirs[col] = combine(d, h4)
    return make_regime(dirs, cfg)
