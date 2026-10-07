"""SQLite repository: candle cache, dominance history, HTTP cache, state, signals, events."""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

import pandas as pd

SCHEMA = """
CREATE TABLE IF NOT EXISTS candles (
    exchange TEXT NOT NULL, symbol TEXT NOT NULL, timeframe TEXT NOT NULL, ts INTEGER NOT NULL,
    open REAL, high REAL, low REAL, close REAL, volume REAL,
    PRIMARY KEY (exchange, symbol, timeframe, ts)
);
CREATE TABLE IF NOT EXISTS dominance (
    ts INTEGER NOT NULL, source TEXT NOT NULL,
    total_mcap REAL, btc_mcap REAL, eth_mcap REAL, usdt_mcap REAL,
    PRIMARY KEY (ts, source)
);
CREATE TABLE IF NOT EXISTS http_cache (
    key TEXT PRIMARY KEY, fetched_at INTEGER NOT NULL, body TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS state (
    key TEXT PRIMARY KEY, updated_at INTEGER NOT NULL, value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS signals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at INTEGER NOT NULL, symbol TEXT NOT NULL, side TEXT NOT NULL,
    grade TEXT NOT NULL, score REAL NOT NULL, status TEXT NOT NULL,
    payload TEXT NOT NULL, updated_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts INTEGER NOT NULL, signal_id INTEGER, kind TEXT NOT NULL, payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_signals_status ON signals(status);
CREATE TABLE IF NOT EXISTS funding (
    exchange TEXT NOT NULL, symbol TEXT NOT NULL, ts INTEGER NOT NULL, rate REAL,
    PRIMARY KEY (exchange, symbol, ts)
);
CREATE TABLE IF NOT EXISTS coin_history (
    id TEXT NOT NULL, ts INTEGER NOT NULL, mcap REAL, volume REAL,
    PRIMARY KEY (id, ts)
);
CREATE TABLE IF NOT EXISTS news (
    id TEXT PRIMARY KEY, source TEXT NOT NULL, kind TEXT NOT NULL, title TEXT NOT NULL,
    url TEXT, published_at INTEGER, seen_at INTEGER NOT NULL, level INTEGER NOT NULL,
    rule TEXT, coins TEXT NOT NULL, alerted INTEGER NOT NULL, prices TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_news_seen ON news(seen_at);
"""

CANDLE_COLUMNS = ["ts", "open", "high", "low", "close", "volume"]


