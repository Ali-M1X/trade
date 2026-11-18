"""Market pulse (2026-11-18, Ali: «when the market's 24 h move turned positive my shorts were stopped;
I want the 4 h and 1 h moves too, with their history, and for a week only hypothetically: what cutting
the risk or stepping into the positions would have done, saved in a small part of the panel»).

The pulse is the average % move of the large-coin basket (panel_regime.BASKET) over the last 1 h,
4 h and 24 h. The agent records it every few minutes (state `pulse`, [[t, h1, h4, h24], ...]); the
first record fills the days before from 1 h candles.

Nothing here changes a trade. Two hypothetical rules are worked out on the panel trades:
- half risk: a trade opened while the pulse ran against its side (1 h and 4 h both past their
  thresholds) would have risked half;
- early exit: a trade still before TP1 would have been closed at the first sample where the pulse
  turned against it (the alarm), at the coin's price then.
The thresholds live in config (`pulse`); changing them re-evaluates the whole history.
"""
from __future__ import annotations

import bisect
import logging

from .panel_regime import BASKET

log = logging.getLogger(__name__)

H = 3_600_000
WINDOWS = (("h1", H), ("h4", 4 * H), ("h24", 24 * H))
TF_MS = {"5m": 300_000, "1h": H}
DEFAULTS = {"enabled": True, "every_min": 5, "keep_days": 30, "h1_pct": 0.5, "h4_pct": 1.0,
            "chart_days": 7}


def settings(cfg: dict) -> dict:
    return {**DEFAULTS, **(cfg.get("pulse") or {})}


def _price_at(path: list[tuple[int, float]], t: int) -> float | None:
    i = bisect.bisect_right(path, (t, float("inf"))) - 1
    return path[i][1] if i >= 0 else None


def moves_at(paths: dict[str, list[tuple[int, float]]], t: int, tol: int = 2 * H) -> list[float] | None:
    """[h1, h4, h24] at time t: the basket's average % move over each window. A coin counts when it
    has prices at both ends (none older than tol before them); at least half the basket must."""
    out = []
    for _, w in WINDOWS:
        vals = []
        for path in paths.values():
            if not path:
                continue
            i = bisect.bisect_right(path, (t, float("inf"))) - 1
            j = bisect.bisect_right(path, (t - w, float("inf"))) - 1
            if i < 0 or j < 0 or path[i][0] < t - tol or path[j][0] < t - w - tol or i == j:
                continue
            vals.append((path[i][1] / path[j][1] - 1) * 100)
        if len(vals) * 2 < len(paths) or not vals:
            return None
        out.append(round(sum(vals) / len(vals), 3))
    return out


def _paths(market, tfs: tuple[str, ...]) -> dict[str, list[tuple[int, float]]]:
    """(close time, close) per basket coin from the given timeframes (finer ones win on ties)."""
    out = {}
    for b in BASKET:
        pts: dict[int, float] = {}
        for tf in tfs:
            try:
                df = market.candles(b, tf)
            except Exception as e:                # noqa: BLE001  (one coin's data must not stop the rest)
                log.info("pulse %s %s: %s", b, tf, e)
                continue
            for ts, close in zip(df["ts"], df["close"]):
                if close:
                    pts.setdefault(int(ts) + TF_MS[tf], float(close))
        if pts:
            out[b] = sorted(pts.items())
    return out


def record(cfg: dict, repo, market, now: int) -> list | None:
    """Add the pulse now to the history (every `every_min`); the first time, the days before too."""
    c = settings(cfg)
    if not c["enabled"]:
        return None
    hist = repo.get_state("pulse", []) or []
    if hist and now - hist[-1][0] < c["every_min"] * 60_000 - 30_000:
        return None
    paths = _paths(market, ("5m",) if hist else ("5m", "1h"))
    if not paths:
        return None
    new = []
    if not hist:                                  # backfill: one sample per hour
        first = min(p[0][0] for p in paths.values())
        t = (first + 24 * H) // H * H + H
        while t < now - H:
            m = moves_at(paths, t)
            if m:
                new.append([t, *m])
            t += H
    m = moves_at(paths, now, tol=30 * 60_000)
    if m:
        new.append([now, *m])
    if not new:
        return None
    keep = now - c["keep_days"] * 24 * H
    hist = [x for x in hist + new if x[0] >= keep]
    repo.set_state("pulse", hist)
    return new[-1]


