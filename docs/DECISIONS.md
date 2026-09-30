# Decisions

Interpretations made where `docs/STRATEGY.md` or `docs/BUILD_PROMPT.md` leave room. Every number here is a value in `config.yaml`.

## Step 1: scaffold, config, storage, data sources

| # | Topic | Decision | Config key |
|---|---|---|---|
| 1 | Exchange names | Exchanges use ccxt ids. Gate.io is `gate` (ccxt has no `gateio` id). KuCoin perpetuals are `kucoinfutures`. | `exchange.*` |
| 2 | Bitunix | Bitunix is not in ccxt (checked with 4.5.84). It stays last in the fallback list and `check-sources` reports it as unsupported. Analysis runs on another exchange's data. The strategy says this is fine, since liquid exchanges give almost the same candles. | `exchange.fallbacks` |
| 3 | Primary exchange | OKX is the default because it offers perpetual candles, funding and open-interest history through ccxt. It will change to whichever exchange `check-sources` shows working from GitHub runners. | `exchange.primary` |
| 4 | Candle source per record | Candles are stored with the name of the exchange that served them, so a backtest never mixes sources without knowing it. | — |
| 5 | Market type | USDT-margined linear perpetuals (`BASE/USDT:USDT`) for COIN/USDT. COIN/BTC and COIN/ETH (Layer 4) are spot pairs, because perpetuals don't exist for them. | `exchange.market_type` |
| 6 | Universe exclusions | Coins in CoinGecko's `stablecoins` and `wrapped-tokens` categories are removed, plus a static list of ids and symbols in case a category call fails or misses a coin. Liquid-staking tokens (stETH and similar) are in the static list because they behave like wrapped ETH. | `universe.exclude_*` |
| 7 | CoinGecko rate limit | At least 2.5 s between calls (the free tier allows about 30 calls a minute). On 429 or 5xx the client honours `Retry-After`, otherwise it waits 15 s and doubles the wait each retry. Responses are cached in SQLite with a TTL for each endpoint. `check-sources` uses a single short retry so a blocked source is reported quickly. | `coingecko.*` |
| 8 | Dominance snapshot | `/global` returns dominance percentages. The code stores market caps in USD derived from them: total, BTC, ETH and USDT. USDT.D = USDT/total, BTC.D = BTC/total, TOTAL2 = total − BTC. | — |
| 9 | Persisting SQLite on GitHub Actions | The database is gzipped and stored as the single file `state.db.gz` on a `data` branch. Each run replaces the branch with one orphan commit and force-pushes, so the repository doesn't grow by one binary per run (every 15 minutes). History isn't needed because signals and events are kept inside the database. The workflow must use a `concurrency` group so two runs never push at once. | `storage.*` |
| 10 | Runtime DB vs backtest data | The runtime database should keep only recent candles. Two years of 4H+1H candles for 100 coins would exceed GitHub's 100 MB file limit, so the backtest will use its own local database file (step 5). | `storage.db_path` |
| 11 | Daily summary | Added as a `run-daily` command next to the ones listed in the build prompt, because the build prompt asks for a daily summary message. | — |
| 12 | Values not given in STRATEGY.md | These are my defaults, to be tuned by the backtest: scanner label quality weights (L5), `squeeze.atr_falling_bars`, `pullback.ma_touch_atr`, `pullback.volume_falling_bars`, `funding_extreme.consecutive`, `rs_leader.new_high_lookback`, level timeframe weights, and `hold.buy_steps` (the strategy says 2–3). | see keys |