class Repository:
    def __init__(self, path: str | Path = ":memory:"):
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(path))
        self.conn.executescript(SCHEMA)

    def close(self) -> None:
        self.conn.close()

    # ---- candles
    def upsert_candles(self, exchange: str, symbol: str, timeframe: str, rows) -> int:
        """rows: iterable of [ts_ms, open, high, low, close, volume]."""
        data = [(exchange, symbol, timeframe, int(r[0]), *map(float, r[1:6])) for r in rows]
        with self.conn:
            self.conn.executemany(
                "INSERT OR REPLACE INTO candles VALUES (?,?,?,?,?,?,?,?,?)", data)
        return len(data)

    def get_candles(self, exchange: str, symbol: str, timeframe: str,
                    since: int | None = None, until: int | None = None) -> pd.DataFrame:
        q = ("SELECT ts, open, high, low, close, volume FROM candles "
             "WHERE exchange=? AND symbol=? AND timeframe=?")
        args: list = [exchange, symbol, timeframe]
        if since is not None:
            q += " AND ts>=?"
            args.append(since)
        if until is not None:
            q += " AND ts<=?"
            args.append(until)
        rows = self.conn.execute(q + " ORDER BY ts", args).fetchall()
        return pd.DataFrame(rows, columns=CANDLE_COLUMNS)

    def prune_candles(self, exchange: str, symbol: str, timeframe: str, keep: int) -> None:
        """Keep only the newest `keep` candles of a series (the runtime DB stays small)."""
        with self.conn:
            self.conn.execute(
                "DELETE FROM candles WHERE exchange=? AND symbol=? AND timeframe=? AND ts < ("
                " SELECT ts FROM candles WHERE exchange=? AND symbol=? AND timeframe=?"
                " ORDER BY ts DESC LIMIT 1 OFFSET ?)",
                (exchange, symbol, timeframe, exchange, symbol, timeframe, keep - 1))

    def last_candle_ts(self, exchange: str, symbol: str, timeframe: str) -> int | None:
        row = self.conn.execute(
            "SELECT MAX(ts) FROM candles WHERE exchange=? AND symbol=? AND timeframe=?",
            (exchange, symbol, timeframe)).fetchone()
        return row[0]

    def candle_series(self, timeframe: str) -> list[tuple[str, str]]:
        """(exchange, symbol) pairs that have candles of this timeframe."""
        return self.conn.execute("SELECT DISTINCT exchange, symbol FROM candles WHERE timeframe=?",
                                 (timeframe,)).fetchall()

    # ---- funding and coin history (backtest data)
    def upsert_funding(self, exchange: str, symbol: str, rows) -> None:
        """rows: iterable of (ts_ms, rate)."""
        with self.conn:
            self.conn.executemany("INSERT OR REPLACE INTO funding VALUES (?,?,?,?)",
                                  [(exchange, symbol, int(t), float(r)) for t, r in rows])

    def get_funding(self, symbol: str) -> pd.DataFrame:
        rows = self.conn.execute("SELECT ts, rate FROM funding WHERE symbol=? ORDER BY ts",
                                 (symbol,)).fetchall()
        return pd.DataFrame(rows, columns=["ts", "rate"])

    def upsert_coin_history(self, coin_id: str, rows) -> None:
        """rows: iterable of (ts_ms, market_cap, volume_usd)."""
        with self.conn:
            self.conn.executemany("INSERT OR REPLACE INTO coin_history VALUES (?,?,?,?)",
                                  [(coin_id, int(t), m, v) for t, m, v in rows])

    def get_coin_history(self, coin_id: str) -> pd.DataFrame:
        rows = self.conn.execute("SELECT ts, mcap, volume FROM coin_history WHERE id=? ORDER BY ts",
                                 (coin_id,)).fetchall()
        return pd.DataFrame(rows, columns=["ts", "mcap", "volume"])

    # ---- dominance
    def add_dominance(self, ts: int, source: str, total_mcap: float, btc_mcap: float,
                      eth_mcap: float, usdt_mcap: float) -> None:
        with self.conn:
            self.conn.execute("INSERT OR REPLACE INTO dominance VALUES (?,?,?,?,?,?)",
                              (ts, source, total_mcap, btc_mcap, eth_mcap, usdt_mcap))

    def get_dominance(self, source: str | None = None) -> pd.DataFrame:
        q = "SELECT * FROM dominance"
        args: tuple = ()
        if source:
            q += " WHERE source=?"
            args = (source,)
        rows = self.conn.execute(q + " ORDER BY ts", args).fetchall()
        return pd.DataFrame(rows, columns=["ts", "source", "total_mcap", "btc_mcap",
                                           "eth_mcap", "usdt_mcap"])

    # ---- http cache
    def cache_get(self, key: str, max_age_s: float, now: float | None = None):
        row = self.conn.execute("SELECT fetched_at, body FROM http_cache WHERE key=?",
                                (key,)).fetchone()
        now = time.time() if now is None else now
        if row and now - row[0] <= max_age_s:
            return json.loads(row[1])
        return None

    def cache_put(self, key: str, value, now: float | None = None) -> None:
        now = int(time.time() if now is None else now)
        with self.conn:
            self.conn.execute("INSERT OR REPLACE INTO http_cache VALUES (?,?,?)",
                              (key, now, json.dumps(value)))

    # ---- key/value state (regime, shortlist, loss streak, ...)
    def set_state(self, key: str, value) -> None:
        with self.conn:
            self.conn.execute("INSERT OR REPLACE INTO state VALUES (?,?,?)",
                              (key, int(time.time()), json.dumps(value)))

    def get_state(self, key: str, default=None):
        row = self.conn.execute("SELECT value FROM state WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    # ---- signals and events
    def add_signal(self, created_at: int, symbol: str, side: str, grade: str, score: float,
                   status: str, payload: dict) -> int:
        with self.conn:
            cur = self.conn.execute(
                "INSERT INTO signals (created_at, symbol, side, grade, score, status, payload,"
                " updated_at) VALUES (?,?,?,?,?,?,?,?)",
                (created_at, symbol, side, grade, score, status, json.dumps(payload), created_at))
        return cur.lastrowid

    def update_signal(self, signal_id: int, status: str, payload: dict, ts: int) -> None:
        with self.conn:
            self.conn.execute("UPDATE signals SET status=?, payload=?, updated_at=? WHERE id=?",
                              (status, json.dumps(payload), ts, signal_id))

    def get_signals(self, statuses: list[str] | None = None) -> list[dict]:
        q = "SELECT id, created_at, symbol, side, grade, score, status, payload, updated_at FROM signals"
        args: list = []
        if statuses:
            q += f" WHERE status IN ({','.join('?' * len(statuses))})"
            args = list(statuses)
        out = []
        for r in self.conn.execute(q + " ORDER BY id", args).fetchall():
            out.append({"id": r[0], "created_at": r[1], "symbol": r[2], "side": r[3],
                        "grade": r[4], "score": r[5], "status": r[6],
                        "payload": json.loads(r[7]), "updated_at": r[8]})
        return out

    def add_event(self, ts: int, kind: str, payload: dict, signal_id: int | None = None) -> None:
        with self.conn:
            self.conn.execute("INSERT INTO events (ts, signal_id, kind, payload) VALUES (?,?,?,?)",
                              (ts, signal_id, kind, json.dumps(payload)))

    def get_events(self, signal_id: int | None = None) -> list[dict]:
        q = "SELECT ts, signal_id, kind, payload FROM events"
        args: tuple = ()
        if signal_id is not None:
            q += " WHERE signal_id=?"
            args = (signal_id,)
        return [{"ts": r[0], "signal_id": r[1], "kind": r[2], "payload": json.loads(r[3])}
                for r in self.conn.execute(q + " ORDER BY id", args).fetchall()]

    # ---- news (news/run.py): one row per headline, prices filled in over the next 24h
    def news_known(self, ids: list[str]) -> set[str]:
        out: set[str] = set()
        for i in range(0, len(ids), 500):
            chunk = ids[i:i + 500]
            q = f"SELECT id FROM news WHERE id IN ({','.join('?' * len(chunk))})"
            out |= {r[0] for r in self.conn.execute(q, chunk).fetchall()}
        return out

    def add_news(self, item: dict) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT OR IGNORE INTO news VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (item["id"], item["source"], item["kind"], item["title"], item.get("url"),
                 item.get("ts"), item["seen_at"], item["level"], item.get("rule"),
                 json.dumps(item.get("coins", [])), int(item.get("alerted", 0)),
                 json.dumps(item.get("prices", {}))))

    def get_news(self, since: int = 0, with_coins: bool = False) -> list[dict]:
        rows = self.conn.execute(
            "SELECT id, source, kind, title, url, published_at, seen_at, level, rule, coins,"
            " alerted, prices FROM news WHERE seen_at>=? ORDER BY seen_at", (since,)).fetchall()
        out = [{"id": r[0], "source": r[1], "kind": r[2], "title": r[3], "url": r[4], "ts": r[5],
                "seen_at": r[6], "level": r[7], "rule": r[8], "coins": json.loads(r[9]),
                "alerted": bool(r[10]), "prices": json.loads(r[11])} for r in rows]
        return [n for n in out if n["coins"]] if with_coins else out

    def set_news_prices(self, news_id: str, prices: dict) -> None:
        with self.conn:
            self.conn.execute("UPDATE news SET prices=? WHERE id=?", (json.dumps(prices), news_id))
