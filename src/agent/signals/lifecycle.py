"""Signal lifecycle as a pure state machine (shared by live runs and the backtest).

pending --fill--> active --TP1--> tp1 --TP2--> tp2 --trail exit--> tp3
   |                 |              |            |
   |                 +--SL--> sl    +--SL at entry--> breakeven
   +--> expired (no fill in time) | cancelled (TP1 before entry, or 4H close beyond SL)

Within one candle a stop is assumed to be hit before a target (conservative), and a
fill is checked against the stop but not the targets.
"""
from __future__ import annotations

from dataclasses import dataclass

OPEN = ("pending", "active", "tp1", "tp2")
CLOSED = ("sl", "breakeven", "tp3", "expired", "cancelled")
H4 = 4 * 3_600_000


@dataclass(frozen=True)
class Event:
    kind: str           # filled | tp1 | tp2 | tp3 | sl | breakeven | expired | cancelled
    ts: int
    price: float | None = None
    r: float | None = None          # R of this leg (tp1/tp2) or the trade total (closes)
    reason: str | None = None


def new_signal(plan: dict, created_ms: int, cfg: dict) -> dict:
    """Lifecycle fields for a fresh signal. Market orders are filled at creation."""
    lc = cfg["lifecycle"]
    s = {
        "side": plan["side"], "entry": plan["entry"], "sl": plan["sl"],
        "tp1": plan["tp1"], "tp2": plan["tp2"], "order": plan["order"],
        "risk": abs(plan["entry"] - plan["sl"]),
        "status": "pending", "created": created_ms,
        "expires_at": created_ms + lc["expiry_bars_4h"] * H4,
        "sl_now": plan["sl"], "open_frac": 1.0, "realized_r": 0.0,
        "filled_at": None, "closed_at": None,
        # candles are processed once each: only those opening after these markers
        "last_candle_ts": created_ms - 1, "last_4h_ts": created_ms - 1,
    }
    if plan["order"] == "market":
        s["status"], s["filled_at"] = "active", created_ms
    return s


def r_at(s: dict, price: float) -> float:
    return (price - s["entry"]) * s["side"] / s["risk"] if s["risk"] else 0.0


def _favourable(s: dict, level: float, high: float, low: float) -> bool:
    return high >= level if s["side"] == 1 else low <= level


def _adverse(s: dict, level: float, high: float, low: float) -> bool:
    return low <= level if s["side"] == 1 else high >= level


def _close(s: dict, kind: str, ts: int, price: float, reason: str | None = None) -> Event:
    s["realized_r"] += s["open_frac"] * r_at(s, price) if kind not in ("expired", "cancelled") else 0
    s["open_frac"] = 0.0
    s["status"], s["closed_at"] = kind, ts
    return Event(kind, ts, price, round(s["realized_r"], 4), reason)


def on_candle(s: dict, ts: int, high: float, low: float, cfg: dict) -> list[Event]:
    """Process one closed candle (any timeframe, typically 15m) that opened at `ts`."""
    if s["status"] in CLOSED or ts <= s["last_candle_ts"]:
        return []
    s["last_candle_ts"] = ts
    t = cfg["trade"]
    out: list[Event] = []
    if s["status"] == "pending":
        touched_entry = _adverse(s, s["entry"], high, low)
        if not touched_entry:
            if _favourable(s, s["tp1"], high, low):
                out.append(_close(s, "cancelled", ts, s["tp1"], "tp1_before_entry"))
            return out
        s["status"], s["filled_at"] = "active", ts
        out.append(Event("filled", ts, s["entry"]))
        if _adverse(s, s["sl_now"], high, low):
            out.append(_close(s, "sl", ts, s["sl_now"]))
        return out

    if _adverse(s, s["sl_now"], high, low):
        kind = "sl" if s["status"] == "active" else "breakeven"
        out.append(_close(s, kind, ts, s["sl_now"]))
        return out
    if s["status"] == "active" and _favourable(s, s["tp1"], high, low):
        frac = t["tp1_close_pct"] / 100
        leg = r_at(s, s["tp1"])
        s["realized_r"] += frac * leg
        s["open_frac"] -= frac
        s["sl_now"] = s["entry"]
        s["status"] = "tp1"
        out.append(Event("tp1", ts, s["tp1"], round(leg, 4)))
    if s["status"] == "tp1" and _favourable(s, s["tp2"], high, low):
        frac = t["tp2_close_pct"] / 100
        leg = r_at(s, s["tp2"])
        s["realized_r"] += frac * leg
        s["open_frac"] -= frac
        s["status"] = "tp2"
        out.append(Event("tp2", ts, s["tp2"], round(leg, 4)))
    return out


def on_4h_close(s: dict, ts: int, close: float, ma25: float, choch_against: bool) -> list[Event]:
    """A closed 4H candle (`ts` = its open time): a pending signal is void if the close is
    beyond the stop; after TP2 the rest is closed on a close beyond MA25 or an opposite
    structure break."""
    if s["status"] in CLOSED or ts <= s["last_4h_ts"]:
        return []
    s["last_4h_ts"] = ts
    if s["status"] == "pending" and (close - s["sl"]) * s["side"] < 0:
        return [_close(s, "cancelled", ts, close, "closed_beyond_sl")]
    if s["status"] == "tp2" and ((close - ma25) * s["side"] < 0 or choch_against):
        return [_close(s, "tp3", ts, close, "ma25" if (close - ma25) * s["side"] < 0 else "choch")]
    return []


def on_time(s: dict, now_ms: int) -> list[Event]:
    if s["status"] == "pending" and now_ms >= s["expires_at"]:
        return [_close(s, "expired", now_ms, None)]
    return []
