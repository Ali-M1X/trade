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

### check-sources on GitHub Actions (ubuntu-latest, US), 2026-09-30

Run: https://github.com/Ali-M1X/trade/actions/runs/36733290337. No CoinGecko key and no Telegram secrets were set.

| Source | Load markets | 4H perp candles | ETH/BTC spot | Funding | Open interest |
|---|---|---|---|---|---|
| okx | ✅ | ✅ | ✅ | ✅ | ✅ history |
| bitget | ✅ | ✅ | ✅ | ✅ | ⚠️ current only |
| gate | ✅ (slow load, 12.8 s) | ✅ | ✅ | ✅ | ✅ history |
| mexc | ✅ | ✅ | ✅ | ✅ | ❌ none in ccxt |
| kucoinfutures | ✅ | ✅ | ❌ futures only | ✅ | ✅ history |
| bitunix | ❌ not in ccxt | | | | |

- **Candle source:** OKX stays primary because it answers every probe, including open-interest history, which OI_BUILDUP needs. Gate is the best fallback for the same reason. Bitget, MEXC and KuCoin Futures cover candles only.
- **CoinGecko** (no key): ping, `/global` and the top-100 universe all worked. `/coins/categories` worked but took 62 s because of 429 retries. Setting the free Demo key (`COINGECKO_API_KEY`) is recommended, and the 1-hour categories cache keeps the call count low.
- **Telegram:** not configured yet, so it runs in dry-run mode.

## Step 2: indicators and structure primitives

| # | Topic | Decision | Config key |
|---|---|---|---|
| 13 | RSI and ATR smoothing | Wilder's method: the first value is the plain mean of the first 14 values, then `avg = (prev·13 + x)/14`. This matches TradingView and Wilder's book. The test compares against Wilder's published RSI example; the published numbers differ by < 0.1 because their sheet rounds intermediate averages. | `indicators.*` |
| 14 | EMA / MACD | `ewm(span=n, adjust=False)` with no value until n bars exist. | `indicators.macd_*` |
| 15 | Bollinger width | `(upper − lower) / middle`, using population standard deviation (as TradingView does). | `indicators.bollinger_*` |
| 16 | RVOL | Current volume ÷ the mean of the **previous** 20 bars, excluding the current bar, so a volume spike doesn't dilute itself. | `indicators.rvol_lookback` |
| 17 | Fractal pivots | A pivot high must be strictly above the N highs on its left and ≥ the N highs on its right, so an equal-high plateau gives one pivot (its first bar). A pivot is only *known* N bars later, and every swing stores that `confirmed` bar. Every consumer uses swings known at the bar being evaluated, so there is no lookahead in backtests. Tests confirm that indicators and BOS/CHoCH at bar k don't change when later bars are added. | `swings.pivot_lr` |
| 18 | Swing alternation | Consecutive pivots of the same kind are merged, keeping the more extreme one, so Dow and wave logic always see H, L, H, L. | — |
| 19 | Dow trend | +1 if the last two swing highs **and** the last two swing lows rise; −1 if both fall; otherwise 0. | — |
| 20 | BOS / CHoCH | A close (not a wick) beyond the latest confirmed swing high or low is a break. A break against the current structure state is a CHoCH; otherwise it is a BOS. Each swing can be broken only once. | — |
| 21 | Direction (+1/0/−1) used by L1, L2, L4 and the cycles | votes = Dow (−1..1) + sign(close − MA25) + sign(close − MA99). Up if votes ≥ 2, down if ≤ −2, otherwise range. So Dow up plus price above at least one MA counts as up, and a Dow range plus price above both MAs also counts as up. | `structure.direction_min_votes` |
| 22 | S/R clusters | Pivot prices are sorted, and each joins the current cluster if it is within 0.5×ATR of the cluster mean. A cluster needs at least 2 touches. Strength = touches + timeframe weight (W 3, D 2, 4H 1). The previous D/W high and low count as levels with 1 touch. | `swings.level_cluster_atr`, `technical.levels.timeframe_weight` |
| 23 | Round numbers | Step = half the price's order of magnitude (142 → 50, 0.53 → 0.05, 83 700 → 5 000), with 2 round levels on each side. They are tagged `round` with strength 0; how much they count is decided in L6. | `structure.round_levels_each_side` |
| 24 | Pin bar | Rejection wick ≥ 2× body and longer than the opposite wick, as the strategy states. A near-doji with a long wick on one side qualifies. The "on a level" condition is checked by the L6 caller. | `technical.candles.pinbar_wick_body` |
| 25 | Trend weakening | 3 candles in a row in the trend direction, with strictly shrinking bodies and strictly growing wicks against the trend (upper wicks in an up-trend). | `technical.candles.weakness_bars` |
| 26 | Divergence | Regular divergence only: the last two swing highs (price HH, oscillator LH → bearish) or the last two swing lows (price LL, oscillator HL → bullish). If both are present, the more recent pair wins. | — |
| 27 | Scope of step 2 | Phase (TREND/ACC/DIST), the three wave cycles and chart patterns are built in step 3 alongside the L6 scoring that uses them. Step 2 provides their building blocks: swings, legs with average length, Dow trend, BOS/CHoCH, direction, levels, divergence and candle patterns. | — |
