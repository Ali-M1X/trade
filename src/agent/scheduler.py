"""Which scheduled tasks are due. Used by `run-scheduled`, which a single frequent cron
(GitHub Actions or a VPS) calls. A task is due when a new period boundary has passed since
its last completed run, so late or skipped cron runs catch up on the next one."""
from __future__ import annotations

HOUR = 3_600_000
DAY = 24 * HOUR
WEEK = 7 * DAY
MONDAY_OFFSET = 4 * DAY          # 1970-01-01 was a Thursday; weeks start Monday 00:00 UTC

# (task, period in ms, offset) in the order they run: manage first, then the funnel
# (so the hourly L6 sees a fresh shortlist), then the reports.
TASKS = [
    ("run-15m", None, 0),
    ("run-4h", 4 * HOUR, 0),
    ("run-1h", HOUR, 0),
    ("run-daily", DAY, 0),
    ("run-weekly", WEEK, MONDAY_OFFSET),
]


def boundary(now_ms: int, period: int, offset: int = 0) -> int:
    """The latest period start at or before now."""
    return (now_ms - offset) // period * period + offset


def due_tasks(now_ms: int, last_runs: dict) -> list[str]:
    out = []
    for name, period, offset in TASKS:
        if period is None or last_runs.get(name, 0) < boundary(now_ms, period, offset):
            out.append(name)
    return out
