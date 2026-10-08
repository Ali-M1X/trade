"""Trend quality ("سلامت و کیفیت روند", Ali's notes of 2026-10-08), measured on one frame for
one trade side.

Every check looks at the trend the trade wants to join (`side`): legs in that direction are
impulses, legs against it are corrections. Each check returns +1 (healthy / strengthening),
-1 (weakening / reversal warning) or 0 (neutral or not enough data). The points per check
are in config (technical.trend_quality.points); the sum is clamped to +-max_points.

Checks (all on completed swings and closed candles, so nothing looks ahead):
  amplitude          impulses larger than corrections
  impulse_size       impulse legs growing / shrinking
  correction_bars    corrections getting shorter in time (stronger) / longer
  impulse_speed      slope of the impulse legs (size per bar) rising / falling
  candle_size        bodies of trend candles growing / shrinking
  body_wick          body-to-range ratio of trend candles rising / falling
  close_spacing      distance between consecutive closes growing / shrinking
  opposite_burst     small candles, then one or two big candles against the trend
  channel            price at the trend side of a channel / broken out of it against
  volume_legs        volume higher on impulses than on corrections / the reverse
  volume_divergence  new extreme on rising / falling volume
  pullback_volume    the current correction on falling / rising volume
  box_breakout       a breakout out of a quiet (dried-up volume) box with / against
  extreme_volume     volume dried up at an extreme against / with the trade
  rsi_persistence    RSI stuck in the overbought (long) / oversold (short) zone
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from ..indicators.swings import Leg, legs
from .frame import Frame

CHECKS = ("amplitude", "impulse_size", "correction_bars", "impulse_speed", "candle_size",
          "body_wick", "close_spacing", "opposite_burst", "channel", "volume_legs",
          "volume_divergence", "pullback_volume", "box_breakout", "extreme_volume",
          "rsi_persistence")


@dataclass
class TrendQuality:
    points: float = 0.0
    checks: dict = field(default_factory=dict)     # name -> -1 / 0 / +1

    @property
    def notes(self) -> list[str]:
        return [f"tq_{k}{'+' if v > 0 else '-'}" for k, v in self.checks.items() if v]


def _trend(x: list[float], rel: float) -> int:
    """+1 if the values rise step by step (last >= first * (1 + rel)), -1 if they fall."""
    if len(x) < 2 or x[0] <= 0:
        return 0
    up = all(b >= a for a, b in zip(x, x[1:])) and x[-1] >= x[0] * (1 + rel)
    down = all(b <= a for a, b in zip(x, x[1:])) and x[-1] <= x[0] * (1 - rel)
    return 1 if up else -1 if down else 0


def _ratio_vote(ratio: float, hi: float, lo: float) -> int:
    if not np.isfinite(ratio):
        return 0
    return 1 if ratio >= hi else -1 if ratio <= lo else 0


def _split(f: Frame, side: int, n: int) -> tuple[list[Leg], list[Leg]]:
    lg = legs(f.swings)[-n:]
    return [l for l in lg if l.direction == side], [l for l in lg if l.direction == -side]


def _leg_volume(f: Frame, leg: Leg) -> float:
    v = f.df["volume"].to_numpy(float)[leg.start + 1:leg.end + 1]
    return float(v.mean()) if len(v) else float("nan")


# ------------------------------------------------------------------ swing based
def amplitude(f, side, p):
    imp, cor = _split(f, side, p["legs"])
    if len(imp) < 2 or len(cor) < 2:
        return 0
    r = np.mean([l.size for l in imp[-3:]]) / max(np.mean([l.size for l in cor[-3:]]), 1e-12)
    return _ratio_vote(r, p["amplitude_good"], p["amplitude_bad"])


def impulse_size(f, side, p):
    imp, _ = _split(f, side, p["legs"])
    return _trend([l.size for l in imp[-3:]], p["trend_rel"]) if len(imp) >= 3 else 0


def correction_bars(f, side, p):
    _, cor = _split(f, side, p["legs"])
    return -_trend([float(l.bars) for l in cor[-3:]], p["trend_rel"]) if len(cor) >= 3 else 0


def impulse_speed(f, side, p):
    imp, _ = _split(f, side, p["legs"])
    return _trend([l.size / max(l.bars, 1) for l in imp[-3:]], p["trend_rel"]) if len(imp) >= 3 else 0


def volume_legs(f, side, p):
    imp, cor = _split(f, side, p["legs"])
    if len(imp) < 2 or len(cor) < 2:
        return 0
    vi = np.nanmean([_leg_volume(f, l) for l in imp[-2:]])
    vc = np.nanmean([_leg_volume(f, l) for l in cor[-2:]])
    return _ratio_vote(vi / vc if vc > 0 else np.nan, p["volume_good"], p["volume_bad"])


def volume_divergence(f, side, p):
    """The last impulse made a new extreme: was it on more or less volume than the one before?"""
    imp, _ = _split(f, side, p["legs"])
    if len(imp) < 2:
        return 0
    a, b = imp[-2], imp[-1]
    sw = {s.idx: s.price for s in f.swings}
    if (sw[b.end] - sw[a.end]) * side <= 0:          # no new extreme: nothing to compare
        return 0
    va, vb = _leg_volume(f, a), _leg_volume(f, b)
    return _ratio_vote(vb / va if va > 0 else np.nan, p["volume_good"], p["volume_bad"])


def pullback_volume(f, side, p):
    """Bars since the last swing, when that swing ended an impulse (we are in a correction)."""
    lg = legs(f.swings)
    if not lg or lg[-1].direction != side or f.n - 1 - lg[-1].end < 2:
        return 0
    vol = f.df["volume"].to_numpy(float)
    cur = vol[lg[-1].end + 1:].mean()
    imp = _leg_volume(f, lg[-1])
    # a quiet correction is healthy, a loud one is a warning
    return _ratio_vote(imp / cur if cur > 0 else np.nan, p["volume_good"], p["volume_bad"])


def channel(f, side, p):
    """Regression lines through the last swing highs and lows. A channel needs both lines
    sloping with the trade and roughly parallel. Long: close in the lower part of it (+1)
    or below its lower line (-1). Short mirrors it."""
    hs = [s for s in f.swings if s.kind == "H"][-p["channel_swings"]:]
    ls = [s for s in f.swings if s.kind == "L"][-p["channel_swings"]:]
    if len(hs) < 3 or len(ls) < 3:
        return 0
    kh, bh = np.polyfit([s.idx for s in hs], [s.price for s in hs], 1)
    kl, bl = np.polyfit([s.idx for s in ls], [s.price for s in ls], 1)
    if kh * side <= 0 or kl * side <= 0 or not (0.5 <= kh / kl <= 2.0):
        return 0
    i = f.n - 1
    top, bot = kh * i + bh, kl * i + bl
    width = top - bot
    if width <= 0:
        return 0
    pos = (f.close - bot) / width                      # 0 = lower line, 1 = upper line
    if side == -1:
        pos = 1 - pos
    if pos < -p["channel_break_atr"] * f.atr / width:
        return -1                                      # broken out against the trade
    return 1 if pos <= p["channel_entry_zone"] else 0


# ------------------------------------------------------------------ candle based
def _windows(f, p):
    df = f.df
    n, m = p["recent_bars"], p["prior_bars"]
    if f.n < n + m:
        return None, None
    return df.iloc[-n:], df.iloc[-n - m:-n]


def _trend_bars(w, side):
    return w[np.sign(w["close"] - w["open"]) == side]


def candle_size(f, side, p):
    rec, pri = _windows(f, p)
    if rec is None:
        return 0
    a, b = _trend_bars(rec, side), _trend_bars(pri, side)
    if len(a) < 2 or len(b) < 3:
        return 0
    r = (a["close"] - a["open"]).abs().mean() / max((b["close"] - b["open"]).abs().mean(), 1e-12)
    return _ratio_vote(r, p["size_good"], p["size_bad"])


def body_wick(f, side, p):
    rec, pri = _windows(f, p)
    if rec is None:
        return 0

    def ratio(w):
        w = _trend_bars(w, side)
        rng = (w["high"] - w["low"]).replace(0, np.nan)
        return ((w["close"] - w["open"]).abs() / rng).mean() if len(w) >= 2 else np.nan
    a, b = ratio(rec), ratio(pri)
    return _ratio_vote(a / b if b and np.isfinite(b) else np.nan, p["body_good"], p["body_bad"])


def close_spacing(f, side, p):
    rec, pri = _windows(f, p)
    if rec is None:
        return 0
    a = rec["close"].diff().abs().iloc[1:].mean()
    b = pri["close"].diff().abs().iloc[1:].mean()
    return _ratio_vote(a / b if b > 0 else np.nan, p["size_good"], p["size_bad"])


def opposite_burst(f, side, p):
    """Several small candles, then one or two big candles against the trade (latest bars)."""
    df, atr = f.df, f.atr
    k = p["burst_small_bars"]
    if f.n < k + 2 or not np.isfinite(atr) or atr <= 0:
        return 0
    body = (df["close"] - df["open"]).to_numpy(float)
    for big in (1, 2):
        last = body[-big:]
        small = body[-big - k:-big]
        if (np.all(last * side < 0) and np.all(np.abs(last) >= p["burst_big_atr"] * atr)
                and np.all(np.abs(small) <= p["burst_small_atr"] * atr)):
            return -1
    return 0


# ------------------------------------------------------------------ volume / box
def box_breakout(f, side, p):
    """A tight box whose volume dried up before a close out of it (in the last few bars)."""
    n, k = p["box_bars"], p["box_break_bars"]
    df = f.df
    if f.n < n + k + 50 or not np.isfinite(f.atr):
        return 0
    box = df.iloc[-n - k:-k]
    hi, lo = box["high"].max(), box["low"].min()
    if hi - lo > p["box_max_atr"] * f.atr:
        return 0
    vol = df["volume"].rolling(5).mean()
    quiet = vol.iloc[-n - k:-k].min() < vol.iloc[-n - k - 50:-k].quantile(p["dryup_quantile"])
    if not quiet:
        return 0
    c = df["close"].iloc[-k:]
    if (c > hi).any() and (c.iloc[-1] > hi):
        return 1 if side == 1 else -1
    if (c < lo).any() and (c.iloc[-1] < lo):
        return 1 if side == -1 else -1
    return 0


def extreme_volume(f, side, p):
    """Volume dried up at an extreme: at a low it hints at a turn up, at a high a turn down."""
    n = p["extreme_bars"]
    df = f.df
    if f.n < n + 5:
        return 0
    w = df.iloc[-n:]
    near = p["extreme_near_atr"] * f.atr
    v5 = df["volume"].iloc[-5:].mean()
    dry = v5 < df["volume"].iloc[-n:].rolling(5).mean().quantile(p["dryup_quantile"])
    if not dry:
        return 0
    if df["low"].iloc[-5:].min() <= w["low"].min() + near:
        return 1 if side == 1 else -1
    if df["high"].iloc[-5:].max() >= w["high"].max() - near:
        return 1 if side == -1 else -1
    return 0


def rsi_persistence(f, side, p):
    rsi = f.col("rsi").iloc[-p["rsi_bars"]:]
    zone = rsi >= p["rsi_hot"] if side == 1 else rsi <= 100 - p["rsi_hot"]
    return -1 if zone.sum() >= p["rsi_min_bars"] else 0


FUNCS = {name: globals()[name] for name in CHECKS}


def trend_quality(f: Frame, side: int, cfg: dict) -> TrendQuality:
    p = cfg["technical"]["trend_quality"]
    tq = TrendQuality()
    for name in CHECKS:
        try:
            v = int(FUNCS[name](f, side, p))
        except (ValueError, ZeroDivisionError, KeyError, np.linalg.LinAlgError):
            v = 0
        tq.checks[name] = v
        tq.points += v * p["points"].get(name, 0)
    tq.points = float(max(-p["max_points"], min(p["max_points"], tq.points)))
    return tq
