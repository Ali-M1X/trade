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


class SignalBook:
    def __init__(self, repo, cfg: dict):
        self.repo, self.cfg = repo, cfg

    # ---- queries
    def open_signals(self) -> list[dict]:
        return self.repo.get_signals(list(OPEN))

    def pause(self) -> dict:
        return self.repo.get_state("loss_streak", {"count": 0, "paused_until": 0})

    # ---- admission
    def admit(self, base: str, side: int, score: float, now_ms: int, corr=None) -> Admission:
        """corr(base_a, base_b) -> correlation of daily returns (or None if unknown)."""
        lc, g = self.cfg["lifecycle"], self.cfg["grades"]
        if now_ms < self.pause()["paused_until"]:
            return Admission(False, "loss_streak_pause")
        open_ = self.open_signals()
        if any(s["symbol"] == base for s in open_):
            return Admission(False, "duplicate")
        if corr is not None:
            twins = [s for s in open_ if s["side"] == ("long" if side == 1 else "short")
                     and (corr(base, s["symbol"]) or 0) > lc["correlation_threshold"]]
            if len(twins) >= lc["max_correlated_same_side"]:
                return Admission(False, "correlated")
        if len(open_) >= lc["max_active"]:
            if score >= g["a_plus"]:
                return Admission(True, out_of_cap=True)
            return Admission(False, "max_active")
        return Admission(True)

    # ---- writes
    def create(self, ev, now_ms: int, meta: dict) -> tuple[int, dict]:
        """Store a new signal from an Evaluation. Returns (id, payload)."""
        plan = ev.plan.to_dict()
        payload = {"plan": plan, "lifecycle": new_signal(plan, now_ms, self.cfg), **meta}
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
