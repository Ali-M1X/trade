from agent.config import live_config


def test_live_config_applies_the_live_variant_on_top(cfg):
    name = cfg["live"]["variant"]
    assert name in cfg["backtest"]["variants"]
    live = live_config(cfg)
    assert live["trade"]["stop_mode"] == "atr" and live["trade"]["tp1_mode"] == "fixed_r"
    assert live["grades"].get("min_signal", "B") == "B"     # grade B is a signal again (#135)
    assert live["technical"]["classic"]["vcp"] is True      # VCP filter (2026-10-08)
    # the base config (backtest baseline, tests) is untouched
    assert cfg["trade"]["stop_mode"] == "level" and "min_signal" not in cfg["grades"]
    assert cfg["technical"]["classic"]["vcp"] is False


def test_live_config_without_a_variant_is_the_base(cfg):
    c = dict(cfg, live={"variant": None})
    assert live_config(c) is c
