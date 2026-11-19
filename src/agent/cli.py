"""Command-line entry points. The same commands run from GitHub Actions or a VPS cron."""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path

from .config import live_config, load_config, load_secrets
from .data.sources_check import as_dicts, check_sources, format_report, working_exchanges


def cmd_check_sources(args, cfg, secrets) -> int:
    probes = check_sources(cfg, secrets)
    print(format_report(probes))
    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(as_dicts(probes), f, indent=2)
    cg_ok = any(p.source == "coingecko" and p.check == "top universe" and p.ok for p in probes)
    return 0 if working_exchanges(probes) and cg_ok else 1


def open_context(cfg, secrets):
    import time

    from .data.coingecko import CoinGecko
    from .data.exchange import ExchangeClient
    from .data.market import LiveMarket
    from .store.repository import Repository
    from .store.storage import make_storage

    storage = make_storage(cfg)
    storage.pull()
    repo = Repository(cfg["storage"]["db_path"])
    now = int(time.time() * 1000)
    market = LiveMarket(cfg, repo, ExchangeClient(cfg), now)
    cg = CoinGecko(cfg, repo=repo, api_key=secrets.coingecko_api_key)
    return storage, repo, market, cg, now


def cmd_run_4h(args, cfg, secrets) -> int:
    from .runs import run_4h, summarize_funnel
    storage, repo, market, cg, now = open_context(cfg, secrets)
    result = run_4h(cfg, repo, market, cg, now)
    print(summarize_funnel(result))
    repo.close()
    storage.push("run-4h")
    return 0


def cmd_run_1h(args, cfg, secrets) -> int:
    from .notify.telegram import Notifier
    from .runs import run_1h, summarize_evaluations
    storage, repo, market, _, now = open_context(cfg, secrets)
    print(summarize_evaluations(run_1h(cfg, repo, market, now, Notifier(cfg, secrets))))
    repo.close()
    storage.push("run-1h")
    return 0


def cmd_run_15m(args, cfg, secrets) -> int:
    from .notify.telegram import Notifier
    from .runs import manage
    storage, repo, market, _, now = open_context(cfg, secrets)
    events = manage(cfg, repo, market, Notifier(cfg, secrets), now)
    print(f"{len(events)} signal events")
    repo.close()
    storage.push("run-15m")
    return 0


def cmd_run_daily(args, cfg, secrets) -> int:
    from .notify.telegram import Notifier
    from .runs import daily, hold_new_entrants
    storage, repo, market, cg, now = open_context(cfg, secrets)
    notifier = Notifier(cfg, secrets)
    if daily(cfg, repo, notifier, now) is None:
        print("no funnel state yet: run-4h first")
    hold_new_entrants(cfg, repo, market, cg, notifier, now)
    repo.close()
    storage.push("run-daily")
    return 0


def cmd_run_weekly(args, cfg, secrets) -> int:
    from .notify.telegram import Notifier
    from .runs import weekly
    storage, repo, market, cg, now = open_context(cfg, secrets)
    ideas = weekly(cfg, repo, market, cg, Notifier(cfg, secrets), now)
    print(f"{len(ideas)} HOLD ideas")
    repo.close()
    storage.push("run-weekly")
    return 0


