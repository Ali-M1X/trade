# Build prompt

You are building **trade-signal-agent** in this repository: a Python service that scans the crypto market, applies a fixed top-down strategy, and sends futures trading signals (plus weekly spot "HOLD" ideas) to Telegram in Persian.

The complete strategy, including every threshold and score, is in `docs/STRATEGY.md`. That document is the source of truth. Implement it exactly; where it is ambiguous, pick the simplest reasonable interpretation, make it a config value, and list it in `docs/DECISIONS.md`.

## Hard constraints
- **Zero running cost.** No paid APIs and no LLM calls at runtime. Only free public endpoints (CoinGecko free/Demo key, exchange public REST via `ccxt`, Telegram Bot API).
- **Runs on GitHub Actions first, a VPS later**, with the same code. No GitHub-specific logic inside the engine; the workflow is just a scheduler.
- **Deterministic and backtestable.** Every signal must be reproducible from stored candles and config.
- **All numbers in `config.yaml`** (thresholds, weights, timeframes, MA periods 7/25/99, RSI 14, MACD 12/26/9, ATR 14, risk 1%, max 5 active signals, A+ override at score ≥85, leverage cap 10x, liquidation distance 2.5× SL, universe = top 100 by market cap excluding stablecoins and wrapped tokens).
- Secrets only via environment variables: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `COINGECKO_API_KEY` (optional). If Telegram secrets are missing, print messages to stdout instead of failing (dry-run mode).

## Architecture
```
src/agent/
  data/        # fetchers: exchange (ccxt, configurable + fallback list), coingecko, dominance builder, local cache
  indicators/  # MA, RSI, MACD, ATR, RVOL, OBV, Bollinger width, pivots/swings, S/R clustering
  analysis/    # structure (Dow, BOS/CHoCH), phase (TREND/ACC/DIST), cycles (W/D/4H waves), patterns, candles
  layers/      # L1 regime, L2 majors, L3 scanners, L4 pairs & correlation, L5 shortlist, L6 technical score, trade builder
  hold/        # weekly spot HOLD scanner
  signals/     # signal lifecycle: new -> active -> TP1/TP2/TP3/SL/expired/cancelled, dedup, max-active rule
  notify/      # Telegram formatter (Persian, exact template in STRATEGY.md) and sender
  store/       # SQLite repository (candles cache, dominance history, signals, events)
  backtest/    # replays stored candles through L1–L6 and the lifecycle; reports win rate, avg R, max drawdown
  cli.py       # entry points: run-4h, run-1h, run-15m, run-weekly, backtest, check-sources
tests/
config.yaml
.github/workflows/
```

## Data sources
- Candles, funding, open interest: `ccxt`, exchange chosen in config with an ordered fallback list (e.g. bitunix, okx, bitget, gateio, mexc, kucoin). GitHub runners are in the US, where Binance and Bybit block requests, so the `check-sources` command must test each exchange and report which respond.
- Top 100, market caps, categories: CoinGecko free API, with rate-limit handling and caching.
- USDT.D, BTC.D, TOTAL2: build them ourselves. Snapshot CoinGecko `/global` every 4H into SQLite; backfill history by reconstructing from the top-100 historical market caps. Optional fallback: `tvdatafeed` without login, behind a config flag.

## Schedules (commands)
- `run-4h`: L1–L5 after each 4H close; saves the regime and shortlist.
- `run-1h`: L6 scoring on the shortlist; creates new signals and sends them.
- `run-15m`: checks entry triggers and manages active signals (fills, TPs, SL, expiry) and sends updates.
- `run-weekly`: HOLD report.
- A daily summary message (regime, BTC/ETH/ETHBTC state, watchlist).

GitHub Actions: one workflow with cron triggers mapped to these commands. The repo is public (unlimited free Actions minutes), so default the trigger-check interval to 15 minutes; keep it configurable (30 min if the repo becomes private). Persist the SQLite file between runs by committing it to a dedicated `data` branch (or an artifact cache), behind a small storage interface so it can become a local file on the VPS.

## Order of work
1. Scaffold, config, storage, and `check-sources`. Report which free sources work from GitHub Actions.
2. Indicators and structure primitives, with unit tests on known candle fixtures.
3. Layers L1–L5, then L6 scoring and the trade builder, each with tests that assert gates and scores.
4. Signal lifecycle and the Telegram formatter (dry-run output first).
5. Backtest on 2 years of 4H/1H data for the top 100; write the results to `docs/BACKTEST.md`.
6. GitHub Actions workflow and a README in Persian explaining setup (BotFather token, chat id, secrets).

Keep the code simple and readable. Don't add features that aren't in STRATEGY.md. After each step, run the tests and summarise what was built and anything uncertain.
