import pytest

from agent.backtest import engine as engine_mod
from agent.backtest.costs import exit_fills, trade_costs
from agent.backtest.report import build_report, stats
from agent.backtest.run import run_backtest
from agent.layers.technical import Evaluation
from agent.layers.trade import TradePlan, size_position
from synthetic_history import build

HOUR = 3_600_000
T0 = 1_760_000_000_000 // (8 * HOUR) * (8 * HOUR)       # on a funding time


def trade(**kw):
    t = {"base": "SOL", "side": 1, "entry": 100.0, "sl": 98.0, "gross_r": 0.0, "status": "tp3",
         "filled_at": T0, "closed_at": T0 + 20 * HOUR, "risk_pct": 1.0, "grade": "A",
         "regime": "alt_season",
         "events": [("filled", T0, 100.0), ("tp1", T0 + 2 * HOUR, 104.0),
                    ("tp2", T0 + 10 * HOUR, 106.0), ("tp3", T0 + 20 * HOUR, 105.0)]}
    t.update(kw)
    return t


def test_exit_fractions(cfg):
    assert [f for _, _, f in exit_fills(trade(), cfg)] == pytest.approx([0.5, 0.3, 0.2])
    sl = trade(events=[("filled", T0, 100.0), ("sl", T0 + HOUR, 98.0)])
    assert [f for _, _, f in exit_fills(sl, cfg)] == [1.0]


def test_costs_in_r(cfg):
    c = trade_costs(trade(gross_r=2.0), cfg, lambda base, ts: None)
    stop = 0.02
    notional = 1 + 0.5 * 1.04 + 0.3 * 1.06 + 0.2 * 1.05
    assert c["fee_r"] == pytest.approx(notional * 0.0005 / stop)
    assert c["slippage_r"] == pytest.approx(notional * 0.0005 / stop)
    # funding at T0+8h (open 0.5 after TP1) and T0+16h (open 0.2 after TP2), default 0.01%
    assert c["funding_r"] == pytest.approx((0.5 + 0.2) * 0.0001 / stop)
    assert c["net_r"] == pytest.approx(2.0 - c["fee_r"] - c["slippage_r"] - c["funding_r"])


def test_funding_uses_history_and_shorts_receive(cfg):
    rates = {T0 + 8 * HOUR: 0.0005, T0 + 16 * HOUR: -0.0002}
    c = trade_costs(trade(side=-1, sl=102.0, events=[("filled", T0, 100.0), ("sl", T0 + 20 * HOUR, 102.0)],
                          gross_r=-1.0), cfg, lambda base, ts: rates.get(ts))
    assert c["funding_r"] == pytest.approx(-(0.0005 - 0.0002) / 0.02)    # net received


def test_unfilled_signals_cost_nothing(cfg):
    c = trade_costs(trade(filled_at=None, status="expired", events=[]), cfg)
    assert c["net_r"] == 0 and c["fee_r"] == 0


def test_stats():
    ts = [trade(net_r=r, gross_r=r + 0.1, closed_at=T0 + i * HOUR, risk_pct=1.0)
          for i, r in enumerate([2.0, -1.0, -1.0, 3.0, -1.0])]
    s = stats(ts + [trade(status="expired", net_r=0)])
    assert s["trades"] == 5 and s["win_rate"] == 40
    assert s["total_r"] == pytest.approx(2.0) and s["avg_r"] == pytest.approx(0.4)
    assert s["profit_factor"] == pytest.approx(5 / 3)
    assert s["max_dd_r"] == pytest.approx(2.0)                   # +2 -> 0
    equity = 1.02 * 0.99 * 0.99 * 1.03 * 0.99
    assert s["return_pct"] == pytest.approx((equity - 1) * 100)
    assert stats([]) == {"trades": 0}


def test_report_has_grade_and_regime_sections(cfg):
    ts = [trade(net_r=1.5, fee_r=0.1, slippage_r=0.1, funding_r=0.0, gross_r=1.7),
          trade(grade="B", regime="neutral", net_r=-1.1, fee_r=0.05, slippage_r=0.05, funding_r=0.0,
                gross_r=-1.0, status="sl")]
    md = build_report(ts, {"signals": 2}, {"alt_season": 10, "neutral": 30},
                      {"start": T0, "end": T0 + 30 * 24 * HOUR}, cfg)
    for needle in ("| Grade A | 1 |", "| Grade B | 1 |", "| alt_season | 1 |", "| neutral – B | 1 |",
                   "alt_season 25%", "neutral 75%", "| tp3 | 1 |", "| sl | 1 |", "Fees 0.075"):
        assert needle in md, needle


# ------------------------------------------------------------------ engine
def test_engine_runs_and_is_deterministic(cfg):
    repo = build(days=150)
    a, _ = run_backtest(cfg, repo, days=15)
    b, _ = run_backtest(cfg, repo, days=15)
    assert a == b and "4H funnel runs: 90" in a


def test_engine_trades_flow_through_book_lifecycle_and_costs(cfg, monkeypatch):
    """Force an A-grade market long on every shortlisted coin to exercise the trade path."""
    def fake_evaluate(base, side, frames, regime, majors, cfg, **kw):
        h4, h1 = frames["4h"], frames["1h"]
        price = h1.close
        plan = TradePlan(1, "market", price, price * 0.998, price * 1.002, price - 1.5 * h4.atr,
                         price + 3 * h4.atr, price + 4.5 * h4.atr, 2, 3, False, price,
                         "cluster", "4h", 2, h4.atr)
        size_position(plan, 1.0, cfg)
        return Evaluation(base, 1, score=80, grade="A", plan=plan, labels=kw.get("labels") or [],
                          gates={"regime": True, "phase": True, "rr": True, "confirmation": True})
    monkeypatch.setattr(engine_mod, "evaluate", fake_evaluate)
    repo = build(days=150)
    report, trades = run_backtest(cfg, repo, days=30)
    closed = [t for t in trades if t["status"] in ("sl", "breakeven", "tp3")]
    assert closed, "expected some closed trades"
    for t in closed:
        assert t["filled_at"] == t["created"]                  # market orders
        assert t["net_r"] < t["gross_r"]                       # costs always bite
        assert t["fee_r"] > 0 and t["slippage_r"] > 0
    sl = [t for t in closed if t["status"] == "sl"]
    assert all(t["gross_r"] == pytest.approx(-1) for t in sl)
    # never more than 5 open at once, one per coin
    assert "| Grade A |" in report and "blocked" not in report.split("Blocked by the book:")[0]
    opened = sorted((t["created"], t["closed_at"] or 1e20, t["base"]) for t in trades)
    for c, _, base in opened:
        live = [b for c2, e2, b in opened if c2 <= c < e2]
        assert len(live) <= cfg["lifecycle"]["max_active"] and live.count(base) == 1