def cmd_run_scheduled(args, cfg, secrets) -> int:
    """Run whatever is due (see scheduler.py) with one storage pull and push."""
    import logging

    from .notify.telegram import Notifier
    from .runs import (daily, hold_new_entrants, manage, run_1h, run_4h, summarize_evaluations,
                       summarize_funnel, weekly)
    from .scheduler import due_tasks
    storage, repo, market, cg, now = open_context(cfg, secrets)
    notifier = Notifier(cfg, secrets)
    last = repo.get_state("last_runs", {})
    tasks = args.only.split(",") if args.only else due_tasks(now, last, (cfg.get("realtime") or {}).get("periods_min"))
    if not args.only and "run-4h" in tasks and funnel_worker_alive(cfg, repo, now):
        tasks.remove("run-4h")                  # the funnel runs in its own process (run-funnel)
    print(f"due: {', '.join(tasks)}")
    failed = []
    # system 3 first (2026-11-19, Ali: its signals as early as possible): a few seconds, its own database,
    # and it never blocks systems 1 and 2
    from . import system3
    from .store.repository import Repository
    if system3.enabled(cfg):
        repo3 = Repository(system3.settings(cfg)["db_path"])
        try:
            system3.run(cfg, repo3, market.client, cg, notifier, now)
        except Exception:
            logging.getLogger(__name__).exception("system3 failed")
        repo3.close()
    evs = None
    for task in tasks:
        try:
            if task == "run-15m":
                print(f"run-15m: {len(manage(cfg, repo, market, notifier, now))} signal events")
            elif task == "run-4h":
                print(summarize_funnel(run_4h(cfg, repo, market, cg, now)))
            elif task == "run-1h":
                evs = run_1h(cfg, repo, market, now, notifier)
                print(summarize_evaluations(evs))
            elif task == "run-daily":
                daily(cfg, repo, notifier, now)
                hold_new_entrants(cfg, repo, market, cg, notifier, now)
            elif task == "run-weekly":
                print(f"run-weekly: {len(weekly(cfg, repo, market, cg, notifier, now))} HOLD ideas")
            mark_run(repo, task, now)
        except Exception:                       # one failing task must not block the others
            logging.getLogger(__name__).exception("%s failed", task)
            failed.append(task)
    from . import system2
    from .signals import shadow
    from .store.repository import Repository
    if shadow.enabled(cfg):
        sh = Repository(cfg["shadow"]["db_path"])
        try:                                    # status panel only: never blocks the live system
            shadow.run_pass(cfg, repo, sh, market, now, tasks, evs, "funnel")
        except Exception:
            logging.getLogger(__name__).exception("shadow failed")
        sh.close()
    if system2.enabled(cfg):
        repo2 = Repository(cfg["system2"]["db_path"])
        sh2 = Repository(cfg["shadow"]["s2_db_path"]) if shadow.enabled(cfg) else None
        try:
            system2.run_system2(cfg, repo, repo2, market, notifier, now, tasks, shadow_repo=sh2)
        except Exception:                       # paper experiment: never blocks the live system
            logging.getLogger(__name__).exception("system2 failed")
        repo2.close()
        if sh2 is not None:
            sh2.close()
    try:                                        # display and hypothetical only (pulse.py)
        from .pulse import record
        record(cfg, repo, market, now)
    except Exception:
        logging.getLogger(__name__).exception("pulse failed")
    try:                                        # measurement period: notes when the rules change
        from .scorecard import record as rules_record
        rules_record(cfg, repo, now)
    except Exception:
        logging.getLogger(__name__).exception("rules fingerprint failed")
    extra = {}
    if cfg.get("news", {}).get("enabled"):
        try:
            extra = news_pass(cfg, repo, market, cg, notifier, now)
        except Exception:                       # experimental: never blocks or fails the signal runs
            logging.getLogger(__name__).exception("news failed")
    _log_run(repo, now, tasks, failed, getattr(market, "client", None))
    repo.close()
    if extra:
        storage.push("run-scheduled " + ",".join(tasks), extra=extra)
    else:
        storage.push("run-scheduled " + ",".join(tasks))
    return 1 if failed else 0


def mark_run(repo, task: str, when: int) -> None:
    """Record a task's run. Re-read first: the funnel process writes the same state."""
    last = repo.get_state("last_runs", {}) or {}
    last[task] = when
    repo.set_state("last_runs", last)


FUNNEL_HB = "funnel_worker"


def funnel_worker_alive(cfg: dict, repo, now: int) -> bool:
    """True while the separate funnel process (run-funnel, its own systemd timer) is running
    the funnel, so the minute run leaves it out. Without that process the minute run keeps
    running the funnel itself, as before."""
    rt = cfg.get("realtime") or {}
    hb = repo.get_state(FUNNEL_HB) or 0
    return bool(rt.get("funnel_every_min")) and now - hb < float(rt.get("funnel_worker_stale_min", 45)) * 60_000


