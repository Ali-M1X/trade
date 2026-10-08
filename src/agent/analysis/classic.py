"""Entry filters from well-known trend-following systems (experiment, 2026-10-08):

  impulse  Elder's impulse system: no trade when both the EMA13 slope and the MACD-histogram
           slope point against it (checked on each of technical.classic.impulse_tfs).
  adx      ADX trend strength: no trade while ADX is below adx_min (no trend).
  vcp      Minervini's volatility contraction: the last corrections get shallower one after
           another and the latest one is quieter (lower volume) than the one before.

Market-phase filters (second experiment, 2026-10-08):

  weekly_tide  Elder's tide: the weekly EMA26 must slope with the trade.
  stage        Weinstein stages on the 30-week (150-day) average: longs only in stage 2 (close
               above a rising MA150), shorts only in stage 4.
  range_dryup  Wyckoff: when the setup comes from a range (ACC/DIST phase), volume in the
               last third of the range must be lower than in the first third.
  dmi          ADX/DMI on the phase timeframe: ADX >= dmi_adx_min and +DI/-DI with the trade.
  er_min       Kaufman efficiency ratio over er_bars on the phase timeframe, signed with the
               trade, must be at least er_min (a straight move, not chop).

Each returns the name of the failed filter (a flag that downgrades the setup to a Watch)
or None. Nothing here runs unless technical.classic switches it on.
"""
from __future__ import annotations

import numpy as np

from ..indicators.swings import legs
from .frame import Frame


def impulse(f: Frame, ema_col: str) -> int:
    """+1 green (EMA and histogram rising), -1 red (both falling), 0 blue."""
    e, h = f.col(ema_col), f.col("macd_hist")
    if len(e) < 2 or np.isnan(e.iloc[-2]) or np.isnan(h.iloc[-2]):
        return 0
    de, dh = np.sign(e.iloc[-1] - e.iloc[-2]), np.sign(h.iloc[-1] - h.iloc[-2])
    return int(de) if de == dh else 0


def vcp(f: Frame, side: int, p: dict) -> bool:
    """Corrections (legs against `side`) shrinking in depth (%), the last one on lower volume."""
    lg = legs(f.swings)[-p["vcp_legs"]:]
    price = {s.idx: s.price for s in f.swings}
    cor = [l for l in lg if l.direction == -side]
    n = p["vcp_contractions"]
    if len(cor) < n:
        return False
    cor = cor[-n:]
    depth = [l.size / price[l.start] for l in cor]
    if not all(b <= a * p["vcp_max_ratio"] for a, b in zip(depth, depth[1:])):
        return False
    vol = f.df["volume"].to_numpy(float)
    v = [vol[l.start + 1:l.end + 1].mean() for l in cor[-2:]]
    return bool(v[1] < v[0])


def slope_with(series, bars: int, side: int) -> bool | None:
    s = series.dropna()
    if len(s) <= bars:
        return None
    return bool((s.iloc[-1] - s.iloc[-1 - bars]) * side > 0)


def stage_ok(f: Frame, side: int, ma: int, bars: int) -> bool | None:
    m = f.col("close").rolling(ma).mean()
    rising = slope_with(m, bars, side)
    if rising is None:
        return None
    return bool(rising and (f.close - m.iloc[-1]) * side > 0)


def dryup(f: Frame, bars: int) -> bool:
    v = f.df["volume"].to_numpy(float)[-bars:]
    k = len(v) // 3
    return bool(k and v[-k:].mean() < v[:k].mean())


def efficiency(f: Frame, bars: int, side: int) -> float | None:
    c = f.col("close").to_numpy(float)
    if len(c) <= bars:
        return None
    path = np.abs(np.diff(c[-bars - 1:])).sum()
    return float((c[-1] - c[-1 - bars]) * side / path) if path > 0 else 0.0


def phase_flags(frames: dict[str, Frame], side: int, cfg: dict, phase_d: str = "",
                phase_4h: str = "") -> list[str]:
    p = cfg["technical"].get("classic") or {}
    flags = []
    if p.get("weekly_tide") and "1w" in frames:
        e = frames["1w"].col("close").ewm(span=p["weekly_ema"], adjust=False, min_periods=p["weekly_ema"]).mean()
        if slope_with(e, 1, side) is False:
            flags.append("weekly_tide_against")
    if p.get("stage") and stage_ok(frames["1d"], side, p["stage_ma"], p["stage_slope_bars"]) is False:
        flags.append("stage_against")
    if p.get("range_dryup"):
        for name, tf in ((phase_d, "1d"), (phase_4h, "4h")):
            if name.split(":")[0] in ("ACC", "DIST"):
                if not dryup(frames[tf], cfg["technical"]["phase"]["acc_min_bars"]):
                    flags.append("range_no_dryup")
                break
    if p.get("dmi"):
        last = frames[p["dmi_tf"]].last
        ok = bool(np.isfinite(last["adx"])) and last["adx"] >= p["dmi_adx_min"] and \
            (last["pdi"] - last["mdi"]) * side > 0
        if not ok:
            flags.append("dmi_against")
    if p.get("er_min"):
        er = efficiency(frames[p["er_tf"]], p["er_bars"], side)
        if er is not None and er < p["er_min"]:
            flags.append("choppy")
    return flags


def classic_flags(frames: dict[str, Frame], side: int, cfg: dict) -> list[str]:
    p = cfg["technical"].get("classic") or {}
    ema_col = f"ema{cfg['indicators'].get('ema_impulse', 13)}"
    flags = []
    if p.get("impulse"):
        for tf in p["impulse_tfs"]:
            if tf in frames and impulse(frames[tf], ema_col) == -side:
                flags.append(f"impulse_against_{tf}")
                break
    if p.get("adx_min"):
        a = frames[p["adx_tf"]].col("adx").iloc[-1]
        if not np.isfinite(a) or a < p["adx_min"]:
            flags.append("adx_low")
    if p.get("vcp") and not vcp(frames[p["vcp_tf"]], side, p):
        flags.append("no_vcp")
    return flags
