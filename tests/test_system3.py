import copy

import pandas as pd

from agent import scorecard as SC
from agent import system3 as S3
from agent.store.repository import Repository

H = S3.H
C = {**S3.DEFAULTS, "_candle_ms": 300_000}
T0 = 1_760_000_400_000 // H * H


def pump_bars(n_flat=170, last_close=1.36, last_vol=200.0):
    """Flat at 1.0, a pump to a climax candle at 1.45 (long upper wick, 5x volume), then a weak candle."""
    bars = [[T0 + k * H, 1.0, 1.01, 0.99, 1.0, 100.0] for k in range(n_flat)]
    px = 1.0
    for _ in range(12):                                   # the run up
        bars.append([T0 + len(bars) * H, px, px * 1.03, px * 0.995, px * 1.025, 150.0])
        px *= 1.025
    bars.append([T0 + len(bars) * H, 1.38, 1.45, 1.37, 1.39, 500.0])        # climax: wick 0.75 of the range
    for p in (1.40, 1.41, 1.40):
        bars.append([T0 + len(bars) * H, p, p + 0.01, p - 0.01, p, 150.0])
    bars.append([T0 + len(bars) * H, 1.40, 1.405, 1.35, last_close, last_vol])  # lower high, under the last low
    return bars


def test_a_pump_with_a_climax_wick_gives_a_short_limit_with_stop_and_62pc_target():
    s = S3.find_setup(pump_bars(), C)
    assert s is not None and s["run_pct"] >= 25
    assert s["limit"] == (1.36 + 1.405) / 2
    assert abs(s["sl"] - max(1.45 * 1.005, s["entry"] * 1.03)) < 1e-12
    assert abs(s["target"] - (1.45 - 0.62 * (1.45 - s["base_low"]))) < 1e-12
    assert s["rr"] >= 1 and 1 <= s["leverage"] <= 10


def test_no_setup_without_the_weak_close_or_on_heavier_volume_than_the_peak():
    assert S3.find_setup(pump_bars(last_close=1.395), C) is None          # not under the previous low
    assert S3.find_setup(pump_bars(last_vol=600.0), C) is None            # more volume than the climax
    assert S3.find_setup(pump_bars()[:150], C) is None                    # too little history


def trade():
    s = S3.find_setup(pump_bars(), C)
    return S3.new_trade("AAA", s, s["close_ts"], C)


def test_fill_then_target_books_net_r_after_maker_fees():
    tr = trade()
    t = tr["created"]
    assert S3.step(tr, [t + 300_000, tr["limit"] - 0.01, tr["limit"] + 0.001, tr["limit"] - 0.02, tr["limit"]], C) == ["filled"]
    assert tr["entry"] == tr["limit"]
    assert S3.step(tr, [t + 600_000, 1.3, 1.31, tr["target"] - 0.01, 1.2], C) == ["tp"]
    assert tr["exit"] == tr["target"] and tr["net_r"] < tr["gross_r"] and tr["gross_r"] > 1


def test_target_before_the_fill_cancels_and_old_orders_expire():
    tr = trade()
    t = tr["created"]
    assert S3.step(tr, [t + 300_000, 1.3, 1.31, tr["target"] - 0.001, 1.25], C) == ["cancelled"]
    assert tr["why"] == "target_first"
    tr = trade()
    assert S3.step(tr, [tr["expires_at"], 1.3, 1.31, 1.29, 1.3], C) == ["expired"]


def test_the_stop_comes_first_and_the_time_limit_closes_at_market():
    tr = trade()
    t = tr["created"]
    S3.step(tr, [t + 300_000, tr["limit"], tr["limit"], tr["limit"] - 0.01, tr["limit"]], C)
    assert S3.step(tr, [t + 600_000, 1.4, tr["sl"] + 0.01, tr["target"] - 0.01, 1.4], C) == ["sl"]
    assert tr["net_r"] < -1
    tr = trade()
    S3.step(tr, [t + 300_000, tr["limit"], tr["limit"], tr["limit"] - 0.01, tr["limit"]], C)
    last = tr["filled_at"] + 48 * H - 300_000
    assert S3.step(tr, [last, 1.37, 1.375, 1.365, 1.37], C) == ["time"]
    assert tr["exit"] == 1.37 and tr["closed_at"] == tr["filled_at"] + 48 * H


def test_no_fill_beyond_three_open_trades():
    tr = trade()
    t = tr["created"]
    assert S3.step(tr, [t + 300_000, tr["limit"], tr["limit"], tr["limit"] - 0.01, tr["limit"]], C, 3) == ["cancelled"]
    assert tr["why"] == "max_open"