def cmd_run_funnel(args, cfg, secrets) -> int:
    """The funnel (run-4h: universe, dominance, majors, shortlist) in its own process, so
    the minute runs never wait for it (2026-11-15: it took ~10 min and blocked them every
    hour). Never pushes: the minute run backs the database up."""
    import logging

    from .runs import run_4h, summarize_funnel
    storage, repo, market, cg, now = open_context(cfg, secrets)
    every = float((cfg.get("realtime") or {}).get("funnel_every_min") or 15)
    repo.set_state(FUNNEL_HB, now)
    last = (repo.get_state("last_runs", {}) or {}).get("run-4h", 0)
    if not args.force and now - last < every * 60_000:
        repo.close()
        return 0
    try:
        print(summarize_funnel(run_4h(cfg, repo, market, cg, now)))
        mark_run(repo, "run-4h", now)
        failed = []
    except Exception:
        logging.getLogger(__name__).exception("run-funnel failed")
        failed = ["run-4h"]
    repo.set_state(FUNNEL_HB, int(time.time() * 1000))
    _log_run(repo, now, ["funnel"], failed, getattr(market, "client", None))
    repo.close()
    return 1 if failed else 0


def _log_run(repo, started: int, tasks: list[str], failed: list[str], client=None) -> None:
    """Last runs for the panel's system-health view: when, how long, what failed, and the last
    error of each exchange (the call and the exchange's own message)."""
    log = repo.get_state("run_log", []) or []
    log.append({"ts": started, "dur": round(time.time() - started / 1000, 1), "tasks": tasks,
                "failed": failed, "ex": dict(getattr(client, "failures", None) or {}),
                "ex_err": dict(getattr(client, "last_error", None) or {})})
    repo.set_state("run_log", log[-300:])


def news_pass(cfg, repo, market, cg, notifier, now) -> dict[str, bytes]:
    """Run the news path and return the context file to publish for the worker."""
    from .news.run import run_news
    res = run_news(cfg, repo, market, cg, notifier, now)
    print(f"news: {res['new']} new, {res['alerts']} alerts"
          + (f", failed: {', '.join(res['failed'])}" if res["failed"] else ""))
    return {cfg["news"]["context_file"]: json.dumps(res["context"], ensure_ascii=False).encode()}


def cmd_run_news(args, cfg, secrets) -> int:
    from .notify.telegram import Notifier
    storage, repo, market, cg, now = open_context(cfg, secrets)
    if args.preview:                 # alert today's headlines too, exchange ones included
        cfg = {**cfg, "news": {**cfg["news"], "fast_path": "none"}}
        if repo.get_state("news_seeded") is None:
            repo.set_state("news_seeded", now)
    extra = news_pass(cfg, repo, market, cg, Notifier(cfg, secrets), now)
    repo.close()
    storage.push("run-news", extra=extra)
    return 0


def _ms(day: str) -> int:
    from datetime import datetime, timezone
    return int(datetime.fromisoformat(day).replace(tzinfo=timezone.utc).timestamp() * 1000)


def cmd_backtest(args, cfg, secrets) -> int:
    import time

    from .backtest.run import run_backtest
    from .store.repository import Repository
    if args.days:
        cfg["backtest"]["days"] = args.days
    if args.top_n:
        cfg["backtest"]["universe_top_n"] = args.top_n
    if args.max_coins is not None:
        cfg["backtest"]["max_coins"] = args.max_coins
    if args.record_watch:
        cfg["backtest"]["record_watch"] = True
    if args.no_cg_charts:
        cfg["backtest"]["cg_charts"] = False
    for key, val in (("replay_start", args.start), ("replay_end", args.end)):
        if val:
            cfg["backtest"][key] = _ms(val)
    repo = Repository(args.db or cfg["backtest"]["db_path"])
    if args.fetch or args.fetch_only:
        from .backtest.data import fetch_history
        from .data.coingecko import CoinGecko
        from .data.exchange import ExchangeClient
        from .data.market import LiveMarket
        now = int(time.time() * 1000)
        client = ExchangeClient(cfg)
        market = LiveMarket(cfg, repo, client, now)
        cg = CoinGecko(cfg, repo=repo, api_key=secrets.coingecko_api_key)
        if args.since:
            from .backtest.data import fetch_long_history
            shard = tuple(int(x) for x in args.shard.split("/")) if args.shard else (0, 1)
            stats = fetch_long_history(cfg, repo, client, market, cg, now, _ms(args.since), shard)
        else:
            stats = fetch_history(cfg, repo, client, market, cg, now)
        print(f"fetched: {stats}")
    if args.fetch_only:
        repo.close()
        return 0
    if args.variant:
        from .backtest.variants import run_variant
        res = run_variant(cfg, repo, args.variant, args.json or f"{args.variant}.json")
        print(f"{args.variant}: {len(res['trades'])} signals")
    else:
        report, _ = run_backtest(cfg, repo, args.out, args.trades)
        print(report)
    repo.close()
    return 0


