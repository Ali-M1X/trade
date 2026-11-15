"""Seed a backtest database from the live one (2026-11-15, panel backfill): the dominance
history (recon + global snapshots, read back with merged_history like live) and the HTTP
cache (CoinGecko coin categories, cached for a week). Lets `backtest --fetch-only
--no-cg-charts` run without CoinGecko's daily charts.
Usage: python scripts/seed_backtest_db.py LIVE.db BACKTEST.db
"""
from __future__ import annotations

import sys

from agent.store.repository import Repository


def main(live: str, out: str) -> None:
    repo = Repository(out)                       # creates the schema
    with repo.conn:
        repo.conn.execute("ATTACH DATABASE ? AS live", (live,))
        repo.conn.execute("INSERT OR REPLACE INTO dominance SELECT * FROM live.dominance")
        repo.conn.execute("INSERT OR REPLACE INTO http_cache SELECT * FROM live.http_cache")
    for table in ("dominance", "http_cache"):
        n = repo.conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        print(f"{out}: {table} {n} rows")
    repo.conn.execute("DETACH DATABASE live")
    repo.close()


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
