"""
Single source of truth for the momentum strategy's parameters.

Everything that scores, sizes, backtests, or recommends imports from here, so
the live path and the backtest can never silently diverge again (the root cause
of the stale Sharpe 0.87 that didn't reproduce). Change a number here and every
consumer changes with it.

Current config = "Aggressive diversified" (Config 4 + catastrophic stop),
chosen for a profit-oriented objective. Continuous out-of-sample 2017-2026:
CAGR 34.5%, Sharpe 1.48, MaxDD 19.2%, 15.5x capital.
"""

# ---- Horizons ----
LOOKBACK = 126          # momentum lookback (trading days)
HOLD     = 21           # rebalance / holding period (trading days)
VOL_WINDOW = 63         # window for inverse-vol sizing & the ret/vol score
COST     = 0.001        # one-way transaction cost (0.1%)

# STAGED ENTRY (buying new positions over several sessions instead of one
# print) tested and REJECTED 2026-08-01. Motivation: reduce single-print
# execution risk on a fresh position. Built as backtest_portfolio.
# run_backtest_laggards_only(stage_days=N) — averages a NEW position's fill
# price over the next N closes instead of the single close on the rebalance
# day (existing-holding top-ups stay single-fill; only fresh entries stage).
# Verified neutral at stage_days=1 (single-close, unchanged production
# output) before testing N>1.
# Walk-forward (19 windows) — every N LOSES, and gets WORSE with more days,
# same monotonic pattern as the regime-hysteresis rejection:
#   single-close (prod): meanCAGR 31.47%  meanSharpe 1.24  worstDD 28.7%  neg 1
#   stage=2d:              meanCAGR 30.31%  meanSharpe 1.20  worstDD 29.5%  neg 3  wins CAGR 2/19 Sharpe 4/19
#   stage=3d:              meanCAGR 29.61%  meanSharpe 1.19  worstDD 30.2%  neg 3  wins CAGR 3/19 Sharpe 4/19
#   stage=5d:              meanCAGR 28.57%  meanSharpe 1.18  worstDD 31.1%  neg 3  wins CAGR 4/19 Sharpe 7/19
# Cause: for a MOMENTUM strategy the days right after a buy decision are, on
# average, still trending in the entry's favor — staging the entry means
# averaging in at progressively worse prices as the trend continues, the
# mirror image of the [[regime-detection-rejected-2026-08]] finding (delay
# costs more than the risk it removes). Also adds negative windows (1->3).
# Single-close fill stays production. stage_days kept as a research handle.

# ---- Universe gate (F&O liquidity proxy) ----
# User only wants to trade names liquid/large enough to have listed F&O
# (futures & options) — for hedgeability and tight spreads, still trading
# cash equity (not the derivatives themselves). Rather than a static F&O
# symbol list (survivorship-biased if applied backward — NSE's F&O roster
# changes twice a year, so today's list didn't exist in 2015), the universe
# is gated by a POINT-IN-TIME liquidity proxy: top UNIVERSE_TOP_N names by
# trailing median daily turnover (Close x Volume) as of each date. This is
# self-updating and survivorship-free by construction — see memory
# fno-universe-migration for the full rationale. Applies to SELECTION only;
# market_breadth_pct()/compute_breadth_series() stay on the full broad
# universe (regime is a market-wide concept, not scoped to what you buy).
UNIVERSE_TOP_N = 200            # approx size of the real NSE F&O roster (~180-190
                                 # as of 2024-2025 SEBI review cycles + small buffer).
                                 # Set by this real-world constraint, NOT tuned for
                                 # backtest performance — walk-forward sensitivity to
                                 # this number is noisy/non-monotonic (150->13.8% CAGR,
                                 # 200->17.0%, 250->14.4%, 300->17.0%), so picking
                                 # whichever N backtests best would be curve-fitting.
                                 # Don't re-tune this off backtest results.
UNIVERSE_TURNOVER_WINDOW = 20   # trading days for the trailing turnover rank

# ---- Regime exposure & breadth ----
# Exposure = fraction of capital deployed; rest held as cash.
# 2026-07-12 boost (user decision, profit-oriented): previous values
# (BULL .95 / SIDEWAYS .60 / BEAR .30 / UNKNOWN .60) scaled x1.25 capped at
# 1.0 — no leverage. Surfaced by the VIX-overlay study's control run: NOT
# alpha, a pure risk-appetite dial. Full-history 17.6%/0.96/DD 35.6% ->
# 19.6%/0.91/DD 39.1%; CAGR improves in 79% of walk-forward windows. The
# user explicitly accepted the deeper drawdown for the extra CAGR — see
# memory research-verdicts-2026-07.
REGIME_EXPOSURE = {
    "BULL":     1.00,
    "SIDEWAYS": 0.75,
    "BEAR":     0.375,
    "UNKNOWN":  0.75,
}

