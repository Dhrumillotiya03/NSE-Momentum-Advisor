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
│   ├── price_data/       ← ~500 stock daily OHLCV CSVs (yfinance, broad Nifty200+500
│   │                        universe — kept for market breadth). Actual TRADING
│   │                        universe is gated at read-time to ~200 F&O-liquid names
│   │                        (see Strategy section below) — don't prune this directory.
│   ├── delivery_data/     ← NSE bhavcopy delivery % per stock (from download_bhavcopy.py)
│   ├── index_data/       ← nifty50.csv
│   ├── sectors.json      ← MANUALLY curated sector map — this is the trusted source,
│   │                        NOT yfinance (yfinance miscategorizes RELIANCE as IT)
│   ├── portfolio_state.json
│   └── sr_daily_log.csv  ← daily S/R snapshot log, stock-wise grouped
└── scripts/               ← everything runs from here

## Live ops / books (2026-07-12 rework — deployment accounting)
- `record_fill.py` is the ONLY writer of portfolio_state.json + trade_history.csv.
  Workflow: exit_engine.py / full_advisor.py print SIGNALS only (they never touch
  the books) → user executes on Zerodha → `python record_fill.py buy|sell SYM ...`
  records the actual fill, updating positions/avg-entry/cash/realized-P&L and the
  journal atomically, with a duplicate-fill guard. Never journal a signal as a trade
  — that's what corrupted the books before the rework.
