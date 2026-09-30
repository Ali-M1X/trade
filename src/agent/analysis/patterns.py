"""Chart patterns from swing geometry: double top/bottom, head & shoulders, triangle,
wedge, flag. A pattern only counts once a close breaks its neckline with RVOL >= the
configured minimum, and that break is recent."""
from __future__ import annotations

from dataclasses import dataclass

from ..indicators.swings import Swing
from .frame import Frame


@dataclass(frozen=True)
class Pattern:
    name: str
    direction: int
    neckline: float
    break_idx: int


def _break(f: Frame, after: int, level: float, d: int, p: dict) -> int | None:
    """Index of the first close beyond `level` in direction d after bar `after`, if that
    close is within the recent bars and has enough RVOL."""
    close, rvol = f.df["close"].to_numpy(), f.df["rvol"].to_numpy()
    for i in range(after + 1, f.n):
        if (close[i] - level) * d > 0:
            recent = i >= f.n - p["pattern_recent_bars"]
            return i if recent and rvol[i] >= p["pattern_confirm_rvol"] else None
    return None


def _kinds(sw: list[Swing]) -> str:
    return "".join(s.kind for s in sw)


def _double(sw, f, p, tol):
    a, n, b = sw[-3:]
    if abs(a.price - b.price) > tol:
        return None
    if _kinds(sw[-3:]) == "LHL":
        name, d = "double_bottom", 1
    elif _kinds(sw[-3:]) == "HLH":
        name, d = "double_top", -1
    else:
        return None
    i = _break(f, b.idx, n.price, d, p)
    return Pattern(name, d, n.price, i) if i is not None else None


def _head_shoulders(sw, f, p, tol):
    if len(sw) < 5:
        return None
    s1, n1, head, n2, s2 = sw[-5:]
    if abs(s1.price - s2.price) > 2 * tol:
        return None
    if _kinds(sw[-5:]) == "LHLHL" and head.price < min(s1.price, s2.price):
        name, d, neck = "inverse_head_shoulders", 1, max(n1.price, n2.price)
    elif _kinds(sw[-5:]) == "HLHLH" and head.price > max(s1.price, s2.price):
        name, d, neck = "head_shoulders", -1, min(n1.price, n2.price)
    else:
        return None
    i = _break(f, s2.idx, neck, d, p)
    return Pattern(name, d, neck, i) if i is not None else None


def _two_each(sw):
    last4 = sw[-4:]
    hs = [s.price for s in last4 if s.kind == "H"]
    ls = [s.price for s in last4 if s.kind == "L"]
    return hs, ls, last4[-1].idx


def _triangle_wedge(sw, f, p, tol):
    if len(sw) < 4:
        return None
    (h1, h2), (l1, l2), end = _two_each(sw)
    dh, dl = h2 - h1, l2 - l1
    flat_h, flat_l = abs(dh) <= tol, abs(dl) <= tol
    if (dh < 0 or flat_h) and (dl > 0 or flat_l) and not (flat_h and flat_l):
        for d, level in ((1, h2), (-1, l2)):
            i = _break(f, end, level, d, p)
            if i is not None:
                return Pattern("triangle", d, level, i)
        return None
    if dh > 0 and dl > dh:                      # rising wedge: lows rise faster, breaks down
        i = _break(f, end, l2, -1, p)
        return Pattern("rising_wedge", -1, l2, i) if i is not None else None
    if dl < 0 and dh < dl:                      # falling wedge: highs fall faster, breaks up
        i = _break(f, end, h2, 1, p)
        return Pattern("falling_wedge", 1, h2, i) if i is not None else None
    return None


def _flag(sw, f, p, tol):
    a, b, c = sw[-3:]
    pole, pullback = abs(b.price - a.price), abs(b.price - c.price)
    if pole < p["flag_pole_atr"] * f.atr or pullback > p["flag_max_retrace"] * pole:
        return None
    if _kinds(sw[-3:]) == "LHL":
        name, d = "bull_flag", 1
    elif _kinds(sw[-3:]) == "HLH":
        name, d = "bear_flag", -1
    else:
        return None
    i = _break(f, c.idx, b.price, d, p)
    return Pattern(name, d, b.price, i) if i is not None else None


def find_patterns(f: Frame, cfg: dict) -> list[Pattern]:
    """Confirmed patterns ending at the latest swings. The newest swing may already be a
    pivot formed after the break, so the sequence ending one swing earlier is checked too."""
    p = cfg["technical"]["patterns"]
    tol = p["pattern_tolerance_atr"] * f.atr
    found: dict[str, Pattern] = {}
    for drop in (0, 1):
        sw = f.swings[:len(f.swings) - drop]
        if len(sw) < 3:
            continue
        for fn in (_double, _head_shoulders, _triangle_wedge, _flag):
            pat = fn(sw, f, p, tol)
            if pat and pat.name not in found:
                found[pat.name] = pat
    return list(found.values())
