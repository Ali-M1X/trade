"""Merge backtest databases fetched in shards (backtest --since --shard I/N).

Usage: python scripts/merge_db.py OUT.db SHARD0.db SHARD1.db ...
Candles, funding and coin history are unioned; the state rows (universe, categories,
meta) come from the first database, which must be shard 0."""
import shutil
import sqlite3
import sys


def merge(out: str, parts: list[str]) -> None:
    shutil.copyfile(parts[0], out)
    con = sqlite3.connect(out)
    for p in parts[1:]:
        con.execute("ATTACH DATABASE ? AS part", (p,))
        for table in ("candles", "funding", "coin_history"):
            con.execute(f"INSERT OR IGNORE INTO {table} SELECT * FROM part.{table}")
        con.commit()
        con.execute("DETACH DATABASE part")
    for table in ("candles", "funding", "coin_history"):
        n = con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        print(f"{table}: {n} rows")
    n = con.execute("SELECT COUNT(DISTINCT symbol) FROM candles WHERE timeframe = '1d'").fetchone()[0]
    print(f"coins with daily candles: {n}")
    con.close()


if __name__ == "__main__":
    merge(sys.argv[1], sys.argv[2:])