- Books were reset 2026-07-12 to a clean baseline (cash ₹10L, no positions) — user
  confirmed everything prior was simulation, no real Zerodha positions exist yet.
  Pre-reset originals archived in data/_quarantine/*_pre_cleanup_2026-07-12.*.
- -18% stop watch is automated: cron weekdays 14:40 IST runs
  scripts/run_exit_check.sh (logs to data/exit_check.log, notify-send on SELL
  signal); run_daily_log.sh also runs exit_engine.py each evening as a backstop.
- Deployment expectation-setting: hard monthly close ⇒ ALL gains are short-term
  capital gains (20%); realistic net CAGR ≈ gross − STCG drag (≈19.6% gross →
  ~15-16% net; run scripts/research_net_returns.py for the current table).
  COST=0.001/side ≈ Zerodha delivery cost stack, excludes slippage.
- SLIPPAGE (2026-07-12, research_slippage.py): COST never modeled market
  impact. Square-root impact model against real historical turnover: at
  current ₹10L capital, median order is 0.01% of 20d ADV — small but a
  representative K=5-20 impact still erodes CAGR 0.7-2.7pp / Sharpe
  0.03-0.13 when folded in, a real secondary drag COST alone misses.
  %ADV consumed scales ~linearly with capital, so at 50x current capital
  (₹5Cr) the same impact model erodes returns much more severely —
  slippage becomes a first-order CAPACITY constraint, not just a cost
  line, well before the strategy could scale far past ₹10L-1Cr. NOT
  folded into production COST (K isn't NSE-calibrated, would launder an
  assumption into a validated number) — re-run before any material
  capital increase.

## Strategy (currently backtested, don't casually re-tune without re-running full backtest)
- Cash equity only (no derivatives), vol-adjusted momentum, monthly rebalance (21 trading days).
- TRADING UNIVERSE (2026-07 migration): gated to F&O-liquid names only — top
  UNIVERSE_TOP_N (~200) of the broad Nifty200+500 pool by trailing 20d median
  turnover (Close x Volume), recomputed point-in-time at every rebalance date
  (see core.liquid_universe() / backtest_portfolio.liquid_symbols_at()). This is
  a LIQUIDITY PROXY, not a static F&O symbol list — deliberately survivorship-bias-
  free (a fixed F&O roster applied backward across 2015-2026 would inflate
  backtested returns, since NSE's F&O list didn't look like today's in 2015).
  Market regime/breadth still reads the FULL broad universe — the gate applies
  only to what gets bought, not how the market is read. See memory
  fno-universe-migration for the full rationale.
- 3-layer: (1) Nifty50 regime filter Bull/Sideways/Bear (breadth-gated, broad
  universe) (2) momentum scoring on the F&O-gated universe (3) inverse-vol
  position sizing with regime-based exposure.
- Params (all in scripts/strategy_config.py, the single source of truth): 126-day
  lookback, 50-day MA filter, RSI 80 overbought cap (advisory), -18% catastrophic
  stop only (no tight intra-hold exits — validated to cost Sharpe/CAGR by
  whipsawing out of momentum trends), 20% max position, sector cap 2/sector
  (enforced in backtest_portfolio.select_top_n_capped — was documented but NOT
  actually enforced in code until the F&O migration surfaced up to 8/10 names in
  one sector during BULL rebalances). REGIME_NAMES SIDEWAYS=3 (was 6, changed
  2026-07 after a walk-forward study found 6 names diluted the few genuinely
  good SIDEWAYS setups — 3 names beats/matches 6 in 16/19 windows; 2 names was
  tested and rejected for worse tail risk, don't re-tune below 3).
  REGIME_NAMES BEAR=4 (was 2, changed 2026-07): BEAR=2 was never
  independently tested and MAX_WEIGHT=0.20 turned out to be a dead letter
  at n=2 — the cap-then-renormalize logic has no 3rd+ name to push excess
  weight into, so 100% of historical BEAR rebalances ended up at ~50/50
  weighting, not ≤20%. BEAR=4 restores the cap to 96% functional and roughly
  halves single-name gap/halt exposure (5.6-13.1% of total capital at n=2
  vs 3.4-8.0% at n=4) for a negligible, mixed-sign return cost. See memory
  concentration-risk-2026-07.
- HARD MONTHLY CLOSE ⇒ LAGGARDS-ONLY REBALANCE (2026-07-12, was: sell
  EVERYTHING at month-end, re-buy next session): the book is fully
  RE-EVALUATED every month-end (exit_engine.py / run_backtest_laggards_only)
  — user mandate is no INTER-month drift (a position silently carried 2-3
  months with no review), NOT a forced full liquidation. A name still in
  the new sector-capped top-N is HELD (rebalanced to its new target weight
  only, cost on the delta, no realized gain/tax event); a name dropping out
  is SOLD; new names are bought next session. Early intra-month exits
  beyond the -18% stop are still not built in (both tested price-based
  variants lost in walk-forward; non-price overlays on holdings tested via
  corporate announcements 2026-07-12 and REJECTED — see memory
  exit-announcements-rejected). Adopting laggards-only itself: costs ~0.8pp
  gross CAGR (fewer round-trips lose a little raw return) but SAVES ~3pp/yr
  NET CAGR — fewer taxable events, NOT LTCG conversion (that barely fires:
  momentum's own turnover displaces names from top-N well before 365 days).
  See memory monthly-close-cost-2026-07 and trading-mandate-constraints.
- REGIME_EXPOSURE boosted 2026-07-12 (user decision after VIX-overlay study's
  control run — a risk-appetite dial, NOT alpha): BULL 1.0 / SIDEWAYS 0.75 /
  BEAR 0.375 / UNKNOWN 0.75 (old values x1.25 capped at 1.0, no leverage).
- Current backtest (F&O-gated universe, SIDEWAYS=3, BEAR=4, boosted
  exposure, LAGGARDS-ONLY engine — run_backtest_laggards_only is now the
  production default in backtest_portfolio.main()/walk_forward.py, full
  2015-2026 history): 16.86% CAGR, Sharpe 0.80, max DD 40.5%. Walk-forward
  (19 overlapping 3y windows): mean CAGR 24.7%, median 23.9%, mean Sharpe
  1.10, 17/19 windows Sharpe-positive. Post-tax net ≈ 13.7% CAGR at
  10bps/side (research_net_returns.py — this script's tax model
  slightly OVERSTATES drag under laggards-only since it assumes every
  period fully realizes; treat as conservative). Legacy hard-close engine
  (run_backtest / --engine hard_close) kept for comparison: 16.24%/0.78/
  40.6% full-history. Trust the walk-forward distribution (python
  walk_forward.py), not any single backtest run — see memory
  feedback-quant-researcher-role for why.
- SURVIVORSHIP AUDIT (2026-07-12, research_survivorship.py): price_data is
  built from TODAY'S index membership, so 2015-2026 departures were absent.
  A 34-name heavyweight departure cohort (HDFC, CAIRN, MINDTREE, DHFL, PSU
  banks, KWALITY... rebuilt from NSE bhavcopy archives via
  download_delisted.py into data/price_data_delisted/ — NEVER merge into
  price_data, the live pipeline must not see dead names) was spliced in:
  the extended panel performs BETTER (+1.6pp full-history, +1.1pp wf mean),
  because among top-200-liquid names the dominant departure mode is mergers
  of good companies (HDFC/MINDTREE/GRUH), not fraud deaths, and the entry
  filters (double momentum + 50MA) never select a name in its death spiral
  (worst dead pick: -22%, stop-truncated). The edge is NOT a survivorship
  artifact; survivor-panel numbers are, if anything, slightly conservative.

## S/R subsystem (separate, already tuned — don't touch unless directly asked)
- `support_resistance.py` — multi-timeframe (monthly+weekly+daily) swing pivots +
  volume profile + wick rejection + delivery volume scoring + reach probability
- Backtested via `sr_backtest.py` (fast, uses `fast=True` flag — REQUIRED or it hangs,
  wick_rejection_score becomes iterrows-based without it) and `sr_backtest_filtered.py`
  (only tests on trend+momentum+regime-filtered conditions)
- Current accuracy: ~65-68% combined S/R hit rate on 21-day forward window — this is
  close to the practical ceiling for price-only daily S/R, don't chase higher without
  a clear new hypothesis
- `sr_daily_logger.py` logs daily S1/R1 always, S2/R2 only if reach-prob beats the
  empirical base rate (~66%, from sr_reach_table.json). WATCHLIST is a FIXED
  hardcoded validation panel — same stocks every day so sr_monthend_analysis.py
  measures accuracy on a consistent panel; do NOT make it dynamic. Runs via cron
  weekdays 6pm (scripts/run_daily_log.sh)
- `sr_dynamic_logger.py` — SEPARATE dynamic watchlist (holdings + top-10 F&O-gated
  momentum) logging to sr_dynamic_log.csv, for extra forward calibration data on
  deployment-relevant names. Never merge it with the fixed panel above. Analyse via
  `python sr_monthend_analysis.py --log ../data/sr_dynamic_log.csv`
- ETF data (GOLDBEES) lives in `data/etf_data/` (download_etf.py), NOT price_data/ —
  price_data is globbed as the universe by core.market_breadth_pct/liquid_universe,
  and a high-turnover ETF there would enter the tradable top-200 and could get bought
  by the strategy. support_resistance.load_stock and sr_monthend_analysis fall back
  to etf_data/ automatically
- `sr_monthend_analysis.py` checks hit-rate, level drift, probability calibration,
  n-sensitivity, distance-vs-accuracy — run only after 2-3+ weeks of logged data

## Known gotchas — do not rediscover these the hard way
- yfinance miscategorizes RELIANCE as IT sector — always trust sectors.json
- Newer yfinance returns MultiIndex columns; naive df.to_csv writes a second
  ",^NSEI,^NSEI,..." header row whose Date parses as NaT. In nifty50.csv this made
  exit_engine's is_last_trading_day_of_month() TRUE every day (NaT sorts last,
  NaT.month != NaT.month) → false month-end liquidation signals mid-month (July
  2026). Fixed: download_index.py flattens columns (download_data/download_etf
  already did); raw Date readers hardened with errors="coerce"+dropna
- Midcap150 was tried and removed — caused survivorship bias, don't re-add
- ADANIENT.NS.NS.csv-style double-suffix bugs have happened before in build_universe.py —
  watch for `.NS.NS` when adding new download logic
- get_levels() in support_resistance.py MUST be called with fast=True in any backtest
  loop over many stocks/dates, or wick_rejection_score's iterrows path makes it hang
- Ollama must be running locally (port 11434) before running ai_assistant.py (needs a
  tool-calling-capable model, e.g. qwen2.5 — llama3 does NOT support tool-calling)
- RCOM is a dead/delisted stock in the portfolio (~₹0.92) — exclude from any S/R or
  accuracy analysis, it's a write-off not a trading position
- Don't apply today's F&O symbol list backward across historical backtests — that's
  survivorship bias (NSE's F&O roster changes ~2x/year). Use the point-in-time
  liquidity proxy (core.liquid_universe / backtest_portfolio.liquid_symbols_at)
  instead — see memory fno-universe-migration
- A truncated/interrupted download write can leave a malformed last CSV row
  (e.g. SUNDARMFIN.NS.csv had a row missing its Date field) that parse_dates
  silently fails on WITHOUT producing NaT — it survives as a literal string in
  the index and can break anything that sorts by date. core.load_stock and
  backtest_portfolio.load_price_matrix now guard against this explicitly
  (pd.to_datetime(..., errors="coerce") + filter); other CSV loaders in the
  repo don't yet — if you hit a sort_index TypeError, check for this first
- backtest_portfolio's scoring used to require a valid price at i+HOLD — a
  LOOKAHEAD (peeks 21 days ahead; systematically excludes names that stop
  trading mid-hold). Removed 2026-07-12; was worth ~+0.4pp phantom CAGR.
  simulate_position_exit also treated a terminated series as "money back"
  (return 0.0) — now exits at last traded price. If other scripts replicate
  the old scoring block (confidence_table.py etc.), check them for the same
  price_exit lookahead before trusting their numbers
- UNIVERSE_TOP_N (~200) is anchored to NSE's real F&O roster size, NOT tuned for
  backtest performance — sensitivity to this number is noisy/non-monotonic
  (150→13.8% CAGR, 200→17.0%, 250→14.4%, 300→17.0%), so don't re-tune it off
  backtest results, that's curve-fitting
- sectors.json sector classifications for the 136 F&O names added in the 2026-07
  migration were filled in from general knowledge, not verified against a live
  authoritative source — good enough for the diversification cap, worth a manual
  skim eventually

## My preferences
- Direct, no filler. Exact file names + line numbers, not vague descriptions.
- Show current code before showing replacement.
- Don't explain "why this is best practice" unless I ask — I want the change, not the lecture.
- When testing changes to the S/R or strategy engine: one variable at a time, re-run
  backtest, report before/after numbers, revert if it doesn't help. Don't bundle
  multiple untested changes together.
- Flag anything that risks overfitting to my specific 3-year backtest window before
  I implement it.