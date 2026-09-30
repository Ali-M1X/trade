"""Synthetic candle builders for layer tests."""
import numpy as np
import pandas as pd

HOUR = 3600 * 1000
TF_MS = {"15m": HOUR // 4, "1h": HOUR, "4h": 4 * HOUR, "1d": 24 * HOUR, "1w": 7 * 24 * HOUR}


def from_closes(closes, volumes=None, wick=0.2, tf="4h", t0=1_700_000_000_000):
    """Each candle opens at the previous close; wicks extend `wick` (fraction of the
    body, at least 0.05% of price) beyond the body."""
    closes = np.asarray(closes, dtype=float)
    opens = np.r_[closes[0], closes[:-1]]
    body = np.abs(closes - opens)
    ext = np.maximum(body * wick, closes * 0.0005)
    df = pd.DataFrame({
        "ts": t0 + np.arange(len(closes)) * TF_MS[tf],
        "open": opens,
        "high": np.maximum(opens, closes) + ext,
        "low": np.minimum(opens, closes) - ext,
        "close": closes,
        "volume": np.full(len(closes), 1000.0) if volumes is None else np.asarray(volumes, float),
    })
    return df


def trend(n=220, start=100.0, drift=0.5, amp=3.0, period=3.0):
    """Zigzag trend: drift per bar plus a sine wave so swings form."""
    i = np.arange(n)
    return start + drift * i + amp * np.sin(i / period)


def accumulation(decline=150, rng=45, top=100.0, bottom=60.0, spring=False, breakout=False):
    """A decline, then a tight range with heavier volume on green candles.
    Optionally a spring (dip below the range low that closes back inside) or a breakout."""
    down = trend(decline, top, (bottom - top) / decline, amp=2.0)
    i = np.arange(rng)
    flat = bottom + 0.8 * np.sin(i * 1.3)
    closes = np.r_[down, flat]
    if spring:
        closes[-2] = bottom - 3.0
        closes[-1] = bottom + 0.2
    if breakout:
        closes[-1] = bottom + 4.0
    vols = np.full(len(closes), 1000.0)
    green = np.r_[False, closes[1:] > closes[:-1]]
    vols[-rng:][green[-rng:]] = 1600.0
    return closes, vols


def waypoints(points, prefix=60, level=None):
    """Linear path through (bar, price) points, preceded by `prefix` gently oscillating
    bars at the first price (so indicators are warmed up)."""
    first = points[0][1] if level is None else level
    pre = first + 0.3 * np.sin(np.arange(prefix) * 1.7)
    path = []
    for (b0, p0), (b1, p1) in zip(points, points[1:]):
        path.extend(np.linspace(p0, p1, b1 - b0 + 1)[:-1])
    path.append(points[-1][1])
    return np.r_[pre, path]


def with_last_volume(closes, last_vol=3000.0, base=1000.0):
    v = np.full(len(closes), base)
    v[-1] = last_vol
    return v
