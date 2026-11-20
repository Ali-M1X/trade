from agent.scheduler import DAY, HOUR, WEEK, boundary, due_tasks

MON = 1_759_708_800_000          # 2025-10-06 00:00 UTC, a Monday


def test_boundaries():
    assert boundary(MON + 5 * HOUR + 7, 4 * HOUR) == MON + 4 * HOUR
    assert boundary(MON + 3 * DAY, WEEK, 4 * DAY) == MON           # weeks start Monday


def test_first_run_does_everything():
    assert due_tasks(MON + 60_000, {}) == ["run-15m", "run-4h", "run-1h", "run-daily", "run-weekly"]


def test_only_what_is_due():
    last = {"run-4h": MON + 4 * HOUR, "run-1h": MON + 5 * HOUR, "run-daily": MON, "run-weekly": MON}
    assert due_tasks(MON + 5 * HOUR + 20 * 60_000, last) == ["run-15m"]
    assert due_tasks(MON + 6 * HOUR + 60_000, last) == ["run-15m", "run-1h"]
    assert due_tasks(MON + 8 * HOUR + 60_000, last) == ["run-15m", "run-4h", "run-1h"]


def test_late_cron_catches_up():
    # the 08:00 run was skipped; a run at 09:40 still does the 4H and 1H work
    last = {"run-4h": MON + 4 * HOUR, "run-1h": MON + 7 * HOUR, "run-daily": MON, "run-weekly": MON}
    assert due_tasks(MON + 9 * HOUR + 40 * 60_000, last) == ["run-15m", "run-4h", "run-1h"]


def test_daily_and_weekly():
    last = {"run-4h": MON + 20 * HOUR, "run-1h": MON + 23 * HOUR, "run-daily": MON, "run-weekly": MON}
    assert "run-daily" in due_tasks(MON + DAY + 60_000, last)
    assert "run-weekly" not in due_tasks(MON + DAY + 60_000, last)
    assert "run-weekly" in due_tasks(MON + WEEK + 60_000, {**last, "run-daily": MON + 6 * DAY})


def test_run_scheduled_runs_due_tasks_and_records_them(cfg, monkeypatch):
    from agent import cli, runs
    from agent.store.repository import Repository

    class Storage:
        pushed = []

        def push(self, msg):
            self.pushed.append(msg)

    repo = Repository()
    repo.close = lambda: None
    storage = Storage()
    now = MON + 4 * HOUR + 60_000
    monkeypatch.setattr(cli, "open_context", lambda c, s: (storage, repo, None, None, now))
    calls = []
    monkeypatch.setattr(runs, "manage", lambda *a: calls.append("15m") or [])
    monkeypatch.setattr(runs, "run_4h", lambda *a: (_ for _ in ()).throw(RuntimeError("boom")))
    monkeypatch.setattr(runs, "run_1h", lambda *a: calls.append("1h") or [])
    monkeypatch.setattr(runs, "daily", lambda *a: calls.append("daily"))
    monkeypatch.setattr(runs, "hold_new_entrants", lambda *a: [])
    monkeypatch.setattr(runs, "weekly", lambda *a: calls.append("weekly") or [])
    monkeypatch.setattr(cli, "news_pass", lambda *a: calls.append("news") or {})
    from agent import system2
    from agent.signals import shadow
    monkeypatch.setattr(shadow, "enabled", lambda c: False)     # tested in test_shadow.py
    monkeypatch.setattr(system2, "enabled", lambda c: False)    # its own manage() would count here
    repo.set_state("last_runs", {"run-daily": MON, "run-weekly": MON, "run-1h": MON + 3 * HOUR,
                                 "run-4h": MON})
    code = cli.main(["run-scheduled"])
    assert code == 1                                   # run-4h failed ...
    assert calls == ["15m", "1h", "news"]              # ... but the others still ran
    last = repo.get_state("last_runs")
    assert last["run-1h"] == now and last["run-4h"] == MON   # a failed task stays due
    assert storage.pushed == ["run-scheduled run-15m,run-4h,run-1h"]


def test_realtime_periods_override():
    last = {"run-4h": MON, "run-1h": MON, "run-daily": MON, "run-weekly": MON}
    periods = {"run-4h": 60, "run-1h": 5}
    assert due_tasks(MON + 4 * 60_000, last, periods) == ["run-15m"]
    assert due_tasks(MON + 6 * 60_000, last, periods) == ["run-15m", "run-1h"]
    assert due_tasks(MON + HOUR + 60_000, last, periods) == ["run-15m", "run-4h", "run-1h"]


def test_run_scheduled_leaves_positions_and_news_to_their_own_processes(cfg, monkeypatch):
    from agent import cli, positions, runs, system2, system3
    from agent.signals import shadow
    from agent.store.repository import Repository

    class Storage:
        pushed = []

        def push(self, msg, extra=None):
            self.pushed.append(msg)

    repo = Repository()
    repo.close = lambda: None
    now = MON + 4 * HOUR + 60_000
    market = type("Market", (), {"client": None})()
    monkeypatch.setattr(cli, "open_context", lambda c, s: (Storage(), repo, market, None, now))
    calls = []
    monkeypatch.setattr(runs, "manage", lambda *a: calls.append("15m") or [])
    monkeypatch.setattr(runs, "run_1h", lambda *a: calls.append("1h") or [])
    monkeypatch.setattr(cli, "news_pass", lambda *a: calls.append("news") or {})
    monkeypatch.setattr(system3, "run", lambda *a: calls.append("s3"))
    monkeypatch.setattr(system3, "enabled", lambda c: True)
    monkeypatch.setattr(shadow, "enabled", lambda c: False)
    monkeypatch.setattr(system2, "enabled", lambda c: False)
    monkeypatch.setattr(system3, "settings", lambda c: {"db_path": ":memory:"})
    last = {"run-daily": now, "run-weekly": now, "run-1h": MON, "run-4h": now}
    repo.set_state("last_runs", last)
    repo.set_state(positions.HB, now - 20_000)
    repo.set_state(cli.NEWS_HB, now - 60_000)
    assert cli.main(["run-scheduled"]) == 0
    assert calls == ["1h"]                             # positions, System 3 and news ran elsewhere
    repo.set_state(positions.HB, now - 10 * 60_000)    # the fast lane stopped: everything runs here again
    repo.set_state(cli.NEWS_HB, now - 10 * 60_000)
    repo.set_state("last_runs", last)
    calls.clear()
    assert cli.main(["run-scheduled"]) == 0
    assert calls == ["s3", "15m", "1h", "news"]