# VOLATILITY-TARGETED EXPOSURE tested and REJECTED 2026-08-01. Hypothesis:
# scale exposure by TARGET_VOL / trailing realized index vol (continuous,
# adapts faster than the 3 discrete regime tiers) instead of the fixed
# REGIME_EXPOSURE table. Built with an exposure_fn hook
# (backtest_portfolio.run_backtest_laggards_only(exposure_fn=...)), clipped
# to [0.5x, 1.5x] scaling and [0,1] final exposure.
# CRITICAL LESSON FROM THE VIX-OVERLAY REJECTION (2026-07, see memory
# research-verdicts-2026-07): "any exposure-scaling signal must beat the
# UNCONDITIONAL-scaling control, not just baseline" — a VIX-conditioned boost
# looked like a +1.3pp winner until an unconditional boost of the same average
# size beat it (+3.0pp), proving the conditioning added nothing. Applied the
# same control here: the vol-target rule's average scale factor was ~1.07x
# (target vol 0.14 vs ~0.134 median realized), so tested against an
# UNCONDITIONAL 1.0722x exposure multiplier, not a naive vs-baseline compare.
#   baseline (fixed tiers):        meanCAGR 31.47%  meanSharpe 1.24  worstDD 28.7%
#   vol-target (target=0.14):      meanCAGR 27.56%  meanSharpe 1.12  worstDD 33.0%  (2 negative windows)
#   unconditional 1.0722x control: meanCAGR 32.44%  meanSharpe 1.23  worstDD 30.3%
# Vol-target LOST to both baseline AND the matched unconditional control on
# CAGR and Sharpe, and made worst-case DD worse, not better — the opposite of
# vol-targeting's usual selling point. Swept target in {0.12,0.15,0.16} x
# window in {20,63}d as a calibration check: every variant loses to baseline
# on Sharpe (0-10/19 windows) and most on CAGR too, with worse worst-DD in
# every single case (30.2-35.5% vs 28.7%). Not a calibration problem — the
# mechanism itself doesn't help here, likely because de-risking on an already-
# lagging realized-vol signal mistimes both the drawdown (too late to avoid
# it) and the recovery (too late to re-lever into it). Fixed discrete regime
# tiers stay. exposure_fn hook is kept (already used for the VIX study) for
# any future exposure-scaling hypothesis — but it MUST be tested against a
# matched unconditional control, not baseline alone, per the VIX lesson.

# Number of names held per regime.
# SIDEWAYS = 3 (was 6, changed 2026-07): SIDEWAYS-regime picks contribute much
# less than BULL's (momentum signal itself is weaker in choppy markets), so
# spreading across 6 names diluted the few genuinely good setups. Walk-forward
# tested (19 overlapping 3y windows): 3 names beats/matches 6 in 16/19 windows,
# lower mean drawdown, fewer negative-Sharpe windows (1 vs 2). A more
# aggressive 2-name version was also tested and REJECTED — higher average
# return but genuinely worse tail risk (a new negative-Sharpe window appeared,
# drawdowns ballooned to 30% in weak windows) — that's concentration risk
# materializing, not a free lunch. Don't re-tune below 3 without new evidence.
# BEAR = 4 (was 2, changed 2026-07): BEAR=2 was never independently tested —
# it just inherited the "fewer names in weak regimes" pattern. Audit
# (research_concentration.py) found MAX_WEIGHT=0.20 is a cap-then-
# renormalize: with only 2 names there's no 3rd+ name for excess weight to
# flow into, so it renormalizes right back to ~50/50 — the cap silently
# didn't bind in 100% of historical BEAR rebalances. Single-name gap/halt
# stress (fraud disclosure, regulatory halt — invisible to any close-based
# backtest): BEAR=2 exposed 5.6-13.1% of TOTAL capital to one name;
# BEAR=4 cuts that to 3.4-8.0% and restores the cap to 96% functional.
# Return cost of widening was negligible and mixed-sign (mean CAGR delta
# -0.4 to -0.5pp across BEAR in {3,4,5}, ~half of windows even preferred
# wider). Don't lower back to 2 without re-addressing the cap-defeat finding.
REGIME_NAMES = {
    "BULL":     10,
    "SIDEWAYS": 3,
    "BEAR":     4,
    "UNKNOWN":  6,
}
MAX_WEIGHT = 0.20               # single-name cap (diversification vs concentration)

