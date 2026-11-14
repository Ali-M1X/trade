"""Shadow book: Watch setups (scored, with a trade plan, but not sent as signals) followed
exactly like real signals, without any message. Ali (2026-11-14) wants the status panel to
show how the unconfirmed setups would have done next to the confirmed ones.

A Watch is sized like a grade-B signal (base risk x the B share). One open shadow trade per
coin; the book's other admission rules (caps, correlation, loss-streak pause) don't apply.
"""
from __future__ import annotations

from ..layers.trade import size_position
from ..regime_systems import system_cfg
from .manager import SignalBook


class Silent:
    """Notifier that sends nothing (shadow trades are never announced)."""

    def send(self, text: str) -> bool:
        return True


def enabled(cfg: dict) -> bool:
    return bool((cfg.get("shadow") or {}).get("enabled"))


def watch_risk_pct(cfg: dict) -> float:
    return cfg["trade"]["base_risk_pct"] * cfg["grades"]["risk_share"]["B"]


def record(cfg: dict, shadow_repo, evs: list, now_ms: int, state: dict) -> list[int]:
    """Open a shadow trade for every Watch evaluation with a plan. state: the funnel state
    the evaluations were made with (its regime picks the system's config)."""
    cfg = system_cfg(cfg, state["regime"]["name"]) or cfg
    book = SignalBook(shadow_repo, cfg)
    open_bases = {s["symbol"] for s in book.open_signals()}
    created = []
    for ev in sorted(evs, key=lambda e: -e.score):
        if ev.grade != "Watch" or ev.plan is None or ev.base in open_bases:
            continue
        size_position(ev.plan, watch_risk_pct(cfg), cfg)
        meta = {"regime": state["regime"], "majors": state["majors"],
                "evaluation": ev.to_dict(), "approved": False}
        sid, _ = book.create(ev, now_ms, meta)
        open_bases.add(ev.base)
        created.append(sid)
    if not shadow_repo.get_state("shadow_since"):
        shadow_repo.set_state("shadow_since", now_ms)
    return created
