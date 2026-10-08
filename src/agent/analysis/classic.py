"""Entry filters from well-known trend-following systems (experiment, 2026-10-08):

  impulse  Elder's impulse system: no trade when both the EMA13 slope and the MACD-histogram
           slope point against it (checked on each of technical.classic.impulse_tfs).
  adx      ADX trend strength: no trade while ADX is below adx_min (no trend).
  vcp      Minervini's volatility contraction: the last corrections get shallower one after
           another and the latest one is quieter (lower volume) than the one before.

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
