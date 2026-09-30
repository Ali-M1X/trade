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
| 24 | Pin bar (stricter, as requested) | The rejection wick (lower wick for a long, upper for a short) must be ≥ 2× the body **and** ≥ 60% of the range. The body must be ≤ 1/3 of the range, and the opposite wick ≤ 20% of the range. The "on a level" condition is checked by the L6 caller. (With the other rules in place, the 1/3 body limit never binds on its own; it is kept as stated.) | `technical.candles.pinbar_*` |
| 25 | Trend weakening | 3 candles in a row in the trend direction, with strictly shrinking bodies and strictly growing wicks against the trend (upper wicks in an up-trend). | `technical.candles.weakness_bars` |
| 26 | Divergence | Regular divergence only: the last two swing highs (price HH, oscillator LH → bearish) or the last two swing lows (price LL, oscillator HL → bullish). If both are present, the more recent pair wins. | — |
| 27 | Scope of step 2 | Phase (TREND/ACC/DIST), the three wave cycles and chart patterns are built in step 3 alongside the L6 scoring that uses them. Step 2 provides their building blocks: swings, legs with average length, Dow trend, BOS/CHoCH, direction, levels, divergence and candle patterns. | — |

## Step 3: layers L1–L6 and the trade builder

### Market phase, cycles, patterns

| # | Topic | Decision | Config key |
|---|---|---|---|
| 28 | TREND↑ / TREND↓ | close > MA25 > MA99, MA25 higher than 10 bars ago, and Dow HH+HL (mirrored for down). | `technical.phase.ma_slope_bars` |
| 29 | ACC / DIST | The last 20 bars (current bar excluded) span < 4×ATR. MA25 moved < 1×ATR over 10 bars and MA7 is within 1×ATR of MA25 ("flat and intertwined"; MA99 can sit far away after a big move). The preceding trend is judged from where the range sits: **below MA99 means it follows a decline (ACC candidate), above means it follows a rally (DIST candidate)**. Comparing with the price N bars back broke on long ranges. ACC also needs mean RVOL of green candles > red candles; DIST the reverse. | `technical.phase.*` |
| 30 | Spring / upthrust / breakout | Spring: in the last 5 bars, a low pierces the range low (measured on the earlier range bars) and the bar closes back above it. Breakout: the current close is above the range high. Upthrust and breakdown mirror these. | `technical.phase.spring_bars` |
| 31 | Phase gate vs "ACC without spring = 6" | The strategy says long only in TREND↑ or ACC (after a spring or breakout), yet also gives ACC without a spring 6 points. I read it as: **the gate is TREND↑ or ACC**; ACC with a spring or breakout is the ideal case and scores like a trend; ACC without one passes the gate but scores only 6. | — |
| 32 | Reversal signs (3 of 4) | Counted on D against the trend being traded: CHoCH against, RSI **or** MACD-histogram divergence against, a volume climax in the last 3 bars, a close on the wrong side of MA99. With 3 or more, the phase gate fails for a trend trade. | `technical.patterns.reversal_signs_needed` |
| 33 | Volume climax | RVOL > 3 with a wick in the trend direction at least as long as the body (upper wick in an up-trend). | `technical.volume.climax_rvol` |
| 34 | Cycle state | For each of W/D/4H: major = that timeframe's structure direction. Current leg = up if the last swing is a low, down if it is a high. Impulse = leg with major, correction = leg against it. | — |
| 35 | "Lower just turned" (10 points) | On 4H, a new higher low (long) confirmed within 6 bars, or a BOS/CHoCH in the trade direction within 6 bars, while the current leg points in the trade direction. | `technical.cycles.fresh_bars` |
| 36 | "Late" (0 points) | The current 4H impulse leg is longer, in bars, than the average of the last 5 legs. | `swings.wave_history_swings` |
| 37 | Cycle cases not in the table | W and D aligned, 4H in impulse but neither fresh nor late → 0. W ranging → 0 (not "aligned"). Medium against Higher → 3 points and risk ×0.5, as stated. | — |
| 38 | "Lower still correcting" (5 points) | Scored 5, and the setup is capped at **Watch**, since the strategy says "watch alert only, no entry". | — |
| 39 | Patterns | Double top/bottom (tops or bottoms within 0.5×ATR), head & shoulders and inverse (shoulders within 1×ATR, neckline = the higher/lower of the two troughs/peaks), triangle (converging, or one side flat within 0.5×ATR), rising/falling wedge, bull/bear flag (pole ≥ 3×ATR, pullback ≤ 50% of the pole). A pattern counts only once a close is beyond the neckline with RVOL ≥ 1.5, and that break is within the last 5 bars. One setup can match two shapes (a double bottom is also a flat-bottom triangle); the +5 is given once either way. | `technical.patterns.*` |

