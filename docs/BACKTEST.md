# Backtest

Generated on GitHub Actions. **Variants:** [run 36762973452](https://github.com/Ali-M1X/trade/actions/runs/36762973452): history fetched once, six variants replayed in parallel, comparison built by `trade-agent backtest-compare`. **Baseline details:** [run 36755789306](https://github.com/Ali-M1X/trade/actions/runs/36755789306). The raw per-variant results and the database are in those runs' artifacts.

## Summary

**None of the four changes, alone or together, makes the strategy profitable.** All R figures are net of fees, slippage and funding.

| Variant | First 273 days: trades | Total R | Avg R | Last 92 days (holdout): trades | Total R | Avg R |
|---|---|---|---|---|---|---|
| baseline | 27 | −6.9 | −0.26 | 27 | −18.4 | −0.68 |
| 1 swing_stop | 4 | −4.4 | −1.09 | 2 | −2.1 | −1.07 |
| 2 min_stop | 25 | −13.2 | −0.53 | 27 | −18.0 | −0.67 |
| 3 tp1_cap | 30 | **−4.4** | −0.15 | 29 | −18.2 | −0.63 |
| 4 cooldown | 27 | −6.9 | −0.26 | 21 | −16.5 | −0.78 |
| combined | 4 | −4.4 | −1.09 | 2 | +0.3 | +0.16 |

- **Choice on the first 273 days: `tp1_cap`** (−4.4R, the least negative; its win rate rose from 26% to 33%). **In the holdout it was no better than the baseline** (−18.2R vs −18.4R), so the improvement didn't carry over.
- **The swing/ATR stop (1) leaves almost no trades:** 6 in a year instead of 54. With the stop a full ATR or a swing away, the first opposing level is rarely ≥ 2R away, so the R:R gate rejects nearly everything. The strategy's own rules, entering within 1×ATR of a support and requiring the next level ≥ 2R away, favour tight stops, and those are the ones that get wicked out (finding 1 in the baseline). The two changes pull against each other. The combined variant's +0.3R holdout is 2 trades and means nothing.
- **The 0.8% minimum stop (2) made the first 273 days worse** (−13.2R). It removed tight-stop losers but also the two biggest winners of the year: TRX +4.87R (0.34% stop) and ETH +2.47R (0.68% stop). It rejected 2 443 hourly L6 evaluations (the same coin is re-scored every hour, so these are not distinct setups).
- **The cooldown (4)** blocked 31 repeat signals; its first 273 days equal the baseline's, and its holdout is slightly less negative in R (−16.5R) but on a lower win rate.
- **The last 3 months were bad for every variant** (baseline −0.68R per trade vs −0.26R before), mostly in the neutral regime (16 of the baseline's 27 holdout trades, −11.1R).
- The "Return" column can be positive while total R is negative (tp1_cap, first 273 days: +1.8% on −4.4R), because A-grade trades carry twice the risk of B-grade ones and A did better.

**What I'd conclude:** these four tweaks don't produce an edge. The losses come from the entry itself (buying a retest of a nearby level with a stop just past it), not from the exits or from repeat signals. Keeping everything at the STRATEGY.md defaults (all four switches are off) and paper-trading is the safe setting. Any further change would be a strategy decision for you: for example, entering only after the 4H turn is confirmed rather than at the level, or dropping the "first opposing level ≥ 2R" gate in favour of a fixed R target. Every switch can be tested the same way: add a variant to `backtest.variants` and run the backtest workflow.

---

## Variant comparison

Same stored history for every variant: 2025-09-30 → 2026-09-30, 69 coins. **Tuning period:** 2025-09-30 → 2026-06-30 (used to choose). **Holdout:** 2026-06-30 → 2026-09-30 (last 92 days, reported only). A trade belongs to the period in which it was opened. All R figures are net of fees, slippage and funding.

| Variant | Changes |
|---|---|
| baseline | STRATEGY.md as built |
| swing_stop | trade.stop_mode = swing_or_atr |
| min_stop | trade.min_stop_pct = 0.8 |
| tp1_cap | trade.tp1_max_r = 3 |
| cooldown | lifecycle.cooldown_after_stop_h = 24 |
| combined | trade.stop_mode = swing_or_atr; trade.min_stop_pct = 0.8; trade.tp1_max_r = 3; lifecycle.cooldown_after_stop_h = 24 |

### Tuning period (first 273 days)

| Variant | Trades | Win rate | Avg R | Total R | Profit factor | Return | Max DD |
|---|---|---|---|---|---|---|---|
| baseline | 27 | 26% | -0.26 | -6.9 | 0.70 | -1.3% | 4.5% |
| swing_stop | 4 | 0% | -1.09 | -4.4 | 0.00 | -2.4% | 2.4% |
| min_stop | 25 | 20% | -0.53 | -13.2 | 0.41 | -4.2% | 5.6% |
| tp1_cap | 30 | 33% | -0.15 | -4.4 | 0.80 | +1.8% | 2.0% |
| cooldown | 27 | 26% | -0.26 | -6.9 | 0.70 | -1.5% | 5.3% |
| combined | 4 | 0% | -1.09 | -4.4 | 0.00 | -2.4% | 2.4% |

### Holdout (last 92 days)

| Variant | Trades | Win rate | Avg R | Total R | Profit factor | Return | Max DD |
|---|---|---|---|---|---|---|---|
| baseline | 27 | 15% | -0.68 | -18.4 | 0.29 | -7.9% | 8.8% |
| swing_stop | 2 | 0% | -1.07 | -2.1 | 0.00 | -1.1% | 1.1% |
| min_stop | 27 | 15% | -0.67 | -18.0 | 0.29 | -7.7% | 8.6% |
| tp1_cap | 29 | 17% | -0.63 | -18.2 | 0.32 | -7.8% | 8.7% |
| cooldown | 21 | 10% | -0.78 | -16.5 | 0.22 | -8.5% | 9.3% |
| combined | 2 | 50% | +0.16 | +0.3 | 1.28 | +0.2% | 0.6% |

### By grade

| Variant | Grade | Tuning: trades | Tuning: avg R | Tuning: total R | Holdout: trades | Holdout: avg R | Holdout: total R |
|---|---|---|---|---|---|---|---|
| baseline | A | 10 | -0.00 | -0.0 | 20 | -0.53 | -10.7 |
| baseline | B | 17 | -0.41 | -6.9 | 7 | -1.11 | -7.7 |
| swing_stop | A | 4 | -1.09 | -4.4 | 2 | -1.07 | -2.1 |
| swing_stop | B | 0 | +0.00 | +0.0 | 0 | +0.00 | +0.0 |
| min_stop | A | 10 | -0.60 | -6.0 | 18 | -0.45 | -8.1 |
| min_stop | B | 15 | -0.48 | -7.2 | 9 | -1.10 | -9.9 |
| tp1_cap | A | 10 | +0.07 | +0.7 | 20 | -0.54 | -10.8 |
| tp1_cap | B | 20 | -0.26 | -5.1 | 9 | -0.82 | -7.4 |
| cooldown | A | 11 | -0.10 | -1.1 | 15 | -0.65 | -9.8 |
| cooldown | B | 16 | -0.37 | -5.8 | 6 | -1.11 | -6.7 |
| combined | A | 4 | -1.09 | -4.4 | 2 | +0.16 | +0.3 |
| combined | B | 0 | +0.00 | +0.0 | 0 | +0.00 | +0.0 |

### By regime, tuning period (total R, trades)

| Regime | baseline | swing_stop | min_stop | tp1_cap | cooldown | combined |
|---|---|---|---|---|---|---|
| alt_season | +0.0 (0) | +0.0 (0) | +0.0 (0) | +0.0 (0) | +0.0 (0) | +0.0 (0) |
| btc_led | -6.0 (13) | -1.1 (1) | -7.4 (12) | -5.3 (17) | -7.1 (14) | -1.1 (1) |
| risk_off | +0.0 (0) | +0.0 (0) | +0.0 (0) | +0.0 (0) | +0.0 (0) | +0.0 (0) |
| capitulation | -0.7 (6) | +0.0 (0) | -0.7 (6) | +2.7 (6) | +0.4 (5) | +0.0 (0) |
| neutral | -0.3 (8) | -3.3 (3) | -5.1 (7) | -1.8 (7) | -0.3 (8) | -3.3 (3) |

### By regime, holdout (total R, trades)

| Regime | baseline | swing_stop | min_stop | tp1_cap | cooldown | combined |
|---|---|---|---|---|---|---|
| alt_season | -1.2 (3) | +0.0 (0) | -3.4 (5) | -3.4 (5) | -2.2 (2) | +0.0 (0) |
| btc_led | -4.9 (7) | +0.0 (0) | -4.9 (7) | -2.4 (7) | -5.5 (5) | +0.0 (0) |
| risk_off | +0.0 (0) | +0.0 (0) | +0.0 (0) | +0.0 (0) | +0.0 (0) | +0.0 (0) |
| capitulation | -1.2 (1) | +0.0 (0) | -1.2 (1) | -1.2 (1) | -1.2 (1) | +0.0 (0) |
| neutral | -11.1 (16) | -2.1 (2) | -8.6 (14) | -11.3 (16) | -7.5 (13) | +0.3 (2) |

### Outcomes and blocks (whole year)

| Variant | tp3 | breakeven | sl | expired | cancelled | L6 rejects: tight stop | blocked: cooldown | blocked: duplicate |
|---|---|---|---|---|---|---|---|---|
| baseline | 3 | 8 | 43 | 6 | 21 | 0 | 0 | 116 |
| swing_stop | 0 | 0 | 6 | 2 | 1 | 0 | 0 | 15 |
| min_stop | 3 | 6 | 43 | 4 | 19 | 2443 | 0 | 116 |
| tp1_cap | 4 | 11 | 44 | 1 | 30 | 0 | 0 | 123 |
| cooldown | 2 | 7 | 39 | 5 | 19 | 0 | 31 | 105 |
| combined | 0 | 1 | 5 | 2 | 1 | 241 | 0 | 15 |

**Choice on the tuning period:** `tp1_cap` had the best total net R (-4.4R over 30 trades, -0.15R per trade; baseline -6.9R). In the holdout it made -18.2R over 29 trades (-0.63R per trade, -7.8%).

## Details: `combined` (whole year)

Period: 2025-09-30 → 2026-09-30 (365 days), exchange candles from okx, universe of 69 coins.

| Group | Trades | Win rate | Avg R (net) | Total R (net) | Avg R (gross) | Profit factor | Max DD (R) | Return | Max DD |
|---|---|---|---|---|---|---|---|---|---|
| **All** | 6 | 16.7% | -0.68 | -4.1 | -0.58 | 0.27 | 5.5 | -2.3% | 3.0% |
| Grade A | 6 | 16.7% | -0.68 | -4.1 | -0.58 | 0.27 | 5.5 | -2.3% | 3.0% |
| Grade B | 0 | – | – | – | – | – | – | – | – |
| Long | 3 | 0.0% | -1.11 | -3.3 | -1.00 | 0.00 | 3.3 | -1.9% | 1.9% |
| Short | 3 | 33.3% | -0.24 | -0.7 | -0.17 | 0.67 | 2.2 | -0.4% | 1.1% |

By regime: btc_led 1 trade (−1.1R), neutral 5 trades (−3.0R, 20% win rate). Outcomes: 1 breakeven, 5 stops, 2 expired, 1 cancelled. Average stop distance 3.27% (median 3.33%); no stop was hit within 4 hours of the fill (baseline: 25 of 43). Average TP1 distance 2.8R. L6 grades over the year: A 19, B 5, Watch 186 (baseline: A 106, B 121); 241 hourly evaluations rejected for a stop under 0.8%.

| Opened | Coin | Side | Grade | Score | Regime | Order | Stop % | TP1 R | Outcome | Gross R | Net R | Hours |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 2026-03-29 | TRX | L | A | 80 | neutral | limit | 1.53 | 3.0 | sl | -1.00 | -1.15 | 26 |
| 2026-04-13 | DOT | S | A | 78 | neutral | limit | 3.91 | 3.0 | sl | -1.00 | -1.03 | 59 |
| 2026-04-30 | DOGE | L | A | 76 | neutral | limit | 7.07 | 2.7 | sl | -1.00 | -1.12 | 557 |
| 2026-05-05 | BTC | L | B | 73 | btc_led | limit | 2.60 | 2.4 | expired | +0.00 | +0.00 | - |
| 2026-05-08 | FIL | L | B | 74 | btc_led | limit | 9.67 | 3.0 | expired | +0.00 | +0.00 | - |
| 2026-05-10 | ETC | L | B | 66 | btc_led | limit | 1.79 | 2.2 | cancelled | +0.00 | +0.00 | - |
| 2026-05-10 | ALGO | L | A | 80 | btc_led | limit | 3.33 | 2.2 | sl | -1.00 | -1.06 | 7 |
| 2026-07-19 | ATOM | S | A | 86 | neutral | market | 1.68 | 3.0 | sl | -1.00 | -1.15 | 32 |
| 2026-07-29 | RENDER | S | A | 93 | neutral | limit | 2.08 | 3.0 | breakeven | +1.50 | +1.47 | 520 |

## Details: `baseline` (whole year)

Period: 2025-09-30 → 2026-09-30 (365 days), exchange candles from okx, universe of 68 coins.
Costs: taker fee 0.05% per side, slippage 0.05% per fill, funding every 8h (exchange history where available, otherwise 0.01% per interval, paid by longs).
Win rate = share of closed trades with net R > 0. Return and drawdown compound each trade's net R × its risk % (1% base × grade share × regime multipliers).

### Results

| Group | Trades | Win rate | Avg R (net) | Total R (net) | Avg R (gross) | Profit factor | Max DD (R) | Return | Max DD |
|---|---|---|---|---|---|---|---|---|---|
| **All** | 54 | 20.4% | -0.47 | -25.3 | -0.30 | 0.49 | 27.1 | -9.1% | 9.9% |
| Grade A | 30 | 23.3% | -0.36 | -10.7 | -0.18 | 0.59 | 12.5 | -4.2% | 5.6% |
| Grade B | 24 | 16.7% | -0.61 | -14.6 | -0.45 | 0.37 | 14.8 | -5.1% | 5.1% |
| Long | 38 | 18.4% | -0.52 | -19.9 | -0.35 | 0.44 | 23.0 | -7.4% | 8.3% |
| Short | 16 | 25.0% | -0.34 | -5.4 | -0.19 | 0.61 | 8.4 | -1.8% | 3.2% |

### By regime at signal time

| Group | Trades | Win rate | Avg R (net) | Total R (net) | Avg R (gross) | Profit factor | Max DD (R) | Return | Max DD |
|---|---|---|---|---|---|---|---|---|---|
| alt_season | 3 | 33.3% | -0.40 | -1.2 | -0.31 | 0.46 | 1.2 | -0.7% | 1.1% |
| alt_season – A | 2 | 50.0% | -0.06 | -0.1 | +0.04 | 0.90 | 1.1 | -0.1% | 1.1% |
| alt_season – B | 1 | 0.0% | -1.09 | -1.1 | -1.00 | 0.00 | 1.1 | -0.5% | 0.5% |
| btc_led | 20 | 20.0% | -0.54 | -10.8 | -0.38 | 0.42 | 13.9 | -3.6% | 5.2% |
| btc_led – A | 3 | 33.3% | -0.20 | -0.6 | -0.08 | 0.74 | 2.3 | -0.6% | 2.3% |
| btc_led – B | 17 | 17.6% | -0.60 | -10.3 | -0.43 | 0.38 | 12.7 | -3.0% | 3.6% |
| risk_off | 0 | – | – | – | – | – | – | – | – |
| capitulation | 7 | 28.6% | -0.27 | -1.9 | -0.13 | 0.67 | 3.1 | -0.3% | 1.2% |
| capitulation – A | 1 | 100.0% | +1.40 | +1.4 | +1.57 | ∞ | 0.0 | +1.4% | 0.0% |
| capitulation – B | 6 | 16.7% | -0.55 | -3.3 | -0.42 | 0.42 | 4.5 | -1.6% | 2.2% |
| neutral | 24 | 16.7% | -0.47 | -11.4 | -0.29 | 0.50 | 14.0 | -4.8% | 6.6% |
| neutral – A | 24 | 16.7% | -0.47 | -11.4 | -0.29 | 0.50 | 14.0 | -4.8% | 6.6% |

Time spent in each regime: alt_season 5%, btc_led 12%, risk_off 1%, capitulation 13%, neutral 69%.

### Signal outcomes

| Outcome | Count |
|---|---|
| tp3 | 3 |
| breakeven | 8 |
| sl | 43 |
| expired | 6 |
| cancelled | 21 |

### Average cost per closed trade (R)

Fees 0.074 · slippage 0.074 · funding 0.019

### Net R by month

| Month | Net R |
|---|---|
| 2025-11 | -1.0 |
| 2026-02 | -2.3 |
| 2026-03 | -1.2 |
| 2026-04 | -0.6 |
| 2026-05 | -2.3 |
| 2026-06 | +0.6 |
| 2026-07 | -5.9 |
| 2026-08 | -4.6 |
| 2026-09 | -7.9 |

### Diagnostics

- Stops: 43 of 54 closed trades; 25 of those within 4 hours of the fill.
- Average stop distance 2.06% (median 1.88%).
- Average TP1 distance 5.8R.
- Entries: 22 market, 32 limit.

### Trades

| Opened | Coin | Side | Grade | Score | Regime | Order | Stop % | TP1 R | Outcome | Gross R | Net R | Hours |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 2025-10-30 | PEPE | S | A | 76 | neutral | limit | 1.42 | 3.4 | cancelled | +0.00 | +0.00 | - |
| 2025-11-03 | DOGE | S | A | 78 | neutral | limit | 0.93 | 5.0 | cancelled | +0.00 | +0.00 | - |
| 2025-11-03 | ETH | S | A | 81 | neutral | limit | 1.61 | 2.8 | cancelled | +0.00 | +0.00 | - |
| 2025-11-04 | ETH | S | A | 78 | neutral | market | 3.27 | 3.4 | sl | -1.00 | -1.03 | 82 |
| 2026-02-21 | ETHFI | S | A | 78 | capitulation | limit | 1.22 | 3.7 | cancelled | +0.00 | +0.00 | - |
| 2026-02-22 | WLD | S | A | 76 | capitulation | limit | 1.46 | 2.6 | expired | +0.00 | +0.00 | - |
| 2026-02-22 | ETHFI | S | B | 66 | capitulation | limit | 1.09 | 9.6 | sl | -1.00 | -1.16 | 18 |
| 2026-02-23 | WLD | S | B | 73 | capitulation | limit | 1.23 | 2.3 | sl | -1.00 | -1.16 | 3 |
| 2026-03-29 | TRX | L | A | 80 | neutral | limit | 0.85 | 9.7 | sl | -1.00 | -1.23 | 2 |
| 2026-04-09 | POL | S | A | 78 | neutral | market | 1.14 | 3.4 | breakeven | +1.70 | +1.64 | 181 |
| 2026-04-13 | DOT | S | A | 78 | neutral | limit | 1.25 | 13.3 | sl | -1.00 | -1.16 | 3 |
| 2026-04-14 | DOT | S | A | 86 | neutral | limit | 1.39 | 12.0 | expired | +0.00 | +0.00 | - |
| 2026-04-23 | GRASS | L | B | 66 | btc_led | limit | 3.91 | 2.8 | breakeven | +2.54 | +2.49 | 22 |
| 2026-04-24 | PYTH | L | B | 66 | btc_led | limit | 2.34 | 3.0 | sl | -1.00 | -1.09 | 11 |
| 2026-04-27 | ETH | L | B | 68 | btc_led | market | 0.60 | 4.6 | sl | -1.00 | -1.35 | 2 |
| 2026-04-28 | AERO | L | B | 72 | btc_led | limit | 1.69 | 2.7 | cancelled | +0.00 | +0.00 | - |
| 2026-04-28 | AERO | L | B | 65 | btc_led | limit | 1.71 | 2.6 | cancelled | +0.00 | +0.00 | - |
| 2026-04-29 | AERO | L | B | 71 | btc_led | limit | 1.68 | 2.0 | sl | -1.00 | -1.12 | 2 |
| 2026-04-30 | DOGE | L | A | 76 | neutral | limit | 5.87 | 3.3 | sl | -1.00 | -1.15 | 557 |
| 2026-04-30 | DOT | S | A | 76 | neutral | limit | 1.40 | 3.9 | sl | -1.00 | -1.12 | 31 |
| 2026-05-02 | TRX | L | B | 66 | btc_led | limit | 0.24 | 7.0 | sl | -1.00 | -1.90 | 15 |
| 2026-05-04 | ETH | L | B | 66 | btc_led | limit | 0.68 | 2.7 | breakeven | +2.83 | +2.47 | 51 |
| 2026-05-05 | BTC | L | B | 73 | btc_led | limit | 0.59 | 10.5 | expired | +0.00 | +0.00 | - |
| 2026-05-06 | VIRTUAL | L | B | 68 | btc_led | market | 1.72 | 3.3 | sl | -1.00 | -1.12 | 1 |
| 2026-05-08 | ONDO | L | B | 65 | btc_led | limit | 2.00 | 7.2 | cancelled | +0.00 | +0.00 | - |
| 2026-05-08 | ALGO | L | A | 75 | btc_led | limit | 1.66 | 8.0 | sl | -1.00 | -1.17 | 68 |
| 2026-05-08 | VIRTUAL | L | B | 66 | btc_led | limit | 2.10 | 2.6 | breakeven | +1.32 | +1.21 | 35 |
| 2026-05-08 | ONDO | L | B | 72 | btc_led | market | 2.44 | 4.3 | sl | -1.00 | -1.08 | 0 |
| 2026-05-08 | FIL | L | B | 74 | btc_led | limit | 2.23 | 13.2 | expired | +0.00 | +0.00 | - |
| 2026-05-09 | ONDO | L | B | 65 | btc_led | limit | 2.27 | 4.9 | sl | -1.00 | -1.09 | 3 |
| 2026-05-09 | ETC | L | B | 71 | btc_led | limit | 0.96 | 5.4 | expired | +0.00 | +0.00 | - |
| 2026-05-10 | ARB | L | B | 66 | btc_led | limit | 2.03 | 3.6 | expired | +0.00 | +0.00 | - |
| 2026-05-10 | VIRTUAL | L | B | 66 | btc_led | limit | 1.69 | 3.3 | sl | -1.00 | -1.12 | 1 |
| 2026-05-10 | VIRTUAL | L | B | 68 | btc_led | limit | 1.63 | 3.4 | sl | -1.00 | -1.12 | 0 |
| 2026-05-17 | TRX | L | A | 78 | neutral | market | 0.34 | 12.6 | breakeven | +6.29 | +4.87 | 265 |
| 2026-06-01 | INJ | L | A | 86 | neutral | market | 2.70 | 2.2 | sl | -1.00 | -1.07 | 4 |
| 2026-06-23 | PENGU | S | B | 71 | capitulation | market | 1.83 | 2.6 | tp3 | +2.50 | +2.42 | 79 |
| 2026-06-24 | DOT | S | A | 93 | capitulation | limit | 1.16 | 3.1 | breakeven | +1.57 | +1.40 | 4 |
| 2026-06-25 | APT | S | B | 66 | capitulation | limit | 1.98 | 2.1 | cancelled | +0.00 | +0.00 | - |
| 2026-06-26 | APT | S | B | 68 | capitulation | market | 2.38 | 2.4 | sl | -1.00 | -1.08 | 4 |
| 2026-06-26 | APT | S | B | 66 | capitulation | market | 1.99 | 3.1 | sl | -1.00 | -1.10 | 4 |
| 2026-07-07 | PYTH | L | A | 83 | neutral | market | 3.78 | 2.4 | sl | -1.00 | -1.05 | 4 |
| 2026-07-16 | PI | S | A | 75 | neutral | limit | 2.96 | 2.1 | sl | -1.00 | -1.07 | 2 |
| 2026-07-19 | ATOM | S | A | 86 | neutral | market | 0.96 | 33.7 | sl | -1.00 | -1.26 | 28 |
| 2026-07-28 | UNI | L | A | 83 | neutral | limit | 1.19 | 2.5 | cancelled | +0.00 | +0.00 | - |
| 2026-07-29 | RENDER | S | A | 93 | neutral | limit | 0.82 | 35.2 | sl | -1.00 | -1.24 | 1 |
| 2026-07-29 | RENDER | S | A | 78 | neutral | limit | 0.83 | 35.0 | sl | -1.00 | -1.24 | 11 |
| 2026-07-30 | UNI | L | A | 82 | neutral | limit | 2.64 | 2.5 | cancelled | +0.00 | +0.00 | - |
| 2026-08-01 | UNI | L | A | 80 | neutral | limit | 2.90 | 2.1 | sl | -1.00 | -1.07 | 1 |
| 2026-08-01 | PUMP | L | A | 80 | neutral | limit | 3.51 | 3.5 | sl | -1.00 | -1.06 | 6 |
| 2026-08-02 | PUMP | L | A | 80 | neutral | limit | 3.60 | 3.4 | sl | -1.00 | -1.06 | 4 |
| 2026-08-11 | AERO | S | B | 73 | capitulation | limit | 0.93 | 2.0 | sl | -1.00 | -1.21 | 17 |
| 2026-08-16 | FIL | S | A | 83 | neutral | limit | 0.88 | 3.2 | tp3 | +3.15 | +2.94 | 79 |
| 2026-08-20 | PUMP | L | A | 84 | neutral | limit | 2.33 | 5.1 | cancelled | +0.00 | +0.00 | - |
| 2026-08-22 | LINK | L | B | 70 | btc_led | market | 2.73 | 4.7 | sl | -1.00 | -1.08 | 16 |
| 2026-08-23 | PUMP | L | B | 68 | btc_led | limit | 3.33 | 3.0 | sl | -1.00 | -1.06 | 7 |
| 2026-08-25 | PUMP | L | B | 66 | btc_led | limit | 3.42 | 2.5 | sl | -1.00 | -1.06 | 6 |
| 2026-09-03 | AAVE | L | B | 73 | btc_led | market | 1.94 | 6.0 | sl | -1.00 | -1.13 | 124 |
| 2026-09-03 | CRV | L | A | 76 | btc_led | market | 1.80 | 2.2 | sl | -1.00 | -1.11 | 0 |
| 2026-09-04 | CRV | L | A | 75 | btc_led | limit | 4.03 | 2.1 | tp3 | +1.76 | +1.70 | 96 |
| 2026-09-07 | ONDO | L | B | 68 | btc_led | limit | 1.11 | 2.6 | cancelled | +0.00 | +0.00 | - |
| 2026-09-07 | TAO | L | B | 66 | btc_led | market | 1.76 | 6.6 | sl | -1.00 | -1.11 | 5 |
| 2026-09-07 | ONDO | L | B | 68 | btc_led | limit | 1.38 | 2.3 | cancelled | +0.00 | +0.00 | - |
| 2026-09-08 | BNB | L | A | 83 | neutral | limit | 0.67 | 2.1 | cancelled | +0.00 | +0.00 | - |
| 2026-09-08 | BNB | L | A | 75 | neutral | market | 0.74 | 3.3 | sl | -1.00 | -1.27 | 0 |
| 2026-09-08 | BNB | L | A | 83 | neutral | limit | 0.67 | 2.1 | cancelled | +0.00 | +0.00 | - |
| 2026-09-08 | BNB | L | A | 80 | neutral | limit | 0.69 | 3.6 | sl | -1.00 | -1.29 | 2 |
| 2026-09-11 | DOT | L | A | 76 | neutral | limit | 1.86 | 3.3 | cancelled | +0.00 | +0.00 | - |
| 2026-09-14 | INJ | L | A | 76 | neutral | market | 1.88 | 2.7 | sl | -1.00 | -1.10 | 5 |
| 2026-09-20 | ARB | L | A | 83 | neutral | limit | 2.92 | 3.6 | sl | -1.00 | -1.07 | 0 |
| 2026-09-20 | NEAR | L | A | 78 | neutral | limit | 2.41 | 4.0 | cancelled | +0.00 | +0.00 | - |
| 2026-09-21 | ARB | L | B | 70 | alt_season | limit | 3.04 | 3.5 | cancelled | +0.00 | +0.00 | - |
| 2026-09-21 | ATOM | L | A | 76 | alt_season | market | 1.34 | 2.4 | sl | -1.00 | -1.15 | 20 |
| 2026-09-21 | UNI | L | B | 65 | alt_season | limit | 2.23 | 2.6 | cancelled | +0.00 | +0.00 | - |
| 2026-09-21 | UNI | L | B | 65 | alt_season | market | 2.17 | 2.5 | sl | -1.00 | -1.09 | 1 |
| 2026-09-21 | ARB | L | A | 85 | alt_season | market | 3.88 | 2.2 | breakeven | +1.08 | +1.03 | 3 |
| 2026-09-23 | ARB | L | A | 85 | neutral | limit | 3.67 | 2.3 | cancelled | +0.00 | +0.00 | - |
| 2026-09-23 | ARB | L | A | 78 | neutral | market | 2.99 | 6.9 | sl | -1.00 | -1.07 | 4 |
| 2026-09-25 | UNI | L | A | 75 | neutral | limit | 2.27 | 2.3 | breakeven | +1.95 | +1.84 | 49 |
| 2026-09-25 | RAY | L | A | 80 | neutral | limit | 2.85 | 6.2 | sl | -1.00 | -1.07 | 1 |
| 2026-09-27 | NEAR | L | A | 78 | alt_season | limit | 2.55 | 2.2 | cancelled | +0.00 | +0.00 | - |

### Funnel activity

4H funnel runs: 2183, shortlist entries: 7283, L6 evaluations: 27937. Grades: A 106, B 121, Watch 1543. Signals opened: 81. Blocked by the book: correlated 3, duplicate 116, loss_streak_pause 25, max_active 2.

## Assumptions and limits

- **Universe:** today's top-100 (plus the coins that were tradable on the exchange), applied to the whole year. Coins that dropped out of the top 100 during the year are missing (survivorship bias); coins listed during the year enter when their candles start.
- **Dominance:** USDT.D, BTC.D and TOTAL2 come from CoinGecko daily market caps of the top 100 (the free tier allows 365 days). The regime therefore uses the D direction only (no 4H snapshots in the past), and during the first ~100 days MA99 is still warming up, so the direction leans on Dow structure.
- **Liquidity and categories:** 24h volume and 7-day/30-day category growth use CoinGecko daily history; coin categories are today's. Spreads are unknown historically, so the spread part of the liquidity score is not given.
- **Open interest:** exchanges keep little OI history, so OI_BUILDUP never fires in the backtest. Trending coins aren't known historically either.
- **Funding:** exchange funding history is used where it exists (OKX keeps a few months); older periods use the default rate. FUNDING_EXTREME and the funding penalty only see the stored history.
- **Execution:** trades are managed on 1H candles (live uses 15m). When one candle touches both the stop and a target, the stop counts. Limit entries fill at the entry price; every fill pays the taker fee and slippage (conservative for limit orders).
- **Reproducible:** the result depends only on the stored candles, the CoinGecko history in the backtest database and `config.yaml`.
