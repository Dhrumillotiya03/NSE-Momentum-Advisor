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
  THE CRITIC WAS BLIND TO ECONOMICS UNTIL 2026-09-01, AND IT MATTERED. The
  2026-08-25 month-end rebalance executed with "critic problems: 0" while
  being wrong by up to 19x. `position_sizes()` correctly returned
  {"weight": "25.2%", "quantity": 394}; the ADVISOR LLM rendered it as prose
  reading "Weight: 25% ... Quantity to Buy: Calculated based on your current
  portfolio value" — DROPPING the quantity — and the TRADER LLM read the
  percentage as a share count and ordered 25 SHARES of each name. HFCL (its
  own top pick) got ₹5,950 against a ₹97,462 target = 6%; RADICO got 119%.
  "25" is a plausible share count for 3 of the 4 names, which is why nothing
  downstream blinked. SECOND, independent failure the same session: orders
  were applied ADD-TO rather than REBALANCE-TO, leaving the book 54.7%
  deployed against the 37.5% BEAR mandate (paper book, same window: 37.8%).
  The old critic only asked "did a BUY produce a position" and "does the
  journal row count match" — record_fill did its job perfectly; the number
  the HUMAN acted on was wrong, which for a signals-only system is precisely
  the failure the sim exists to rehearse. NOTE position_sizes' docstring
  claims "'how much quantity' can never be improvised by the LLM" — true of
  the TOOL, false of the PIPELINE, since the tool's output still has to
  survive being re-rendered into prose. Critic now also checks ECONOMICS:
  check_notional_vs_plan (executed RUPEES vs position_sizes()'s own plan,
  ±40% — rupees not share counts, so it is independent of wording),
  check_exposure (deployed share vs REGIME_EXPOSURE, ±10pp),
  check_advice_truncated (the trader only ever sees the first 6000 chars and
  a month-end review is the longest message of the year). Verified by
  replaying the real 08-25 orders: 4 problems where the old critic raised 0,
  and a control executing position_sizes()' own quantities raises 0.
  Affordability downsizes are now logged as deviations instead of silently
  shrinking a fill. ALSO FIXED: `model_accuracy` scored MANDATED month-end
  rotations as directional sell forecasts — under laggards-only a month-end
  sell fires because the name left the top-N, so scoring it by "did it keep
  falling" reads badly forever by construction; mandated and discretionary
  sells are now separated. See memory
  agent-sim-critic-blind-to-economics-2026-09.
  Its equity.csv still held the un-repaired 2026-08-19 row from the
  early-rebalance bug (cash ₹256.30 against a journal with no 08-19 trade);
  repaired 2026-09-01 to ₹1,004,348.30 / ₹634,489.70, validated by
  reconstructing 08-17 from the journal and matching its logged equity to the
  paisa (backup data/_quarantine/agentsim_equity_pre_0819fix_2026-09-01.csv).
  paper_trader had been replayed in August; agent_sim had not, and it has no
  catch-up mechanism — it covered 31/37 sessions vs paper's 37/37.
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
  Same intraday service ALSO runs log_market_depth.py (2026-08-10,
  PREREG_slippage_depth_calibration.md, RESEARCH DATA ONLY — infrastructure
  for Study 4 of the state-of-the-art program, see memory
  state-of-the-art-program-2026-08): snapshots 5-level bid/ask depth for
  the F&O-liquid universe (kite.quote()'s depth field — verified present
  in the same MODE_FULL tick shape live_ticker.py already reads, just not
  stored) to data/market_depth/depth_YYYY-MM-DD.csv. Kite depth has NO
  historical endpoint (memory kite-intraday-capability-2026-08) — unlike
  intraday price bars, this cannot be backfilled, so the logger exists to
  start the clock, not to run a one-off backfill. Goal: calibrate
  research_slippage.py's uncalibrated square-root impact constant K
  against real observed spread/depth instead of an assumed value — the
  actual calibration is deferred until enough sessions accumulate to
  design its decision rule against real data characteristics, not a
  guessed schema.
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
- CAPACITY CURVE AT REAL CAPITAL (2026-08-14,
  research_slippage_capacity.py). The ₹10L above is a config.yaml DEFAULT,
  not a decision — the real Zerodha book is ₹68L, so the "re-run before
  scaling capital" trigger was already met and unnoticed (the assumed and
  actual figures live in different files). USER'S INTENT, asked directly
  2026-08-14: a ₹10-20L CARVE-OUT, NOT converting the whole book. CAGR
  drag vs a 27.35% baseline — ₹10L: 0.95/1.89/3.76pp at K=5/10/20; ₹20L:
  1.34/2.67/5.29pp; ₹68L whole book: 2.46/4.89/9.61pp; ₹2Cr:
  4.20/8.28/16.08pp. So the planned carve-out is FINE (impact stays at or
  below the modelled 10bps/side commission until ~K=10) while a full-book
  conversion would NOT have been. K IS STILL UNCALIBRATED — treat the
  K=5..20 spread as real uncertainty, not a range to pick from; narrowing
  it is exactly what log_market_depth.py exists for, which puts the depth
  collection problem ON THE CRITICAL PATH (depth → K → impact at real
  capital → is this deployable), not off to the side in research infra.
  Still NOT folded into production COST, same reasoning as above.
  METHOD (reusable): %ADV is strictly LINEAR in capital (order value =
  capital × exposure × weight; ADV is capital-independent), so order sizes
  are collected ONCE and scaled — verified, not assumed (direct collection
  at ₹20L reproduces the scaled vector with max abs diff 0.0 across 657
  orders). Aggregate impact as mean(k·√%ADV) over REAL orders, NOT
  k·√(median %ADV): impact is concave and order sizes are right-skewed
  (p99 ≈ 40× median), so collapsing to the median first understates the
  drag — research_slippage.py's own Part C does this. Tax stacks on top
  and is the larger drag at this cadence (research_net_returns.py). See
  memory slippage-capacity-curve-2026-08.

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
  whipsawing out of momentum trends), 20% max position — WHICH DOES NOT BIND
  AT THE PRODUCTION n, see the MAX_WEIGHT entry below; realised single-name
  weight is 33.3% in SIDEWAYS and 25.0% in BEAR — sector cap 2/sector
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
  EFFECT SIZE CORRECTED DOWN 2026-09-01 — THE STUDY'S BASELINE RAN THE WRONG
  ENGINE. `walk_forward.run_window` defaulted to `run_backtest` (the LEGACY
  hard-close engine), and research_conviction_sizing.py built its baseline
  with a bare `run_window(matrix, index, turnover, s, e)` while PRINTING
  "Running BASELINE (production run_backtest_laggards_only, sizing_fn=None)".
  Candidates DID pass engine=run_backtest_laggards_only, so the study measured
  hard_close vs laggards_only + conviction, not conviction. Measured on
  current data: hard_close 28.90%, laggards_only+inverse-vol 29.91%,
  laggards_only+conviction 0.50 31.76% — every candidate got a free ~1pp CAGR
  head start from the engine alone. Corrected, tilt=0.50 is **+1.85% mean
  CAGR, CI [+0.54%,+3.17%], 26/36 windows** (recorded: +2.89%,
  [+1.33%,+4.33%], 33/36). It STILL clears the pre-registered bar, so THE
  ADOPTION STANDS — at ~2/3 the effect and a materially weaker win rate. Quote
  the corrected figures. `engine` is now a REQUIRED argument on run_window
  (TypeError if omitted) rather than defaulting to anything; all four affected
  baselines are explicit. The trend-quality and exit-flow REJECTIONS are
  unaffected in direction (the confound favoured the candidates they
  rejected). See memory walk-forward-baseline-engine-2026-09 — and note what
  caught it: not any CI/win-count/era-split check, all of which describe the
  DELTA and were consistently wrong, but running ONE configuration down TWO
  code paths and requiring the answers to match.
  New production baseline (python walk_forward.py --engine laggards_only,
  standard 19-window/6mo-step config): mean CAGR 33.4%, median 36.0%, mean
  Sharpe 1.25, mean max DD 21.5%, worst DD 30.2%, 1/19 negative windows.
  THESE ARE FIXED-GRID, ONE-PHASE, 21-DAY-SAMPLED-DRAWDOWN, GROSS — corrected
  2026-09-04, see the REAL CALENDAR entry below. The honest figures (real
  last-Tuesday calendar, daily-curve drawdown, `python walk_forward.py
  --real-calendar`): mean CAGR ~30%, Sharpe ~1.2, worst-case DD ~37%, 2/19
  negative. Net of STCG + measured impact ~25% mean. FULL PANEL on the real
  calendar is only +20.6% — a compounding-path artifact (see below), NOT the
  number to quote. And the ERA SPLIT is the load-bearing caveat: real-calendar
  non-overlapping eras are 2015-2018 +13.9% / 2019-2021 +64.0% / 2022-2026
  +3.0% — the strategy has made low-single-digit CAGR since 2022 against a 41%
  drawdown, and the ~30% walk-forward mean is 2019-2021 propagating through
  overlapping windows.
  (Prior baseline before this change: 27.18% single-run CAGR/Sharpe
  1.09/DD 27.65%, wf mean 31.47%/1.24 — see the 2026-08-01 universe-coverage
  entry above. Re-run before quoting any number; they drift with every
  data pull.) `core.position_size` (rupee allocation given an already-known
  weight) is UNCHANGED — it never computed the weight itself, so no
  behavior to fix there. `full_advisor.py`'s ATR-based per-trade risk
  sizing is a DIFFERENT, separate mechanism (risk-per-trade off stop
  distance, not portfolio-weight allocation) — NOT touched, was never part
  of this study's evidence, would need its own separate validation.
