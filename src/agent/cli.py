"""Command-line entry points. The same commands run from GitHub Actions or a VPS cron."""
from __future__ import annotations

import argparse
import json
import logging
import sys

from .config import load_config, load_secrets
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
    from .runs import run_1h, summarize_evaluations
    storage, repo, market, _, now = open_context(cfg, secrets)
    print(summarize_evaluations(run_1h(cfg, repo, market, now)))
    repo.close()
    storage.push("run-1h")
    return 0


def not_yet(step: int):
    def run(args, cfg, secrets) -> int:
        print(f"{args.command}: not implemented yet (build step {step})", file=sys.stderr)
        return 2
    return run


COMMANDS = {
    "check-sources": cmd_check_sources,
    "run-4h": cmd_run_4h,
    "run-1h": cmd_run_1h,
    "run-15m": not_yet(4),
    "run-daily": not_yet(4),
    "run-weekly": not_yet(4),
    "backtest": not_yet(5),
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
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING,
                        format="%(levelname)s %(name)s: %(message)s")
    return COMMANDS[args.command](args, load_config(args.config), load_secrets())


if __name__ == "__main__":
    sys.exit(main())
