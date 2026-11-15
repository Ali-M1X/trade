import copy
import sqlite3

from agent import cli
from agent.store.repository import Repository
from agent.store.storage import _snapshot

NOW = 1_794_700_800_000


def test_mark_run_keeps_what_the_other_process_wrote(tmp_path):
    path = tmp_path / "s.db"
    a, b = Repository(path), Repository(path)
    a.set_state("last_runs", {"run-1h": 1})
    stale = a.get_state("last_runs")
    cli.mark_run(b, "run-4h", 5)                     # the funnel process
    cli.mark_run(a, "run-15m", 7)                    # the minute run, which read before
    assert stale == {"run-1h": 1}
    assert a.get_state("last_runs") == {"run-1h": 1, "run-4h": 5, "run-15m": 7}


def test_minute_run_leaves_the_funnel_to_a_live_worker(cfg, tmp_path):
    repo = Repository(tmp_path / "s.db")
    c = copy.deepcopy(cfg)
    c["realtime"] = {"funnel_every_min": 15, "funnel_worker_stale_min": 45}
    assert not cli.funnel_worker_alive(c, repo, NOW)                  # never ran: the minute run keeps it
    repo.set_state(cli.FUNNEL_HB, NOW - 10 * 60_000)
    assert cli.funnel_worker_alive(c, repo, NOW)
    assert not cli.funnel_worker_alive(c, repo, NOW + 60 * 60_000)    # worker stopped: back to the minute run
    assert not cli.funnel_worker_alive({"realtime": {}}, repo, NOW)


def test_backup_snapshot_is_a_consistent_database(tmp_path):
    path = tmp_path / "s.db"
    repo = Repository(path)
    repo.set_state("x", {"a": 1})
    raw = _snapshot(path)
    out = tmp_path / "copy.db"
    out.write_bytes(raw)
    conn = sqlite3.connect(out)
    assert conn.execute("SELECT value FROM state WHERE key='x'").fetchone()[0] == '{"a": 1}'
