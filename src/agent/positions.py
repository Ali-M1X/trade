"""The fast lane (2026-11-20, Ali: "as close to real time as we can get").

Open positions of systems 1 and 2 and their Watch books, and System 3, run in their own long-running
process (`trade-agent run-positions`, systemd trade-positions.service) instead of waiting behind the
minute run's scoring, news and the rest (median 85 s a run, 2.4 min between runs on 2026-11-20).

The rules do not change: a level is booked on a CLOSED 5m candle, so systems 1 and 2 are checked a few
seconds after every 5m close (`lag_s`, so the exchange has the candle final); System 3 runs once a
minute as before. After each pass the lane writes a small live.json that the panel reads every few
seconds, so a TP or a stop shows without waiting for the full panel build.

While this process is alive (its heartbeat in state `positions_worker`) the minute run leaves run-15m
and System 3 to it; when it stops, the minute run does them again as before.
"""
from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path

log = logging.getLogger(__name__)

HB = "positions_worker"
M5 = 300_000
MIN = 60_000
DEFAULTS = {"enabled": True, "lag_s": 10, "tick_s": 3, "stale_s": 120, "max_minutes": 10,
            "live_json": None, "recent_h": 6}


def settings(cfg: dict) -> dict:
    return {**DEFAULTS, **((cfg.get("realtime") or {}).get("positions") or {})}


def configured(cfg: dict) -> bool:
    return bool((cfg.get("realtime") or {}).get("positions")) and settings(cfg)["enabled"]


def alive(cfg: dict, repo, now: int) -> bool:
    """True while the fast lane is running, so the minute run leaves positions and System 3 to it."""
    if not configured(cfg):
        return False
    hb = repo.get_state(HB) or 0
    return now - hb < settings(cfg)["stale_s"] * 1000


def candle_due(now: int, done: int, lag_s: float) -> int | None:
    """The open time of the 5m slot whose candles are now final (`lag_s` after its close), when that
    slot has not been handled yet."""
    slot = (now - int(lag_s * 1000)) // M5 * M5
    return slot if slot > done else None


def manage_books(cfg: dict, client, notifier, now: int) -> int:
    """run-15m of every book: system 1, its Watch book, system 2, its Watch book (as the minute run
    did, in the same order and with the same messages). One market, so each coin is fetched once."""
    from . import system2
    from .data.market import LiveMarket
    from .runs import manage
    from .signals import shadow
    from .store.repository import Repository
    n = 0
    repo = Repository(cfg["storage"]["db_path"])
    try:
        market = LiveMarket(cfg, repo, client, now)
        books = [("system1", cfg, cfg["storage"]["db_path"], notifier)]
        if shadow.enabled(cfg):
            books.append(("shadow", cfg, cfg["shadow"]["db_path"], shadow.Silent()))
        cfg2 = None
        if system2.enabled(cfg):
            try:
                cfg2 = system2.system2_config(cfg)
            except (Exception, SystemExit):         # a broken system 2 config must not stop system 1
                log.exception("system2 config")
        if cfg2 is not None:
            books.append(("system2", cfg2, cfg["system2"]["db_path"], system2.Labelled(notifier)))
            if shadow.enabled(cfg):
                books.append(("system2 shadow", cfg2, cfg["shadow"]["s2_db_path"], shadow.Silent()))
        for name, c, path, out in books:
            r = repo if path == cfg["storage"]["db_path"] else Repository(path)
            try:
                evs = manage(c, r, market, out, now)
                n += len(evs)
                if evs:
                    print(f"{name}: {len(evs)} events")
            except Exception:                       # one book failing must not block the others
                log.exception("%s positions failed", name)
            finally:
                if r is not repo:
                    r.close()
        from .cli import mark_run
        mark_run(repo, "run-15m", now)
    finally:
        repo.close()
    return n


def write_live(cfg: dict, now: int) -> str | None:
    """live.json for the panel: the open and recently changed trades as the database has them now."""
    path = settings(cfg)["live_json"]
    if not path or not Path(path).parent.is_dir():
        return None
    from .panel import live_positions
    data = live_positions(cfg, now, settings(cfg)["recent_h"] * 3_600_000)
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, path)
    return path


def system3_pass(cfg: dict, client, cg, notifier, now: int) -> None:
    from . import system3
    from .store.repository import Repository
    if not system3.enabled(cfg):
        return
    repo3 = Repository(system3.settings(cfg)["db_path"])
    try:
        system3.run(cfg, repo3, client, cg, notifier, now)
    except Exception:
        log.exception("system3 failed")
    finally:
        repo3.close()


def heartbeat(cfg: dict, now: int) -> None:
    from .store.repository import Repository
    repo = Repository(cfg["storage"]["db_path"])
    try:
        repo.set_state(HB, now)
    finally:
        repo.close()


def serve(cfg: dict, secrets, max_minutes: float | None = None, once: bool = False, clock=time.time,
          sleep=time.sleep, client=None, cg=None, notifier=None) -> int:
    """Run passes until `max_minutes` have gone by (systemd then restarts the service, which pulls new
    code first). Systems 1 and 2: one pass per closed 5m candle. System 3: one pass per minute."""
    from .data.coingecko import CoinGecko
    from .data.exchange import ExchangeClient
    from .notify.telegram import Notifier
    from .store.storage import make_storage
    s = settings(cfg)
    make_storage(cfg).pull()
    client = client or ExchangeClient(cfg)
    notifier = notifier or Notifier(cfg, secrets)
    if cg is None:
        from .store.repository import Repository
        cg = CoinGecko(cfg, repo=Repository(cfg["storage"]["db_path"]), api_key=secrets.coingecko_api_key)
    t0 = clock()
    done_5m = done_min = beat = 0
    limit = (max_minutes if max_minutes is not None else s["max_minutes"]) * 60
    while True:
        now = int(clock() * 1000)
        if now - beat >= 15_000:                    # a small write, not on every tick
            heartbeat(cfg, now)
            beat = now
        changed = False
        minute = now // MIN * MIN
        if minute > done_min:
            done_min = minute
            system3_pass(cfg, client, cg, notifier, now)
        slot = candle_due(now, done_5m, s["lag_s"])
        if slot is not None:
            done_5m = slot
            start = clock()
            n = manage_books(cfg, client, notifier, now)
            print(f"positions: {n} events in {clock() - start:.1f}s")
            changed = True
        if changed or once:
            try:
                write_live(cfg, int(clock() * 1000))
            except Exception:                       # the panel's copy only
                log.exception("live.json failed")
        if once or clock() - t0 >= limit:
            return 0
        sleep(s["tick_s"])
