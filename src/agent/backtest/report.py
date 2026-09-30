"""Backtest statistics and the docs/BACKTEST.md report."""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone

REGIME_NAMES = ["alt_season", "btc_led", "risk_off", "capitulation", "neutral"]
CLOSED_TRADES = ("sl", "breakeven", "tp3")


def _date(ms) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d") if ms else "-"


def stats(trades: list[dict]) -> dict:
    """Closed trades only (filled and exited). R figures are net unless named gross."""
    done = sorted([t for t in trades if t["status"] in CLOSED_TRADES], key=lambda t: t["closed_at"])
    n = len(done)
    if not n:
        return {"trades": 0}
    net = [t["net_r"] for t in done]
    wins = [r for r in net if r > 0]
    losses = [r for r in net if r <= 0]
    cum = peak = dd_r = 0.0
    equity = eq_peak = 1.0
    dd_pct = 0.0
    for t in done:
        cum += t["net_r"]
        peak = max(peak, cum)
        dd_r = max(dd_r, peak - cum)
        equity *= 1 + t["risk_pct"] / 100 * t["net_r"]
        eq_peak = max(eq_peak, equity)
        dd_pct = max(dd_pct, (eq_peak - equity) / eq_peak * 100)
    hours = [(t["closed_at"] - t["filled_at"]) / 3_600_000 for t in done]
    return {
        "trades": n,
        "win_rate": len(wins) / n * 100,
        "avg_r": sum(net) / n,
        "total_r": sum(net),
        "avg_gross_r": sum(t["gross_r"] for t in done) / n,
        "avg_cost_r": sum(t["gross_r"] - t["net_r"] for t in done) / n,
        "profit_factor": (sum(wins) / -sum(losses)) if losses and sum(losses) < 0 else float("inf"),
        "max_dd_r": dd_r,
        "return_pct": (equity - 1) * 100,
        "max_dd_pct": dd_pct,
        "avg_hours": sum(hours) / n,
    }


def _row(label: str, s: dict) -> str:
    if not s.get("trades"):
        return f"| {label} | 0 | – | – | – | – | – | – | – | – |"
    pf = "∞" if s["profit_factor"] == float("inf") else f"{s['profit_factor']:.2f}"
    return (f"| {label} | {s['trades']} | {s['win_rate']:.1f}% | {s['avg_r']:+.2f} | {s['total_r']:+.1f} | "
            f"{s['avg_gross_r']:+.2f} | {pf} | {s['max_dd_r']:.1f} | {s['return_pct']:+.1f}% | {s['max_dd_pct']:.1f}% |")


HEADER = ("| Group | Trades | Win rate | Avg R (net) | Total R (net) | Avg R (gross) | Profit factor | "
          "Max DD (R) | Return | Max DD |\n|---|---|---|---|---|---|---|---|---|---|")


def build_report(trades: list[dict], engine_stats: dict, regime_hours: dict, meta: dict,
                 cfg: dict) -> str:
    b = cfg["backtest"]
    outcome = Counter(t["status"] for t in trades)
    lines = [
        "# Backtest",
        "",
        f"Period: {_date(meta['start'])} → {_date(meta['end'])} ({b['days']} days), "
        f"exchange candles from {meta.get('exchange', '?')}, universe of {meta.get('coins', '?')} coins.",
        f"Costs: taker fee {b['taker_fee_pct']}% per side, slippage {b['slippage_pct']}% per fill, "
        f"funding every {b['funding_interval_h']}h (exchange history where available, otherwise "
        f"{b['default_funding_pct']}% per interval, paid by longs).",
        "Win rate = share of closed trades with net R > 0. Return and drawdown compound each "
        "trade's net R × its risk % (1% base × grade share × regime multipliers).",
        "",
        "## Results",
        "",
        HEADER,
        _row("**All**", stats(trades)),
        _row("Grade A", stats([t for t in trades if t["grade"] == "A"])),
        _row("Grade B", stats([t for t in trades if t["grade"] == "B"])),
        _row("Long", stats([t for t in trades if t["side"] == 1])),
        _row("Short", stats([t for t in trades if t["side"] == -1])),
        "",
        "### By regime at signal time",
        "",
        HEADER,
    ]
    for name in REGIME_NAMES:
        for grade in (None, "A", "B"):
            sel = [t for t in trades if t["regime"] == name and (grade is None or t["grade"] == grade)]
            if grade and not sel:
                continue
            lines.append(_row(f"{name}{'' if grade is None else ' – ' + grade}", stats(sel)))
    total_h = sum(regime_hours.values()) or 1
    lines += [
        "",
        "Time spent in each regime: " + ", ".join(
            f"{n} {regime_hours.get(n, 0) / total_h * 100:.0f}%" for n in REGIME_NAMES) + ".",
        "",
        "### Signal outcomes",
        "",
        "| Outcome | Count |",
        "|---|---|",
    ]
    for k in ("tp3", "breakeven", "sl", "expired", "cancelled", "pending", "active", "tp1", "tp2"):
        if outcome.get(k):
            lines.append(f"| {k} | {outcome[k]} |")
    costs = [t for t in trades if t["status"] in CLOSED_TRADES]
    if costs:
        n = len(costs)
        lines += [
            "",
            "### Average cost per closed trade (R)",
            "",
            f"Fees {sum(t['fee_r'] for t in costs) / n:.3f} · slippage {sum(t['slippage_r'] for t in costs) / n:.3f} · "
            f"funding {sum(t['funding_r'] for t in costs) / n:.3f}",
        ]
    by_month = Counter()
    for t in costs:
        by_month[_date(t["closed_at"])[:7]] += t["net_r"]
    if by_month:
        lines += ["", "### Net R by month", "", "| Month | Net R |", "|---|---|"]
        lines += [f"| {m} | {r:+.1f} |" for m, r in sorted(by_month.items())]
    st = engine_stats
    lines += [
        "",
        "### Funnel activity",
        "",
        f"4H funnel runs: {st.get('funnel_runs', 0)}, shortlist entries: {st.get('shortlisted', 0)}, "
        f"L6 evaluations: {st.get('evaluations', 0)}. Grades: A {st.get('grade_A', 0)}, "
        f"B {st.get('grade_B', 0)}, Watch {st.get('grade_Watch', 0)}. Signals opened: "
        f"{st.get('signals', 0)}. Blocked by the book: " + ", ".join(
            f"{k.removeprefix('blocked_')} {v}" for k, v in sorted(st.items()) if k.startswith("blocked_")) + ".",
    ]
    return "\n".join(lines) + "\n"