def state_of(sample, c: dict) -> int:
    """+1: the market pushes up (against shorts), -1: down (against longs), 0: neither."""
    if not sample:
        return 0
    h1, h4 = sample[1], sample[2]
    if h1 >= c["h1_pct"] and h4 >= c["h4_pct"]:
        return 1
    if h1 <= -c["h1_pct"] and h4 <= -c["h4_pct"]:
        return -1
    return 0


def _sample_at(hist: list, t: int, tol: int = 90 * 60_000):
    i = bisect.bisect_right(hist, [t, float("inf")]) - 1
    return hist[i] if i >= 0 and t - hist[i][0] <= tol else None


def _until(t: dict, now: int) -> int:
    """The end of the stretch where a trade still risks its stop: TP1, the close, or now."""
    tp1 = [e[1] for e in t.get("events") or [] if e[0] == "tp1" and e[1]]
    ends = [x for x in (tp1[0] if tp1 else None, t.get("closed_at")) if x]
    return min(ends) if ends else now


def view(hist: list, trades: list[dict], prices: dict, now: int, cfg: dict) -> dict:
    """The panel's pulse: now, the chart, the alarm stretches, and each trade's hypothetical
    (t["pulse"] = {"entry": [h1, h4, against], "alarm": {...} or None})."""
    c = settings(cfg)
    if not hist:
        return {}
    since = hist[0][0]
    # alarm stretches: where the state is up or down
    spans: list[dict] = []
    cur = None
    for s in hist:
        st = state_of(s, c)
        if st and cur and cur["dir"] == st and s[0] - cur["to"] <= 90 * 60_000:
            cur["to"] = s[0]
            cur["peak_h1"] = max(cur["peak_h1"], s[1]) if st > 0 else min(cur["peak_h1"], s[1])
            cur["peak_h4"] = max(cur["peak_h4"], s[2]) if st > 0 else min(cur["peak_h4"], s[2])
        elif st:
            cur = {"dir": st, "from": s[0], "to": s[0], "peak_h1": s[1], "peak_h4": s[2]}
            spans.append(cur)
        else:
            cur = None
    for t in trades:
        t["pulse"] = None
        if not t.get("filled_at") or t["filled_at"] < since or not t.get("entry") or not t.get("sl"):
            continue
        risk = abs(t["entry"] - t["sl"])
        at = _sample_at(hist, t["filled_at"])
        against = bool(at and state_of(at, c) == -t["side"])
        p = {"entry": [at[1], at[2], against] if at else None, "alarm": None}
        end = _until(t, now)
        i = bisect.bisect_right(hist, [t["filled_at"], float("inf")])
        for s in hist[i:]:
            if s[0] > end:
                break
            if state_of(s, c) == -t["side"]:
                px = _price_at(prices.get(t["base"]) or [], s[0])
                if px and risk:
                    gross = t["side"] * (px - t["entry"]) / risk
                    p["alarm"] = {"t": s[0], "h1": s[1], "h4": s[2], "price": px,
                                  "r": round(gross - (t.get("cost_r") or 0), 3)}
                break
        t["pulse"] = p
    keep = now - c["chart_days"] * 24 * H
    series = [s for s in hist if s[0] >= keep]
    if len(series) > 600:                         # the chart needs no more points than that
        step = len(series) / 600
        series = [series[int(k * step)] for k in range(600)] + [series[-1]]
    last = hist[-1]
    return {"now": last if now - last[0] <= 30 * 60_000 else None, "since": since,
            "series": series, "spans": spans,
            "state": state_of(last, c) if now - last[0] <= 30 * 60_000 else 0,
            "params": {k: c[k] for k in ("h1_pct", "h4_pct")}}