# CORRELATION-AWARE (RISK-PARITY) SIZING tested and REJECTED 2026-08-01.
# Production sizing is plain inverse-vol, which provably ignores correlation
# between held names — measured diversification ratio only 1.62 on the
# current top-10 book, a real structural gap. Built a shrunk-covariance
# equal-risk-contribution allocator (backtest_portfolio.risk_parity_weights,
# opt-in via run_backtest_laggards_only(sizing_fn=...)) as a mechanism-first
# candidate (not pattern-mined) to close that gap.
# Walk-forward (19 windows, several shrink/window combos: shrink in
# {0.3,0.5,0.7}, corr-window in {63,126}d): every variant landed within
# +-0.3pp mean CAGR and +-0.01 mean Sharpe of the inverse-vol baseline
# (31.47%/1.24), winning only 8-12/19 windows — a wash, not an improvement,
# and well below this repo's >=14/19 bar. Root cause: MAX_PER_SECTOR=2 is
# already doing most of the diversification work — measured mean pairwise
# correlation across the sector-capped top-4/top-10 book is ~0.30, not high
# enough to leave much for a correlation-aware reallocation to capture once
# the sector cap has already separated the names. Sizing stays plain
# inverse-vol. risk_parity_weights() is kept as a research handle (sizing_fn
# param) in case the sector cap or MAX_PER_SECTOR is ever loosened, which
# would change this calculus.
BREADTH_BULL_MIN = 0.50         # need >=50% of universe above 200DMA to allow BULL

# REGIME DETECTION UPGRADES tested and REJECTED 2026-08-01, both pieces:
#
# (a) SYMMETRIC BREADTH GATE for BEAR/SIDEWAYS (extending the existing
# BULL-only weak-breadth demotion above) was considered and DROPPED before
# writing any code — measured first: breadth level has ~zero-to-slightly-
# NEGATIVE correlation with forward 21d index return within BEAR (-0.066,
# n=30) and SIDEWAYS (-0.114, n=34) historical periods, the opposite sign a
# "weak breadth confirms bearishness" gate would assume. Only the existing
# BULL-side demotion (price>ma50>ma200 but breadth<50% -> demote to SIDEWAYS)
# has a measured basis; don't extend it symmetrically without new evidence.
#
# (b) N-DAY HYSTERESIS on the 50/200DMA regime flip (backtest_portfolio.
# confirmed_regime_fn, opt-in via run_backtest_laggards_only(regime_fn=...)):
# motivated by real whipsaw (24% of regime "runs" at rebalance cadence last
# exactly one period before reverting; raw daily signal flips 270x over full
# history) but REJECTED — walk-forward (19 windows) shows every confirmation
# delay LOSES, monotonically worse with longer delay:
#   no hysteresis (prod): meanCAGR 31.47%  meanSharpe 1.24  worstDD 28.7%  wins  -
#   confirm=3d:            meanCAGR 29.20%  meanSharpe 1.19  worstDD 37.7%  wins CAGR 7/19  Sharpe 7/19
#   confirm=5d:             meanCAGR 27.37%  meanSharpe 1.17  worstDD 34.1%  wins CAGR 4/19  Sharpe 8/19
#   confirm=10d:           meanCAGR 25.42%  meanSharpe 1.05  worstDD 33.1%  wins CAGR 2/19  Sharpe 3/19
# Cause: hysteresis only changes the call on 14/130 rebalance dates (11%) but
# those are exactly the regime TRANSITION points, where REGIME_EXPOSURE/
# REGIME_NAMES swing hardest (e.g. BULL 1.0x/10 names vs BEAR 0.375x/4 names)
# — a delayed confirmation means running the WRONG exposure/name-count
# precisely when a new trend is accelerating fastest, which costs far more
# than the whipsaw it prevents. The raw same-day flip stays production.
# confirmed_regime_fn is kept as a research handle in case a future study
# wants a smaller/asymmetric delay (e.g. confirm only BEAR->BULL, not the
# reverse) — untested here, would need its own walk-forward run.
MAX_PER_SECTOR = 2               # diversification cap on top-N selection (was
                                 # documented in CLAUDE.md but not actually enforced
                                 # anywhere in code until the F&O universe migration
                                 # found unenforced concentration up to 8/10 names in
                                 # one sector during BULL rebalances — see sectors.json
                                 # and memory fno-universe-migration)

