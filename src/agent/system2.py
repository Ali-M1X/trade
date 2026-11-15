"""High-risk system 2 (paper trading, 2026-10-09).

Runs next to the live system on the same funnel shortlist and market data, with the
`system2.variant` config (other L6 section weights, 2% / 1% risk). Its signals, events and
state live in their own database (system2.db_path) so the two systems never block or close
each other's trades. Every message it sends starts with HEADER. It sends no Watch alerts:
the live system already sends them.
"""
from __future__ import annotations

import logging

from .backtest.variants import merge
from .notify import formatter as fmt
from .runs import manage, run_1h

log = logging.getLogger(__name__)
HEADER = "🔥 سیستم ۲ (High Risk)"


class Labelled:
    """Notifier wrapper that puts HEADER on top of every message."""

    def __init__(self, notifier):
        self.inner = notifier

    def send(self, text: str) -> bool:
        return self.inner.send(f"{HEADER}\n{text}")


def enabled(cfg: dict) -> bool:
    return bool((cfg.get("system2") or {}).get("enabled"))


def system2_config(cfg: dict) -> dict:
    name = cfg["system2"]["variant"]
    variants = cfg["backtest"]["variants"]
    if name not in variants:
        raise SystemExit(f"system2.variant {name!r} is not in backtest.variants")
    # system 2 stays on the dominance regime when system 1 trades by price regime
    regime = {**(variants[name].get("regime") or {}), "source": "dominance"}
    return merge(cfg, {**variants[name], "watch_alerts": {"enabled": False}, "regime": regime})


def run_system2(cfg: dict, repo, repo2, market, notifier, now_ms: int, tasks: list[str],
                shadow_repo=None) -> None:
    """repo: the live database (funnel state, candles). repo2: system 2's own database.
    shadow_repo: where system 2's Watch setups are followed (status panel), if anywhere."""
    cfg2, out = system2_config(cfg), Labelled(notifier)
    key = "funnel_dominance" if repo.get_state("funnel_dominance") else "funnel"
    if "run-15m" in tasks:
        print(f"system2 run-15m: {len(manage(cfg2, repo2, market, out, now_ms))} signal events")
    evs = None
    if "run-1h" in tasks:
        evs = run_1h(cfg2, repo, market, now_ms, out, book_repo=repo2, state_key=key)
        print(f"system2 run-1h: {sum(e.is_signal for e in evs)} A/B of {len(evs)} evaluated")
    if shadow_repo is not None:
        from .signals import shadow
        try:                                    # status panel only
            shadow.run_pass(cfg2, repo, shadow_repo, market, now_ms, tasks, evs, key)
        except Exception:
            log.exception("system2 shadow failed")
    if "run-weekly" in tasks:
        out.send(fmt.performance_message(repo2.get_signals(), now_ms))
