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


def not_yet(step: int):
    def run(args, cfg, secrets) -> int:
        print(f"{args.command}: not implemented yet (build step {step})", file=sys.stderr)
        return 2
    return run


COMMANDS = {
    "check-sources": cmd_check_sources,
    "run-4h": not_yet(3),
    "run-1h": not_yet(3),
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