# ---- Idle-cash yield (ADOPTED 2026-07-13) ----
# The engines historically credited 0% on uninvested cash, but regime
# exposure keeps 0-62.5% of the momentum sub-capital idle — in live
# deployment that cash sits in a liquid ETF (e.g. LIQUIDCASE/LIQUIDBEES on
# Zerodha: no exit load, T+1, ~6-7% gross) instead of earning nothing.
# This is accounting realism with a CAUSAL mechanism (money-market rates
# exist), not a tuned parameter — 0.06 is set BELOW prevailing liquid-fund
# yields deliberately; do not tune it against backtest output. Ops mandate:
# idle cash in the real Zerodha account must actually be parked in the
# liquid ETF for the backtest to remain honest. Approximation note: cash
# freed mid-period by a -18% stop accrues the full period's yield (rare,
# negligible, conservative to fix later).
CASH_YIELD = 0.06

# ---- Gold sleeve (ADOPTED 2026-07-13, research_lowvol_sleeve.py) ----
# GOLD_ALLOC of TOTAL capital is held in GOLD_SYMBOL (GOLDBEES ETF,
# data/etf_data/), rebalanced back to target at each month-end alongside the
# momentum book; the momentum engine runs on the remaining (1 - GOLD_ALLOC)
# as its own sub-capital with regime exposure unchanged. Mechanism is
# DIVERSIFICATION, not alpha: gold's 21d-return correlation to the momentum
# book is +0.01 over 2015-2026. Evidence (held to the statistical-hygiene
# bar): 85/15 blend vs 100% momentum — Sharpe 0.85 -> 0.98, MaxDD 40.5% ->
# 31.4%, CAGR 17.00% -> 17.33% (point estimate; treat as ~flat); paired
# block-bootstrap Sharpe delta +0.14, 95% CI [+0.06, +0.22] — the FIRST
# config delta in this project to clear 95% significance — and better
# Sharpe AND MaxDD in 16/16 rolling 3y walk-forward windows.
# HONEST CAVEAT: gold's 2015-2026 INR CAGR (15.9%, incl. +74% in 2025) is
# historically exceptional and should NOT be extrapolated — the durable part
# of this decision is the ~zero correlation (structural), not gold's return.
# If gold mean-reverts to ~6-8% nominal, expect the sleeve to COST ~1-1.5pp
# gross CAGR in exchange for the drawdown/Sharpe benefit. Sized at 0.15
# (the mildest gold blend tested) partly for that reason and partly to keep
# multiple-comparisons debt low — do not creep this up because a bigger gold
# number backtests better over this gold-friendly decade.
# (A low-vol equity sleeve was tested in the same study and NOT adopted:
# +0.52 correlation to momentum, costs CAGR, and doubles the manual monthly
# trading workload for a marginal Sharpe gain beyond what gold provides.)
# DISABLED 2026-07-17 by user decision ("no gold/intl sleeve for now") —
# risk-appetite call, not an evidence reversal; the evidence above stands.
# Re-enable by restoring 0.15 (all sleeve code paths check alloc > 0).
GOLD_ALLOC = 0.0
GOLD_SYMBOL = "GOLDBEES.NS"

# ---- International sleeve (ADOPTED 2026-07-13, research_intl_sleeve.py) ----
# Same construction and evidence bar as the gold sleeve: INTL_ALLOC of TOTAL
# capital in an INR-denominated Nasdaq-100 ETF (MON100), rebalanced to
# target each month-end; momentum runs on the remaining
# (1 - GOLD_ALLOC - INTL_ALLOC). Mechanism: different equity market +
# implicit USD exposure (INR tends to depreciate in Indian risk-off, so the
# INR value of a US asset cushions exactly when the domestic book bleeds).
# Correlation of 21d returns to the momentum sleeve: +0.10; to gold +0.19.
# Evidence (paired bootstrap vs the 85/15 gold-only production): Sharpe
# delta +0.14, 95% CI [+0.05, +0.23] — significant; CAGR point +0.7pp
# (NOT significant, P=77%); better Sharpe in 16/16 rolling 3y windows,
# MaxDD 29.2% -> 24.8%. HONEST CAVEAT: Nasdaq 2015-2026 (plus the INR
# depreciation tailwind) was an exceptional decade — do NOT size this off
# its backtested CAGR; 0.10 is the MILDEST tested intl weight, same
# anti-creep rule as GOLD_ALLOC. Deeper blends (up to 60/20/20) backtest
# "better" — that is exactly the trap; momentum is the alpha engine and
# stays >= 70%. Tax: MON100 is non-equity-oriented for tax (LTCG 12.5%
# only after 24m, STCG slab) — reflected in research_net_returns.
# DISABLED 2026-07-17 alongside GOLD_ALLOC (same user decision; evidence
# stands, restore 0.10 to re-enable).
INTL_ALLOC = 0.0
INTL_SYMBOL = "MON100.NS"

