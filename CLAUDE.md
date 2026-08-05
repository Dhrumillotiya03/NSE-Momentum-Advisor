# stock_ai — Project Context

## What this is
Quant momentum ADVISORY system for NSE India. Not a toy project — this is being
prepared for real capital deployment via Zerodha. Side income goal, not day trading —
decisions made weekly/monthly.

PRIMARY USE (clarified 2026-08-01, supersedes the portfolio-manager framing):
this is a SIGNAL/ADVICE engine, not a portfolio manager. The questions it must
answer well are (1) which stocks to buy right now, (2) at what price, (3) when
to exit a named stock — INCLUDING stocks the system does not track — and (4)
what to do over a stated horizon, with chart/level analysis. The user's real
Zerodha book, capital figure and cash balance are NOT the focus; do not build
around them or nag about them. NO SLEEVES: gold (GOLDBEES) and international
(MON100) were diversification features for a portfolio-manager framing the user
does not want — GOLD_ALLOC/INTL_ALLOC stay 0.0 and the LLM must never
recommend them (sleeve_status is deliberately NOT in the assistant's toolset).

## Live data (2026-08-03 — Kite Connect, first paid dependency)
User is now open to reacting faster on existing swing positions (NOT a new
same-day-trading strategy) and wanted TRUE live/real-time NSE quotes, not
yfinance's ~15-min delay. Added Zerodha Kite Connect (₹500/month) — this
SUPERSEDES the "zero-cost tooling only" mandate in memory
trading-mandate-constraints for price DATA specifically (strategy logic,
universe, everything else stays as before; no automated order placement
was added — record_fill.py's signals-only rule is unchanged).
- **The Kite Connect account belongs to a COLLABORATOR, not the user** —
  his Zerodha login, his ₹500/month subscription. His ACCOUNT PASSWORD is
  deliberately never stored anywhere in this system: the one-time browser
  login (`kite_auth.py login`) is designed so he types it directly into
  Zerodha's own page, never into any script or file here.
- Credentials live in `data/secrets/` (api key/secret, TOTP secret, cached
  daily access token) — `600` perms, directory `700`, covered by
  `.gitignore`'s `data/` rule (verify with `git check-ignore -v
  data/secrets/*` before ever touching this directory) — the repo's history
  of accidentally publishing personal data (see memory
  repo-public-data-exposure) makes this a real risk, not a formality.
- `kite_auth.py` — SEMI-automated daily refresh (2026-08-03), since Kite's
  `request_token` can only come from an actual browser login and Zerodha
  provides no programmatic password-login endpoint. `python kite_auth.py
  refresh` (the recommended daily command) opens the login page in a
  browser automatically, prints a fresh TOTP code (generated from the
  stored secret via `pyotp`) right when needed, then waits for the
  collaborator/user to paste back the `request_token` from the redirected
  URL and exchanges it immediately — about 30 seconds of manual work
  (Client ID + his password, typed live + the printed TOTP), everything
  else automatic. Caches the access token to
  `data/secrets/kite_access_token.json`. FULL automation (storing the
  account password to script the login itself) was explicitly considered
  and REJECTED, even after the collaborator provided written approval to
  store it — the password is HIS account's master credential, doing this
  would mean reverse-engineering Zerodha's login page (unofficial, fragile,
  breaks whenever they change it, real risk to his account standing with
  the broker), and consent doesn't change what happens if it leaks from a
  repo with a documented history of exactly that. See memory
  kite-connect-live-feed-2026-08. Lower-level `login`/`exchange` subcommands
  still exist for scripting/debugging. Access tokens expire ~24h.
- `live_quotes.py` now tries Kite Connect FIRST (true real-time, if a
  token is cached and valid), falls back to yfinance (~15-min delayed) if
  Kite is unavailable for any reason, then the last CSV close (flagged
  stale). This fallback chain means EVERY existing consumer
  (`should_i_sell`, `intraday_watch.py`, `horizon_advice`, exit checks)
  transparently gets true live prices with zero changes to their own code
  when Kite is connected, and degrades gracefully to the old behavior on a
  day the token wasn't refreshed — nothing breaks either way.
  `get_quotes()` (plural) batches ALL symbols into ONE Kite API call.
- `live_ticker.py` — a terminal, tick-by-tick live price display (like
  Kite's own watchlist), built on `KiteTicker` (websocket, not polling).
  DISPLAY ONLY: reads no signals, writes nothing, not called by any other
  script — closing it has zero effect on the pipeline. Defaults to held
  positions + today's top-10 buy candidates; accepts explicit symbols as
  args. Needs a real terminal (curses) — cannot be smoke-tested headlessly;
  the underlying pieces (token resolution, websocket tick reception) were
  verified working in isolation before wiring the display on top.

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
- REAL BOOK IMPORTED 2026-07-14 (supersedes the 2026-07-12 "no real
  positions" reset): user's actual Zerodha holdings are now in
  portfolio_state.json — 9 positions, ₹67L invested, all pre-existing
  DISCRETIONARY picks (none bought by the strategy): AARTIIND (PLEDGED —
  cannot sell without unpledging, note field on the position), BEL,
  KALYANKJIL, KFINTECH, RELIANCE, VOLTAS, WIPRO, GOLDBEES x9000 (the gold
  sleeve, already ≈15% of book), RCOM (dead write-off, in
  EXIT_EXCLUDE_SYMBOLS). Cash is 0 by user instruction — the imported book
  is REFERENCE/monitoring only; user is NOT live-trading through this
  system yet and will connect real Zerodha usage ONLY IF the agent-sim
  month (and paper gate) run well. Don't nag about the cash figure. The
  user runs the system irregularly (not daily) — record_fill discipline
  after each real trade is what will keep the books honest once live.
  Pre-reset originals: data/_quarantine/*_pre_cleanup_2026-07-12.*.
- AGENT-SIM (2026-07-14, agent_sim.py, nightly in run_daily_log.sh): LLM
  trader-persona asks the real ai_assistant for advice against a SANDBOXED
  book copy (data/_agent_sim/, seeded from the real portfolio, sim cash
  ₹5L), executes its JSON orders through the real record_fill code, code
  critic checks books/journal consistency. It's an INTERFACE test (advice→
  human→fill→books), NOT an alpha test. `python agent_sim.py report` for
  findings. Its first run caught: record_fill accepted qty<=0 fills (now
  hard-aborts), sell verdicts lacked quantities (now qty_to_sell), stock
  CSVs lag the index intraday (sim fills at last close ≤5d).
- -18% stop watch is automated at THREE cadences: intraday_watch.py via
  systemd timer stockai-intraday (every 15 min during market hours,
  weekdays — live yfinance quotes ~15-min delayed; alerts STOP breach HIGH,
  >5% intraday DROP MED, BOOK-level drawdown from tracked peak (2026-07-17:
  whole real book incl. sleeves/write-offs + cash, peak ratchets in
  data/book_peak.json; -10% MED / -20% HIGH, deepest level only, once per
  day), and S/R level crossings INFO with the explicit
  caveat that auto-selling at resistance was backtested and REJECTED — the
  S/R alert is information for human discretion, NEVER wire it into
  exit_engine/paper_trader/agent_sim); legacy cron weekdays 14:40 IST
  (run_exit_check.sh) kept as backstop; run_daily_log.sh evenings.
  Alert dedupe: one per (day, symbol, type) via data/intraday_seen.csv.
  The same intraday service also runs market_scanner.py — universe-wide
  discovery (~200 F&O-liquid names, batched yfinance 15m bars): JUMP >=+4%,
  SURGE (>1.5x 20d avg volume, up-moves only), NEWHIGH (52w), each flag
  fused with core.compute_score vs today's cached top-N cutoff and labeled
  QUALIFIES / ELIGIBLE / chase-risk. notify-send only on QUALIFIES. Flags
  accumulate in data/scanner_log.csv WITH score context so flag quality is
  measurable — do NOT promote scanner flags into an entry rule without
  walk-forward evidence (raw daily jumpers mean-revert; the fusion label
  exists precisely because BIOCON +6.4% failed the filter while WELCORP
  +2.2% qualified on day one).
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
- SCORER IS UNIFIED (2026-07-17 audit fix): core.momentum_score is THE one
  per-name scorer — live (compute_score → advisor/exit_engine/scanner) and
  both backtest engines all call it. Convention: 50DMA gate + vol_63 windows
  END YESTERDAY (exclude the evaluation bar) — the validated backtest
  convention; include-today was walk-forward-tested 2026-07-17 and is NOT
  better. NEVER re-inline a copy of the scoring block — the two copies
  drifted once already (live ranked BUYs by a different score than the
  validated one). confidence_table.py still has its own vectorized vol
  (rolling 63 incl. today) — tolerated because its output is decile-bucketed
  win-rate priors, not a ranking; align it if it's ever rebuilt for ranking.
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
  is SOLD; new names are bought next session. MANDATE RELAXED 2026-08-01:
  selling is now allowed AT ANY TIME (no compulsion to wait for month-end);
  only the month-end REBALANCE remains compulsory. That is a PERMISSION, not
  a signal — early intra-month exits are still not built in, because all
  THREE tested price-based variants lost in walk-forward: tight trailing/50MA,
  resistance-fade, and (2026-08-01) a ratcheting giveback trailing stop
  (-5 to -6pp mean CAGR, wins only 4-6/19 windows, adds a negative window;
  research handle kept as run_backtest_laggards_only(trail_stop=...), default
  None). Non-price overlays tested via corporate announcements 2026-07-12 and
  REJECTED — see memory exit-announcements-rejected. Discretionary early
  sells by the user are fine; just record them via record_fill.py.
  Adopting laggards-only itself: costs ~0.8pp
  gross CAGR (fewer round-trips lose a little raw return) but SAVES ~3pp/yr
  NET CAGR — fewer taxable events, NOT LTCG conversion (that barely fires:
  momentum's own turnover displaces names from top-N well before 365 days).
  See memory monthly-close-cost-2026-07 and trading-mandate-constraints.
- REGIME_EXPOSURE boosted 2026-07-12 (user decision after VIX-overlay study's
  control run — a risk-appetite dial, NOT alpha): BULL 1.0 / SIDEWAYS 0.75 /
  BEAR 0.375 / UNKNOWN 0.75 (old values x1.25 capped at 1.0, no leverage).
- SLEEVES DISABLED 2026-07-17 (user decision — "no gold/intl sleeve for now",
  a risk-appetite call, NOT an evidence reversal): GOLD_ALLOC=0.0,
  INTL_ALLOC=0.0 in strategy_config.py. Production is momentum-only again
  (+ idle-cash yield, which stays). All sleeve code paths remain (they gate
  on alloc > 0) — restore 0.15/0.10 to re-enable. The evidence below is kept
  for that day; both sleeves were the only deltas ever to clear 95%
  significance, and disabling them raises expected MaxDD (33.4% vs 22.6% on
  current data) — flagged once, user's call.
- GOLD SLEEVE + IDLE-CASH YIELD (ADOPTED 2026-07-13, DISABLED 2026-07-17 —
  see above; research_lowvol_sleeve.py
  — see memory gold-sleeve-2026-07): GOLD_ALLOC=0.15 of TOTAL capital in
  GOLDBEES, rebalanced to target each month-end (1% drift band); the
  momentum book runs on the other 85% as its own sub-capital with regime
  exposure unchanged. Mechanism is DIVERSIFICATION (21d-return correlation
  to the momentum book +0.01), NOT alpha — the FIRST config delta to clear
  95% significance (paired-bootstrap Sharpe +0.14, CI [+0.06,+0.22]; better
  Sharpe AND MaxDD in 16/16 rolling 3y windows). Caveat: gold's 2015-26
  15.9% INR CAGR is exceptional — the durable benefit is the correlation;
  don't upsize the sleeve because bigger gold backtests better this decade.
  Crash autopsy: the old 40.5% max DD was the 2018-2020 GRIND (two flat/down
  years in a row), not a rebound crash — exactly what gold diversifies.
  A LOW-VOL equity sleeve was tested in the same study and REJECTED (+0.52
  corr to momentum, costs CAGR, doubles manual workload). CASH_YIELD=0.06:
  idle (unexposed) cash accrues a liquid-ETF yield in engines + paper
  trader (+1.57pp CAGR of accounting realism) — OPS MANDATE: real idle cash
  must actually be parked in LIQUIDCASE-type ETF or the backtest overstates.
- INTERNATIONAL SLEEVE (ADOPTED 2026-07-13, DISABLED 2026-07-17 — see above;
  research_intl_sleeve.py):
  INTL_ALLOC=0.10 in MON100 (Nasdaq-100 INR ETF), same construction and
  evidence bar as gold — production is now a 75/15/10 three-sleeve book
  (momentum/gold/intl). Corr to momentum +0.10; Sharpe delta vs the 85/15
  baseline +0.14 [CI +0.05,+0.23] SIGNIFICANT, better Sharpe 16/16 windows.
  Same exceptional-decade caveat as gold (Nasdaq 2015-26 + INR depreciation
  tailwind): durable claim is correlation/currency, not return level. 0.10
  is the mildest tested weight — anti-creep rule applies; momentum stays
  >=70%, don't stack more sleeves (next one reinvents an index fund).
- SKIP-MONTH MOMENTUM (research_skip_month.py, 2026-07-13): the academic
  12-2 construction (momentum legs ending at i-21) tested and REJECTED —
  -3.5 to -5.1pp CAGR, worse in 13/16 wf windows. US-style last-month
  reversal does NOT hold for this NSE setup; recent-month strength is
  signal here. Engine's skip_days param stays 0 in production.
- Current backtest (F&O-gated universe, SIDEWAYS=3, BEAR=4, boosted
  exposure, MOMENTUM-ONLY — sleeves disabled 2026-07-17, so the production
  default run_backtest_gold_blend degenerates to laggards_only + cash
  yield; RE-RUN 2026-08-01 on current data): 19.18% CAGR, Sharpe 0.92,
  max DD 37.12%. Walk-forward (19 overlapping 3y windows): mean CAGR
  26.22%, median 27.24%, mean Sharpe 1.16, worst window DD 29.1%, 1/19
  negative windows. (The previously documented 21.79%/1.01/33.4% was data
  as of 2026-07-17 — two more weeks of BEAR tape moved it. Re-run the
  backtest before quoting these; they drift with every download.) For
  comparison the three-sleeve config on the SAME data: 22.22%/1.32/22.6%,
  0/19 negative windows — the sleeves' loss shows up in DD/consistency,
  not CAGR. Post-tax net ≈ momentum 20% STCG FY-netted
  (research_net_returns.py). Legacy hard-close engine (run_backtest /
  --engine hard_close) kept for comparison. Trust the walk-forward
  distribution (python walk_forward.py), not any single backtest run — see
  memory feedback-quant-researcher-role for why.
- PARAM ROBUSTNESS (2026-07-17, research_param_robustness.py +
  _tier2.py): are the core params curve-fit to one cycle? Tier-1 (per-era
  grids) + Tier-2 (paired 19-window walk-forward, pre-registered rule:
  curve-fit only if an alternative wins >=14/19 windows AND higher mean
  CAGR). HOLD=21, MA_GATE=50, BULL_N=10: ROBUST (MA gate is nearly inert —
  entire grid incl. NO gate within 0.9pp CAGR; don't bother re-tuning it).
  LOOKBACK=126 and VOL_WIN=63 each got a BOUNDARY signal (147 resp. 126
  wins exactly 14/19) — but 24 alternatives were tested at a p≈0.06
  threshold, so ~1-2 crossings are EXPECTED by chance; treated as amber,
  not proof. DO NOT retune to grid winners (that's the curve-fit this
  study detects); a LOOKBACK/VOL_WIN change needs the usual bar (paired
  block-bootstrap CI + mechanism). CSVs in data/_research/. AMBERS
  RESOLVED same day (research_param_bootstrap.py): LOOKBACK 147 delta is
  pure noise (Sharpe -0.05, P(better) 37%) — 126 fully cleared; VOL_WIN
  126 NOT significant on Sharpe (CI [-0.11,+0.48]) so no change, but its
  CAGR delta CI [+0.2pp,+15.3pp] excludes 0 — the one candidate worth
  re-testing on FRESH out-of-sample data after the paper/sim months
  (grid-selected post-hoc, so the bar is higher than the sleeves').
- STATISTICAL HYGIENE (2026-07-12, research_statistical_hygiene.py): with
  ~128 rebalance periods (~10.7y), Sharpe confidence intervals are WIDE —
  autocorrelation-adjusted (Lo 2002) 95% CI on the current 0.85 point
  Sharpe is [0.01, 1.69]. The strategy's core edge clears significance
  easily (P(true Sharpe≤0)=0.9%), but NONE of this session's 3 adopted
  deltas (BEAR=4, laggards-only, exposure boost) individually clear 95%
  significance on a paired block-bootstrap — treat 1-2pp CAGR deltas
  between configs as suggestive, not proven, unless backed by a causal
  mechanism (tax rules, structural risk) rather than pattern alone. See
  memory statistical-hygiene-2026-07 before adopting future config changes
  off point estimates.
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
- CONVICTION-WEIGHTED SIZING ADOPTED 2026-08-05 (research_conviction_sizing.py,
  PREREG_conviction_sizing.md — part of a broader "state of the art" research
  program the user opened up: alpha/parameters/portfolio construction all in
  scope, only monthly cadence + signals-only + cash-equity + F&O-universe
  held fixed). Production sizing was plain inverse-vol (1/vol_63,
  MAX_WEIGHT-capped) — blind to how strong the momentum SCORE itself was, a
  name scoring 55 sized the same as one scoring 21 if vol matched.
  `backtest_portfolio.conviction_weights(scores, vols, names, tilt)` blends
  inverse-vol with score-proportional sizing: raw_weight = (1/vol)^(1-tilt) *
  score^tilt. Walk-forward (36 windows, 3y/3mo-step): tilt in {0.25,0.50,0.75}
  ALL cleared the pre-registered bar (bootstrap 95% CI excludes zero AND
  wins >=12/N windows AND DD doesn't worsen >2pp mean) — +1.89%/+2.89%/+4.78%
  mean CAGR delta, monotonic, wins in 31-33/36 windows, and WORST-CASE
  drawdown IMPROVED at every tilt (38.9%→34.8-37.0%), not worsened — the
  first genuinely adoptable result in this repo's whole sizing/exposure
  research line (risk-parity and vol-targeting both failed the same bar,
  see below). Adopted tilt=0.50 (`strategy_config.CONVICTION_TILT`) — the
  robust interior point of a monotonic all-significant range, not the most
  extreme value, matching how REGIME_EXPOSURE/MAX_PER_SECTOR were chosen.
  ADVERSARIAL CHECKS run before trusting a result this clean (a lesson from
  this repo's own history of near-miss false positives): worst-case DD
  checked separately from mean DD (both improved); per-window breakdown
  checked for concentration (33 positive/3 negative, no single outlier
  window driving the mean — ruled out the "one cell doing all the work"
  trap that caught H5 in the S/R improvement batch); confirmed NOT a
  turnover/transaction-cost artifact (sizing_fn only reallocates capital
  among the SAME already-selected top-N, cannot change trade count); an
  early/late window split was found (2015-2016-start windows averaged
  +6.96pp delta vs +1.73pp for 2017+ windows) and investigated — ruled out
  universe-size and "just amplifies an already-strong bull run" explanations
  (early windows had LOWER baseline return AND lower vol, the opposite of
  that story), re-ran the bootstrap on ONLY the later windows as a
  conservative check and it still clears the bar (+1.73%, CI
  [+0.94%,+2.32%]) — the adoption does not depend on the early era.
  WIRED CONSISTENTLY: backtest_portfolio.run_backtest_laggards_only's
  default sizing (was inverse-vol, sizing_fn=None path), paper_trader.py,
  and ai_assistant.position_sizes() — the latter two had each independently
  hand-inlined their OWN copy of plain inverse-vol (found while wiring this
  in; same drift-risk class as the pre-2026-07-17 momentum_score copies).
  Legacy run_backtest (hard-close engine, kept for historical comparison
  only) intentionally left on plain inverse-vol — not production.
  New production baseline (python walk_forward.py --engine laggards_only,
  standard 19-window/6mo-step config): mean CAGR 33.4%, median 36.0%, mean
  Sharpe 1.25, mean max DD 21.5%, worst DD 30.2%, 1/19 negative windows.
  (Prior baseline before this change: 27.18% single-run CAGR/Sharpe
  1.09/DD 27.65%, wf mean 31.47%/1.24 — see the 2026-08-01 universe-coverage
  entry above. Re-run before quoting either number; they drift with every
  data pull.) `core.position_size` (rupee allocation given an already-known
  weight) is UNCHANGED — it never computed the weight itself, so no
  behavior to fix there. `full_advisor.py`'s ATR-based per-trade risk
  sizing is a DIFFERENT, separate mechanism (risk-per-trade off stop
  distance, not portfolio-weight allocation) — NOT touched, was never part
  of this study's evidence, would need its own separate validation.
- CORRELATION-AWARE (RISK-PARITY) SIZING and VOLATILITY-TARGETED EXPOSURE
  both tested and REJECTED 2026-08-01 — kept here as the reason conviction
  sizing (above) was framed as a DIFFERENT question, not a re-test of a
  closed one. Risk-parity (shrunk-covariance equal-risk-contribution
  weights, `backtest_portfolio.risk_parity_weights`, still in the codebase
  as a research handle): a wash vs inverse-vol across every shrink/window
  variant (within ±0.3pp CAGR, 8-12/19 windows) — MAX_PER_SECTOR=2 already
  does the diversification work a correlation-aware scheme would try to
  add, so there's no residual structure left to exploit. Vol-targeted
  exposure: lost to BOTH baseline and a matched unconditional-scaling
  control, and made worst-case DD WORSE at every calibration tested — de-
  risking off trailing realized vol mistimes both the drawdown and the
  recovery. See memory risk-parity-sizing-rejected-2026-08 and
  vol-target-exposure-rejected-2026-08.
- TREND-QUALITY SECOND SCORING FACTOR tested and REJECTED 2026-08-05
  (research_trend_quality_factor.py, PREREG_trend_quality_factor.md) — same
  research program as conviction sizing above. Hypothesis: a price-only
  "was this return earned via a smooth grind or a lumpy path" factor
  (signed R² of log-price vs time over the LOOKBACK window) could add
  cross-sectional information beyond ret_6m/vol_63. Correlates 0.77 with
  the existing score (expected, given the eligibility gate already requires
  positive momentum) but leaves real independent variation. 5 configs
  tested (3 blend weights + 2 tiebreak-only thresholds), 0/5 cleared the
  bar — all CIs included zero, best candidate (10%-tiebreak) came closest
  (+1.14%, CI [-0.23%,+2.31%]) but still failed. Pattern: tiebreak-only
  reranking consistently beat full blending, and lighter blend weights beat
  heavier ones — consistent with the high correlation meaning most of what
  the factor would add is already implicit in the existing score. VALUE
  and QUALITY factors (P/E, P/B, ROE) were considered and ruled OUT before
  any code was written: yfinance (already a dependency) only exposes a LIVE
  snapshot, not point-in-time history (quarterly_financials covers 5
  quarters, nowhere near the ~10y backtest window) — building them now
  would mean either a new paid fundamentals vendor or applying TODAY's P/E
  to 2015-2026 prices, textbook look-ahead bias. Flagged as blocked on a
  data source, not tested. See memory trend-quality-factor-rejected-2026-08.

## S/R subsystem (separate, already tuned — don't touch unless directly asked)
- `support_resistance.py` — multi-timeframe (monthly+weekly+daily) swing pivots +
  volume profile + wick rejection + delivery volume scoring + reach probability
- HORIZON = TO MONTH-END, WHERE MONTH-END IS THE LAST TUESDAY (2026-07-31, user
  spec — the rebalance date). `sr_horizon.py` is the single source of truth:
  last_tuesday_of_month / horizon_end (rolls to next month once that Tuesday
  passes) / trading_days_until / scale_probability_to_horizon. The window
  SHRINKS through the month — run Aug 2 → 17 trading days, run Aug 12 → 9 —
  so probabilities must shrink with it.
  `reach_probability_v2(df, level, direction, forward_days, cur)` previously
  ACCEPTED forward_days and IGNORED it (table is baked at 21d), so a 9-day
  question silently got a 21-day answer — a systematic overstatement. Horizon
  is now resolved best-source-first: (1) native table at exactly that horizon,
  (2) sqrt-time interpolation between the two bracketing native tables,
  (3) sqrt-of-time rescale p_h = 1-(1-p)**sqrt(h/21) as a last resort.
  `cur` overrides the reference price so LIVE quotes drive
  distance+probability instead of the last CSV close.
- METRIC BUG FIXED 2026-07-31 — THE BIG ONE. sr_reach_table.json does NOT
  measure what every consumer thinks. Its builder scores outcomes with
  sr_backtest.test_support/test_resistance, a touch-AND-BOUNCE test that
  returns None when a level is never touched — and sr_build_reachtable DROPS
  those rows. So the table is P(bounce | touched), with untouched levels
  invisible, while sr_daily_logger / analyse_table / sr_monthend_analysis all
  label and score it as P(touch). Damage concentrates in far cells: distant
  levels are rarely touched, so they're mostly dropped instead of counted as
  misses, leaving survivors conditioned on having been reached. The old table
  is therefore nearly FLAT in distance (12%+ ≈66%, ABOVE 0-2%'s ≈57%) while
  real forward data decays hard (76% at 0-5% → 3% at 10-15% → 0% beyond 15%).
  That also explains its weak OOS corr 0.173.
  `sr_build_touchtable.py` builds the correct P(touch) table: pure touch test,
  untouched = a real miss, complete-window requirement (the old builder
  accepted len(future)>=5 and scored 6-bar windows as 21-day outcomes — the
  same truncation bias as the fake-100% analysis bug), wrong-side levels
  skipped, and thin cells fall back to the DISTANCE-marginal rate rather than
  the global base (a global fallback hands far cells a near-base number,
  re-creating the exact distortion). Result at 21d: decays 94.5%→5.9% across
  distance, OOS corr **0.529 vs 0.173**, 0/24 fallback cells, 7860 train rows.
  Built at 5/10/15/21d (`--forward N`); support_resistance prefers
  sr_touch_table*.json and falls back to the legacy file if absent — the
  fallback now prints a LOUD warning to stderr, because degrading silently to
  a table that answers a different question is exactly the failure this
  subsystem already had. The legacy sr_reach_table.json is KEPT as that
  safety net; don't delete it, and don't let the warning be suppressed.
  Empirical-vs-sqrt check: scaling 21d→10d was off by mean 4.3pp (max 7.3pp)
  and systematically understates near levels / overstates far ones — hence
  native tables + interpolation rather than one rescaled table.
  P(touch) is now guaranteed MONOTONIC non-decreasing in horizon (verified
  1-23d); an earlier nearest-table-match version was not (13d read 94% while
  14d read 90%, i.e. a longer horizon looked less likely).
  NOTE: probabilities now legitimately span ~5-95%, NOT the old 57-78 band —
  any threshold or bucket keyed to that band is stale (sr_monthend_analysis's
  calibration bins and legacy-row detector were both fixed for this; the
  detector is now DATE-anchored, since a band test would discard valid modern
  rows).
  Callers omitting forward_days are unchanged (default = table's 21d).
  Anything gating on a probability must scale its THRESHOLD by the same
  horizon — sr_daily_logger's S2/R2 base-rate gate does; a raw 66% gate
  against scaled probs empties S2/R2 exactly when the window is tightest.
  LIVE QUOTES ARE THE DEFAULT (2026-08-04) — `python support_resistance.py SYM`
  pulls CMP from live_quotes.py with no flag. `--no-live` forces the last CSV
  close; `--live` is still accepted as a no-op so old invocations keep working.
  Live is auto-suppressed under `--as-of` (that flag asks about a DIFFERENT
  date, so stamping today's price on it would mix two points in time) and the
  run says so. Stale fallbacks are dropped, not silently shown as live, and a
  dead feed degrades to closes with the source stated.
  NOTE this changes only the INTERACTIVE path — sr_daily_logger,
  sr_dynamic_logger, sr_backtest and sr_build_touchtable never touch live
  quotes and must not: they call log_stock/get_all_levels directly, where
  `cur` defaults to the last completed close. A backtest that saw live prices
  would be scoring on data unavailable at the decision point.
  `python support_resistance.py SYM --as-of YYYY-MM-DD` tests a horizon
  without waiting for the calendar. Levels sitting on
  the wrong side of live price render as BROKEN (a support below price has
  been breached — real information, and it happens routinely intraday).
- LIVE PRICE NOW DRIVES LEVEL SELECTION, not just distance (2026-08-04).
  `get_all_levels`/`get_levels`/`get_trade_levels` take an optional `cur=`
  reference price (default: last close, so BACKTESTS AND THE TABLE BUILDERS
  ARE BYTE-IDENTICAL — verified). analyse_table threads the live quote in.
  Previously the live price was applied only AFTER level selection, so
  `cur` inside get_all_levels came from the last CSV close and the
  above/below split, the +-8% proximity window and the 52w fallbacks were all
  anchored to a stale price. Effect was real: WIPRO at CMP 188.66 showed
  187.52 as "R1 (BROKEN -0.6%)" — a resistance price had already cleared —
  where it is correctly S1 at -0.6% with a true R1 at 202.50 above.
  The PIVOTS are unchanged by this (swing points and volume nodes from
  completed bars); only which of them count as support vs resistance, and
  which fall inside the proximity window, shift with price.
  NOTE the asymmetry this leaves: levels re-rank off the live price, but the
  P(touch) table was calibrated against levels selected from completed bars.
  That is the right trade for a month-horizon question (the alternative is
  ranking off a stale anchor), but it is an approximation, not an exact
  match to the calibration setup — don't quietly "fix" it by reverting.
- QUOTE PROVENANCE (2026-08-03): live_quotes' `stale` boolean could not
  distinguish a REAL-TIME Kite tick from a ~15-MIN-DELAYED yfinance quote —
  both return stale=False, so every caller silently treated a quarter-hour-old
  price as current. Added `get_quote_detail`/`get_quotes_detail` returning
  (price, stale, source) with SOURCE_KITE/SOURCE_YF/SOURCE_CSV/SOURCE_NONE
  and SOURCE_DELAY_SECONDS. `get_quote`/`get_quotes` keep their 2-tuple shape,
  so existing callers are untouched. analyse_table now reports CMP provenance
  ("3 real-time (Kite)" vs "2 ~15-min delayed (yfinance)") and names delayed
  symbols explicitly — a delayed quote is usable, pretending it is live is not.
- PARTIAL-CANDLE GUARD ON THE INTERACTIVE PATH (2026-08-03): sr_daily_logger
  had `drop_partial_candle` but analyse/analyse_table did NOT — so the tool
  most likely to be run mid-session (`--live`) was the least protected, and a
  partial candle's High/Low invent swing points that vanish at the close. The
  guard now lives in support_resistance.drop_partial_candle (one shared
  implementation) and analyse_table applies it, reporting when it fires:
  levels then reflect the last CLOSED session while CMP is live, which is the
  correct combination for a month-horizon question — but it must be STATED,
  not implied.
- The S/R footer now names how a horizon was resolved — native empirical
  table / interpolated between two empirical tables / sqrt-extrapolated —
  instead of always claiming a sqrt "rescale". Most real horizons are
  interpolated between the 15d and 21d tables, and calling that a rescale
  understates it.
- intraday_watch.py prewarms the quote cache with ONE batched `get_quotes`
  call per pass (book valuation + alert loop) instead of a Kite request per
  symbol — same results, far fewer API calls on a 15-minute timer.
  Loggers stamp HorizonEnd/HorizonDays; rows predating those columns are NaN
  and readers must treat them as 21d.
- LOGGER OUTPUT IS THREE FILES (2026-08-03, user spec). Each logger run writes:
  (1) `sr_daily_log.csv` — the cumulative append-only record, unchanged; this
  is what sr_monthend_analysis reads and must not be rotated or truncated.
  (2) `sr_today.csv` — today only, OVERWRITTEN each run. A convenience view;
  nothing is lost by overwriting since (1) and (3) keep the history.
  (3) `sr_month_YYYY-MM.csv` — one file per CALENDAR month, appended daily.
  Daily rows are plain (no avg columns). ONCE the month's data collection
  reaches the rebalance day (last Tuesday), one `AVG` summary row per stock is
  appended — Date literally reads `AVG` — holding the month's mean CMP/S1/R1
  plus `Days` = the number of sessions averaged. Rows also carry `High`/`Low`
  from the SAME bar as CMP.
  The completeness test is `max(logged date) >= last Tuesday`, NOT equality:
  if that Tuesday is an NSE holiday no row falls exactly on it and an equality
  test would silently never write the averages.
  AVG rows are DERIVED: every write strips existing ones and recomputes from
  the daily rows, so re-running after month-end never averages an average back
  into itself (verified — a 4th day updates the mean to the true 4-day figure,
  and the AVG row count stays fixed). Any reader of these files must filter
  `Date == "AVG"`; sr_monthend_analysis.load_log does this before parsing
  dates, since "AVG" would otherwise become NaT and be scored as a snapshot.
  sr_dynamic_logger writes the same trio under `sr_dynamic_today.csv` /
  `sr_dynamic_month_YYYY-MM.csv` — NEVER share files with the fixed panel.
  Month files are keyed on each row's own DATA date, so a catch-up run
  straddling a month boundary files each row under the right month. Averages
  are recomputed from scratch on every write and rows dedupe on
  (Date, Symbol), so re-running a day REPLACES its row rather than
  double-counting it into the mean.
- THE S/R MODEL IS AT ITS CEILING (2026-08-03) — the search is CLOSED. A
  16-variant sweep (research_sr_model_sweep.py), a ceiling analysis
  (research_sr_ceiling.py) and a paired block bootstrap
  (research_sr_model_bootstrap.py) were run on the full 500-symbol universe
  at 5/10/15/21d. NOTHING cleared the pre-registered bar (+0.02 OOS corr AND
  a majority of horizons). The decisive number: an ORACLE bucket table fitted
  ON THE HOLDOUT ITSELF — i.e. one that has seen the answers — beats
  production by only **+0.008**. The 24-cell lookup already extracts ~99% of
  what (distance x vol) can express, so bucket/vol/threshold retuning is
  curve-fitting a difference smaller than noise. That is the mechanism, so a
  future "promising" grid result is noise by construction — don't chase it.
  MIN_CELL is INERT at current data volume (50 and 100 both scored exactly
  0.0000 delta). The best variant, logistic(+ATR,trend,strength), won 4/4
  horizons at +0.0100 but its bootstrap CI includes zero at 0/4 horizons
  (21d borderline: P(better) 95.9%, CI [-0.0002,+0.0325]) — not significant,
  and implausible anyway against +0.008 of total headroom. KEEP the bucket
  table: matches a fitted model within noise, inspectable, no fitting
  dependency, and it GUARANTEES monotonicity in horizon (a fitted model does
  not — that invariant broke once already). Only a NEW DATA SOURCE (intraday
  bars, order-book depth, options positioning) could move this; rearranging
  daily OHLC cannot. See memory sr-model-sweep-exhausted-2026-08.
- VOLATILITY IS ALREADY THE TABLE'S 2nd AXIS — don't "add" it. The P(touch)
  table is keyed on (distance x realised vol) and the vol axis carries real
  signal: at 8-12% distance, 21d P(touch) is 14.6% for a <25%-vol name vs
  38.1% for a 45%+ one. A PARKINSON high-low-range estimator was tested as a
  drop-in replacement for the close-to-close one (research_sr_vol_estimator.py,
  pre-registered rule: adopt only if OOS corr +>=0.02 AND wins a majority of
  horizons) and REJECTED — it loses at all four: 5d -0.0012, 10d -0.0021,
  15d -0.0028, 21d -0.0024. Mechanism: the two correlate 0.89 and disagree on
  bucket for only 27% of names, and Parkinson ignores OVERNIGHT GAPS, which on
  NSE carry a real share of the move to a level. See memory
  sr-vol-estimator-rejected-2026-08; don't re-test without a gap-preserving
  estimator (Garman-Klass / Rogers-Satchell) or a separate gap feature.
- S2/R2 ARE NOW ALWAYS LOGGED (2026-07-31). They used to be written only when
  their probability beat the base rate, but the log is the MEASUREMENT record:
  a level never written can never be scored, so the gate was destroying
  exactly the low-probability observations needed to calibrate the low end,
  and biased the logged sample toward levels the model already liked.
  Filtering is a DISPLAY concern; `S2_shown`/`R2_shown` record what the gate
  would have decided so display stays reconstructible. Any gate consuming a
  horizon-scaled probability must scale its THRESHOLD by the same horizon —
  a raw base-rate gate against scaled probs empties S2/R2 exactly when the
  window is tightest.
- LEVEL MIN-SEPARATION (2026-08-05, get_all_levels) — S1/R1 used to be ordered
  PURELY by proximity, so S1 was whatever pivot sat closest to spot even at
  0.1% away. Measured on the 2026-08-04 panel: 20/61 names had a level inside
  0.5%, median S1 −2.1% / R1 +1.9%, and 30/61 carried S1_prob ≥90% — trivially
  true and useless for a month-horizon decision. The `final` strength score was
  already computed and then THROWN AWAY by the sort. Levels are now filtered to
  be at least `min_sep` from spot AND from each other (pairwise, so S1/S2/S3
  cannot be three points in one cluster). min_sep = 0.25x the 21-day sigma,
  floored 1%, capped 6% — scaled to the HORIZON's movement, not the day's,
  because the question is "where might price go by month-end". A first attempt
  used 0.35x DAILY sigma and was far too weak (0.77% on a 35%-vol name). Result:
  median S1 −2.1%→−4.5%, R1 +1.9%→+4.0%, levels within 0.5% 20/61→1/61,
  S1_prob≥90 30→1. Names showing S1 beyond −15% are NOT over-filtered — they
  are at 85-93% of their 52w range, where no nearby support exists, and their
  4-8% probabilities correctly say so. Falls back to the unfiltered nearest if
  the filter would return nothing (a caller receiving None cannot distinguish
  "no structure" from "filtered out"). CSV COLUMNS ARE UNCHANGED. The P(touch)
  tables stay valid — they are keyed on (distance x vol), not on which pivot
  was picked, so a 4.5% level gets the correct 4-6% bucket either way; but the
  far cells now see more use than when the tables were built, so re-check
  calibration at the next sr_monthend_analysis run.
- MONTHLY CONTAINMENT BAND (2026-08-04, containment_band.py) — S1/R1 answer
  P(TOUCH) ("will price reach this"), which is the WRONG QUANTITY for the
  user's actual question ("a level it won't dip below this month"). Verified on
  the 2026-08-04 panel: median S1 −2.1%, median R1 +1.9%, corr(|distance|,prob)
  −0.879/−0.907 — the probability column had become a restatement of distance,
  and a "94% support" is a level with a ~6% chance of HOLDING. Structural, not a
  calibration defect: level selection picks the NEAREST pivot, optimal for
  "will it get there" and worst-possible for "will it hold". The band is a
  quantile of forward CLOSING excursion per (vol bucket, horizon) — no pivot, no
  touch table in the path. FLOOR AND CEILING ARE FITTED SEPARATELY and must
  never be mirrored: the asymmetry SIGN FLIPS with the fitting window (2023-26
  intraday had downside 1.07-1.36x upside; the shipped 2016-21 daily fit has the
  UPSIDE wider at high vol, +25.8% vs −14.3% at 45%+). Fitted from the DAILY
  archive via build_containment_table_daily.py, NOT Kite intraday — containment
  needs only closes so 11y beats 3y, and the Kite fit had an inverted split
  (3,784 train vs 13,988 holdout, Kite starts 2023-08) producing bands too wide
  (86-93% hold vs 85% claimed). Daily fit: 0/16 cells outside ±10pp, floor held
  79.5-88.3%. WIDTH IS REGIME-DEPENDENT — quarterly 15th-pct floor ranged
  6.0%-14.8% on 2024-26 data; a typical-month expectation, NOT a guarantee.
  Printed by analyse_table below the S1/R1 table so both are visible together.
  TRADING THE BAND WAS TESTED AND REJECTED (PREREG_tradeable_levels.md,
  research_tradeable_levels.py, 199 symbols / 39,987 obs): holdout win 43.9%
  (UP) / 49.0% (DOWN) against a pre-registered 55% bar. Mechanism is ADVERSE
  SELECTION — at a 5% band, FILLED observations were contained 9.5% of the time
  vs 100% for unfilled; the fill itself is the bad news, and trend conditioning
  did not rescue it. So the band ships as a RISK tool with that negative result
  printed alongside it. DESCRIPTIVE ONLY — never wire into exit_engine/
  paper_trader/agent_sim/the scorer. See memory sr-containment-band-2026-08.
- KITE INTRADAY (2026-08-04, fetch_intraday_kite.py → data/intraday_data/,
  RESEARCH ONLY): 15-min bars reach back to Aug 2015, NOT weeks as assumed —
  200d/request, 200 symbols x 3y in 18.2 min, 0 failures. Order-book depth is
  SNAPSHOT-ONLY (no historical endpoint), so flow features remain a
  collect-now-evaluate-later project while bars are testable today. TWO TRAPS:
  (1) Kite is UNADJUSTED vs price_data's yfinance-ADJUSTED (NATIONALUM ratio
  1.744 in 2016 → 1.000 today) so levels/price/path must come from ONE source
  per observation, and this directory must never be written into price_data/
  (globbed as the tradable universe); (2) Muhurat and Saturday special sessions
  (~4 and ~7 bars) are present — filter bars/session >= 20, or any "N
  consecutive bars" test is trivially satisfied. First real use showed
  intraday's value is FILL REALISM: daily-Low touch tests overstate win rate by
  ~2pp vs a 30-min persistence rule and ~9pp vs close-only. See memory
  kite-intraday-capability-2026-08.
- Backtested via `sr_backtest.py` (fast, uses `fast=True` flag — REQUIRED or it hangs,
  wick_rejection_score becomes iterrows-based without it) and `sr_backtest_filtered.py`
  (only tests on trend+momentum+regime-filtered conditions)
- Current accuracy: ~65-68% combined S/R hit rate on 21-day forward window — this is
  close to the practical ceiling for price-only daily S/R, don't chase higher without
  a clear new hypothesis
- `sr_daily_logger.py` logs daily S1/R1 always, S2/R2 only if reach-prob beats the
  empirical base rate (~66%, from sr_reach_table.json). WATCHLIST is a FIXED
  hardcoded validation panel — same stocks every day so sr_monthend_analysis.py
  measures accuracy on a consistent panel; do NOT make it dynamic. SCHEDULING
  (fixed 2026-07-14 — the whole evening pipeline was previously ONLY a manual
  Desktop launcher, never actually cron'd): systemd user timer stockai-daily
  (~/.config/systemd/user/stockai-daily.{service,timer}) runs
  scripts/run_daily_log.sh weekdays 18:15 IST with Persistent=true — if the
  machine was off at 18:15, it fires at next boot; a market-hours guard in the
  script skips runs during NSE hours (partial-candle pollution). agent_sim
  self-heals a missed month-end (runs the rotation LATE at today's prices,
  logged month_end="late"). Fully-off days are simply skipped — sim/paper are
  idempotent per index date. Manual runs still work (Desktop launcher or
  ./run_daily_log.sh)
- `sr_dynamic_logger.py` — SEPARATE dynamic watchlist (holdings + top-10 F&O-gated
  momentum) logging to sr_dynamic_log.csv, for extra forward calibration data on
  deployment-relevant names. Never merge it with the fixed panel above. Analyse via
  `python sr_monthend_analysis.py --log ../data/sr_dynamic_log.csv`
- Ops/monitoring scripts (all in run_daily_log.sh unless noted):
  `data_integrity_check.py` — nightly scan of all data CSVs for the three
  corruption classes that have actually bitten (bad-Date rows, decimal-shift
  price glitches, stale series); notify-send on WARN. Since 2026-07-17 it
  also detects CORPORATE ACTIONS on held names (real/paper/sim books):
  yfinance back-adjusts, so a split rewrites history and the books' qty/avg
  silently diverge (plus a false -18% STOP alert as the price halves) —
  detected by snapshotting each held name's close at a reference date
  (data/held_close_snapshot.json, self-healing on any qty change) and
  warning if that PAST close later shifts >15%, with the implied factor.
  Reconcile via `python record_fill.py adjust SYM FACTOR` (factor = new
  shares per old: 1:5 split -> 5, 1:1 bonus -> 2) — preserves invested
  value, journals an ADJUST row, never touches cash. `news_watchdog.py` —
  ALERT-ONLY LLM (Ollama qwen2.5:7b, keyword fallback) severity-classifies
  fresh NSE announcements on held names, notify-send on HIGH; it is NOT a
  trading signal and must never be wired into exit_engine/paper_trader
  (the automated announcement exit veto was backtested and REJECTED — see
  memory exit-announcements-rejected). ADVISORY-CALL LEDGER (2026-07-17):
  every full_advisor.py BUY call is appended to data/advisor_calls_log.csv
  (deduped per data-date+symbol; nightly via `full_advisor.py --log` in
  run_daily_log.sh, manual runs log too). `python call_report.py` scores
  them Univest-style — fill at buy_at (Low touch within 10d), then
  first-touch target-vs-stop race (42d from fill, same-day both =
  AMBIGUOUS counted as stop), plus a fill-model-free 21d mark from
  call-day close; aggregates by regime. Measurement only — the ledger must
  never feed back into scoring/selection. `gate_report.py` (manual, monthly) —
  scores each completed paper month at its percentile of the production
  backtest's 21d-return distribution; this is the deployment gate made
  quantitative (2+ months below p10 = live path diverges, investigate).
- STALE-DATA GUARD ON THE ADVISOR (2026-08-01, full_advisor.py): the nightly
  data_integrity_check.py can WARN on a stale CSV, but nothing stopped
  full_advisor.py from issuing a BUY call quoting a days-old close as
  "current price" in between checks. compute_buy_calls now compares each
  candidate's last bar against the INDEX's last bar (not wall-clock — the
  market can be legitimately closed with zero staleness) and excludes any
  name more than MAX_STALE_SESSIONS=3 sessions behind, surfacing the skip
  list in both the quiet (`--log`) and full report output. Returns a 4-tuple
  now: (regime, top_sectors, buy_list, stale_skipped).
- BACKTEST UNIVERSE COVERAGE BUG FIXED 2026-08-01 (backtest_portfolio.
  load_price_matrix): the NaN-share filter was computed over the FULL
  2015-2026 panel, so any name listed after ~2020 necessarily had >20%
  leading NaN and was dropped for its ENTIRE history. 185 of 498 candidate
  columns were being silently excluded — 100% pure leading-NaN (recent
  listing), 0% genuine interior gaps, confirmed by inspection before
  changing anything. 39% of TODAY's live-eligible universe (35 of 89 names,
  including the entire current top-4: WELCORP/LAURUSLABS/RADICO/ADANIENSOL)
  had literally never appeared in any backtest. The engines were already
  point-in-time safe for this (momentum_score/liquid_symbols_at both return
  None/skip on insufficient history up to bar i) — only the matrix loader's
  filter was global instead of per-column-since-listing. Fixed: NaN share
  now measured from each column's own first valid date. Coverage is now
  100% (89/89 eligible names present). Result: 19.18%->27.18% CAGR,
  Sharpe 0.92->1.09, MaxDD 37.1%->27.65%; walk-forward mean CAGR
  26.22%->31.47%, mean Sharpe 1.16->1.24, still 1/19 negative windows.
  SANITY-CHECKED against curve-fitting via the recently-listed tail: names
  listed pre-2021 ALONE already deliver 23.26% CAGR/Sharpe 1.00 (most of
  the gain), and walk-forward on that mature-only subset (32.86%/1.31)
  slightly BEATS the full-universe walk-forward — the improvement is not
  concentrated in thin, fragile recent-IPO history. 52 of the newly-restored
  pre-2021 names are established F&O constituents that had been silently
  excluded from every backtest since this bug existed (HAL, HDFCLIFE,
  HDFCAMC, AUBANK, BANDHANBNK, DIXON, CAMS, ADANIGREEN, CDSL...). This is
  the new production baseline — re-run before quoting older numbers.
- MONTH-END = LAST TUESDAY (2026-08-01 user spec, fixed in code). The trading
  engine used the last CALENDAR trading day while the S/R subsystem already used
  the last Tuesday — so live rebalances ran up to 3 days LATE nearly every month
  (July 2026 fired 07-31; spec says 07-28). `exit_engine.is_last_trading_day_of_month`
  now resolves via `exit_engine.rebalance_day()` → sr_horizon.last_tuesday_of_month,
  rolled BACK to the prior session if that Tuesday is an NSE holiday (March 2026
  → Mon 03-30). One definition now drives exit_engine, paper_trader, agent_sim and
  ai_assistant; verified to fire exactly once per month across 199 months. The
  BACKTEST is unaffected — it rebalances on a fixed 21-day grid, not calendar
  dates (still 20.80% CAGR after the change).
- CHART ANALYSIS (chart_analysis.py, new 2026-08-01) — candlestick/price-action
  read: trend structure (higher-highs/lower-lows via swing points), 20/50/200 EMA
  posture + stacking, position in 52w range, volume behaviour (surge, up-vs-down
  volume), volatility squeeze/expansion, and named candle patterns (hammer,
  shooting star, engulfing, doji, marubozu, morning/evening star) over the last
  10 bars, each with date + bars_ago so a claim is checkable. MULTI-TIMEFRAME
  (2026-08-01): every function is a pure OHLC-in dict-out call, so the SAME
  trend/MA/range logic also runs on weekly-resampled bars (resample_weekly,
  W-FRI) — no separate weekly code path to drift. `timeframe_agreement`
  reports ALIGNED/MIXED/CONFLICTING between daily and weekly trend structure
  (e.g. daily UPTREND inside a weekly RANGE/DOWNTREND = unconfirmed setup,
  not a real breakout). Needs ~60 weekly bars (~14mo); below that the weekly
  key degrades to an explicit "insufficient history" rather than a spurious
  read. On 2026-08-01's top-8 advisor candidates, 5/8 showed daily UPTREND
  vs weekly RANGE_OR_TRANSITION — genuinely differentiating, not decorative.
  Exposed as the assistant's `chart_analysis` tool and printed per buy call
  in full_advisor. HARD RULE: it is DESCRIPTIVE ONLY. Nothing in chart_analysis.py may be wired
  into exit_engine/paper_trader/agent_sim or the scorer — candle patterns are
  not walk-forward validated here, and every auxiliary overlay tested so far
  (delivery%, OI/PCR, announcements, resistance-fade, trailing stop) was
  REJECTED. Thresholds are the conventional textbook ones, deliberately NOT
  tuned on this universe (tuning them here would be curve-fitting).
  ANCHORED VWAP (2026-08-01, anchored_vwap_from_last_swing_low): volume-
  weighted average price from the most recent swing low to now — "are buyers
  since this trend attempt began net profitable or underwater". Distinct
  from support_resistance.py's fixed-252d volume-profile HISTOGRAM (that
  module is separately owned/tuned, not touched); {} fallback if no volume.
  RELATIVE STRENGTH (2026-08-01, relative_strength): stock return minus
  Nifty return over 21/63/126d, PLUS whether the RS LINE (price/index ratio)
  itself is trending — catches "up 20% in an 18%-up market" (barely
  outperforming despite a strong raw number) and separates ACCELERATING
  outperformance from a fading one. Needs an index series passed in
  (core.load_index()) — this is the one chart_analysis function that isn't
  pure-df, since relative strength is definitionally vs an external
  benchmark; callers (ai_assistant, full_advisor) load the index once and
  pass it through, not per-symbol.
- HORIZON ADVICE (ai_assistant.horizon_advice, new 2026-08-01) — the
  composite "what should I do with X over horizon Y" tool: ties regime +
  momentum score/rank + S/R levels (with reach probability correctly scaled
  to the ACTUAL requested horizon via sr_horizon, never a flat 21d number)
  + chart_analysis into one narrative. Works for any symbol, tracked or not;
  accepts an optional horizon_date (defaults to this system's month-end/
  last-Tuesday horizon) and optional entry_price. Read-only composition over
  already-validated/already-descriptive components — computes nothing new,
  so it inherits the "descriptive, not a signal" status of its parts. Flags
  a support/resistance level as `too_far_to_be_relevant` past 15% distance
  (a momentum breakout name's nearest support is routinely 40-80% below
  price — technically correct but meaningless to surface as a bare
  percentage without that flag). Assistant toolset is now 10 tools.
- EARNINGS AWARENESS (ai_assistant.earnings_watch, new 2026-08-01) —
  DISPLAY-ONLY estimated next-earnings date, surfaced in stock_status and
  horizon_advice (flagged if it falls inside the requested horizon: "expect
  a volatility event"). Method: NSE 'Outcome of Board Meeting' announcements
  whose text matches "financial result" or "financial statem" (NSE phrasing
  varies and data/announcements/ text is truncated) ARE the historical
  earnings dates; projects the next one as last_result + median(sane
  trailing gaps, 75-100 days, filtering backfill/pagination artifacts in the
  announcement history — verified against RELIANCE's real gaps which
  included spurious 545-546-day values). Prefers a formally-SCHEDULED board
  meeting date (companies announce ~1 week ahead) over the cadence estimate
  when one exists and is still in the future. Surfaces
  `announcements_data_age_days` and a `data_stale_warning` above 14 days —
  download_announcements.py is a ONE-TIME backfill script, NOT in the nightly
  run_daily_log.sh pipeline, so this data goes stale in practice (measured
  20-22 days stale on 2026-08-01) unless re-run manually or added to the
  nightly pipeline (an ops decision, not made here — adds an NSE network
  call to the daily run). NEVER wired into exit_engine/scoring — same
  descriptive-only status as chart_analysis, and the automated announcement-
  driven exit VETO was separately backtested and REJECTED already (memory
  exit-announcements-rejected) — this is awareness, not a trading rule.
  BUG CAUGHT DURING TESTING: horizon_end_date must be compared as a
  CALENDAR date, not a trading-day count — an earlier version compared a
  calendar-day projection against sr_horizon's trading-day count and
  produced a false "outside horizon" on a real case (WELCORP: estimate 21
  calendar days out, horizon 17 trading days, both correctly inside the same
  ~25-calendar-day window). Fixed to compare against the actual end date.
- ADVISOR REBUILT 2026-08-01 (full_advisor.py) — it was recommending stocks the
  validated strategy would never buy. Three structural defects, all fixed:
  (1) a TOP-3 SECTOR GATE that isn't in the backtest at all (backtest ranks the
  whole gated universe + 2-per-sector CAP). It blocked 9 of the top 10 names by
  momentum score; advisor called DIXON (score 13.8) while the strategy wanted
  WELCORP/LAURUSLABS/RADICO/ADANIENSOL (40-51). ZERO overlap. Sector scores were
  noise-level apart (0.117 vs 0.088) — PHARMA missed the cut by 0.007 and took
  LAURUSLABS with it. Gate removed; select_top_n_capped now applied, and each
  call carries `in_strategy_top_n`. (2) LEVELS WERE ANTI-MOMENTUM: entry was
  priced AT nearest support and rr measured against it, but momentum leaders sit
  35-44% above support ⇒ unfillable limits, rr 0.01-0.71 always failing rr>=1,
  and stops far wider than the -18% engine stop. get_trade_levels is now ATR-based
  (ENTRY/STOP/TARGET_ATR_MULT in full_advisor.py): shallow pullback entry floored
  at nearby support, stop capped at CATASTROPHIC_STOP, target = resistance or ATR
  projection for names at 52w highs. (3) hardcoded RSI>75 hard-reject contradicted
  RSI_OVERBOUGHT=80 being ADVISORY — now a flag, not a filter. Result: 3 → 73
  candidates, entries 0.6-2.3% below close, and the advisor's top-4 now EQUALS
  the strategy's top-4. The July ledger's 1/8 fill rate was this bug, so
  pre-2026-08-01 advisor_calls_log rows are NOT comparable to later ones.
- SECTORS.JSON GAP FIXED 2026-08-01: 14 eligible names (WELCORP, RADICO,
  LLOYDSME, SONACOMS, M&MFIN, NAM-INDIA, EXIDEIND, HEG, CHENNPETRO, IIFL,
  360ONE, PPLPHARMA, ANANTRAJ, MANAPPURAM) were unmapped and all shared ONE
  "UNMAPPED" bucket in select_top_n_capped — so unrelated businesses consumed
  the 2-per-sector cap against each other. Now mapped (backup:
  data/_quarantine/sectors_pre_2026-08-01.json). Backtest 19.18%→20.80% CAGR,
  but walk-forward says +0.72pp mean CAGR / +0.03 Sharpe winning only 11/19
  and 10/19 windows — NOT significant by this repo's bar. Justification is
  MECHANICAL (a data defect), not performance; don't cite it as an edge.
- ETF data (GOLDBEES, MON100) lives in `data/etf_data/` (download_etf.py), NOT price_data/ —
  price_data is globbed as the universe by core.market_breadth_pct/liquid_universe,
  and a high-turnover ETF there would enter the tradable top-200 and could get bought
  by the strategy. support_resistance.load_stock and sr_monthend_analysis fall back
  to etf_data/ automatically
- OPS CADENCE: NOTHING new needs running daily. run_daily_log.sh (systemd
  timer stockai-daily, weekdays 18:15 IST, Persistent=true) already runs the
  downloads + both S/R loggers; it now also rotates cron_daily_log.log at 5MB
  (it was append-only and unbounded). `./sr_monthly_review.sh` is the ONCE-A-
  MONTH read-only review (run after the month's last Tuesday): fixed panel at
  the production horizon, at 21d, at 10d, plus the dynamic panel, with the
  reading notes inline. It writes nothing and is safe to re-run.
  Do NOT rebuild the P(touch) tables monthly — they're built from ~10y of
  history so a month barely moves them, and rebuilding on the same cadence you
  MEASURE on lets the thing under test change underneath the test. Rebuild
  only when price history is materially extended, then re-check the
  monotonicity invariant.
- `sr_monthend_analysis.py` checks hit-rate, level drift, probability calibration,
  n-sensitivity, distance-vs-accuracy — run only after 2-3+ weeks of logged data.
  Section 0 is DATA QUALITY (duplicate pairs, partial days, frozen price
  series) and must be read FIRST — a pipeline bug and a miscalibrated model
  look alike in the hit rate but need opposite fixes. `--to-month-end` states
  explicitly when it is IGNORED (log has no HorizonDays yet) rather than
  silently falling back to 21d.
  MEASUREMENT BUG FIXED 2026-07-31 — it reported a fake 100% hit rate on every
  panel/level/bucket. Two compounding causes, both about the hit/miss asymmetry:
  (1) the old `resolved = FwdDays>=21 | Hit` filter kept every hit but dropped
  unresolved misses; with a log shorter than 21 trading days NO row ever
  reached FwdDays>=21, so "resolved" collapsed to hits-only ⇒ 100% by
  construction. (2) even after fixing that, returning True the moment a touch
  occurred (bar 3) while a miss waited the full window meant hits resolved
  early and misses never did — still 100%. check_touch now scores ONLY when
  the full W-bar window exists, so both outcomes face the same bar count, and
  returns None (excluded, never counted as a miss) otherwise.
  A 21d rate needs 21 bars AFTER the log date — with ~1 month of data, use
  `--window 7/10` for a genuinely resolvable (but shorter, non-comparable to
  the 21d ~65-68% baseline) horizon. `--exclude-day0` drops levels already
  inside the touch band at log time (guaranteed hits, zero predictive content,
  worth ~7pp). `--to-month-end` scores each row against its own logged
  HorizonDays = the production question. First honest read (2026-07-31, fixed
  panel, 10d, day-0 excluded): S1 60.4% / R1 59.6% (n≈101-104); with day-0
  included 69.1%/68.4%. Dynamic panel S1 looks terrible (21-28%) purely
  because its supports sit a median -10.7% away (S2 -20%) vs -1.9% on the
  fixed panel — composition, NOT miscalibration; distance decay is clean and
  monotone (0-5%: 76%, 10-15%: 3%, 15%+: 0%). v2 is NOT yet confirmed or
  contradicted — no 21d window has closed. Re-run `--window 21` once it has.
  Data quality of the first month: zero duplicate (Date,Symbol) pairs
  (data-date stamping works), corp-action guard present but never fired, BUT
  10 legacy pre-v2 rows dated 2026-07-03 carry probs outside v2's 57-78 range
  (22-100) and partial-coverage days exist (07-15: 3/15 symbols, 07-17: 2/15).

## Known gotchas — do not rediscover these the hard way
- yfinance miscategorizes RELIANCE as IT sector — always trust sectors.json
- GOLDBEES.NS.csv arrived with two rows (2019-12-19/20) at exactly 1/100th
  price (failed yfinance split-adjustment) → fake -99%/+10400% returns that
  blew one walk-forward window up to +330% annualized. CSV patched;
  bp.load_gold_period_returns has a 3x-vs-rolling-median spike guard so a
  refetch can't silently reintroduce it. If a blended metric ever looks
  impossibly good, check the component series for a decimal-shift glitch
  FIRST — a blend of bounded sleeves cannot outperform all its components
- Testing paper_trader changes: NEVER call step() against the real
  ../data/paper_state.json mid-day — it consumes the once-per-date
  idempotency slot and tonight's cron run would then skip. Sandbox by
  monkeypatching paper_trader.STATE_PATH/LOG_PATH/EQUITY_PATH to scratch
  copies first
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
  (pd.to_datetime(..., errors="coerce") + filter); support_resistance
  load_stock/load_delivery guarded too (2026-07-17 — the S/R loggers read
  through them); other CSV loaders in the repo may not be — if you hit a
  sort_index TypeError, check for this first
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
- trim_partial.py only cleans price CSVs at pipeline time — ANY later download
  during market hours (a parallel research session, an ad-hoc script) re-writes
  today's PARTIAL candle into price_data (caught live 2026-07-15: 46 files
  re-polluted minutes after the pipeline trimmed 416). S/R loggers defend
  themselves (sr_daily_logger.drop_partial_candle) and stamp rows with the
  DATA date, not the run date (a mid-market run's data ends at yesterday's
  close — wall-clock stamping double-logged the same snapshot under two dates).
  Other consumers (exit_engine, paper_trader, agent_sim) trust file state — if
  a mid-market number looks off, check the CSV's last row for today's date first
- A machine booted with a stale clock (NTP syncs mid-run) can leave systemd
  timer LAST-run stamps in the FUTURE — the next scheduled firing is then
  silently skipped (happened 2026-07-15: daily timer stamped 19:26 IST by a
  13:30 boot run, so the real 18:15 run never fired). If an evening run seems
  missing, `systemctl --user list-timers` and compare LAST against wall clock;
  `systemctl --user start stockai-daily.service` runs it manually

## My preferences
- Direct, no filler. Exact file names + line numbers, not vague descriptions.
- Show current code before showing replacement.
- Don't explain "why this is best practice" unless I ask — I want the change, not the lecture.
- When testing changes to the S/R or strategy engine: one variable at a time, re-run
  backtest, report before/after numbers, revert if it doesn't help. Don't bundle
  multiple untested changes together.
- Flag anything that risks overfitting to my specific 3-year backtest window before
  I implement it.