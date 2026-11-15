"""Join replay results (backtest --variant X --record-watch --json) into the status panel's
backfill file: every trade the replay produced, confirmed (approved) and Watch (not), per
system. Usage: python scripts/panel_backfill.py OUT.json system1=combo_avoid.json system2=s2_avoid.json
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

KEEP = ("base", "side", "grade", "score", "created", "status", "order", "entry", "sl", "tp1",
        "tp2", "risk_pct", "gross_r", "fee_r", "slippage_r", "funding_r", "net_r", "filled_at",
        "closed_at", "events", "approved", "regime")


def main(out: str, inputs: list[str]) -> None:
    trades, meta = [], {}
    for item in inputs:
        system, path = item.split("=", 1)
        res = json.loads(Path(path).read_text(encoding="utf-8"))
        meta[system] = {"variant": res.get("variant"), **res["meta"], "stats": res.get("stats", {})}
        for t in res["trades"]:
            trades.append({"system": system, **{k: t.get(k) for k in KEEP}})
    trades.sort(key=lambda t: t["created"])
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    Path(out).write_text(json.dumps({"meta": meta, "trades": trades}, ensure_ascii=False),
                         encoding="utf-8")
    approved = sum(t["approved"] for t in trades)
    print(f"{out}: {len(trades)} trades ({approved} confirmed, {len(trades) - approved} Watch)")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2:])