def test_system3_settings_leave_the_system_1_and_2_count_alone(cfg):
    base = SC.fingerprints(cfg)
    c = copy.deepcopy(cfg)
    c["system3"] = {"enabled": True, "risk_pct": 2}
    assert SC.fingerprints(c) == base


class FakeMarket:
    def __init__(self, bars):
        self.bars = bars

    def candles(self, base, tf):
        rows = self.bars if tf == "1h" else []
        return pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close", "volume"])


class FakeCG:
    def top_universe(self, n):
        top = [{"symbol": f"T{i}", "total_volume": 1e9, "high_24h": 1, "low_24h": 1} for i in range(20)]
        return top + [{"symbol": "aaa", "total_volume": 5e6, "high_24h": 1.45, "low_24h": 1.1},
                      {"symbol": "bbb", "total_volume": 1e6, "high_24h": 2, "low_24h": 1}]


class Sent:
    def __init__(self):
        self.msgs = []

    def send(self, text):
        self.msgs.append(text)
        return True


def test_scan_opens_one_pending_short_and_the_panel_shows_it(cfg, tmp_path):
    bars = pump_bars()
    now = bars[-1][0] + H + 120_000
    c = {**cfg, "system3": {"enabled": True, "db_path": str(tmp_path / "s3.db")}}
    repo = Repository(c["system3"]["db_path"])
    out = Sent()
    found = S3.scan(c, repo, FakeMarket(bars), FakeCG(), out, now)
    assert [f["base"] for f in found] == ["AAA"]                          # BBB: under the volume floor
    assert len(repo.get_signals()) == 1 and "سیستم ۳" in out.msgs[0]
    S3.scan(c, repo, FakeMarket(bars), FakeCG(), out, now + H)          # the same setup is not taken twice
    assert len(repo.get_signals()) == 1
    repo.close()
    v = S3.panel_view(c, now)
    assert v["trades"][0]["status"] == "pending" and v["account"]["balance"] == 100


class FakeClient:
    class Ex:
        markets = {"AAA/USDT:USDT": {"swap": True, "linear": True, "quote": "USDT", "base": "AAA"}}

    def call(self, method):
        return "gate", {"AAA/USDT:USDT": {"last": 1.36, "high": 1.45, "low": 1.1}}

    def exchange(self, name):
        return self.Ex()


def test_the_fast_copy_signals_on_the_forming_candle_with_its_own_account(cfg, tmp_path):
    bars = pump_bars()
    now = bars[-1][0] + 20 * 60_000                         # 20 minutes into the last (forming) candle
    c = {**cfg, "system3": {"enabled": True, "fast": True, "db_path": str(tmp_path / "s3.db")}}
    repo = Repository(c["system3"]["db_path"])
    out = Sent()
    assert S3.scan_fast(c, repo, FakeMarket(bars), FakeClient(), out, now) == []   # no universe before the hourly scan
    repo.set_state("s3_universe", ["AAA"])
    found = S3.scan_fast(c, repo, FakeMarket(bars), FakeClient(), out, now)
    assert [f["base"] for f in found] == ["AAA"] and "سریع" in out.msgs[0]
    S3.scan_fast(c, repo, FakeMarket(bars), FakeClient(), out, now + 60_000)   # once per forming candle
    sig = repo.get_signals()
    assert len(sig) == 1 and sig[0]["payload"]["s3"]["mode"] == "fast"
    assert sig[0]["payload"]["s3"]["expires_at"] == now + 6 * H
    repo.close()
    v = S3.panel_view(c, now)
    assert v["trades"][0]["mode"] == "fast" and v["accounts"]["fast"]["pending"] == 1
    assert v["accounts"]["hourly"]["pending"] == 0


def test_the_run_publishes_the_panel_data_at_once(cfg, tmp_path):
    import json
    c = {**cfg, "system3": {"enabled": True, "db_path": str(tmp_path / "s3.db"), "live_json": str(tmp_path / "www" / "s3.json")}}
    assert S3.publish(c, 123) is None                                   # no panel folder here: nothing written
    (tmp_path / "www").mkdir()
    repo = Repository(c["system3"]["db_path"])
    S3.scan(c, repo, FakeMarket(pump_bars()), FakeCG(), None, pump_bars()[-1][0] + H + 120_000)
    repo.close()
    path = S3.publish(c, 456)
    v = json.loads(open(path, encoding="utf-8").read())
    assert v["now"] == 456 and v["trades"][0]["base"] == "AAA"