### Layers 1–5

| # | Topic | Decision | Config key |
|---|---|---|---|
| 40 | Index direction (L1) | Each index (USDT.D, BTC.D, TOTAL2) gets a direction on D and 4H. If they agree, or one is ranging, the other's direction is used; if they point opposite ways, the index counts as ranging. | `regime.timeframes` |
| 41 | Dominance candles | 4H snapshots are resampled into OHLC candles (4H and D). The daily history reconstructed from market caps feeds only the D candles; the 4H candles use real 4H snapshots. | — |
| 42 | Dominance history | The CoinGecko free/Demo tier returns at most 365 days of history, so the backfill is 1 year (the build prompt said 2). History = sum of the top-100 coins' market caps (stablecoins included, since USDT.D needs them), rescaled at the join so it continues the `/global` series smoothly. The 4H regime is ranging until enough 4H snapshots exist (about 2–3 weeks for MA99 on 4H); the D direction carries the regime until then. | `regime.backfill_days` |
| 43 | Regime bias and risk | alt_season: long, risk 1. btc_led: long; full risk for BTC and alts whose COIN/BTC is up on D, 0.5 for other alts. risk_off and capitulation: short only (both forbid longs for futures). neutral: both sides, risk 0.5, A-grade only (a B becomes Watch). | `regime.regimes` |
| 44 | ETHBTC bonus | "+5 for Ethereum-ecosystem alts and all alts" amounts to +5 for every coin except BTC, applied in the L5 score. | `majors.ethbtc_up_bonus` |
| 45 | BTC/TOTAL2 divergence | The sign of the BTC score (L2) against the TOTAL2 direction (L1); opposite and both non-zero → risk ×0.5. | `majors.divergence_risk_multiplier` |
| 46 | COIN/BTC and COIN/ETH | Built synthetically from the two USDT perpetuals, dividing each OHLC field separately as TradingView does for spread symbols. Real spot pairs don't exist for many alts on OKX, and this keeps one source. | — |
| 47 | Candle closes | Every fetch drops the still-forming candle, so all analysis runs on closed candles only. | — |
| 48 | SQUEEZE | Bollinger width percentile rank ≤ 10 over 120 D bars, and ATR lower than 5 bars ago. | `scanners.squeeze.*` |
| 49 | EARLY_TREND | Close above the highest high of the previous 30 D bars with RVOL ≥ 2. The strategy gives no width limit for the range, so none is applied. | `scanners.early_trend.*` |
| 50 | RS_LEADER | 7-day and 30-day return minus BTC's, both at or above the 90th percentile of the universe, plus COIN/BTC closing at its 30-day high. | `scanners.rs_leader.*` |
| 51 | PULLBACK | D Dow up **and price above MA99**. The combined direction is not used, because a pullback often dips under MA25. Price within 0.382–0.618 of the last up leg, or within 0.5×ATR of MA25/MA99, with mean RVOL of the last 3 bars < 1. | `scanners.pullback.*` |
| 52 | EXHAUSTION | On D: more than 3×ATR from MA25 and RSI > 75 (short) or < 25 (long), plus RSI or MACD divergence against the move on 4H. | `scanners.exhaustion.*` |
| 53 | OI_BUILDUP / FUNDING_EXTREME / VOLUME_ANOMALY | OI up ≥ 15% over 3 days with price within ±3%. The last 3 funding rates all > 0.05% (short) or all < −0.03% (long). The last D volume > 3× the average of the previous 30 D bars, with the day's change < 5%. | `scanners.*` |
| 54 | HOT_SECTOR | CoinGecko's category list has no 7-day change, so category growth is computed from the universe coins in each category: Σcap_now / Σ(cap_now / (1 + 7d%)) − 1. A category needs at least 2 coins. Coin categories come from `/coins/{id}`, cached for a week (one call per coin, about 4 minutes on the first run). | `scanners.hot_sector.*` |
| 55 | Universe | Top 100 without stablecoins or wrapped tokens, plus CoinGecko trending coins and up to 50 coins from each hot category, limited to coins that have a USDT perpetual on the exchange and $10M+ 24h volume. | `universe.*` |
| 56 | Pair strength (L4) | Each pair's alignment with the side (up = 1, range = 0.5, down = 0), weighted D 2 : 4H 1 and USDT 0.5 : BTC 0.35 : ETH 0.15. So USDT up with BTC down scores about 0.5 ("just riding BTC"). An independent coin (correlation ≤ 0.5 with positive RS) gets +0.1, or +0.2 in a neutral regime. | `pairs.*` |
| 57 | Correlation ≥ 0.8 | The coin is allowed only in the direction of BTC's D structure. | `pairs.corr_high` |
| 58 | L5 score parts | Regime 30 = half from the bias (1 same side, 0.5 in a neutral regime) and half from the BTC score ((BTC × side + 6)/12). Pairs 30 = 30 × pair strength. Labels 25 = 25 × min(1, Σlabel quality / 2). Liquidity 15 = 10 for volume (log scale from $10M to $500M) + 5 for spread (0.05% full, 0.30% zero). | `shortlist.*` |
| 59 | Side per coin | A coin is scored for each side its labels allow (SQUEEZE and other "both" labels allow both); the better side is kept. | — |

