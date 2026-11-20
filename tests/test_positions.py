import json

from agent import positions as P
from agent.panel import collect, live_positions, payload
from agent.signals.lifecycle import new_signal
from agent.store.repository import Repository
from test_panel import NOW, START, _add, _cfg, _plan

M5 = 300_000


def test_a_5m_slot_is_handled_once_lag_seconds_after_its_close():
    t = 1_795_190_400_000                                   # a 5m boundary
    assert P.candle_due(t + 5_000, t - M5, 10) is None      # the candle that just closed is not final yet
    assert P.candle_due(t + 10_000, t - M5, 10) == t
    assert P.candle_due(t + 200_000, t, 10) is None         # done already
    assert P.candle_due(t + M5 + 10_000, t, 10) == t + M5


def test_the_minute_run_leaves_positions_to_a_live_fast_lane_only(cfg, tmp_path):
    repo = Repository(str(tmp_path / "s.db"))
    assert not P.alive(cfg, repo, NOW)                      # no heartbeat: the minute run does everything
    repo.set_state(P.HB, NOW - 30_000)
    assert P.alive(cfg, repo, NOW)
    assert not P.alive(cfg, repo, NOW + 10 * 60_000)        # the lane stopped: back to the minute run
    off = {**cfg, "realtime": {**cfg["realtime"], "positions": {"enabled": False}}}
    assert not P.alive(off, repo, NOW)
    repo.close()


def _books(cfg, tmp_path):
    c = _cfg(cfg, tmp_path)
    repo = Repository(c["storage"]["db_path"])
    _add(repo, "OPENCOIN", NOW - 3_600_000, "active", new_signal(_plan(), NOW - 3_600_000, c))
    _add(repo, "WAITING", NOW - 600_000, "pending", new_signal(_plan(order="limit"), NOW - 600_000, c))
    repo.upsert_candles("gate", "OPENCOIN/USDT:USDT", "5m", [[NOW - M5, 104, 106, 103, 105.0, 1]])
    repo.close()
    sh = Repository(c["shadow"]["db_path"])
    _add(sh, "SHADOW", NOW - 7_200_000, "active", new_signal(_plan(side=-1, entry=50, sl=55), NOW - 7_200_000, c),
         grade="Watch", side="short")
    sh.close()
    return c


def test_live_json_has_the_same_numbers_as_the_full_build(cfg, tmp_path):
    c = _books(cfg, tmp_path)
    full = {t["id"]: t for t in payload(c, collect(c, NOW))["trades"]}
    live = live_positions(c, NOW)
    assert {r["id"] for r in live["trades"]} == {i for i, t in full.items() if t["status"] in ("active", "pending")}
    for r in live["trades"]:
        for k, v in r.items():
            assert full[r["id"]][k] == v, (r["id"], k)


class FakeClient:
    names = ["gate"]

    def __init__(self, rows):
        self.rows = rows

    def fetch_history(self, symbol, tf, since, until=None):
        return "gate", [r for r in self.rows.get((symbol.split("/")[0], tf), []) if r[0] >= since]


class Sent:
    def __init__(self):
        self.msgs = []

    def send(self, text):
        self.msgs.append(text)
        return True


def test_one_pass_books_tp1_and_writes_live_json_for_the_panel(cfg, tmp_path, monkeypatch):
    c = _books(cfg, tmp_path)
    (tmp_path / "www").mkdir()
    c["realtime"] = {**c["realtime"], "positions": {"live_json": str(tmp_path / "www" / "live.json")}}
    c["system3"] = {"enabled": False}
    c["system2"]["variant"] = cfg["system2"]["variant"]
    slot = (NOW + 60_000) // M5 * M5
    client = FakeClient({("OPENCOIN", "5m"): [[slot - M5, 105, 111, 104, 109, 1]]})   # reaches TP1 (110)
    monkeypatch.setattr("agent.store.storage.make_storage", lambda cfg: type("S", (), {"pull": lambda self: True})())
    out = Sent()
    clock = lambda: (slot + 15_000) / 1000                                       # 15 s after that candle closed
    assert P.serve(c, None, once=True, clock=clock, client=client, cg=object(), notifier=out) == 0
    repo = Repository(c["storage"]["db_path"])
    sig = [s for s in repo.get_signals() if s["symbol"] == "OPENCOIN"][0]
    assert sig["status"] == "tp1" and len(out.msgs) == 1
    assert repo.get_state(P.HB) == slot + 15_000
    assert repo.get_state("last_runs")["run-15m"] == slot + 15_000
    repo.close()
    live = json.loads((tmp_path / "www" / "live.json").read_text(encoding="utf-8"))
    row = [r for r in live["trades"] if r["id"].endswith("OPENCOIN-" + str(NOW - 3_600_000))][0]
    assert row["status"] == "tp1" and [e[0] for e in row["events"]][-1] == "tp1" and row["open"]