def cmd_backtest_compare(args, cfg, secrets) -> int:
    import json
    from pathlib import Path

    from .backtest.variants import comparison_report
    results = [json.loads(Path(p).read_text(encoding="utf-8")) for p in args.inputs]
    text = comparison_report(results, cfg, args.detail)
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(text, encoding="utf-8")
    print(text)
    return 0


def cmd_backtest_robust(args, cfg, secrets) -> int:
    import json
    from pathlib import Path

    from .backtest.robust import robust_report
    results = [json.loads(Path(p).read_text(encoding="utf-8")) for p in args.inputs]
    text = robust_report(results, args.baseline)
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(text, encoding="utf-8")
    print(text)
    return 0


def cmd_panel(args, cfg, secrets) -> int:
    """Write the status panel page (read-only; for a web server on the VPS)."""
    from .panel import build
    cfg = live_config(cfg)
    if getattr(args, "pull", False):
        # panel-only server (2026-11-15, Ali): the agent keeps running on GitHub Actions and
        # this machine only shows it, so take the latest databases from the data branch first
        from .store.storage import make_storage
        if not make_storage(cfg).pull():
            print("data branch not found: building from the local databases")
    from .ai_review import pull_llm
    pull_llm(cfg)                                   # the LLM reviewers' scores (GitHub Actions, branch ai)
    if (cfg.get("panel") or {}).get("translate_news"):
        from .news.translate import pull_remote
        pull_remote(cfg)                            # Persian titles GitHub Actions translated (branch ai)
    print(build(cfg, args.out))
    return 0


def cmd_ai_review(args, cfg, secrets) -> int:
    """The LLM reviewers' pass (GitHub Actions, .github/workflows/ai-review.yml): scores for new
    signals, lessons from closed trades, written to data/ai_llm.json. Local reviewers run in
    every panel build instead."""
    from . import ai_review
    cfg = live_config(cfg)
    now = int(time.time() * 1000)
    cases = ai_review.cases(cfg, now)
    store = ai_review.run_llm(cfg, cases, now)
    n = {k: sum(1 for v in store["preds"].values() if k in v) for k in (store.get("ran") or {})}
    print(f"ai-review: {len(cases)} trades, scored {n}, errors {store.get('errors')}")
    return 0


def cmd_translate_news(args, cfg, secrets) -> int:
    """Persian titles for the panel's news (GitHub Actions, ai-review.yml), in case the translation
    hosts refuse the server: fills data/news_fa.json, which goes to the ai branch."""
    from .news import translate
    from .panel_insights import HOUR, NEWS_DAYS
    from .store.repository import Repository
    cfg = live_config(cfg)
    path = Path(cfg["storage"]["db_path"])
    if not path.exists():
        print(f"translate-news: no database at {path}")
        return 0
    repo = Repository.open_readonly(path)
    try:
        news = repo.get_news(int(time.time() * 1000) - NEWS_DAYS * 24 * HOUR)
    finally:
        repo.close()
    titles = [n["title"] for n in sorted(news, key=lambda n: -(n.get("ts") or n["seen_at"]))
              if n.get("rule") != "ignored"]
    out = translate.cache_path(cfg)
    before = len(translate.load(out))
    got = translate.translate_titles(out, titles, max_new=args.max, budget_s=args.budget)
    print(f"translate-news: {len(titles)} titles, {len(got)} in Persian, {len(translate.load(out)) - before} new")
    return 0


def cmd_backup(args, cfg, secrets) -> int:
    """Copy the databases to the data branch now (agent on the server, before switching back to GitHub)."""
    from .store.storage import GitBackupStorage, make_storage
    storage = make_storage(cfg)
    if isinstance(storage, GitBackupStorage):
        storage.push("backup", force=True)
    else:
        storage.push("backup")
    print(f"backed up {storage.db_path}")
    return 0


def cmd_panel_api(args, cfg, secrets) -> int:
    """Serve the panel's "took it / skipped" marks (127.0.0.1 only; Caddy proxies /api/)."""
    from .panel_api import serve
    print(f"panel-api on {args.host}:{args.port}")
    serve(cfg, args.host, args.port)
    return 0