### Layer 6 and the trade

| # | Topic | Decision | Config key |
|---|---|---|---|
| 60 | Timeframes for scoring | Phase: D with 4H confirmation. Dow: D and 4H. Volume, candles, RSI/MACD/SMA health, patterns and levels: 4H. Confirmations: 1H. Reversal signs: D. | — |
| 61 | Dow case "D aligned, 4H ranging" | Not in the table, so it scores 0 (only "4H aligned, D ranging" gets 8). | `technical.dow.*` |
| 62 | Levels used for trades | 4H/D/W pivot clusters, the previous D/W high and low, and round numbers. Score: a D/W cluster with ≥ 3 touches = 15; any other cluster or previous-period level = 8; a round number alone = 0 (it still counts as a level for entry and targets). | `technical.levels.*` |
| 63 | Entry | The closest level at most 1×ATR(4H) behind the price is the support. Entry zone = level ± 0.2×ATR. If the price is already inside the zone, the entry is at market; otherwise it is a limit order at the level. | `trade.entry_zone_atr` |
| 64 | Stop | The lowest pivot in the support cluster (or the level itself) minus 0.5×ATR(4H), mirrored for shorts. Rejected if the stop is more than 3×ATR from the entry. | `trade.sl_buffer_atr`, `trade.max_sl_atr` |
| 65 | R:R gate and TP1 | The first opposing level beyond the entry zone must be ≥ 2R away; it becomes TP1. If there is no opposing level, TP1 = 2R. | `trade.tp1_min_r` |
| 66 | TP2 | The next D/W level beyond TP1; otherwise 3R. **If TP1 is already past 3R**, the next level of any timeframe is used, and failing that TP1 + 1R, so TP2 is always beyond TP1 (a test caught TP2 landing below TP1). | `trade.tp2_r` |
| 67 | Volume section | +5 if a 4H break in the trade direction within 6 bars had RVOL ≥ 1.5, or, with no recent break (a pullback setup), if the last 3 bars averaged RVOL < 1. +5 if OBV rose over 20 bars (fell, for shorts). −5 for a climax. Clamped to 0–10. | `technical.volume.*` |
| 68 | Candles section | +5 for a trigger candle on the last 4H bar (a pin bar counts only if its wick reaches the entry zone, i.e. it formed "on the level"), +5 for a body of 0.8–2×ATR, −5 for three weakening candles. A body over 2.5×ATR marks a "chase" and caps the setup at Watch. | `technical.candles.*` |
| 69 | Health section | RSI: +5 if RSI is within 40–80 and stayed ≥ 40 over the last 20 bars (shorts mirrored: 20–60, ≤ 60); −5 for RSI divergence against the trade. MACD: +5 if the MACD line is on the trade side of zero and the histogram is moving the trade's way. SMA: +5 for MA7 > MA25 > MA99 with price above MA25 (mirrored for shorts); −5 if price is more than 3×ATR from MA25. Patterns: +5. Clamped to 0–15. | `technical.patterns.*` |
| 70 | Section floors | Each section is clamped to 0…its maximum, so a penalty reduces that section but never takes points from another. | — |
| 71 | 1H confirmations | (a) a low (high, for shorts) in the last 6 bars reached the entry zone and the close is back beyond the level; (b) a CHoCH in the trade direction within 6 bars; (c) a trigger candle on the last bar; (d) RSI crossed back over 50 within 6 bars, or the MACD histogram turned to the trade side within 3 bars; (e) RVOL ≥ 1.5 on the last bar. | `technical.confirmation.*` |
| 72 | Confirmation points | 2.5 per confirmation, 10 max: 2 → 5, 3 → 7.5, 4+ → 10. Fewer than 2 → 0 and the gate fails. | — |
| 73 | Watch vs no signal | If the regime or phase gate fails, or no valid trade exists (no level, stop too wide, R:R), there is no output at all. If those pass but something from "near setup" applies (no 1H confirmation yet, lower cycle still correcting, chase candle, BTC weak and score < 80, a B in a neutral regime, or zero risk multiplier), an A/B grade is lowered to **Watch** (a "near setup" alert). | — |
| 74 | Risk per trade | base 1% × grade share (A 1, B 0.5) × regime risk for the side and coin × 0.5 if BTC/TOTAL2 diverge × 0.5 for a counter-trend cycle. | `grades.risk_share` |
| 75 | Leverage | floor(1 ÷ (2.5 × stop %)), at least 1x, at most 10x. Stop 3.4% → 11 → 10x, matching the strategy example. | `trade.leverage_cap` |
| 76 | Funding filter | −5 from the total when funding in the trade's direction exceeds 0.05% (longs paying more than +0.05%, shorts facing less than −0.05%). | `trade.funding_*` |
| 77 | Analysis window | Each timeframe is analysed on its last 300 closed candles, plus 120 warm-up bars for the indicators. This keeps each run and the backtest fast and makes results depend only on stored candles. | `analysis.window_bars`, `storage.warmup_bars` |

### Live smoke run on GitHub Actions, 2026-09-30 16:02 UTC

Run: https://github.com/Ali-M1X/trade/actions/runs/36741131896 (throwaway local database; nothing sent).

- `run-4h`: about 9 min. 4.5 min of that is the one-time 365-day dominance backfill; later runs reuse the stored history.
  - Regime **neutral** (USDT.D ↓, BTC.D range, TOTAL2 ↑). BTC +3, ETH +2, ETHBTC +1.
  - Watchlist 35 coins; shortlist 8, all longs: SOON, GRASS, 0G, NIGHT, ICP, UNI, PUMP, ARB.
- `run-1h`: 10 s, all 8 evaluated.
  - PUMP scored 65.5 (a B) and became **Watch** because a neutral regime allows A only.
  - The others failed a gate: no level to lean on after a breakout (ICP), R:R < 2 (UNI, ARB, GRASS), phase (SOON), or no 1H confirmation yet (0G, NIGHT).
- A first run found that new listings got no weekly candles: pagination started before the listing date and stopped at the first empty page. Fixed by falling back to the latest candles.
