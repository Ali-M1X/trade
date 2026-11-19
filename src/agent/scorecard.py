"""Measurement period (2026-11-19, Ali chose «۱ و ۲»: keep the rules still for four weeks and write the
success test down now).

- Rules lock: every run stores a fingerprint of each system's trading rules (the live config with its
  variant applied, without display, data-source and storage sections). When a fingerprint changes, that
  system's count starts again from the change. A change to the code of how trades are picked or managed
  moves `scorecard.since` by hand.
- Success test: after `min_trades` closed approved trades, the average net R, the profit factor and the
  account's deepest drawdown are checked against the targets (the drawdown limit fails it at any count). Watch trades are shown next to them as the
  reference (do the scores add anything?).

Nothing here changes a trade.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

DAY = 86_400_000
DEFAULTS = {"since": "2026-11-19", "weeks": 4, "min_trades": 100, "avg_r": 0.1, "pf": 1.2, "max_dd_pct": 20,
            "real_risk_pct": 1}
# sections that do not change which trades are taken or how they are run
NOT_RULES = {"exchange", "coingecko", "tvdatafeed", "schedule", "telegram", "shadow", "panel", "pulse", "ai",
             "storage", "backtest", "live", "system2", "system3", "scorecard", "hold", "watch_alerts"}
S1, S2 = "سیستم ۱", "سیستم ۲"


def settings(cfg: dict) -> dict:
    return {**DEFAULTS, **(cfg.get("scorecard") or {})}


def _ms(day: str) -> int:
    return int(datetime.strptime(str(day), "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp() * 1000)


def _fp(cfg: dict) -> str:
    rules = {k: v for k, v in cfg.items() if k not in NOT_RULES}
    blob = json.dumps(rules, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


def fingerprints(cfg: dict) -> dict[str, str]:
    """Each live system's rules fingerprint. cfg: the base config or the live one (applying the live
    variant twice gives the same config)."""
    from .config import live_config
    from .system2 import enabled, system2_config
    live = live_config(cfg)
    out = {S1: _fp(live)}
    if enabled(live):
        out[S2] = _fp(system2_config(live))
    return out


def record(cfg: dict, repo, now: int) -> dict:
    """Append each system's fingerprint when it changed (state `rules_fp` = {system: [[t, fp], ...]}).
    The first record of a system carries t = 0: its rules are taken as in force since `since`."""
    hist = repo.get_state("rules_fp", {}) or {}
    changed = False
    for name, fp in fingerprints(cfg).items():
        h = hist.setdefault(name, [])
        if not h or h[-1][1] != fp:
            h.append([now if h else 0, fp])
            del h[:-20]
            changed = True
    if changed:
        repo.set_state("rules_fp", hist)
    return hist


def _closed(t: dict) -> bool:
    return bool(t.get("filled_at")) and not t.get("open") and t.get("net_r") is not None and bool(t.get("closed_at"))


def _group(trades: list[dict], start: int, now: int, simulate) -> dict:
    done = sorted([t for t in trades if _closed(t)], key=lambda t: t["closed_at"])
    rs = [t["net_r"] for t in done]
    gain, loss = sum(r for r in rs if r > 0), -sum(r for r in rs if r < 0)
    acc = simulate(trades, 100.0, start, now) if trades else None
    return {"closed": len(rs), "open": len([t for t in trades if t.get("open") and t.get("filled_at")]),
            "signals": len(trades), "wins": len([r for r in rs if r > 0]),
            "net_r": round(sum(rs), 4), "avg_r": round(sum(rs) / len(rs), 4) if rs else None,
            "pf": round(gain / loss, 4) if loss else None, "loss_r": round(loss, 4),
            "max_dd": round(acc["max_dd"], 4) if acc else None,
            "pnl_pct": round(acc["pnl"], 4) if acc else None}


def _checks(g: dict, c: dict) -> dict:
    """Each target: True met, False missed, None not measurable yet."""
    return {"avg_r": None if g["avg_r"] is None else g["avg_r"] >= c["avg_r"],
            "pf": (g["net_r"] > 0 if g["closed"] else None) if g["pf"] is None else g["pf"] > c["pf"],
            "max_dd": None if g["max_dd"] is None or not g["closed"] else g["max_dd"] < c["max_dd_pct"]}


def view(cfg: dict, trades: list[dict], hist: dict, now: int) -> dict:
    """The panel's measurement period: when each system's count starts, the approved and Watch results
    since then, and the verdict (measuring / pass / fail / extend)."""
    from .panel import simulate
    c = settings(cfg)
    since = _ms(c["since"])
    cur = fingerprints(cfg)
    systems = {}
    for name in cur:
        h = hist.get(name) or []
        changed = h[-1][0] if h and h[-1][1] == cur[name] else (now if h else 0)
        systems[name] = {"start": max(since, changed), "changed_at": changed if changed > since else None,
                         "pending": bool(h) and h[-1][1] != cur[name]}
    first = min(s["start"] for s in systems.values())
    mine = [t for t in trades if t["system"] in systems and t["created"] >= systems[t["system"]]["start"]]
    groups = {}
    for key, approved in (("approved", True), ("watch", False)):
        sel = [t for t in mine if bool(t["approved"]) == approved]
        groups[key] = _group(sel, first, now, simulate)
        for name in systems:
            groups[f"{key}:{name}"] = _group([t for t in sel if t["system"] == name], systems[name]["start"],
                                             now, simulate)
    a = groups["approved"]
    checks = _checks(a, c)
    ends = first + c["weeks"] * 7 * DAY
    if checks["max_dd"] is False:                 # the drawdown limit fails the test at any count
        status = "fail"
    elif a["closed"] >= c["min_trades"]:
        status = "pass" if all(checks.values()) else "fail"
    else:
        status = "extend" if now >= ends else "measuring"
    return {"since": since, "first": first, "ends": ends, "days": c["weeks"] * 7, "status": status,
            "targets": {k: c[k] for k in ("min_trades", "avg_r", "pf", "max_dd_pct", "real_risk_pct")},
            "systems": systems, "groups": groups, "checks": checks}