- MAX_WEIGHT=0.20 IS A DEAD LETTER AT THE PRODUCTION n — MEASURED, TESTED,
  NOT CHANGED (2026-09-01, research_max_weight_cap.py +
  _adversarial.py, PREREG_max_weight_cap.md). The cap is applied as
  clip-then-renormalize in four places (backtest_portfolio.py's sizing block,
  paper_trader.py, ai_assistant.py, and described to the user by
  recommend.py). Whenever 1/n >= MAX_WEIGHT every name clips and renormalizing
  n identical values returns exactly 1/n — straight back ABOVE the cap it just
  applied. You cannot have 3 names each <=20% summing to 100%. Measured over
  126 rebalances: SIDEWAYS (n=3) 62/62 fully clipped, BEAR (n=4) 18/30, BULL
  (n=10) 0/34 — so **63.5% of all rebalances are EXACTLY equal-weighted and
  CONVICTION_TILT does nothing at all in them**, and the live paper book (BEAR
  since 2026-07-10) has never once used the sizing change this repo adopted in
  August. concentration-risk-2026-07 diagnosed this exact mechanism at BEAR=2
  and strategy_config records the fix as complete — it was not, 4 x 20% = 80%
  < 100%. TESTED: cap 0.30, cap 0.35, and an absolute cap (clip at 0.20,
  un-allocable remainder to cash). **0/3 cleared**; cap_0.35 is marginal
  (+0.59%, CI [+0.09%,+1.39%], but 22/36 windows against a 22.74 gate) and per
  the prereg that CLOSES the line rather than prompting a retune. The
  decomposition is the valuable part: the TILT is the mechanism (+1.85% at
  today's cap, +3.63% with headroom) while CONCENTRATION ALONE IS A DRAG
  (-1.19%, CI [-2.42%,-0.28%], 9/36) — raising the cap buys more tilt and pays
  for it with concentration, and the two nearly cancel. Do NOT raise the cap
  to unblock the tilt; a future attempt needs a lever that does not buy
  concentration at the same time (tilting the NUMBER of names or the deployed
  fraction), pre-registered separately. Also measured: at cap 0.35 the worst
  single-name weight reaches 45.0% (vs 33.3% today), and honouring the cap
  absolutely costs ~-3.6pp CAGR for ~-5.1pp worst-case DD — a real risk/return
  trade-off, recorded not adopted. THE FIRST RUN OF THIS STUDY SAID ADOPT
  (+3.45%, 34/36) and was wrong — see the run_window engine-default bug in the
  conviction-sizing entry above; the adversarial checklist is what caught it.
  Production behaviour is UNCHANGED; the deliverable was the documentation fix
  (recommend.py no longer claims a 20% cap, and no longer claims inverse-vol
  sizing either — that was superseded 2026-08-05 and never updated).
- REBALANCE TIMING LUCK IS THE LARGEST UNMEASURED TERM IN EVERY NUMBER THIS
  REPO QUOTES (2026-09-02, research_timing_luck.py). The rebalance grid's
  PHASE — which day of the month the book rotates — is arbitrary, and had
  never been measured. Running the IDENTICAL strategy on all 21 phases
  (backtest_portfolio.run_backtest_laggards_only gained a `phase=` argument;
  phase=0 is byte-identical to every historical number):
      full panel  CAGR 21.94% .. 33.13%   SPREAD 11.19pp, sd 3.23pp
                  Sharpe 0.89 .. 1.31     maxDD 23.29% .. 33.49%
  Same rules, same data, same universe — only the calendar phase differs.
  **That spread is larger than every effect the research programme has ever
  adopted or rejected**: conviction sizing +1.85pp (ADOPTED), cap_0.35
  +0.59pp, sectors.json +0.72pp, trend-quality +1.14pp. Controlled for the
  obvious confound — a passive Nifty buy-and-hold over the same slicing moves
  only 0.89pp, so the strategy moves 10x more and this is PHASE, not start
  date. Mechanism is standard (Newfound Research, "Quantifying Timing Luck"):
  timing-luck vol scales with turnover x portfolio vol / sqrt(rebalance
  frequency), and monthly + high-turnover + 3-4 names is the worst case on all
  three axes, so a large number here is expected, not anomalous.
  PHASE-AVERAGED WALK-FORWARD (19 windows x 7 phases): mean CAGR **31.51%,
  sd 2.61pp, range 28.42%-34.50%**. The long-quoted 33.4% is phase 0, which
  sits near the TOP of that range, and its worst-case DD (30.2%) is the BEST
  of the seven phases (others reach 35.8%). QUOTE 31.5% +- 2.6pp, not 33.4%.
  Reassuringly, phase 0 is only the 43rd percentile on the full panel, so the
  repo has not been quoting a cherry-picked phase — but it has been quoting a
  point estimate where a distribution was needed.
  THE LEVELS/DELTAS DISTINCTION, MEASURED — this is the load-bearing caveat and
  it CORRECTS the alarming first reading. The 11pp applies to LEVELS (absolute
  CAGR/Sharpe/DD). A PAIRED delta between two configs on the SAME phase largely
  cancels the phase term: the ADOPTED conviction tilt re-tested across phases
  0/5/10/15 measures **+1.87/+1.93/+1.65/+1.75pp, PASS on 4/4**, mean +1.80%
  against the +1.85% recorded on one phase — a 0.28pp spread while the baseline
  LEVEL underneath it moves 5.3pp (27.27%-32.59%). So **past adopt/reject
  decisions are NOT invalidated by timing luck**, because they were all paired.
  What IS true: phase-sensitivity of the DELTA is diagnostic. A real effect
  barely moves; a null one swings (residual momentum ranged -1.30 to +0.21pp on
  the same config, and a single-phase test landing on phase 10 would have
  reported a mild positive). Hence the phase gate is a DISCRIMINATOR to run on
  anything marginal, not a blanket invalidation.
  CONSEQUENCES: (1) never quote a LEVEL from one phase — use the phase-averaged
  figure; for a marginal DELTA (under ~2pp) clear the bar on >=3 phases (now a
  pre-registered gate, see PREREG_residual_momentum.md, which was its first use
  and where it worked); (2) part of
  gate_report's 7.35% per-period sd is timing luck, not market noise; (3) the
  BACKTEST steps a fixed 21-day grid while LIVE rotates on the last Tuesday —
  they are not even the same phase, and divergence_check.py cannot see this
  because it compares selection on a given day, not the rebalance calendar.
  walk_forward.py now prints this caveat above every run and takes `--phase`.
  TRANCHING MEASURED, NOT ADOPTED — it conflicts with the fixed mandate.
  Splitting capital across N sleeves on staggered phases (the Jegadeesh-Titman
  overlapping-portfolio construction) is exact to compute, since sleeves are
  self-contained and total wealth is their sum:
      1 tranche  mean +27.86%  sd 3.35%  spread 11.49%
      2          mean +27.73%  sd 1.69%  spread  5.70%   (sd -50%)
      4          mean +28.03%  sd 0.74%  spread  2.53%   (sd -78%)
      7          mean +28.24%  sd 0.60%  spread  1.37%   (sd -82%)
  Mean is unchanged — it is a VARIANCE reduction, not alpha, exactly as
  Jegadeesh-Titman report. But it needs N decision dates per month against the
  mandated one (last Tuesday), N x the positions on a 3-4 name book, and N x
  the STCG events. MANDATE CONFIRMED 2026-09-04 (one last-Tuesday rebalance,
  not relaxed) so this is NOT ADOPTED — and simulating the real last-Tuesday
  calendar (see below) removes the timing-luck problem tranching solved.
  Also measured: tranching does NOT reduce single-name concentration (every
  sleeve converges on the same momentum leader). (The 3-tranche row is worse than 2 because 21/3=7
  spaces the phases in a way that happens to correlate on this panel — a
  small-sample artifact of only 21 offsets, not a real non-monotonicity.)
- THE BACKTEST NOW SIMULATES THE REAL LAST-TUESDAY CALENDAR (2026-09-04,
  research_real_calendar.py, research_turn_of_month.py,
  real-rebalance-calendar-2026-09). `run_backtest_laggards_only` gained
  `rebalance_idx=<session indices>` — an explicit schedule replacing the fixed
  `LOOKBACK+21+phase .. n-HOLD .. HOLD` grid. `None` = fixed grid, verified
  EXACT under PYTHONHASHSEED=0 (13665296.343058722 — the engine has
  pre-existing set-iteration nondeterminism at ~1e-8 relative, so pin the seed
  for any byte-identical check). `backtest_portfolio.last_tuesday_rebalance_idx
  (matrix)` builds the real schedule via `exit_engine.rebalance_day` (133
  rebalances, session gaps 16-25, mean 20.6, vs the grid's rigid 21).
  `performance()` gained `years=`/`avg_period_days=` (both None = old formula,
  every existing caller byte-identical). `walk_forward.py --real-calendar`
  runs it with daily-curve drawdown.
  WHY: the fixed grid is not the calendar the user trades, and its PHASE is
  worth 11pp of CAGR (timing luck). Under a firm last-Tuesday rule there is
  exactly ONE calendar, so the phase stops being a free parameter — this is
  the real fix for timing luck, and it is why tranching was measured and then
  dropped rather than adopted.
  NUMBERS (real calendar, daily drawdown): walk-forward mean CAGR ~30%, Sharpe
  ~1.2, worst-case DD ~37%, 2/19 negative. Full panel is only +20.6% — a
  COMPOUNDING-PATH artifact: the 4 recent windows where the real calendar lags
  the fixed grid (-27 to -34pp, 2020-2021 vintages) are the large-capital
  windows and dominate the compound. Do NOT quote +20.6%; quote the
  walk-forward.
  TURN-OF-THE-MONTH: sweeping the rebalance day across the month shows a sharp
  full-panel U-trough — the last ~5 calendar days are the WORST (+20.6%),
  mid-month the best (+30-35%). Looks like 11pp free. IT IS NOT — walk-forward,
  mid-month beats last-Tuesday only 8/19 windows (+1.42%), failing the >=14/19
  bar. Same lesson as timing luck (full-panel CAGR is compounding path, not
  edge). The rebalance day STAYS on the last Tuesday.
- EVERY PUBLISHED DRAWDOWN WAS ~5.8pp TOO LOW (2026-09-04,
  drawdown-understated-by-sampling-2026-09). The engines return equity sampled
  ONLY at ~21-day rebalance points, and `performance()` took max drawdown from
  that array, so any drawdown that formed AND recovered inside a holding period
  was invisible. Measured across phases 0/5/10/15: period-sampled vs daily-curve
  maxDD = 28.80->36.93, 29.01->34.59, 25.26->32.98, 24.32->26.04 (mean +5.79pp,
  worst +8.14pp). The `daily_marks=` hook (additive, byte-identical)
  reconstructs the daily curve. Real-calendar walk-forward worst-case DD is
  37%, not the long-quoted 30%. gate_report's reference distribution still uses
  period sampling — flagged, not yet propagated.
- FULL-LIQUIDATION-EVERY-LAST-TUESDAY COSTS -1.21%/yr GROSS vs laggards-only
  (2026-09-04, PREREG_month_end_liquidation.md, research_month_end_liquidation.py,
  month-end-liquidation-cost-2026-09). User's mandate empties the whole book
  each last Tuesday; production is laggards-only (hold names still in the
  top-N). `run_backtest_laggards_only(liquidate_all=True)` — byte-identical
  otherwise, a TRUE one-difference comparison (unlike legacy run_backtest which
  also still sizes on plain inverse-vol). Walk-forward real calendar: mean
  delta -1.21%/yr, 95% CI [-1.61%,-0.78%], loses 18/19 windows, plus a modest
  STCG-deferral penalty (laggards defers tax on the ~13% of names it keeps;
  both realise ~0 LTCG). USER'S CALL 2026-09-04: keep laggards-only — it
  re-scores every name every last Tuesday, so nothing rides unreviewed; the
  only difference is a round-trip on a name that would be rebought anyway, and
  discretionary early profit-taking narrows the gap further. Engine default
  stays `liquidate_all=False`; the switch exists to quote the number.
- REGIME_NAMES BREADTH RE-TEST — REJECTED 0/5 + 0/4 (2026-09-04,
  PREREG_regime_names.md, regime-names-breadth-rejected-2026-09). Widening
  SIDEWAYS 3->5/6/8 and BEAR 4->6/8/10 costs 2-4pp/yr CAGR for 5-7pp less
  drawdown; crossing it with a harder CONVICTION_TILT (so MAX_WEIGHT=0.20
  finally binds at n>=5) still fails (best C tilt 1.00 at 2/4 phases, needed
  >=3). Breadth STAYS 3/4. The 25%-of-total-capital maximum single-name weight
  (n=3 -> 33.3%, n=4 -> 25.0%) is a DELIBERATE, PRICED risk, not an oversight.
- PROFIT-TAKING WATCH — DISPLAY ONLY (2026-09-04, profit_watch.py,
  PREREG_profit_taking_trigger.md, profit-taking-watch-2026-09). Surfaces four
  profit-take flags (BIG_GAIN >=+25%, GIVEBACK_IN_PROFIT, RESISTANCE_IN_PROFIT,
  RSI_EXHAUSTION — all require the position GREEN) in intraday_watch.py (INFO,
  no notify) and trade_sheet.py ("PROFIT?" flag). Logs every fire to
  data/profit_exit_log.csv for later scoring vs the laggards-only
  counterfactual (pre-registered gate: >=30 independent (symbol,month) fires,
  symbol-clustered bootstrap, phase gate). SAME HARD RULE as chart_analysis /
  news_watchdog: no import by exit_engine / paper_trader / agent_sim. Prior is
  LOW (every price-based intra-month exit tested here was rejected) — the
  untested part is the profit-ONLY asymmetry.
- RESIDUAL (IDIOSYNCRATIC) MOMENTUM TESTED AND REJECTED 2026-09-02, 0/12
  (PREREG_residual_momentum.md, research_residual_momentum.py). Researched
  from the literature at the user's request rather than proposed from memory:
  Blitz, Huij & Martens (2011) report residual momentum earning ~2x the
  risk-adjusted profit of total-return momentum by ranking on the residual of a
  factor regression. Chosen over any "add an indicator" idea precisely because
  it is NOT an auxiliary overlay — it recomputes the EXISTING score from data
  already on disk (prices + nifty50.csv), matching the only pattern that has
  worked here. FEASIBILITY GATE, recorded in the prereg BEFORE the test: corr
  with the production score 0.864 Pearson / 0.835 Spearman — HIGHER than
  trend-quality's 0.77, which was rejected for that collinearity; the one
  counter-argument was that top-3/top-4 overlap is just 1/3 and 2/4 so the
  books differ where the strategy trades. True, and irrelevant — they differ,
  and the difference is not an improvement. RESULT: 3 configs (replace /
  rank-blend 50 / tiebreak 10%) x 4 phases x 36 windows, every delta negative
  or trivially positive, TWO cells with CIs excluding zero in the UNFAVOURABLE
  direction (tiebreak phase 0 -2.00% [-3.31%,-0.67%], phase 15 -1.41%
  [-2.81%,-0.33%]). Only the market factor is buildable (no point-in-time
  fundamentals, same blocker as value/quality), so this is the CAPM-residual
  version and a null here does NOT refute Blitz et al.'s FF3 result — only what
  is constructible from this repo's data. RECORDED NOT ADOPTED: resid_replace
  improved mean DD on all four phases (-1.27 to -1.61pp) consistently; the rule
  is on return, return is negative, and a drawdown-only win is not an adoption
  (same call as absolute_0.20). This is the 9th consecutive rejection of a
  change to what the strategy RANKS on — everything touching selection has
  failed, while the one success touched SIZING. See memory
  residual-momentum-rejected-2026-09.
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
- MARKET-DEPTH LOGGER BUILT 2026-08-10 (log_market_depth.py,
  PREREG_slippage_depth_calibration.md, Study 4 of the state-of-the-art
  program) — INFRASTRUCTURE ONLY, calibration deferred. Kite's live
  5-level bid/ask depth has NO historical endpoint (memory
  kite-intraday-capability-2026-08), so it can't be backfilled the way
  data/intraday_data/'s 3 years of price bars were — every session without
  a logger running is unrecoverable. Snapshots kite.quote()'s depth field
  (best bid/ask + full 5-level book, JSON-encoded) for
  core.liquid_universe() every 15 min, piggybacking the existing
  stockai-intraday timer. Writes to data/market_depth/depth_YYYY-MM-DD.csv
  (research data only, gitignored, nothing in the trading pipeline reads
  it). Goal: calibrate research_slippage.py's uncalibrated square-root
  impact constant K against real spread/depth instead of an assumed value.
  The actual calibration is deliberately NOT designed yet — its decision
  rule will be written once enough sessions accumulate to design it
  against real data characteristics, not a guessed schema.
  COLLECTION IS MUCH SLOWER THAN THE SCHEDULE IMPLIES (audited 2026-08-14) —
  `python log_market_depth.py coverage` prints what was ACTUALLY captured.
  Four days after the build it held 3 snapshots on ONE day (a full session
  is ~25), for two independent reasons, both previously silent: (1) the
  Kite access token was expired for all 6 intraday firings on 08-10 —
  every other Kite consumer degrades to yfinance/last-close, but depth has
  NO fallback and just stops, and depth has no historical endpoint so the
  session is lost PERMANENTLY (now fires a notify-send, deduped once per
  day, naming the one fix: `python kite_auth.py refresh`); (2) the machine
  was off during market hours 08-11..08-13 — the intraday timer ran on only
  9 distinct days in the month to 08-14, so on a laptop used irregularly
  the depth clock ticks far slower than the calendar. Budget Study 4's
  timeline off `coverage` output, NOT off elapsed days.
  EMPTY BOOKS WERE BEING WRITTEN AS DATA (fixed 2026-08-14): market_open_now()
  runs to 15:45 because intraday_watch reads ~15-min-DELAYED yfinance quotes,
  but depth is REAL-TIME Kite — after the 15:30 close Kite keeps answering
  quote() and returns an ALL-ZERO book (price/quantity/orders all 0 at every
  level). Measured: 0% empty at 15:00 and 15:16, 85% at 15:30:33, 100% after.
  A zero spread with zero size would poison the very constant K this file
  exists to calibrate. Now filtered on the BOOK, not the clock (also covers
  halts/pre-open/illiquid names for free — same principle as fix_stale_bar.py
  preferring a measured OHLC ratio over an inferred plateau). The existing
  file was cleaned with the same rule (1000 -> 430 rows, backup
  data/_quarantine/depth_2026-08-14_precleanup.csv), and `coverage` reports
  symbols-PER-SNAPSHOT so a mostly-empty near-close snapshot cannot read as
  a full one.
- STUDY 4 FEASIBILITY GATE RUN 2026-09-01 BEFORE MORE COLLECTION
  (research_depth_feasibility.py, PREREG amendment 1). The pre-registration
  deferred the calibration's design until "enough sessions accumulate", but
  never asked the most basic question: IS A 5-LEVEL BOOK DEEP ENOUGH TO SPAN
  THE ORDERS THIS STRATEGY PLACES? That is CROSS-SECTIONAL (book shape), not
  time-series, so one session answers it — and answering it in 2027 after
  committing months of market-hours uptime would have been the expensive
  order. Panel: 1,230 book observations, 221 symbols, 4 sessions.
  (1) INSTRUMENT PASSES AT CARVE-OUT SIZES ONLY. Orders fitting inside the
  visible 5 levels: 98.5% at Rs 1L (Rs 10L capital), 93.8% at Rs 2L (Rs 20L),
  66.5% at Rs 6.8L (Rs 68L), 32.3% at Rs 20L (Rs 2Cr). The visible 5 levels
  are a median 0.53% of the FULL book, so censoring is a property of the
  5-level WINDOW, not of the stock's liquidity.
  (2) THE FEED HAS A HARD CEILING. The visible ask window spans a median of
  only 5.15 bps, so that is the maximum impact it can ever report. Measured
  impact as a share of that ceiling: 35% at Rs 1L, 41% at Rs 2L, 64% at Rs 20L,
  77% at Rs 50L — above ~Rs 10L/order the number reports the INSTRUMENT, not
  the market. THIS RETIRES AN OPEN QUESTION: the Rs 2Cr end of the capacity
  curve can NEVER be calibrated from 5-level depth, so keep quoting K=5..20
  there and stop expecting collection to resolve it.
  (3) MEASURED IMPACT IS WELL BELOW THE ASSUMED K, where measurable. Per-side
  median impact (size-weighted fill vs mid, walking the resting ask) vs model
  prediction at the same order size — Rs 1L: 1.82 bps measured vs 3.74 (K=5) /
  7.47 (K=10) / 14.95 (K=20); Rs 2L: 2.10 vs 5.26 / 10.51 / 21.03. Implied
  K = 2.44 and 2.02, BELOW the bottom of the assumed range. About half the
  measured cost is just the half-spread (median 1.40 bps) — a carve-out-sized
  order barely walks past the touch. So the Rs 10-20L carve-out's modelled
  0.95-2.67pp CAGR drag is CONSERVATIVE, and a static-book walk is itself a
  patient-order UPPER bound (real execution gets replenishment).
  (4) THE SHALLOW EXPONENT DOES NOT REFUTE THE SQUARE-ROOT LAW. Fitting
  log(impact)=a+b*log(size) within each book gives median b=0.183 (balanced
  panel, books uncensored at EVERY size: 0.148) vs the model's 0.5, 97-99% of
  books below 0.5 — and censoring composition is ruled out because the
  balanced panel agrees. BUT saturation (2) produces the identical signature
  and the two are NOT separable inside a 5-level window. Recorded as a BOUND
  ("within the top of book, impact grows much more slowly than sqrt; past
  level 5 is unobservable"), NOT as a refutation — "we refuted Almgren-Chriss
  on 4 sessions of top-of-book data" is the overclaim the prereg habit exists
  to prevent.
  STILL NOT FOLDED INTO PRODUCTION COST — 4 sessions, one volatility regime.
  The amendment PRE-REGISTERS the adoption rule: >=20 sessions spanning >1
  volatility tercile, restricted to sizes under the 60%-of-ceiling saturation
  threshold, reported per liquidity tier if implied K spreads >2x across
  turnover terciles, and adopt the UPPER end of the session range, not the
  mean. Collection stays worthwhile — its remaining job is day-to-day and
  regime VARIATION at carve-out sizes, which one quiet session cannot give.
- EXIT-SIDE DELIVERY%/OI FLOW SIGNALS tested and REJECTED 2026-08-10
  (research_exit_flow_signals.py + _pertrigger.py,
  PREREG_exit_side_flow_signals.md, Study 5 — final study in the
  state-of-the-art program's original queue). The 2026-08-01 mandate
  relaxation ("selling any time is permitted") reopened non-price EXIT
  triggers as fair game — a DIFFERENT mechanism from the already-rejected
  ENTRY-side rank-blend versions of the same data (delivery% decay /
  OI unwind on an ALREADY-HELD position, not "is this stock currently
  elevated" at buy time). Went in with a deliberately LOW prior: 3 prior
  auxiliary-override mechanisms had already failed on this strategy
  (entry blend x2, event-veto x1 — see exit-announcements-rejected memory,
  whose own conclusion states the strategy "doesn't like auxiliary
  overrides, on ANY mechanism tried so far"). Built a mandatory
  per-trigger false-positive/true-positive check IN ADVANCE (the
  announcements study's exact failure mode was passing in aggregate while
  hiding a bad per-event breakdown). 3/4 configs (delivery-decay 0.70,
  both OI-unwind variants) rejected cleanly on the aggregate walk-forward
  bar. Delivery-decay 0.50 PASSED the aggregate bar (+2.52% mean CAGR,
  95% CI [+0.74%,+4.02%], 29/36 window wins) but the per-trigger check
  caught it: only 3 raw trigger events fired across the ENTIRE 2015-2026,
  ~200-name universe (two of them the same stock two weeks apart), all
  landing in one narrow late-2021/mid-2022 stretch — with 36 OVERLAPPING
  3-year windows, 2-3 raw events land inside a large overlapping subset of
  "windows," fully explaining the 29/36 apparent win rate with zero real
  repeatable signal behind it. NOT a threshold-tuning problem (a 50%+
  10-day-rolling delivery decay vs entry-time level is measured genuinely
  rare — 0.0% of days for RELIANCE/TCS/INFY, 1.2-1.6% for more volatile
  names, over 11 years) — per this study's own pre-registered rule, a
  mechanism that fails this way CLOSES rather than prompts a search for a
  better threshold. 4th independent confirmation of the auxiliary-override
  pattern, now across 3 different data sources (delivery%, OI/PCR,
  corporate announcements) and 3 different mechanism shapes (entry blend,
  event-veto, exit flow-decay). Added exit_signal_fn hook to
  run_backtest_laggards_only (checked daily on held positions, same shape
  as trail_stop, verified byte-identical to production with it unset) —
  kept as infrastructure for any future exit-trigger idea, same pattern as
  trail_stop/sizing_fn/regime_fn/score_fn. See memory
  exit-flow-signals-rejected-2026-08. This closes the state-of-the-art
  program's originally-queued 5 studies — see memory
  state-of-the-art-program-2026-08 for the full program summary: 1
  adoption (conviction sizing, the one improvement that used information
  the strategy already computes), the rest rejected (every attempt to
  bring in an EXTERNAL signal failed), 1 infrastructure build with
  analysis deferred.

## Options range-selling TESTED AND REJECTED 2026-09-02 — line CLOSED
User asked for S/R levels defining a monthly min/max so they could sell CE+PE
around them (short strangle: profit if price stays inside to expiry). This is
a DIFFERENT STRATEGY, not an S/R change — negative-skew payoff, different
mechanism (the variance risk premium). Pre-registered
`PREREG_options_range_selling.md`; three feasibility gates run BEFORE building
any forecasting/backtest machinery, mirroring the 2026-09-01 depth gate.
- GATE A (`research_vrp_gate.py`, 2433 cycles / 60 symbols / 2021-2026):
  gross VRP (ATM straddle implied move minus realised move) median **1.53pp,
  95% CI [1.06, 1.98]** — a real, significant effect, but BELOW the frozen
  2pp floor. FAILS. 75.9% of expiry dates positive, so not concentration.
- GATE A2 (`research_strangle_pnl.py`, 2414 cycles): direct P&L of the ACTUAL
  5% OTM strangle, motivated by volatility SKEW being a distinct mechanism
  from ATM pricing (a legitimately different question, pre-registered as
  AMENDMENT 1, not a retune of A's floor). Win rate 68.9% and median cycle
  **+82.8% of premium** — but mean only **+4.5%, CI [-11.8%, +19.3%]**, and
  **-0.5% after a generous 5%-of-premium cost haircut**. 2 of 3 criteria FAIL.
- **THE CI WIDTH IS THE REAL FINDING.** At n=2414 over 58 expiry dates the
  mean is STILL indistinguishable from zero. That is structural to a
  tail-dominated payoff, not fixable with more data: you could trade this
  profitably for a year and not know whether you had an edge or were simply
  pre-tail. A strategy that cannot tell you it is working is not deployable.
- **TAIL, in margin terms** (vs Gate C's Rs132,468/lot): p1 = **-97.1% of
  margin blocked**, p5 = -47.3%. Worst cycle -2627% of premium (ITC).
- **TAIL CORRELATION KILLS THE DIVERSIFICATION COUNT.** Gate C measured
  "Rs10L supports ~7 naked names", but the 50 worst cycles span only 21
  expiry dates, 8 of them sharing ONE date; worst 5 dates = 61% of all
  negative-date loss; INFY and TCS both in the worst 5 on the SAME expiry.
  7 positions is NOT 7 independent bets — a bad month breaches many at once.
- IRON CONDOR DOES NOT RESCUE IT (arithmetic, not preference): the gross mean
  is already ~zero, and paying for wings converts "occasionally catastrophic,
  mean zero" into "reliably slightly negative".
- GATE C (`research_margin_gate.py`) PASSED and was never the constraint:
  naked strangle median Rs132,468/lot, condor Rs68,369 (1.89x benefit).
- GATE B (`log_options_depth.py`, live option spreads) is MOOT — no edge left
  for spreads to eat. Built, briefly wired to stockai-intraday, then UNWIRED
  same day; kept as a handle. The equity depth collector is untouched.
- A REAL BUG WAS CAUGHT MID-STUDY: the NSE F&O archive's underlying price is
  NOT split/bonus-adjusted, so RELIANCE's real 2024 1:1 bonus produced a fake
  ~55% "crash" (raw 2995.95 -> 1332.10 against a true ~-11% move). Fixed by
  taking realised_move from price_data/'s adjusted close; implied_move is a
  same-date ratio and unaffected. Same adjusted-vs-unadjusted trap as
  fix_stale_bar.py/readjust_archive.py, first seen here in the DERIVATIVES
  archive. `download_fo_bhavcopy.py` was widened to retain strike/expiry/
  close/settle (verified byte-identical fo_data/ output, max abs diff 0.0).
- WHAT THE USER'S UNDERLYING QUESTION STILL MAPS TO: `containment_band.py`
  already answers "a level price probably won't breach this month" and is
  calibrated (0/16 cells outside +-10pp). It ships as a RISK/expectation tool.
  The options APPLICATION is what failed, not the band.
- DO NOT REOPEN with a different moneyness/tenor — AMENDMENT 1 forecloses a
  third cut, pre-registered before the result was seen. A genuinely different
  underlying (index options) would be a NEW strategy needing its own prereg,
  and abandons the per-stock requirement that motivated this.

## TRADE SHEET (trade_sheet.py, 2026-09-02) — the 60-stock "what do I trade this at" view
User's operational need: they scan a 60+ name watchlist and want tradeable
levels per name without running full_advisor once per stock. Writes
`data/trade_sheet.csv` (overwritten, NOT a measurement record) and prints a
table; wired into run_price_update.sh (~15s, read-only apart from its own CSV).
- ROWS = strategy top-N UNION held UNION WATCHLIST. Including the top-N is
  LOAD-BEARING, not cosmetic: the WATCHLIST is the FIXED S/R calibration panel
  and has no reason to contain what the strategy wants. Measured 2026-09-02 —
  the live BEAR top-4 (HFCL/WELCORP/RADICO/ATHERENERG) were NONE of them on the
  watchlist, so a watchlist-only sheet showed 61 names and nothing worth
  buying, which invites acting on the best merely-ELIGIBLE row instead.
- LEVELS come from `full_advisor.get_trade_levels` — the single ATR
  construction (support_resistance delegates to it since 2026-09-01), so the
  sheet CANNOT drift from what the advisor prints. Top-N reuses
  `select_top_n_capped` exactly as exit_engine calls it, not a reimplementation.
- THE `Status` COLUMN IS THE POINT. TOP-N / HELD / ELIGIBLE #k / NOT ELIGIBLE
  / NOT IN UNIV / STALE. Printing Buy/Stop/Target for 61 names WITHOUT it would
  recreate advisor-strategy-divergence-2026-08 (advisor recommending names the
  validated strategy would never buy, zero overlap with its own top-4). Levels
  on a non-TOP-N row are reference geometry, and the printed legend says so.
- Stale names (>MAX_STALE_SESSIONS behind the index) get levels SUPPRESSED,
  not quoted off an old close — same guard as full_advisor.compute_buy_calls.
- RSI >= RSI_OVERBOUGHT renders as e.g. `85.1!` — a FLAG, never a filter (the
  hardcoded RSI>75 hard-reject was removed 2026-08-01 and must not return).
- Separate HELD POSITIONS section prints qty/entry/CMP/P&L and the -18%
  CATASTROPHIC_STOP level off each position's ACTUAL entry — for a held name
  the actionable number is the exit, not a fresh entry. Reads `entry_price`
  (record_fill.py's schema).
- S1/R1 are carried for range context ONLY. They do not mark reversals
  (measured, permutation-controlled) — never use them as a trigger. They are
  BYTE-IDENTICAL to what `live_ticker.py` shows (both go through
  `core.sr_levels` -> `get_levels`; verified on 5 names 2026-09-02) — the only
  difference is the ANCHOR: live_ticker passes a live quote fetched once at
  launch, so its distances move through the session, while the sheet is
  close-based. Neither is "more realistic" than the other.
  A `~` SUFFIX MARKS A PROJECTED LEVEL (2026-09-02): `get_all_levels` returns
  strength 0 when no historical pivot sits inside the reachable band and it
  projects one off the containment band instead. The sheet used to print those
  identically to a 6-touch pivot. Measured: 19/65 supports and 14/65
  resistances are projections — INCLUDING the support on all four TOP-N names,
  which is structural rather than incidental (a momentum leader near its highs
  has no pivot below it by construction). Same flag-not-filter precedent as
  the RSI `!`.

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
- LOGGERS RECORD THE PREVIOUS COMPLETED SESSION (2026-08-19, user spec —
  `sr_daily_logger.LOG_PREVIOUS_SESSION`, set False to revert). `log_stock`
  now drops any bar dated today (or later — `>=`, so an NTP-skew future bar
  goes too) via `drop_today_bar`, so a run at ANY hour produces the identical
  row. `drop_partial_candle` only removed today's bar before 16:00, so an
  EVENING run logged today — precisely the 17:00-23:00 window in which Kite's
  historical_data() has not settled (Bhavcopy ~19:00-20:00+), recording prices
  that get revised overnight. sr_dynamic_logger imports `log_stock` so it
  inherits; sr_backtest/sr_build_touchtable do NOT call it and are unaffected;
  backfill_sr_log still works (a past target date is never "today").
  The 00:30 pipeline is UNCHANGED — at 00:30 no bar is dated today, so nothing
  is dropped. Only 16:00-24:00 runs shift. LIVE QUOTES ARE NOW SUPPRESSED in
  the loggers as a consequence: a live tick describes NOW while the row
  describes a prior session, and dating a row to one session while pricing it
  from another is the same two-points-in-time error `--as-of` already avoids.
  `log_stock` discards a passed `live_price`, and main() skips the fetch.
- MEASUREMENT-RECORD CONTAMINATION, MEASURED NOT FIXED (2026-08-19,
  `audit_sr_log.py`, read-only). The above was found by a control test:
  rebuilding an already-logged date reproduced the ARCHIVE close, not the
  logged CMP. Auditing the whole log, **296 of 1085 rows (27%)** carry a CMP
  that matches no archive bar for their own date — whole-panel on 2026-08-03
  (56), 08-04 (54), 08-13 (59), 08-17 (59), plus a 1-3/day tail through July.
  Two causes, both now prevented: pre-settlement evening runs, and mid-session
  runs where the row took the last COMPLETED bar's date but a LIVE tick's
  price. Effect on measured accuracy (10d, day-0 excluded): removing them
  moves R1 61.5%→66.1% and overall 45.3%→47.7%, S1 essentially unchanged
  (+0.1pp) — direction consistent, but ~1.5 SE at n≈250-370 and the clean set
  is a SUBSET of the full, so NOT significant; don't quote it as a correction.
  DELIBERATELY NOT REPAIRED: the rows do record what the system saw at the
  time, and rebuilding them (`backfill_sr_log.py --date`) makes the record
  uniform at the cost of discarding that. Run the audit before trusting any
  hit-rate number that spans these dates.
- LOGGER OUTPUT IS THREE FILES (2026-08-03, user spec). Each logger run writes:
  (1) `sr_daily_log.csv` — the cumulative append-only record, unchanged; this
  is what sr_monthend_analysis reads and must not be rotated or truncated.
  (2) `sr_today.csv` — today only, OVERWRITTEN each run. A convenience view;
  nothing is lost by overwriting since (1) and (3) keep the history.
  (3) `sr_month_YYYY-MM.csv` — one file per REBALANCE CYCLE (named for the
  month its target last-Tuesday falls in — NOT the calendar month rows were
  LOGGED in; see the 2026-08-31 fix below, this was wrong until then),
  appended daily. Daily rows are plain (no avg columns). ONCE the month's data
  collection reaches the rebalance day (last Tuesday), one `AVG` summary row
  per stock is appended — Date literally reads `AVG` — holding the month's
  mean CMP/S1/R1 plus `Days` = the number of sessions averaged. Rows also
  carry `High`/`Low` from the SAME bar as CMP.
  The completeness test is `max(logged date) >= last Tuesday`, NOT equality:
  if that Tuesday is an NSE holiday no row falls exactly on it and an equality
  test would silently never write the averages.
  AVG rows are DERIVED: every write strips existing ones and recomputes from
  the daily rows, so re-running after month-end never averages an average back
  into itself (verified — a 4th day updates the mean to the true 4-day figure,
  and the AVG row count stays fixed). Any reader of these files must filter
  `Date == "AVG"`; sr_monthend_analysis.load_log does this before parsing
  dates, since "AVG" would otherwise become NaT and be scored as a snapshot.
  A SYMBOL COULD BE SILENTLY MISSING ITS AVG ROW, FIXED 2026-08-31
  (`build_avg_rows`). The close-basis-only filter (avoid a day you happened to
  run multiple times outweighing a day you ran once) was applied GLOBALLY —
  `if len(closes): daily = closes` — before the per-symbol groupby, so it only
  checked whether ANY symbol had a close-basis row that month, not whether
  THIS one did. A symbol whose entire recorded history for the month was
  live-basis (logged once, mid-session, on the day it entered or left the
  panel, with no close-basis row ever written) lost its only data and got NO
  AVG row at all, rather than falling back to what it had. Found live:
  AARTIIND/GOLDBEES (August, one live-basis row each on 08-03, the day they
  left the fixed panel) and ANGELONE/PAYTM/PFC/SHRIRAMFIN (July, one
  live-basis row each on 07-31) were silently absent from every AVG summary.
  Fixed to apply the close-preference PER SYMBOL inside the groupby, falling
  back to that symbol's own live-basis rows only when it has no close-basis
  ones — verified zero regressions among the 61+15 symbols that were already
  correct. Backups: `data/_quarantine/sr_month_2026-0{7,8}_pre_avgfix_
  2026-08-31.csv` and the sr_dynamic equivalents (dynamic panel had no
  missing symbols this time, but shares the same buggy function via
  `sr_dynamic_logger`'s import of `write_month`).
- BIGGER BUG IN THE SAME AREA, FOUND AND FIXED SAME DAY (2026-08-31, user
  spec — asked directly whether trailing-days-of-a-month data correctly
  rolls into next month's cycle): month files were grouped by the CALENDAR
  DATE a row was logged on, but `sr_horizon.horizon_end()` rolls a row's own
  target to the FOLLOWING month's rebalance on and after the current month's
  last Tuesday ITSELF (a row logged 2026-08-25 already has HorizonEnd
  2026-09-29 — verified live: 2026-08-24 HorizonDays=1/HorizonEnd=08-25,
  2026-08-25 HorizonDays=25/HorizonEnd=09-29, i.e. the row logged ON the
  rebalance day is already the FIRST row of the NEXT cycle, not the last of
  the one ending that day). So `sr_month_2026-08.csv` was blending TWO
  different rebalance cycles: 08-03..08-24 (August's own cycle, HorizonEnd
  08-25) AND 08-25..08-28 (the START of September's cycle, HorizonEnd
  09-29) — contaminating August's AVG summary with September-cycle rows, and
  guaranteeing `sr_month_2026-09.csv` would start short by however many
  trailing-August-calendar-days actually belonged to it. The user's own
  framing was exactly right: truncating those days out of September
  undercounts its true ~21-session cycle. Confirmed the SAME bug existed one
  cycle back too — `sr_month_2026-07.csv` held 07-29..07-31 rows that
  targeted August's cycle (ANGELONE/PAYTM/PFC/SHRIRAMFIN's 07-31 rows,
  exactly the four "recovered" by the AVG-row fix immediately above — they
  were never really single-day July stragglers, they were August-cycle data
  mis-filed under July's last calendar day).
  FIX (`sr_daily_logger.write_month`): rows are now grouped by `cycle_key()`
  — each row's own `HorizonEnd` month, falling back to the row's own Date's
  month only for legacy rows logged before HorizonEnd existed (nothing else
  is knowable about those). NON-OBVIOUS COMPLETENESS WRINKLE: because the
  rebalance-day row itself already belongs to the NEXT cycle, a cycle's own
  file can NEVER contain a row dated on its own target — so `month_is_complete`
  can no longer prove completeness from a cycle's own rows alone. It now
  accepts an `extra_max_date` (the latest date seen anywhere in the current
  write) and `write_month` additionally SWEEPS every other existing
  `sr_month_*.csv`/`sr_dynamic_month_*.csv` file each run, finalizing (writing
  AVG rows into) any that have reached their target via this run's date even
  though none of today's own rows landed in them — this is what lets August's
  file get its AVG rows written on exactly the run that first produces a
  September row, rather than never, since August's own file can no longer
  self-trigger. VERIFIED by replaying the full daily log for both panels
  day-by-day through the new logic in a scratch directory before touching
  production: August's file correctly stops at 08-24 (complete, AVG written
  on the 08-25 run), September's file correctly starts collecting from 08-25
  (not yet complete). PRODUCTION FILES REBUILT (superseding the AVG-only fix
  above, which was still computed against cycle-contaminated file content):
  read all existing `sr_month_2026-0{7,8}.csv` daily rows (both panels),
  regrouped by `cycle_key`, rewritten fresh per cycle rather than merged
  (merge_log only replaces MATCHING (Date,Symbol) pairs, so it cannot by
  itself remove a row that no longer belongs in a file at all). Result:
  August fixed panel 983 rows/63 symbols/complete=True (07-31..08-24);
  September fixed panel created fresh, 244 rows/61 symbols/complete=False
  (08-25..08-28, correctly awaiting 09-29); July fixed panel now correctly
  holds only the 15 symbols with no logged HorizonEnd at all (285 rows).
  Dynamic panel rebuilt identically. Backups:
  `data/_quarantine/sr_{,dynamic_}month_2026-0{7,8}_pre_cyclefix_
  2026-08-31.csv`. `sr_monthend_analysis.py --month` carried the identical
  bug (filtered by `Date`'s calendar month, not each row's own `HorizonEnd`)
  and is fixed the same way — verified `--month 2026-08` now reports "Date
  range: 2026-07-31 -> 2026-08-24" (correctly picking up the July-31 tail
  AND correctly excluding the August-25-onward head of the next cycle).
  The published August S/R review's actual NUMBERS are unaffected by this:
  `analyse_hit_rates` already excludes any row whose forward window hasn't
  closed, and the 08-25..08-28 rows' September horizon (needing data out to
  09-29) had zero closed windows against the archive either way — the bug
  only ever corrupted file-level AVG summaries and any future `--month
  2026-09` cohort query, not anything already reported.
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
  never feed back into scoring/selection.
  LEDGER HEADER DRIFT FIXED 2026-08-11: CALL_COLUMNS gained
  in_strategy_top_n/rsi on 2026-08-01 but log_calls appended under the
  file's OLD 13-column header, so `call_report.py` raised ParserError on
  EVERY run for 10 days — silently, since a traceback inside the nightly
  pipeline scrolls past. Worse, log_calls' dedupe read sits in a bare
  `except: pass`, so the duplicate guard was OFF that whole time (no
  duplicates landed only because the pipeline is idempotent per date).
  log_calls now rewrites the file with the union of columns on any schema
  change instead of appending a differently-shaped row, and a dedupe read
  failure prints a loud warning. Backup:
  data/_quarantine/advisor_calls_log_preheaderfix_2026-08-10.csv. See
  memory advisor-ledger-header-drift-2026-08 — this is the THIRD
  measurement-instrument failure of the same family (fake 100% S/R hit
  rate, July's cash-month scored "consistent", now this), so periodically
  run every measurement script purely to confirm it still EXECUTES.
  FOURTH ONE FOUND 2026-09-01, AND EXECUTING WAS NOT ENOUGH — call_report ran
  fine and printed "TARGET-before-STOP 21%, avg R -0.30" on the first full
  month of post-rebuild calls, which reads as a losing advisory system. Two
  aggregation defects, both already in this repo's catalogue:
  (1) THE STATISTICAL UNIT WAS THE CALL, NOT THE SYMBOL. full_advisor logs its
  top ~8 names EVERY session, so 145 "calls" were 21 symbols re-observed in
  one shared tape; the 24 decided calls came from SIX symbols, and 15 of the
  19 stops were two names (ADANIENSOL x9, LLOYDSME x6) re-logged daily as one
  deteriorating position drifted down. Same error as counting S/R log rows
  instead of dates, same shape as the exit-flow study's 3 events driving 29/36
  window wins.
  (2) THE RACE WAS SCORED WHILE CENSORED. HORIZON=42 sessions but no call had
  more than 20 sessions of forward data, so ZERO races could complete and every
  "resolved" row had resolved EARLY — a self-selected subset, the mirror image
  of the hit/miss asymmetry behind the fake 100% S/R number.
  Corrected reading of the same month: holding from the call-day close vs
  Nifty over the identical span gives per-SYMBOL alpha +2.49%, 95% CI
  [-0.91%,+6.01%], 12/21 symbols positive — not significant, but nowhere near
  an indictment; the naive row-level CI is 1.8x too narrow. The stops that
  fired mostly fired on names that genuinely kept falling. Now: every headline
  is per-SYMBOL with a symbol-clustered bootstrap (`--by-call` for the old
  figures, labelled do-not-quote); the race is NOT REPORTED until calls have
  the full horizon; and a new "THE PICK, SEPARATED FROM THE LEVEL" block
  reports Nifty-relative alpha, because full_advisor's ATR entry/stop/target
  geometry has never been walk-forward validated and the PICK is the only half
  backed by evidence. Caught while building the fix: the ledger already has an
  `alpha` column (momentum alpha), so a derived `alpha` written only on the
  has-forward-bars branch left zero-bar calls carrying the LEDGER's value into
  a return average — reading as +715%; derived keys are now
  hold_ret/bench_ret/pick_alpha, pre-initialised to NaN. See memory
  call-report-unit-and-censoring-2026-09.
  `gate_report.py` (manual, monthly) —
  scores each completed paper REBALANCE PERIOD at its percentile of the
  production backtest's 21d-return distribution (2+ periods below p10 = live
  path diverges, investigate).
  IT IS A PLUMBING CHECK, NOT THE DEPLOYMENT DECISION (corrected 2026-09-01
  off gate_report's own power table). The reference per-period sd is 7.35%,
  so at the planned 3-6 periods the gate detects a strategy quietly losing
  1-3%/period only 22-54% of the time, against a 16-31% FALSE-ALARM rate on
  a strategy behaving exactly as backtested. Those numbers are close enough
  together that a "consistent" verdict at 3-6 periods rules out GROSS
  BREAKAGE and establishes nothing else — it is roughly a coin flip on the
  thing that actually matters. Reaching 80%+ power needs ~12 periods (a
  year), which is not the plan. So do NOT read "3-6 consistent periods" as
  clearance to deploy capital.
  WHAT THE DEPLOYMENT CASE ACTUALLY RESTS ON, in descending order of
  evidential weight: (1) the walk-forward distribution (19 windows, mean CAGR
  ~30% mean / Sharpe ~1.2 / worst DD ~37% on the REAL last-Tuesday calendar
  with daily drawdown, 2/19 negative — corrected 2026-09-04 from the
  fixed-grid 33.4%/30.2%; and note the era split, 2022-2026 is only +3%) —
  this is the edge evidence and always was;
  (2) divergence_check.py showing the LIVE path selects identically to the
  BACKTEST path (deterministic, daily, and this repo has shipped that drift
  three times, so it is a real check rather than a formality); (3) the cost
  stack being survivable at the intended size — STCG drag
  (research_net_returns.py) plus impact, which the 2026-09-01 depth
  feasibility gate measured at ~2 bps/side at carve-out order sizes, roughly
  half what K=5 predicts. The paper gate's job is to catch the failure those
  three cannot see: an operational break between signal and fill.
  PERIODS, NOT CALENDAR MONTHS (2026-08-11): it used to group by calendar
  month, which measures a DIFFERENT object than the reference distribution —
  the book rotates on the last Tuesday, so calendar-August (07-31→08-31)
  holds the SEPTEMBER rotation's names for its last ~4 sessions, while every
  reference return is one 21-session holding period of ONE set of names.
  Periods now run rebalance-day→rebalance-day via exit_engine.rebalance_day,
  and a period is not scored until its CLOSING rebalance day is logged (a
  partial mark is not a result). Adds `deployed` (mean share of equity at
  risk) + `regime`: REGIME_EXPOSURE caps BEAR at 0.375, so a BEAR period is
  STRUCTURALLY muted vs a pooled all-regime reference and can land
  bottom-decile while behaving exactly as designed — READ THOSE COLUMNS
  BEFORE treating a low percentile as divergence. A regime-conditional
  reference distribution was considered and REJECTED: 128 periods slice too
  thin by regime, and it becomes a way to explain away every bad month.
  `--attrib` gives per-position contribution + the equity-vs-cash-yield
  split (on a simulated August, ~HALF the return was idle-cash yield, which
  a bare percentile hides). Gate clock: periods end 08-25, 09-29, 10-27 → 3
  scored ≈late Oct, 6 ≈late Jan. See memory gate-period-definition-2026-08.
  THE GATE HAS ALMOST NO STATISTICAL POWER, MEASURED 2026-09-01 — read it as a
  PLUMBING check, not as validation. The reference distribution's per-period sd
  is 7.35%, which swamps any plausible degradation at 3-6 observations.
  Simulating the gate's OWN rule (one period < p5, or two < p10) against the
  reference resampled with a constant per-period handicap:
      periods | healthy | -1%/pd | -2%/pd | -3%/pd | -5%/pd
            3 |   16%   |  22%   |  27%   |  30%   |  44%
            6 |   31%   |  41%   |  48%   |  54%   |  72%
  "healthy" is the FALSE-ALARM rate on a strategy behaving exactly as
  backtested. A live path bleeding -2%/period (≈-22%/yr) passes 3 clean periods
  73% of the time; at -5%/period (-46%/yr) it still passes 56%. At n=3 the
  detection rate is barely above the false-alarm rate. This is a SAMPLE-SIZE
  fact — no rework of gate_report fixes it, and 12 periods only reaches 76% at
  -2%/pd while false alarms climb to 54%. So "1 SCORED period, 66th pctile,
  consistent" carries almost no information (80% of random draws land p10-p90
  by construction). gate_report now PRINTS this power table on every run so a
  clean verdict cannot be over-read. The high-power instrument for "does the
  live path implement the validated strategy" is divergence_check.py —
  deterministic, daily. Context on the one scored period (2026-08-25, +4.10%):
  WELCORP alone contributed +2.71pp and cash yield +0.75pp, so equity picking
  net of the single best name was +0.65pp. The paper book also entered 4
  sessions after the period's opening rebalance (the last-Tuesday rule landed
  mid-flight); the engine's own 07-28 book held to 08-25 returns +5.64% vs the
  +4.10% realised, and the 07-28 and 08-03 top-4 share only 2 of 4 names — so
  entry timing is a real live-vs-backtest cost the reference does not model.
  See memory gate-has-almost-no-power-2026-09.
- `divergence_check.py` (2026-08-11, read-only, in run_price_update.sh) —
  gate_report scores the paper book's RETURN, which cannot separate "strategy
  fine, market unkind" from "the LIVE path selects differently than the
  BACKTEST". This repo has shipped exactly that drift three times (exit_engine
  missing the sector cap, two drifted momentum_score copies, three inlined
  inverse-vol copies), so a bottom-decile gate month is uninterpretable
  without this check. Diffs pool / scores / top-N / conviction weights between
  core.scan_universe (per-symbol CSVs) and the backtest path (merged matrix).
  Current: pool + selection + weights IDENTICAL. Score diffs are reported
  under three headings, only the third being a bug: (1) STALE CSV —
  load_price_matrix ffills(limit=5), live doesn't; the 2026-08 cluster is all
  corporate_action_watch.json names, self-resolving; (2) NON-SESSION MATRIX
  ROWS — the matrix index is the UNION of all symbols' dates and holds 12
  dates ABSENT from nifty50.csv (New Year's Day, special sessions); a symbol
  lacking one gets ffilled, shifting its lookback a bar. Prices byte-identical;
  verified second-order (session-only matrix leaves today's top-4 unchanged) so
  deliberately NOT "fixed" — changing the matrix index moves every historical
  backtest number for a sub-noise effect; (3) a diff on FRESH ALIGNED data =
  real divergence, currently zero. Forward-fill is 0.020% of the post-listing
  panel but RISING (1→11 symbols over three weeks) — re-check if it climbs.
  See memory live-backtest-divergence-check-2026-08.
- PAPER SELECTION STALENESS WARNING (2026-08-11, paper_trader.py):
  full_advisor screens candidates >MAX_STALE_SESSIONS behind; core.scan_universe
  does NOT, so a stale name can be SELECTED into the paper book — the gate's own
  evidence. It cannot fill at a fake price (close_on returns None for a missing
  bar, so the order retries ≤3 sessions and expires), but it can silently
  consume one of only n slots. WARNS rather than drops: dropping would change
  strategy behaviour on a transient, self-resolving data condition.
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
  FIRED UP TO 4 SESSIONS EARLY — FIXED 2026-08-22, AND IT CORRUPTED BOTH LIVE
  BOOKS. The equality test `last == rebalance_day(...)` is only valid with a
  calendar that already extends PAST the target. The live calendar stops at
  today, so rebalance_day's holiday roll-back ("latest session at or before
  the last Tuesday") had nothing to roll back to and returned TODAY — making
  EVERY session in the week before the target test True. August 2026 (target
  08-25) fired on 08-18, 08-19, 08-20 AND 08-21. paper_trader duly ran a real
  month-end rotation on 08-19: sold ADANIENSOL 57sh @1538 booking a realised
  −₹6,300.67, trimmed WELCORP, queued HFCL; agent_sim did the same (sold
  ADANIENSOL 41sh, bought ₹493k WELCORP, ran cash to ₹256 which blocked a
  RADICO order). It would have rotated again on EVERY later run — only the
  user's missed 08-18/20/21 runs kept it to one. Missed because the
  last-Tuesday rule landed 2026-08-03, AFTER July's rebalance, so August was
  the first month to exercise it live, and the original "once per month across
  199 months" check used the FULL calendar where the roll-back resolves fine.
  THE LIVE TEST IS NOW "first session at or after the target"
  (`prev < target <= last`): cannot fire early, exactly one session satisfies
  it, needs no future calendar. Both the previous and current month's targets
  are checked — a holiday last-Tuesday at a month's END pushes the firing
  session into the NEXT month, and testing only that month's target skipped
  the rebalance entirely (March 2026 and May 2011 never fired in an
  intermediate version). DELIBERATE SPEC DEVIATION: a holiday last Tuesday now
  fires on the NEXT session, not the previous one (March 2026 → 04-01) — a
  session late instead of early, because no live check can tell a holiday from
  a date that has not arrived, and firing EARLY is what corrupted the book.
  Re-verified by replaying all 4,081 sessions with the calendar truncated to
  each one (i.e. as the pipeline actually sees it): 199/200 target months fire
  exactly once, 0 more than once, only Aug 2026 unfired because 08-25 is still
  future. SELF-HEALS A MISSED RUN: if the pipeline does not run on the
  rebalance day it fires on the next session instead, once — verified both
  ways. BOOKS REPAIRED 2026-08-22 (backups
  data/_quarantine/*_pre_rebalancebug_2026-08-22.*): the 08-19 rotation was
  reversed in both books and paper_trader replayed 08-18..08-21 through its
  OWN step() against a truncated index (not a reimplementation). Both reverts
  were verified by repricing the restored positions at the 08-17 closes and
  matching each book's own logged 08-17 equity to the paisa. See memory
  rebalance-fired-early-2026-08.
- THE BOOKS STEP ON THE LAST COMPLETED SESSION, ANY RUN HOUR (2026-08-22 user
  spec, `core.last_completed_session`). paper_trader and agent_sim took
  `index[-1]` directly, so which session they processed depended on WHAT TIME
  the pipeline ran: trim_partial.py strips today's partial candle only before
  16:00 (`now.hour >= 16` → returns without touching anything), so a morning
  run processed YESTERDAY while a 16:00+ run processed TODAY — on a bar Kite
  has not settled (Bhavcopy ~19:00-20:00, the very reason the price pull was
  moved off an evening slot; see kite-settlement-lag-2026-08). Confirmed in
  the pipeline's own log: runs at 11:03/11:58/14:58/11:21 all processed the
  previous session, while 16:43/18:15/20:10 processed the same day. The user
  runs the pipeline 11:00-17:00, i.e. straddling that boundary. Both books now
  derive the date via `core.last_completed_session` (newest bar STRICTLY
  before today, `>=` so an NTP-skew future bar is dropped too) — the same
  guarantee sr_daily_logger.drop_today_bar already gave the S/R measurement
  record. Verified identical output at 11:00/15:00/16:00/18:00/20:00, and the
  step is byte-identical to the previous behaviour for a pre-16:00 run
  (sandbox replay of 08-21 reproduced ₹1,036,025 exactly). LIVE/MONITORING
  TOOLS ARE DELIBERATELY UNCHANGED and must stay that way — market_scanner,
  intraday_watch, live_quotes, live_ticker and the announcement feeds are
  supposed to see today; only the BOOKS are point-in-time. Consequence to
  expect: the books always lag one session, so a rebalance dated 08-25 is
  executed by the run on 08-26 — correct, and the month-end check self-heals a
  missed run (see the month-end entry above).
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
- FIBONACCI RETRACEMENT + STOCHASTIC RSI ADDED 2026-08-31
  (chart_analysis.fib_retracement / stoch_rsi), DISPLAY ONLY, RESEARCHED
  BEFORE BUILDING per user instruction. User asked for TradingView-style Fib
  levels and a two-line oscillator crossover they'd seen "predict" a reversal;
  both were checked against TradingView's OWN documentation and the academic
  literature first (WebSearch/WebFetch, not assumed). TradingView itself makes
  NO accuracy claim for either: Auto Fib is "based on the theory that markets
  will retrace a specific portion of a move" and is recommended for use "with
  other tools"; Stochastic RSI's own docs warn "this can generate many more
  signals and therefore more bad signals" and call trading crossovers against
  trend "a dangerous proposition." fib_retracement reproduces TradingView's
  Auto Fib (ZigZag swing_points -> 23.6/38.2/50/61.8/78.6% of the range,
  anchored on the most recent swing high/low). Its display output only lists
  levels and a leg description — it makes no directional touch claim, so the
  bug below (found while TESTING it, not in this display function itself)
  never reached what the user actually sees. stoch_rsi
  matches TradingView's own 14/14/3/3 defaults (%K/%D on RSI, not price).
  Wired into chart_analysis.analyse()/summarise()/summarise_plain() the same
  way every other function in the file is — descriptive fields, plain-English
  caveat, verified zero errors across the full 61-symbol fixed panel.
  TESTED, NOT JUST SHIPPED (2026-08-31, research_fib_stochrsi.py,
  PREREG_fib_stochrsi.md pre-registered BEFORE running): reused the exact
  flow-change + permutation-control harness built for the August 2026 S/R
  review (see below), applied point-in-time (monthly test dates, `past =
  df[df.index<=td]`, same convention as sr_backtest.py) across the F&O-liquid
  ~200-name universe and up to ~11 years of history. THREE CONFIGS, FIXED
  BEFORE SEEING RESULTS: FIB-ALONE, STOCHRSI-ALONE, COMBINED (fib touch AND
  same-direction StochRSI cross within 2 sessions — the lowest-prior config,
  a 7th instance of the auxiliary-overlay-confirms-a-level shape already
  rejected six times on this strategy). A REAL BUG WAS CAUGHT IN THE RESEARCH SCRIPT
  (research_fib_stochrsi.py — NOT chart_analysis.py itself) BEFORE TRUSTING
  THE RESULT: the first run scored FIB-ALONE at -33pp vs control, an
  implausibly large NEGATIVE edge — traced to computing touch direction ONCE
  per fib LEG and applying it to all 5 ratio levels, when by the test date
  price had often already retraced PAST some of them (a live example: DOWN
  leg, price at 212.32, but the 23.6-61.8% levels sat at 206.72-211.78, all
  BELOW spot, only 78.6% still genuinely ahead) — testing an already-passed
  level as "will price fall back through it" is a momentum-unfavourable
  question by construction and alone produced the spurious result. Fixed to
  classify touch direction PER LEVEL against current price (matching how S/R
  levels are classified everywhere else in this codebase), and added the
  day-0 guard fib_retracement itself doesn't have (a level already inside the
  touch band at test time is a guaranteed hit with zero predictive content —
  the same defect min-separation fixed for S1/R1, absent here since Fib
  levels are unfiltered arithmetic fractions). RESULT after the fix (full
  ~200-symbol, 11-year run, same day): 0/3 CONFIGS CLEARED. FIB-ALONE
  (n=45,321 touches) essentially reproduces Tsinaslanidis & Guijarro verbatim:
  real 64.7% vs control 64.2%, +0.5pp, CI [-0.1,+1.1] — statistically
  detectable at this n but an order of magnitude below the 5pp bar, and would
  not survive one round-trip's transaction cost regardless. STOCHRSI-ALONE
  (n=4,548): +1.6pp, CI [-0.5,+3.6], OOS point estimate FLIPS SIGN (-1.8pp) —
  an in-sample fluctuation, not a real effect (a --quick 30-symbol smoke test
  had shown a misleading +7.4pp that collapsed to +0.1pp OOS on the small
  sample, illustrating exactly why the full run mattered). COMBINED (n=3,174,
  fib touch AND same-direction cross within 2 sessions) is the most decisive
  of the three: CI EXCLUDES ZERO IN THE UNFAVOURABLE DIRECTION (-4.3pp, CI
  [-6.1,-2.6]) — requiring both signals to agree filters for a WORSE subset,
  not a better one, while discarding 93% of the touches to get there. 7th
  confirmation of the auxiliary-overlay-fails pattern on this strategy.
  chart_analysis.py's fib_retracement/stoch_rsi remain permanently
  display-only; per the pre-registration this line of work is CLOSED, not
  queued for a different threshold or config.
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
- THE ADVISOR'S LEVEL FIX NEVER REACHED THE S/R TOOL (2026-09-01). There were
  TWO functions named `get_trade_levels` — `full_advisor.get_trade_levels`
  (REBUILT 2026-08-01, ATR-based) and `support_resistance.get_trade_levels`
  (the ORIGINAL anti-momentum construction: buy_zone = nearest support,
  stop = support*0.97, rr measured from support instead of from where you
  would actually buy). The interactive S/R tool printed the defective one —
  as `📥 Buy / 🛑 Stop / 🎯 Target` in `--verbose` and as the `R:R` column in
  the default table — for a month after the advisor was fixed. Same failure
  class as the two drifted momentum_score copies and the three inlined
  inverse-vol copies: one quantity computed in two places, fixed in one.
  MEASURED ON THE 60-NAME FIXED PANEL, the defect had CHANGED SHAPE since
  2026-08-01 but not gone away — min-separation (2026-08-05) caps levels near
  spot, so the old "stop 40% wide" symptom no longer appears (0/60 wider than
  -18%). What remained: entries a median -6.81% below spot (vs -1.17% now),
  i.e. still unfillable inside a month for a momentum name, and — the live
  danger — **R:R inflated to a median 4.77 against an honest 1.88**, because
  risk was a fixed 3% of support rather than a real stop distance from a real
  entry. Every setup in that table read ~2.5x better than it was.
  FIXED by DELEGATION, not by re-patching: `support_resistance.get_trade_levels`
  now calls `full_advisor.get_trade_levels`, so there is ONE construction.
  full_advisor's gained `cur=`/`symbol=` kwargs (default None) so the live
  reference price still drives level selection on the interactive path;
  verified byte-identical advisor output across 40 names with them unset.
  Import is LAZY inside the function — full_advisor imports get_levels from
  support_resistance, so a module-level import is circular. Return shape
  unchanged, both call sites untouched. Invariants now hold 60/60 (entry <=
  spot, stop below entry, stop never wider than the catastrophic stop, target
  above entry) — exactly what the old version violated.
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
- PRICE DOWNLOAD SPLIT OUT, THEN THE WHOLE PIPELINE MERGED BACK INTO ONE
  (2026-08-06, then 2026-08-07). Kite's historical_data() for a session is
  NOT final at 18:15 IST — official settlement (Bhavcopy) typically processes
  after 19:00-20:00 IST, sometimes later. run_daily_log.sh used to call
  update_prices_kite.py at 18:15 as its first step, which raced that
  settlement: the overlap-agreement check (AGREE_TOL=0.005) passed because
  the in-flux value was still close enough to the prior close, but Kite's OWN
  historical_data() for that same date returned a materially different
  (now-final) print the next morning — caught live 2026-08-06 as a mass
  ~350/500-symbol version of the narrower stale-write bug fix_stale_bar.py
  (below) was built for. First fix (2026-08-06): price download alone moved
  to a new `run_price_update.sh` on its own ~00:30 IST timer, run_daily_log.sh
  kept the rest at 18:15. SECOND CHANGE (2026-08-07, user request): merged
  BACK into ONE pipeline — run_price_update.sh now runs update_prices_kite.py
  + trim_partial.py + repair_price_gaps.py AND everything run_daily_log.sh
  used to run at 18:15 (data_integrity_check, market_scanner, both S/R
  loggers, exit_engine, paper_trader, full_advisor, news_watchdog, agent_sim,
  exit_shadow, sim_charts), all on the single `stockai-price-update` systemd
  timer (Tue-Sat 00:30 IST — the day-shift because a 00:30 run for Monday's
  session lands in early Tuesday; TimeoutStartSec raised 1800->3600 for the
  longer combined run), logging to the original cron_daily_log.log (which
  lives at `data/cron_daily_log.log`, NOT the repo root). The old
  `stockai-daily` 18:15 timer is DISABLED (`systemctl --user disable --now`)
  but run_daily_log.sh is kept as a manual/standalone script — it still works
  if run by hand (e.g. the Desktop launcher), it just no longer fires on its
  own schedule. Rationale for merging back: with price download already
  moved post-settlement, nothing downstream needed a separate evening slot —
  the S/R loggers, exit engine, etc. always only read the last COMPLETED
  close, never live — so keeping two pipelines just meant two schedules and
  an ordering to track for no remaining benefit. Net effect: EVERYTHING,
  including paper trading and advisor calls (previously evening, human-review
  timing), now lands just after midnight instead — a real behavior change
  the user explicitly chose over keeping trading/advisory logic on a separate
  evening timer. See memory kite-settlement-lag-2026-08 and
  pipeline-merge-2026-08.
- STALE-BAR AUTO-FIX + CORPORATE-ACTION DIAGNOSIS (2026-08-06,
  fix_stale_bar.py). Kept as a safety net alongside the 00:30 timer above
  (a genuinely stale write is still possible — e.g. a symbol whose repair
  script ran mid-settlement, the original 2026-08-05 ~100-symbol incident
  this was built for), but should trigger far less often now that price
  download itself runs after Bhavcopy settles. `update_prices_kite.py`
  calls `fix_stale_bar.auto_fix()` before its normal append step: for each
  symbol, if the newest CSV bar disagrees with Kite's current value but
  EVERY older bar still agrees closely, that's a stale write — silently
  corrected. If instead several consecutive PRIOR days sit at the same
  offset (a plateau, allowing up to 2 intervening near-zero-diff "clean"
  days — a real adjustment settles over a few sessions, not instantly) and
  that plateau is adjacent to today, that's a genuine dividend/split
  re-adjustment in progress — left untouched, reported separately. Never
  conflate the two: a first version checked "does any prior bar disagree at
  all" and misclassified 292/500 symbols; scanning the whole lookback window
  for a plateau anywhere (not just adjacent to today) misclassified ANGELONE
  (an old, already-settled 3-week-old plateau blocking an unrelated fresh
  glitch). The diagnosis is written to `data/corporate_action_watch.json`
  (`{"updated": date, "symbols": [...]}`) rather than making `sr_daily_logger`
  re-derive it with its own Kite calls (risk of the two disagreeing). A
  SCOPED run (explicit symbols on argv) MERGES into that file rather than
  overwriting it — a full run replaces the whole list, but a one-symbol
  manual re-check must not drop every other symbol from today's diagnosis.
  `sr_daily_logger.py` reads the watch file and splits its "symbol logged
  behind today" warning into two: real problems ("investigate, NOT
  expected") vs corporate-action — before this split, both looked
  identical to a non-technical operator. NOTE the corporate-action half USED to
  read "informational only, self-resolves, re-running will not speed this up"
  and that was FALSE past AGREE_TOL; it now names the repair command. See the
  adjustment-lag deadlock entry below.
- OHLC-UNIFORMITY TEST ADDED 2026-08-11 (fix_stale_bar.py) — the plateau
  heuristic above is NO LONGER the primary discriminator; it is now a
  fallback. THE DEADLOCK IT FIXED: `agreement()` splices at the CSV's
  NEWEST bar, so if that bar is bad the splice is refused, the CSV never
  advances, and the same bad bar stays the splice point FOREVER —
  self-perpetuating, never self-heals. Caught live 2026-08-11 with
  BRITANNIA/CEATLTD/COALINDIA/COFORGE/ICICIBANK stuck 6 sessions at
  2026-08-04 and PCBL 5 at 08-05, while the operator-facing message still
  said "self-resolves in a few sessions" — it could not. Each had a REAL
  late-July dividend whose plateau had ALREADY SETTLED (0.000% days on
  07-31/08-03), then took an unrelated PARTIAL-CANDLE write on 08-04/05;
  the plateau walk-back's 2-clean-day tolerance found the settled plateau
  just beyond the gap and vetoed the repair. Same class as the ANGELONE
  misclassification, but with the stale plateau ending 1-2 days before the
  glitch instead of three weeks — i.e. tightening the clean-day tolerance
  cannot fix this, the two cases are genuinely indistinguishable from
  close-price history alone. THE FIX measures the bar instead of inferring
  from history: a corporate action rescales ALL FOUR OHLC fields by an
  IDENTICAL ratio with volume unchanged (measured CHENNPETRO
  0.95820/0.95818/0.95822/0.95813 vol 1.007x, INDUSTOWER 0.96377/0.96368/
  0.96372/0.96373 vol 1.000x); a partial candle CANNOT — the session's Open
  is final the moment it prints so it sits at EXACTLY parity, while
  Close/Low/Volume are frozen mid-session (measured: all six stuck names
  open_ratio 1.00000, close_ratio 1.007-1.012, Kite volume 1.03-3.4x the
  CSV's). The groups separate by ~100x (genuine actions spread ~9e-5 across
  fields, partial candles 7.8e-3 to 2.1e-2), so UNIFORM_TOL=0.0005 sits an
  order of magnitude clear of BOTH sides rather than splitting a close
  call. A measured non-uniform bar now OVERRIDES the plateau (the plateau
  only means a corporate action happened recently, NOT that today's bar is
  part of it); the plateau is consulted only when the bar's own shape is
  unmeasurable (missing OHLC columns). Full-universe dry-run confirmed
  narrow scope — exactly 6 fixed, 2 correctly protected, 492 untouched (v1
  of this logic wrongly flagged 292/500, so a narrow result is the check
  that matters). After applying, all six advanced 08-04 -> 08-10 on the
  next update and divergence_check.py reported live/backtest selection
  IDENTICAL. NOTE 46 of 500 price CSVs have no `Symbol` column at all — a
  pre-existing schema variation, not damage; append_new builds rows from
  each file's own columns so it is preserved.
- ADJUSTMENT-LAG DEADLOCK + THE "SELF-RESOLVES" MESSAGE WAS FALSE (2026-08-31,
  `readjust_archive.py`). A SECOND, distinct way the splice point freezes
  forever — and the one the operator was actively told to ignore. When a
  dividend goes ex, the live feeds back-adjust the whole history by a constant
  ratio; the archive is append-only and never rewritten, so it alone keeps the
  old level. Past AGREE_TOL (0.5%) `append_new` returns "disagree", writes
  nothing, and the refused bar STAYS the newest bar and therefore stays the
  splice point. Both `update_prices_kite` and `sr_daily_logger` announced this
  as "NOT an error, nothing to fix ... self-resolves in a few sessions,
  re-running will not speed this up". That is true only BELOW the tolerance —
  i.e. never in the case that actually prints it. Five symbols froze under that
  banner: CHENNPETRO/INDUSTOWER/HINDPETRO (repaired 2026-08-14) and
  BATAINDIA/CESC (9 sessions, to 2026-08-31). Both messages now name the repair
  command instead. Sessions missed this way are NOT recoverable later if the
  name is needed live.
  THE REPAIR IS A UNIFORM RESCALE, NOT A yfinance RE-DOWNLOAD —
  `redownload_fix.py` was retired for reasons that still stand (intermittent
  NaN-OHLC bars; a wholesale rewrite moves every historical price). A rescale
  touches one multiplicative constant, keeps each row's provenance, leaves
  Volume alone, and reproduces exactly what the accepted 2026-08-14 repair did
  (verified against `_quarantine/INDUSTOWER.NS_pre_readjust_2026-08-14.csv`:
  O/H/L ratio 0.963730, std 8e-8, volume untouched).
  TWO REFERENCES, BECAUSE KITE'S OWN HISTORY IS NOT DIVIDEND-CONSISTENT — the
  trap that made the first version of this tool refuse a correct repair.
  BATAINDIA paid TWO dividends (Rs 9 ex 07-31, Rs 25 ex 08-19) and kite/yfinance
  measures 1.01301 BEFORE 07-31 and exactly 1.00000 from 07-31 on: yfinance
  back-adjusted the Rs 9, Kite did not. So Kite cannot judge whether the ARCHIVE
  is internally consistent (measuring against it showed a bogus two-level
  0.97938/0.96680 split). Use yfinance to validate the archive whole-history
  (it is the archive's native adjustment convention — yf/csv was a single
  uniform factor, field spread 3e-11 across all 2873 rows) and Kite ONLY at the
  splice point, which is the one bar `append_new` compares. Both must pass.
  Anything relying on Kite history matching the archive across an ex-date is
  making this same mistake, `fix_stale_bar.py`'s plateau walk-back included.
- STALENESS IS NOW REPORTED BY OUTCOME, NOT BY CAUSE (2026-08-31,
  `update_prices_kite.stale_report`). The per-symbol statuses are cause-based
  (no-token / disagree / no-data) and each cause had its own quiet path:
  "no-token" collapsed into one aggregate line reading "delisted/renamed, left
  untouched", "no-data" was never printed at all. So symbols stopped updating
  for MONTHS with nothing in the log looking wrong — found 2026-08-31 by
  diffing every CSV against the index: GSPL 77 sessions behind, AKZOINDIA 54,
  JBCHEPHARM 26, GUJGASLTD 20, RELINFRA 19. Only BATAINDIA/CESC were flagged
  anywhere. Measuring the OUTCOME (how far behind the index is each CSV) catches
  all of those with one check and keeps catching causes nobody has thought of
  yet — same principle as fix_stale_bar preferring a measured OHLC ratio over an
  inferred plateau. Runs at the end of every price update.
  RELINFRA WAS NOT DELISTED, JUST RENAMED: it moved to NSE's trade-for-trade
  series, so Kite's tradingsymbol is `RELINFRA-BE` and `nse.get("RELINFRA")`
  missed. It was trading normally the whole time and its splice point agreed to
  0.0000% — 19 sessions lost to a suffix. Fixed via
  `update_prices_kite.SYMBOL_ALIASES` (archive filename -> Kite tradingsymbol);
  only add an entry once the splice check has been seen to pass, since a wrong
  mapping appends another company's prices into this one's history. The other
  four ARE genuine corporate events and are correctly left frozen (price_data is
  never pruned): GSPL and GUJGASLTD merged (GUJGASLTD now re-lists with only 31
  bars of history from 2026-07-17, far short of the 126d lookback), AKZOINDIA
  delisted, JBCHEPHARM gone from both feeds.
  INDEX FILES ARE NOT COVERED BY fix_stale_bar — it scans PRICE_DIR only, so
  `indiavix.csv` sat frozen 27 days on a textbook partial-candle write (2026-08-04
  Open/High/Low matched Kite EXACTLY, Close 12.10 vs 12.19), visible the whole
  time as `INDIA VIX disagree 0.74%` in the log and as the sole
  data_integrity_check warning. Repaired by hand 2026-08-31 (+18 bars). VIX is
  not in the trading path (only support_resistance's INDEX_FILES map; the VIX
  overlay was rejected) so impact was nil — but nifty50.csv rides the same
  uncovered path and is NOT harmless: it defines sessions, regime and month-end.
- INTRADAY TIMER IS NOW MONOTONIC, NOT CALENDAR (2026-09-01). `stockai-intraday`
  ran `OnCalendar=Mon..Fri *-*-* 09..15:00/15:00`, which systemd evaluates
  against the REALTIME clock. This laptop repeatedly boots with a stale clock
  that NTP then corrects (the gotcha already in this file, first hit
  2026-07-15). On 2026-09-01 it booted ~5h09m FAST: systemd computed the next
  firing as the FOLLOWING day 09:00 and would have skipped the whole session.
  `systemctl restart` AND `daemon-reexec` both failed to shake it loose — the
  user manager holds the stale reference. Diagnosis is visible as a journal
  whose newest entry is stamped hours ahead of `date`.
  This is not cosmetic: depth has NO historical endpoint, so a skipped session
  is lost permanently and Study 4 is gated on sessions collected. Replaced with
  `OnBootSec=2min` + `OnUnitActiveSec=15min` — monotonic, counts elapsed time,
  immune to realtime jumps. Firing all day is safe and cheap because all three
  ExecStart scripts (`intraday_watch`, `market_scanner`, `log_market_depth`)
  call `market_open_now()` first and exit in ~0.7s outside NSE hours: ~69 extra
  no-op firings/day, ~2 min CPU total. Old unit kept at
  `~/.config/systemd/user/stockai-intraday.timer.bak-2026-09-01`.
  NOTE `stockai-price-update` (the nightly pipeline) is still `OnCalendar` and
  still disabled — the user runs it manually — so it carries the same latent
  skew bug if ever re-enabled.
- OPS CADENCE: NOTHING new needs running daily. run_price_update.sh (systemd
  timer stockai-price-update, Tue-Sat 00:30 IST, Persistent=true) already
  runs the full pipeline — price update, both S/R loggers, exit/paper/advisor/
  agent-sim, everything (see the merge entry above) — and rotates
  cron_daily_log.log at 5MB (it was append-only and unbounded). `./sr_monthly_review.sh` is the ONCE-A-
  MONTH read-only review (run after the month's last Tuesday): fixed panel at
  the production horizon, at 21d, at 10d, plus the dynamic panel, with the
  reading notes inline. It writes nothing and is safe to re-run.
  Do NOT rebuild the P(touch) tables monthly — they're built from ~10y of
  history so a month barely moves them, and rebuilding on the same cadence you
  MEASURE on lets the thing under test change underneath the test. Rebuild
  only when price history is materially extended, then re-check the
  monotonicity invariant.
- AUGUST 2026 REVIEW — THE MODEL IS SOUND; THE REPORT WAS THE PROBLEM
  (2026-08-31, first month logged entirely under the P(touch) table AND
  min-separation, i.e. the calibration re-check CLAUDE.md flagged as
  outstanding). Production-horizon cohort (log dates 08-03..08-24, all
  pointing at the 08-25 last Tuesday, 16 dates x 61 symbols, 2,957 scored
  levels): S1 32.1% / R1 34.7%, weighted mean calibration gap -10.1pp, 7 of
  10 probability bins overconfident. NONE of that is decay. Discrimination is
  strong — AUC 0.839 (S1 0.875, R1 0.805), Brier skill +26.6% over the base
  rate, corr(predicted, actual) across the 16 horizon lengths +0.997 — so the
  RANKING is right and only the LEVEL is off. The level is off because AUGUST
  REALISED 5.4% ANNUALISED ON THE INDEX, THE 0.3rd PERCENTILE OF EVERY
  16-SESSION WINDOW SINCE 2010, against a 13.3% trailing figure.
  reach_probability_v2 keys on distance x TRAILING 252d vol so it cannot see a
  regime that shifts inside the month: it assumed a median 31.0% while 20.2%
  materialised, with 90% of rows quieter than assumed. Split on the regime
  that actually arrived, the model was CALIBRATED where vol came in at or
  above assumed (+5.1pp, mildly underconfident) and hot only where it came in
  quieter (-11.7pp). A table fitted on ~11y of average tape SHOULD overpredict
  in the calmest month of that history. DO NOT FLAT-SHIFT THE PROBABILITIES:
  a -10.1pp shift lifts Brier skill to +31.2% ON THIS MONTH and is wrong in
  the next normal one. Two hypotheses were generated and REJECTED in the same
  review: (a) the sub-5d sqrt-extrapolation path (native tables exist only at
  5/10/15/21d and _interpolate_horizon_prob needs bracketing, so 1-4d falls
  through to a sqrt rescale) is NOT broken — raw gap -13.7pp vs -9.9pp native,
  but conditioned on vol the paths are indistinguishable (-12.3 vs -12.9); the
  sub-5d rows are simply the month's last four and quietest sessions;
  (b) a SHORTER trailing vol window does not help — every window overestimated
  August by 30-41% (median realised/assumed 0.59-0.70 at 21/42/63/126/252d)
  and the best (42d) cuts MAE only 12.0->11.0pp while its correlation with
  realised vol collapses 0.406->0.258. Worth knowing: the 16-variant sweep
  varied the vol ESTIMATOR but every one uses `.tail(252)`, so window length
  had never been tested. ONE finding survives — the distance x vol
  INTERACTION is under-modelled: near levels are vol-insensitive (0-3% band
  misses ~4pp in BOTH regimes) while far levels flip from +15.2pp (normal vol)
  to -20.1pp (quiet) at 5-8%+. That does NOT contradict
  sr-touch-table-distance-calibration-2026-08, which pooled ACROSS regimes
  where this conditions ON them. Pre-registered in
  PREREG_sr_vol_regime_interaction.md and deliberately NOT run — it needs six
  months under the current code (not before 2027-02) because the effect is
  defined by the contrast BETWEEN regimes and August supplies one side.
  Also confirmed: MIN-SEPARATION WORKED (levels within 0.5% of spot 81 -> 0,
  day-0 guaranteed-hit contamination now 1.8% S1 / 1.4% R1 / 0.0% S2-R2,
  median |dist| 1.9-2.3% -> 4.5-4.8%), and LOG_PREVIOUS_SESSION KILLED THE CMP
  CONTAMINATION OUTRIGHT (31% of rows before 2026-08-19, 0 of 427 after).
  On the open question of the 296 historical contaminated rows: LEAVE THEM and
  exclude at analysis time (`--exclude-contaminated`) — five dates of sixteen,
  moving S1 32.1->30.4% and R1 34.7->31.3%, both well inside the confidence
  intervals, and rebuilding trades a real record of what the system saw for a
  uniformity the numbers do not need.
- THE LEVELS DO NOT MARK FLOW CHANGES — TESTED 2026-08-31, NEGATIVE (section 1d,
  `analyse_acted_outcomes`). The user's working definition of S/R is "a level
  where the stock CHANGES ITS FLOW: on reaching support it then rises, on
  reaching resistance it then falls". That is a touch-AND-BOUNCE question — the
  metric the OLD sr_reach_table measured and that the P(touch) rebuild
  deliberately replaced — so the system predicts REACHING while it was being
  read as predicting TURNING. Measured across every August log date at each
  row's own horizon: touched levels moved 2% the right way before the wrong way
  69% (S1) / 67% (R1) of the time, which looks like a large edge and is not.
  A PERMUTATION CONTROL — the same race at a distance shuffled across the
  symbols logged that day, so it has the same distance distribution and the
  same tape but no link to where support actually sits — scores 73% / 62%.
  FLOW minus control +0.4pp, 95% CI [-6.9, +7.1], P(level better) 56%. The
  dynamic panel independently agrees (60/58% vs control 62/61%, control HIGHER).
  TWO MECHANICAL ARTEFACTS inflate the raw number and both must be controlled
  for in any future version of this test: (1) a bar whose Low reaches a level
  almost always CLOSES above its own low — 86% of August's touches closed
  favourably on the touch bar itself, median +1.09% — so the race must start on
  the NEXT bar (starting on the touch bar put the 1% race at 88%); (2) a quiet
  mean-reverting tape bounces off any price. A FIRST version of the control was
  wrong in a way worth recording: it rebuilt the level as cmp0*(1-dist) using
  that level's OWN distance, which reconstructs the level EXACTLY, so "control"
  and "real" were the same number and the difference was 0.0 by construction.
  ALSO: neither factor the user proposed conditioning on carries information
  here. P(touch) does NOT predict flow change (AUC 0.488; terciles flat at
  70/72/68%; corr with return -0.024) and it is ANTI-predictive for the
  narrower "did it hold" question (AUC 0.384 — high P(touch) means a NEAR
  level, and near levels are both easy to reach and easy to break, so a high
  S1_prob must never be read as a strong support). HorizonDays shows a gradient
  (76% at 1-4d to 68% at 13-16d) but is PERFECTLY COLLINEAR with the calendar
  date inside one month — every August row points at 2026-08-25, so H=16 IS
  08-03 — and the two cannot be separated without more months. Flow at a 3%
  threshold falls to 40-52%, i.e. at or below a coin flip: the small-scale
  bounce is noise around a level, not directional follow-through. This is the
  5th independent negative on trading these levels, after
  PREREG_tradeable_levels (199 symbols / 39,987 obs, holdout 43.9%/49.0% vs a
  55% bar), the S/R improvement batch, the exit-into-resistance test and the
  containment-band trading test. TREAT THE SUBSYSTEM AS ANSWERING "WHERE MIGHT
  PRICE GO" (it does that well — AUC 0.839), NOT "WHERE WILL PRICE TURN".
  Entry pricing lives in full_advisor.py's ATR-based entry/stop/target; the
  "level it should not dip below" question is containment_band.py's, and that
  ships as a RISK tool because trading it failed on adverse selection.
- ANTI-PREDICTIVE-PROBABILITY CAVEAT NOW PRINTS ON THE INTERACTIVE PATH
  (2026-09-01). The finding that a high S1_prob/R1_prob means a NEAR level —
  easy to reach AND easy to break, AUC 0.384 for "holds", i.e. mildly
  ANTI-predictive — lived only in the month-end report, not where the number
  is actually read before a decision. Both interactive paths now print it:
  `analyse_table` (default) after the P(touch) legend, and `analyse`
  (`--verbose`) under the level list. Nothing else imports either function, so
  the change cannot reach the loggers or the measurement record. Also relabelled
  `--verbose`'s S1 marker from "← buy zone" to "← nearest support" — it was the
  exact misreading the new caveat warns against, printed four lines above it.
- `sr_monthend_analysis.py` checks hit-rate, level drift, probability calibration,
  n-sensitivity, distance-vs-accuracy — run only after 2-3+ weeks of logged data.
  REWORKED 2026-08-31 after the August review above, because the report itself
  was the most dangerous thing in the subsystem — a sound model reading "32%
  against a 65-68% baseline" with no explanation is how a working instrument
  gets thrown away, and this repo has misread its own measurements three times
  already (fake 100% S/R hit rate, a cash-only month scored "consistent",
  call_report silently broken 10 days). Changes:
  (1) THE PRODUCTION HORIZON IS NOW THE DEFAULT — every row scores against its
  own logged HorizonDays. The old fixed-21d default reported "R1 95.3%" on the
  August log: no August row had 21 sessions of forward data, so the figure was
  built almost entirely from JULY rows, and July predates min-separation.
  `--window N` still forces a fixed horizon (and reproduces the old output
  exactly — verified byte-identical at --window 21); `--to-month-end` is kept
  as a no-op so existing invocations and sr_monthly_review.sh keep working.
  (2) `--month YYYY-MM` scopes to one month's cohort. This is the natural unit:
  every row in a month shares ONE horizon end (its last Tuesday), and pooling
  months mixes code generations.
  (3) `--exclude-contaminated` drops rows whose CMP matches no archive bar for
  its own date, recomputed inline rather than read from audit_sr_log's CSV so
  the exclusion can never run against a stale audit. Data quality (section 0)
  is deliberately assessed BEFORE the exclusion — a day thinned by filtering is
  not a "partial day", and reporting it as one invents a pipeline fault.
  (4) CONFIDENCE INTERVALS RESAMPLE DATES, NOT ROWS. 61 symbols on one session
  share the market's move; treating them as independent understates the
  interval by ~sqrt(panel size), enough to make an ordinary month look like a
  significant deviation. Same lesson as
  sr-touch-table-distance-calibration-2026-08.
  (5) NEW SECTION 1b (by horizon length) — the production window shrinks 16->1
  through the month, so the pooled rate averages two different questions.
  Buckets under n=20 are printed but EXCLUDED from the correlations: with
  --exclude-contaminated removing whole panel-days, a 2-row bucket swings
  0-100% on one symbol and drags corr(predicted, actual) from 0.956 to 0.797.
  (6) NEW SECTION 1c (volatility regime) — prints the vol the table assumed vs
  the vol that materialised, the index percentile of the month's tape, and
  calibration split on that ratio. READ IT BEFORE SECTION 1. Diagnostic only:
  realised vol is not knowable at log time and must never feed a live
  probability.
  (7) NEW SECTION 1d (acted-on outcomes) — reports REACHED / +HELD / +FLOW side
  by side, because the subsystem is routinely read as answering all three at
  once and they give very different numbers. THE CONTROL COLUMN IS NOT OPTIONAL
  and must never be dropped: FLOW alone reads ~70% and looks like a large edge,
  while a permuted-distance control reads ~71%. +HELD is reported BY HORIZON
  too, since it is nearly automatic with one session left (85% at 1-4d vs 37%
  at 13d+) and the pooled figure is not comparable across the month — pooled
  HELD read 53% on August while the month-START levels held only 28%.
  (8) Calibration now compares actual against the MEAN PREDICTED probability
  in each bucket, not the bucket MIDPOINT — the midpoint misstates the gap
  whenever a bucket is wide or rows cluster at one end (August's 0-20 bucket
  averaged 5.7% predicted against a 9.5% midpoint, so the old version reported
  a 7pp error where the truth was 3pp). Overconfidence is flagged when the
  prediction falls outside the date-clustered CI, replacing a fixed +-10pp rule
  that fired on noise in thin buckets and stayed silent on real misses in dense
  ones. AUC and Brier skill are printed alongside, because a large calibration
  gap with a high AUC is a LEVEL problem, not a broken model.
  `./sr_monthly_review.sh [YYYY-MM]` is scoped to one month accordingly.
  THE DYNAMIC PANEL CANNOT BE BACKFILLED and its two missing August sessions
  (08-18, 08-20) are left as gaps: its membership is recomputed daily from
  core.scan_universe, which has no point-in-time mode, so reconstructing a
  missed date would invent panel membership. backfill_sr_log.py is fixed-panel
  only and now says so.
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