COMMANDS = {
    "check-sources": cmd_check_sources,
    "run-4h": cmd_run_4h,
    "run-1h": cmd_run_1h,
    "run-15m": cmd_run_15m,
    "run-daily": cmd_run_daily,
    "run-weekly": cmd_run_weekly,
    "run-scheduled": cmd_run_scheduled,
    "run-funnel": cmd_run_funnel,
    "run-news": cmd_run_news,
    "backtest": cmd_backtest,
    "backtest-compare": cmd_backtest_compare,
    "backtest-robust": cmd_backtest_robust,
    "panel": cmd_panel,
    "backup": cmd_backup,
    "panel-api": cmd_panel_api,
    "ai-review": cmd_ai_review,
    "translate-news": cmd_translate_news,
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="trade-agent")
    parser.add_argument("--config", help="path to config.yaml")
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in COMMANDS:
        p = sub.add_parser(name)
        if name == "check-sources":
            p.add_argument("--json", help="also write the probe results to this file")
        if name == "backtest":
            p.add_argument("--fetch", action="store_true", help="download history first")
            p.add_argument("--db", help="backtest database (default: backtest.db_path)")
            p.add_argument("--days", type=int, help="replay the last N days (default: backtest.days)")
            p.add_argument("--max-coins", type=int, help="limit the universe (0 = all)")
            p.add_argument("--top-n", type=int, help="with --fetch: coins from the top N by market cap")
            p.add_argument("--out", default="docs/BACKTEST.md", help="report path")
            p.add_argument("--trades", default="data/backtest_trades.csv", help="trade list CSV")
            p.add_argument("--fetch-only", action="store_true", help="download history, don't replay")
            p.add_argument("--variant", help="replay one config variant (backtest.variants)")
            p.add_argument("--json", help="with --variant: where to save the raw results")
            p.add_argument("--since", help="with --fetch: multi-year history from this date "
                           "(YYYY-MM-DD; no CoinGecko charts, needs regime.source price)")
            p.add_argument("--shard", help="with --since: fetch only shard I of N coins (I/N, from 0)")
            p.add_argument("--start", help="replay from this date (YYYY-MM-DD)")
            p.add_argument("--end", help="replay until this date (YYYY-MM-DD)")
            p.add_argument("--record-watch", action="store_true",
                           help="also follow Watch setups in a shadow book (approved=false)")
            p.add_argument("--no-cg-charts", action="store_true",
                           help="with --fetch: no CoinGecko daily charts; coin history from "
                           "daily candles, dominance seeded beforehand (scripts/seed_backtest_db.py)")
        if name == "run-news":
            p.add_argument("--preview", action="store_true",
                           help="also alert headlines already out and exchange news (testing)")
        if name == "panel":
            p.add_argument("--out", default="panel", help="folder for index.html")
            p.add_argument("--pull", action="store_true",
                           help="first download the databases from the data branch (the agent "
                           "runs elsewhere, e.g. on GitHub Actions)")
        if name == "translate-news":
            p.add_argument("--max", type=int, default=600, help="new titles at most")
            p.add_argument("--budget", type=float, default=300, help="seconds at most")
        if name == "panel-api":
            p.add_argument("--host", default="127.0.0.1")
            p.add_argument("--port", type=int, default=8787)
        if name == "run-funnel":
            p.add_argument("--force", action="store_true", help="run even if not due")
        if name == "run-scheduled":
            p.add_argument("--only", help="comma-separated tasks to force, e.g. run-4h,run-1h")
        if name == "backtest-compare":
            p.add_argument("inputs", nargs="+", help="variant result JSON files")
            p.add_argument("--out", default="docs/BACKTEST.md")
            p.add_argument("--detail", default="combined", help="variant shown in full")
        if name == "backtest-robust":
            p.add_argument("inputs", nargs="+", help="variant result JSON files (same history)")
            p.add_argument("--baseline", default="ph_stage", help="variant the others are judged against")
            p.add_argument("--out", default="ROBUST.md")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING,
                        format="%(levelname)s %(name)s: %(message)s")
    cfg = load_config(args.config)
    if args.command.startswith("run-"):      # live runs follow live.variant; backtests don't
        cfg = live_config(cfg)
    return COMMANDS[args.command](args, cfg, load_secrets())


if __name__ == "__main__":
    sys.exit(main())
