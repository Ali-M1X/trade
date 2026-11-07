from agent.signals.lifecycle import new_signal, on_candle


def test_breakeven_at_r_moves_stop_to_entry():
    cfg = {"lifecycle": {"expiry_bars_4h": 3},
           "trade": {"breakeven_at_r": 1.0, "tp1_close_pct": 50, "tp2_close_pct": 25}}
    plan = {"side": 1, "entry": 100.0, "sl": 98.0, "tp1": 104.0, "tp2": 106.0, "order": "market"}
    s = new_signal(plan, 0, cfg)
    assert on_candle(s, 1, 102.5, 99.0, cfg) == [] and s["sl_now"] == 100.0
    ev = on_candle(s, 2, 101.0, 99.5, cfg)
    assert ev[0].kind == "breakeven" and s["realized_r"] == 0.0


def test_breakeven_off_by_default():
    cfg = {"lifecycle": {"expiry_bars_4h": 3}, "trade": {"tp1_close_pct": 50, "tp2_close_pct": 25}}
    plan = {"side": 1, "entry": 100.0, "sl": 98.0, "tp1": 104.0, "tp2": 106.0, "order": "market"}
    s = new_signal(plan, 0, cfg)
    on_candle(s, 1, 102.5, 99.0, cfg)
    assert on_candle(s, 2, 101.0, 97.5, cfg)[0].kind == "sl"