# ---- Risk control ----
# Profit-oriented: no tight intra-hold exits (they cost ~0.3 Sharpe and CAGR by
# whipsawing out of momentum trends). Only a WIDE catastrophic circuit breaker
# to cap true tail disasters (fraud/news gaps) between rebalances. Backtest-
# validated: adding this to the no-exit config slightly IMPROVED all metrics.
CATASTROPHIC_STOP = 0.82        # exit if price < entry * 0.82  (-18% from entry)

# ---- Scoring filter (heuristic that beat ML out-of-sample) ----
# A stock is eligible only if 6m and 3m momentum are both positive and it trades
# above its 50DMA; score = 126d return / 63d volatility.
RSI_OVERBOUGHT = 80             # advisory cap for the recommender (was 75; loosened
                                # for profit orientation — momentum names run hot)

# ---- Exit engine ----
# Priority order (see exit_engine.py): (1) catastrophic stop, always on;
# (2) month-end re-qualification gate, always on — sells names that no longer
# pass compute_score's eligibility filter or fell out of the regime's top-N.
#
# An early "good exit" take-profit rule (resistance-fade: sell overbought
# names near a strong resistance with low further-reach probability) was
# tried and REJECTED (2026-07) after backtest_portfolio.py --compare-early-exit:
# CAGR 17.27% vs 17.42%, same Sharpe (0.87), same MaxDD — small but consistent
# net negative, same conclusion as the CATASTROPHIC_STOP comment above (tight
# intra-hold exits whipsaw out of momentum trends). Root cause: the empirical
# reach_probability_v2 table shows momentum names near a resistance still
# push through ~58-60% of the time — "low continuation odds" is empirically
# false for this setup, so fading it is backwards for a momentum strategy.
# Don't re-add this premise without a different hypothesis than resistance-fade.
#
# RATCHETING TRAILING STOP tested and REJECTED 2026-08-01, after the user
# relaxed the mandate to allow selling at any time (month-end rebalance stays
# compulsory; intra-month exits are now PERMITTED, just still unsupported).
# Giveback stop vs the high-since-entry, high-water mark carried across
# rebalances (backtest_portfolio.run_backtest_laggards_only(trail_stop=...)).
# Full-history looked like a cheap DD trade (-0.3pp CAGR, MaxDD 37.1->29.7 at
# 0.88) — but the 19-window walk-forward says otherwise:
#   base    meanCAGR 26.22%  meanSharpe 1.16  worstDD 29.1%  1 negative window
#   0.85    meanCAGR 21.14%  meanSharpe 1.01  worstDD 31.9%  2 negative
#   0.88    meanCAGR 21.13%  meanSharpe 1.07  worstDD 28.5%  2 negative
#   0.90    meanCAGR 20.25%  meanSharpe 1.08  worstDD 24.8%  2 negative
# Every level LOSES on mean CAGR (-5 to -6pp), wins only 4-6 of 19 windows,
# and ADDS a negative window. Third independent rejection of the tight
# intra-hold exit family (after tight-trailing/50MA and resistance-fade).
# The permission to sell early is real; the EVIDENCE still says don't.

# Non-strategy holdings: flag for manual review, never auto-sell via the
# momentum re-qualification gate (gold ETF, delisted/BE-series, manual entries).
EXIT_EXCLUDE_SUFFIXES = ["-BE.NS", "-BE"]
EXIT_EXCLUDE_SYMBOLS = ["GOLDBEES.NS", "MON100.NS", "HARCR.NS",
                        "RCOM.NS"]  # dead/delisted write-off (~₹0.92) — flag only, never auto-exit
