"""Trading costs in R: taker fee and slippage on every fill, funding while open.

R = the distance from entry to the original stop. A cost of c% of notional on a fill of
fraction f at price p costs f * (p / entry) * c% / stop%, in R.
"""
from __future__ import annotations

HOUR = 3_600_000


def exit_fills(trade: dict, cfg: dict) -> list[tuple[int, float, float]]:
    """(ts, price, fraction) for each exit, from the lifecycle events."""
    t = cfg["trade"]
    left = 1.0
    out = []
    for kind, ts, price in trade["events"]:
        if kind == "tp1":
            f = t["tp1_close_pct"] / 100
        elif kind == "tp2":
            f = t["tp2_close_pct"] / 100
        elif kind in ("sl", "breakeven", "tp3"):
            f = left
        else:
            continue
        out.append((ts, price, f))
        left -= f
    return out


def trade_costs(trade: dict, cfg: dict, funding_at=None) -> dict:
    """funding_at(base, ts) -> rate as a fraction, or None if unknown."""
    b = cfg["backtest"]
    gross = trade["gross_r"]
    zero = {"gross_r": gross, "fee_r": 0.0, "slippage_r": 0.0, "funding_r": 0.0, "net_r": gross}
    if not trade["filled_at"]:
        return zero
    entry, side = trade["entry"], trade["side"]
    stop = abs(entry - trade["sl"]) / entry
    if stop <= 0:
        return zero
    fills = [(trade["filled_at"], entry, 1.0)] + exit_fills(trade, cfg)
    notional = sum(f * p / entry for _, p, f in fills)
    fee_r = notional * b["taker_fee_pct"] / 100 / stop
    slip_r = notional * b["slippage_pct"] / 100 / stop

    # funding on the part still open at each funding time
    step = b["funding_interval_h"] * HOUR
    exits = exit_fills(trade, cfg)
    end = trade["closed_at"] or (exits[-1][0] if exits else trade["filled_at"])
    funding_r = 0.0
    ts = (trade["filled_at"] // step + 1) * step
    while ts <= end:
        open_frac = 1.0 - sum(f for t_exit, _, f in exits if t_exit < ts)
        if open_frac <= 1e-9:
            break
        rate = funding_at(trade["base"], ts) if funding_at else None
        if rate is None:
            rate = b["default_funding_pct"] / 100
        funding_r += side * rate * open_frac / stop       # longs pay a positive rate
        ts += step
    net = gross - fee_r - slip_r - funding_r
    return {"gross_r": gross, "fee_r": fee_r, "slippage_r": slip_r, "funding_r": funding_r,
            "net_r": net}
