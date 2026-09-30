"""`backtest` command: optionally fetch history, replay it, write the report."""
from __future__ import annotations

import csv
import logging
import time
from pathlib import Path

from .costs import trade_costs
from .engine import Backtest
from .report import build_report

log = logging.getLogger(__name__)
DAY = 86_400_000

LIMITS = """
## Assumptions and limits

- **Universe:** today's top-100 (plus the coins that were tradable on the exchange), applied to the whole year. Coins that dropped out of the top 100 during the year are missing (survivorship bias); coins listed during the year enter when their candles start.
- **Dominance:** USDT.D, BTC.D and TOTAL2 come from CoinGecko daily market caps of the top 100 (the free tier allows 365 days). The regime therefore uses the D direction only (no 4H snapshots in the past), and during the first ~100 days MA99 is still warming up, so the direction leans on Dow structure.
- **Liquidity and categories:** 24h volume and 7-day/30-day category growth use CoinGecko daily history; coin categories are today's. Spreads are unknown historically, so the spread part of the liquidity score is not given.
- **Open interest:** exchanges keep little OI history, so OI_BUILDUP never fires in the backtest. Trending coins aren't known historically either.
- **Funding:** exchange funding history is used where it exists (OKX keeps a few months); older periods use the default rate. FUNDING_EXTREME and the funding penalty only see the stored history.
- **Execution:** trades are managed on 1H candles (live uses 15m). When one candle touches both the stop and a target, the stop counts. Limit entries fill at the entry price; every fill pays the taker fee and slippage (conservative for limit orders).
- **Reproducible:** the result depends only on the stored candles, the CoinGecko history in the backtest database and `config.yaml`.
"""


def simulate(cfg: dict, repo, days: int | None = None) -> dict:
    """Replay the stored history with this config. Returns trades (with costs), the
    engine counters, hours per regime and the period."""
    meta = repo.get_state("bt_meta")
    if not meta:
        raise SystemExit("no backtest data: run `backtest --fetch` first")
    days = days or cfg["backtest"]["days"]
    end = meta["end"]
    start = end - days * DAY
    t0 = time.time()
    bt = Backtest(cfg, repo, start, end)
    trades = bt.run()
    for t in trades:
        t.update(trade_costs(t, cfg, bt.h.funding_rate_at))
    log.info("replay finished in %.0fs: %d signals", time.time() - t0, len(trades))
    return {"trades": trades, "stats": dict(bt.stats), "regime_hours": dict(bt.regime_hours),
            "meta": {**meta, "start": start, "end": end, "days": days, "coins": len(bt.rows)}}


def run_backtest(cfg: dict, repo, out_path: str | None = None, trades_csv: str | None = None,
                 days: int | None = None) -> tuple[str, list[dict]]:
    res = simulate(cfg, repo, days)
    trades, meta = res["trades"], res["meta"]
    cfg_days = {**cfg, "backtest": {**cfg["backtest"], "days": meta["days"]}}
    report = build_report(trades, res["stats"], res["regime_hours"], meta, cfg_days) + LIMITS
    if out_path:
        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        Path(out_path).write_text(report, encoding="utf-8")
    if trades_csv:
        cols = ["id", "base", "side", "grade", "score", "regime", "status", "order", "entry", "sl",
                "risk_pct", "created", "filled_at", "closed_at", "gross_r", "fee_r", "slippage_r",
                "funding_r", "net_r"]
        Path(trades_csv).parent.mkdir(parents=True, exist_ok=True)
        with open(trades_csv, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
            w.writeheader()
            w.writerows(trades)
    return report, trades
