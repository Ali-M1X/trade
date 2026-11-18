"""Signal book: admission rules, persistence and the loss-streak brake."""
from __future__ import annotations

from dataclasses import dataclass

from .lifecycle import CLOSED, OPEN, Event, new_signal

DAY = 86_400_000


@dataclass(frozen=True)
class Admission:
    ok: bool
    reason: str | None = None
    out_of_cap: bool = False


def risk_free(sig: dict) -> bool:
    """An open trade whose stop is at or beyond its entry (moved there at TP1)."""
    lcy = (sig.get("payload") or {}).get("lifecycle") or {}
    if sig.get("status") in ("tp1", "tp2"):
        return True
    entry, sl = lcy.get("entry"), lcy.get("sl_now")
    if not entry or sl is None or not lcy.get("filled_at"):
        return False
    return sl >= entry if sig.get("side") == "long" else sl <= entry


class SignalBook:
    def __init__(self, repo, cfg: dict):
        self.repo, self.cfg = repo, cfg

    # ---- queries
    def open_signals(self) -> list[dict]:
        return self.repo.get_signals(list(OPEN))

    def pause(self) -> dict:
        return self.repo.get_state("loss_streak", {"count": 0, "paused_until": 0})

    # ---- admission
    def admit(self, base: str, side: int, score: float, now_ms: int, corr=None,
              risk_pct: float | None = None) -> Admission:
        """corr(base_a, base_b) -> correlation of daily returns (or None if unknown).
        risk_pct: the new trade's risk, for lifecycle.max_open_risk_pct."""
        lc, g = self.cfg["lifecycle"], self.cfg["grades"]
        if now_ms < self.pause()["paused_until"]:
            return Admission(False, "loss_streak_pause")
        open_ = self.open_signals()
        if any(s["symbol"] == base for s in open_):
            return Admission(False, "duplicate")
        hours = lc["cooldown_after_stop_h"]
        if hours:
            recent = [s for s in self.repo.get_signals(["sl"]) if s["symbol"] == base
                      and (s["payload"]["lifecycle"]["closed_at"] or 0) > now_ms - hours * 3_600_000]
            if recent:
                return Admission(False, "cooldown")
        if corr is not None:
            twins = [s for s in open_ if s["side"] == ("long" if side == 1 else "short")
                     and (corr(base, s["symbol"]) or 0) > lc["correlation_threshold"]]
            if len(twins) >= lc["max_correlated_same_side"]:
                return Admission(False, "correlated")
        # 2026-11-15 (Ali): a trade past TP1 has its stop at entry and profit taken, so it risks
        # nothing more and no longer takes one of the max_active places
        at_risk = [x for x in open_ if not (lc.get("risk_free_outside_cap") and risk_free(x))]
        # 2026-11-18 (Ali, system 2 at 4%): the risk of all open trades together stays under the cap
        cap = lc.get("max_open_risk_pct")
        if cap and risk_pct:
            open_risk = sum((x["payload"].get("plan") or {}).get("risk_pct") or 0.0 for x in at_risk)
            if open_risk + risk_pct > cap + 1e-9:
                return Admission(False, "max_open_risk")
        if len(at_risk) >= lc["max_active"]:
            if score >= g["a_plus"]:
                return Admission(True, out_of_cap=True)
            return Admission(False, "max_active")
        return Admission(True)

    # ---- writes
    def create(self, ev, now_ms: int, meta: dict) -> tuple[int, dict]:
        """Store a new signal from an Evaluation. Returns (id, payload)."""
        plan = ev.plan.to_dict()
        payload = {"plan": plan, "lifecycle": new_signal(plan, now_ms, self.cfg), **meta,
                   # the base risk it was sized with (2026-11-18: the panel restates older trades)
                   "base_risk_pct": (self.cfg.get("trade") or {}).get("base_risk_pct")}
        sid = self.repo.add_signal(now_ms, ev.base, "long" if ev.side == 1 else "short",
                                   ev.grade, ev.score, payload["lifecycle"]["status"], payload)
        self.repo.add_event(now_ms, "created", {"grade": ev.grade, "score": ev.score}, sid)
        return sid, payload

    def save(self, sig: dict, events: list[Event], now_ms: int) -> str | None:
        """Persist lifecycle changes and events. Returns a pause notice if the brake engaged."""
        lcy = sig["payload"]["lifecycle"]
        self.repo.update_signal(sig["id"], lcy["status"], sig["payload"], now_ms)
        notice = None
        for e in events:
            self.repo.add_event(e.ts, e.kind, {"price": e.price, "r": e.r, "reason": e.reason},
                                sig["id"])
            if e.kind in CLOSED:
                notice = self._streak(e, now_ms) or notice
        return notice

    def _streak(self, e: Event, now_ms: int) -> str | None:
        st = self.pause()
        if e.kind == "sl":
            st["count"] += 1
        elif e.kind in ("tp3", "breakeven"):
            st["count"] = 0
        notice = None
        if st["count"] >= self.cfg["lifecycle"]["loss_streak_pause"]:
            st = {"count": 0, "paused_until": (e.ts // DAY + 1) * DAY}   # next D close
            notice = "loss_streak_pause"
        self.repo.set_state("loss_streak", st)
        return notice
