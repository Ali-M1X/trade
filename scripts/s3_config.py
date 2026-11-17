"""System 3 test (2026-11-17): prepare a replay of the live rules on small and mid coins
without touching config.yaml or the engine (CI copy only).

  - writes OUT.yaml = config.yaml + variants s3_s1_small / s3_s2_small: system 1 (combo_avoid)
    and system 2 (s2_avoid) with a $3M volume floor and 0.2 % slippage per fill (thin books)
  - drops the largest coins (rank < MIN_RANK) from the backtest database's universe; BTC, ETH
    and the regime basket keep their candles, so the regime reads the same

Usage: python scripts/s3_config.py config.yaml OUT.yaml backtest.db
"""
from __future__ import annotations

import copy
import json
import sqlite3
import sys

import yaml

MIN_RANK = 20
SMALL = {"universe": {"min_volume_24h_usd": 3_000_000}, "backtest": {"slippage_pct": 0.2}}


def merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in over.items():
        out[k] = merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def main(src: str, out: str, db: str) -> None:
    cfg = yaml.safe_load(open(src, encoding="utf-8"))
    v = cfg["backtest"]["variants"]
    v["s3_s1_small"] = merge(v["combo_avoid"], SMALL)
    v["s3_s2_small"] = merge(v["s2_avoid"], SMALL)
    with open(out, "w", encoding="utf-8") as f:
        yaml.safe_dump(cfg, f, allow_unicode=True, sort_keys=False)
    con = sqlite3.connect(db)
    rows = json.loads(con.execute("SELECT value FROM state WHERE key = 'bt_universe'").fetchone()[0])
    keep = [r for r in rows if r.get("u_rank", 0) >= MIN_RANK]
    con.execute("UPDATE state SET value = ? WHERE key = 'bt_universe'", (json.dumps(keep),))
    con.commit()
    print(f"{out}: s3_s1_small, s3_s2_small · universe {len(rows)} -> {len(keep)} coins (rank >= {MIN_RANK})")


if __name__ == "__main__":
    main(*sys.argv[1:4])
