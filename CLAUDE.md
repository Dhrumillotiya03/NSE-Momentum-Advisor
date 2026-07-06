# stock_ai — Project Context

## What this is
Quant momentum trading system for NSE India. Not a toy project — this is being
prepared for real capital deployment via Zerodha. Side income goal, not day trading —
decisions made weekly/monthly.

## Machine / environment
- Ubuntu, Anaconda `base` env, Python
- ALL scripts must be run from `scripts/` — they use relative paths (`../data/`)
- Never run scripts from repo root or anywhere else, paths will break

## Structure
stock_ai/
├── config.yaml
├── data/
│   ├── price_data/       ← 472 stock daily OHLCV CSVs (yfinance)
│   ├── delivery_data/     ← NSE bhavcopy delivery % per stock (from download_bhavcopy.py)
│   ├── index_data/       ← nifty50.csv
│   ├── sectors.json      ← MANUALLY curated sector map — this is the trusted source,
│   │                        NOT yfinance (yfinance miscategorizes RELIANCE as IT)
│   ├── portfolio_state.json
│   └── sr_daily_log.csv  ← daily S/R snapshot log, stock-wise grouped
└── scripts/               ← everything runs from here

## Strategy (currently backtested, don't casually re-tune without re-running full backtest)
- Vol-adjusted momentum, monthly rebalance (21 trading days), universe = Nifty200+500 (472 stocks)
- 3-layer: (1) Nifty50 regime filter Bull/Sideways/Bear (2) momentum scoring (3) inverse-vol
  position sizing with regime-based exposure
- Params: 126-day lookback, 50-day MA filter (recently reverted from 20d), RSI 75 overbought
  cap, 7% hard stop, 10% trailing stop from 20d high, 20% max position, sector cap 2/sector
- Current backtest: 23.2% annual return, Sharpe 0.87, max DD 10.3%, 3yr total return 62.71%

## S/R subsystem (separate, already tuned — don't touch unless directly asked)
- `support_resistance.py` — multi-timeframe (monthly+weekly+daily) swing pivots +
  volume profile + wick rejection + delivery volume scoring + reach probability
- Backtested via `sr_backtest.py` (fast, uses `fast=True` flag — REQUIRED or it hangs,
  wick_rejection_score becomes iterrows-based without it) and `sr_backtest_filtered.py`
  (only tests on trend+momentum+regime-filtered conditions)
- Current accuracy: ~65-68% combined S/R hit rate on 21-day forward window — this is
  close to the practical ceiling for price-only daily S/R, don't chase higher without
  a clear new hypothesis
- `sr_daily_logger.py` logs daily S1/R1 always, S2/R2 only if reach-prob >50%
- `sr_monthend_analysis.py` checks hit-rate, level drift, probability calibration,
  n-sensitivity, distance-vs-accuracy — run only after 2-3+ weeks of logged data

## Known gotchas — do not rediscover these the hard way
- yfinance miscategorizes RELIANCE as IT sector — always trust sectors.json
- Midcap150 was tried and removed — caused survivorship bias, don't re-add
- ADANIENT.NS.NS.csv-style double-suffix bugs have happened before in build_universe.py —
  watch for `.NS.NS` when adding new download logic
- get_levels() in support_resistance.py MUST be called with fast=True in any backtest
  loop over many stocks/dates, or wick_rejection_score's iterrows path makes it hang
- Ollama must be running locally (port 11434) before running ai_strategist.py
- RCOM is a dead/delisted stock in the portfolio (~₹0.92) — exclude from any S/R or
  accuracy analysis, it's a write-off not a trading position

## My preferences
- Direct, no filler. Exact file names + line numbers, not vague descriptions.
- Show current code before showing replacement.
- Don't explain "why this is best practice" unless I ask — I want the change, not the lecture.
- When testing changes to the S/R or strategy engine: one variable at a time, re-run
  backtest, report before/after numbers, revert if it doesn't help. Don't bundle
  multiple untested changes together.
- Flag anything that risks overfitting to my specific 3-year backtest window before
  I implement it.