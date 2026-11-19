import pandas as pd
import pytest

from agent import pulse as P
from agent.store.repository import Repository

H = 3_600_000
NOW = 1_800_000_000_000 // H * H


def path(start_px, pct_per_h, hours, step=H, end=NOW):
    """(close time, close) ending at `end`: a steady move of pct_per_h % an hour."""
    n = hours * H // step
    return [(end - (n - k) * step, start_px * (1 + pct_per_h / 100) ** ((k * step) / H)) for k in range(n + 1)]


def test_moves_at_averages_the_basket_over_each_window():
    paths = {"BTC": path(100, 1.0, 30), "ETH": path(50, -1.0, 30)}
    h1, h4, h24 = P.moves_at(paths, NOW)
    assert h1 == pytest.approx(0.0, abs=1e-3)                            # equal and opposite
    paths = {"BTC": path(100, 1.0, 30), "ETH": path(50, 1.0, 30)}
    h1, h4, h24 = P.moves_at(paths, NOW)
    assert h1 == pytest.approx(1.0, abs=1e-3) and h4 == pytest.approx((1.01 ** 4 - 1) * 100, abs=1e-3)
    assert h24 == pytest.approx((1.01 ** 24 - 1) * 100, abs=1e-3)
    assert P.moves_at({"BTC": path(100, 1.0, 10)}, NOW) is None          # no price 24 h back


class Market:
    def __init__(self, tfs):
        self.tfs, self.asked = tfs, []

    def candles(self, base, tf):
        self.asked.append((base, tf))
        pts = self.tfs.get(tf, [])
        return pd.DataFrame({"ts": [t - P.TF_MS[tf] for t, _ in pts], "close": [c for _, c in pts]})


def test_record_backfills_once_then_adds_every_few_minutes(cfg):
    repo = Repository(":memory:")
    m = Market({"1h": path(100, 0.2, 24 * 5), "5m": path(100, 0.2, 30, step=300_000)})
    last = P.record(cfg, repo, m, NOW)
    hist = repo.get_state("pulse")
    assert len(hist) > 24 * 3 and hist[-1][0] == NOW and last == hist[-1]
    assert {tf for _, tf in m.asked} == {"1h", "5m"}
    assert hist[-1][1] == pytest.approx(0.2, abs=1e-3)
    m2 = Market({"5m": path(100, 0.2, 30, step=300_000, end=NOW + 300_000)})
    assert P.record(cfg, repo, m2, NOW + 60_000) is None                   # not due yet
    P.record(cfg, repo, m2, NOW + 300_000)
    assert {tf for _, tf in m2.asked} == {"5m"}
    assert repo.get_state("pulse")[-1][0] == NOW + 300_000
    assert P.record({**cfg, "pulse": {"enabled": False}}, repo, m2, NOW + 900_000) is None


def trade(side, filled_at, entry=100.0, sl=95.0, **kw):
    return {"base": "AAA", "side": side, "filled_at": filled_at, "entry": entry, "sl": sl, "events": [],
            "closed_at": None, "cost_r": 0.1, **kw}


def test_view_marks_trades_opened_against_the_pulse_and_the_first_alarm(cfg):
    c = {**cfg, "pulse": {"h1_pct": 0.5, "h4_pct": 1.0}}
    t0 = NOW - 10 * H
    hist = [[t0 + k * H, 0.1, 0.2, 0.3] for k in range(11)]
    hist[0] = [t0, 0.8, 1.5, 2.0]                         # up at the first short's fill
    hist[5] = [t0 + 5 * H, 0.6, 1.2, 1.0]                 # up again: against shorts
    hist[7] = [t0 + 7 * H, -0.7, -1.4, -1.0]              # down: against longs
    prices = {"AAA": [(t0 + k * H, 100.0 + k) for k in range(11)]}
    short = trade(-1, t0)
    long_ = trade(1, t0 + H)
    late = trade(-1, t0 + 6 * H, closed_at=t0 + 8 * H)    # closed before any other up alarm
    past_tp1 = trade(1, t0 + H, events=[("tp1", t0 + 3 * H, 103.0)])
    old = trade(1, t0 - 5 * H)                            # before the history: nothing said
    pv = P.view(hist, [short, long_, late, past_tp1, old], prices, NOW, c)
    assert short["pulse"]["entry"] == [0.8, 1.5, True]
    assert short["pulse"]["alarm"]["t"] == t0 + 5 * H                     # the fill sample is not an alarm
    assert short["pulse"]["alarm"]["r"] == pytest.approx(-1 * (105 - 100) / 5 - 0.1)
    assert long_["pulse"]["entry"][2] is False
    assert long_["pulse"]["alarm"]["t"] == t0 + 7 * H and long_["pulse"]["alarm"]["price"] == 107.0
    assert late["pulse"]["alarm"] is None
    assert past_tp1["pulse"]["alarm"] is None                             # risk-free after TP1
    assert old["pulse"] is None
    assert [s["dir"] for s in pv["spans"]] == [1, 1, -1]
    assert pv["state"] == 0 and pv["now"] == hist[-1] and pv["params"] == {"h1_pct": 0.5, "h4_pct": 1.0, "h24_pct": 2.0}


def test_panel_carries_the_pulse_without_touching_the_accounts(cfg, tmp_path):
    from agent.panel import collect
    c = {**cfg, "storage": {**cfg["storage"], "db_path": str(tmp_path / "state.db")},
         "system2": {**cfg.get("system2", {}), "enabled": False}, "shadow": {"enabled": False}}
    repo = Repository(c["storage"]["db_path"])
    repo.set_state("pulse", [[NOW - H, 0.7, 1.3, 2.0], [NOW, 0.6, 1.1, 2.1]])
    repo.close()
    data = collect(c, NOW)
    assert data["pulse"]["now"] == [NOW, 0.6, 1.1, 2.1] and data["pulse"]["state"] == 1


def test_regime_view_reads_closed_4h_candles_only():
    """2026-11-18 (Ali): the 24 h move said «up to the closed candle of 19:30» at 19:04: the forming
    candle (stored at its last fetch) was counted."""
    from agent.panel_regime import BAR, BASKET, regime_view
    repo = Repository(":memory:")
    now = NOW + 90 * 60_000                                # 1.5 h into a 4H candle
    start = now // BAR * BAR
    for b in BASKET:
        rows = [[start - k * BAR, 100, 101, 99, 100, 1] for k in range(60, 0, -1)]
        rows.append([start, 100, 120, 100, 120, 1])        # the forming candle: +20% so far
        repo.upsert_candles("gate", f"{b}/USDT:USDT", "4h", rows)
    rv = regime_view(repo, now)
    assert rv["bars"][-1][0] == start - BAR
    assert rv["metrics"]["move_24h"] == 0
