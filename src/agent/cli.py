"""Command-line entry points. The same commands run from GitHub Actions or a VPS cron."""
from __future__ import annotations

import argparse
import json
import logging
import sys

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
    tasks = args.only.split(",") if args.only else due_tasks(now, last)
    print(f"due: {', '.join(tasks)}")
    failed = []
    for task in tasks:
        try:
            if task == "run-15m":
                print(f"run-15m: {len(manage(cfg, repo, market, notifier, now))} signal events")
            elif task == "run-4h":
                print(summarize_funnel(run_4h(cfg, repo, market, cg, now)))
            elif task == "run-1h":
                print(summarize_evaluations(run_1h(cfg, repo, market, now, notifier)))
            elif task == "run-daily":
                daily(cfg, repo, notifier, now)
                hold_new_entrants(cfg, repo, market, cg, notifier, now)
            elif task == "run-weekly":
                print(f"run-weekly: {len(weekly(cfg, repo, market, cg, notifier, now))} HOLD ideas")
            last[task] = now
            repo.set_state("last_runs", last)
        except Exception:                       # one failing task must not block the others
            logging.getLogger(__name__).exception("%s failed", task)
            failed.append(task)
    extra = {}
    if cfg.get("news", {}).get("enabled"):
        try:
            extra = news_pass(cfg, repo, market, cg, notifier, now)
        except Exception:                       # experimental: never blocks or fails the signal runs
            logging.getLogger(__name__).exception("news failed")
    repo.close()
    if extra:
        storage.push("run-scheduled " + ",".join(tasks), extra=extra)
    else:
        storage.push("run-scheduled " + ",".join(tasks))
    return 1 if failed else 0


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


def cmd_backtest(args, cfg, secrets) -> int:
    import time

    from .backtest.run import run_backtest
    from .store.repository import Repository
    if args.days:
        cfg["backtest"]["days"] = args.days
    if args.max_coins is not None:
        cfg["backtest"]["max_coins"] = args.max_coins
    repo = Repository(args.db or cfg["backtest"]["db_path"])
    if args.fetch or args.fetch_only:
        from .backtest.data import fetch_history
        from .data.coingecko import CoinGecko
        from .data.exchange import ExchangeClient
        from .data.market import LiveMarket
        now = int(time.time() * 1000)
        client = ExchangeClient(cfg)
        stats = fetch_history(cfg, repo, client, LiveMarket(cfg, repo, client, now),
                              CoinGecko(cfg, repo=repo, api_key=secrets.coingecko_api_key), now)
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


COMMANDS = {
    "check-sources": cmd_check_sources,
    "run-4h": cmd_run_4h,
    "run-1h": cmd_run_1h,
    "run-15m": cmd_run_15m,
    "run-daily": cmd_run_daily,
    "run-weekly": cmd_run_weekly,
    "run-scheduled": cmd_run_scheduled,
    "run-news": cmd_run_news,
    "backtest": cmd_backtest,
    "backtest-compare": cmd_backtest_compare,
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
            p.add_argument("--out", default="docs/BACKTEST.md", help="report path")
            p.add_argument("--trades", default="data/backtest_trades.csv", help="trade list CSV")
            p.add_argument("--fetch-only", action="store_true", help="download history, don't replay")
            p.add_argument("--variant", help="replay one config variant (backtest.variants)")
            p.add_argument("--json", help="with --variant: where to save the raw results")
        if name == "run-news":
            p.add_argument("--preview", action="store_true",
                           help="also alert headlines already out and exchange news (testing)")
        if name == "run-scheduled":
            p.add_argument("--only", help="comma-separated tasks to force, e.g. run-4h,run-1h")
        if name == "backtest-compare":
            p.add_argument("inputs", nargs="+", help="variant result JSON files")
            p.add_argument("--out", default="docs/BACKTEST.md")
            p.add_argument("--detail", default="combined", help="variant shown in full")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING,
                        format="%(levelname)s %(name)s: %(message)s")
    cfg = load_config(args.config)
    if args.command.startswith("run-"):      # live runs follow live.variant; backtests don't
        cfg = live_config(cfg)
    return COMMANDS[args.command](args, cfg, load_secrets())


if __name__ == "__main__":
    sys.exit(main())
