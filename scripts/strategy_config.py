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
BREADTH_BULL_MIN = 0.50         # need >=50% of universe above 200DMA to allow BULL
MAX_PER_SECTOR = 2               # diversification cap on top-N selection (was
                                 # documented in CLAUDE.md but not actually enforced
                                 # anywhere in code until the F&O universe migration
                                 # found unenforced concentration up to 8/10 names in
                                 # one sector during BULL rebalances — see sectors.json
                                 # and memory fno-universe-migration)

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

# Non-strategy holdings: flag for manual review, never auto-sell via the
# momentum re-qualification gate (gold ETF, delisted/BE-series, manual entries).
EXIT_EXCLUDE_SUFFIXES = ["-BE.NS", "-BE"]
EXIT_EXCLUDE_SYMBOLS = ["GOLDBEES.NS", "HARCR.NS"]
