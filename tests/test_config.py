from agent.config import load_secrets


def test_required_numbers_from_build_prompt(cfg):
    ind = cfg["indicators"]
    assert (ind["ma_fast"], ind["ma_mid"], ind["ma_slow"]) == (7, 25, 99)
    assert ind["rsi_period"] == 14
    assert (ind["macd_fast"], ind["macd_slow"], ind["macd_signal"]) == (12, 26, 9)
    assert ind["atr_period"] == 14
    assert cfg["trade"]["base_risk_pct"] == 1.0
    assert cfg["lifecycle"]["max_active"] == 5
    assert cfg["grades"]["a_plus"] == 85
    assert cfg["trade"]["leverage_cap"] == 10
    assert cfg["trade"]["liquidation_sl_multiple"] == 2.5
    assert cfg["universe"]["top_n"] == 100
    assert set(cfg["universe"]["exclude_categories"]) == {"stablecoins", "wrapped-tokens"}


def test_grade_thresholds_are_ordered(cfg):
    g = cfg["grades"]
    assert g["watch"] < g["B"] < g["A"] < g["a_plus"]


def test_shortlist_weights_sum_to_100(cfg):
    assert sum(cfg["shortlist"]["weights"].values()) == 100


def test_secrets_from_env_and_dry_run():
    s = load_secrets({})
    assert not s.telegram_enabled and s.coingecko_api_key is None
    s = load_secrets({"TELEGRAM_BOT_TOKEN": "t", "TELEGRAM_CHAT_ID": "1", "COINGECKO_API_KEY": ""})
    assert s.telegram_enabled and s.coingecko_api_key is None
