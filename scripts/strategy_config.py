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

# ---- Regime exposure & breadth ----
# Exposure = fraction of capital deployed; rest held as cash.
REGIME_EXPOSURE = {
    "BULL":     0.95,
    "SIDEWAYS": 0.60,
    "BEAR":     0.30,
    "UNKNOWN":  0.60,
}
# Number of names held per regime.
REGIME_NAMES = {
    "BULL":     10,
    "SIDEWAYS": 6,
    "BEAR":     2,
    "UNKNOWN":  6,
}
MAX_WEIGHT = 0.20               # single-name cap (diversification vs concentration)
BREADTH_BULL_MIN = 0.50         # need >=50% of universe above 200DMA to allow BULL

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
# (2) early "good exit" take-profit, OFF by default — only enable after
# backtest_portfolio.py's WITH/WITHOUT comparison shows it helps, since tight
# intra-hold exits have historically cost Sharpe+CAGR by whipsawing out of
# momentum trends (see CATASTROPHIC_STOP comment above); (3) month-end
# re-qualification gate, always on — sells names that no longer pass
# compute_score's eligibility filter or fell out of the regime's top-N.
EARLY_EXIT_ENABLED = False      # default OFF — do not enable without walk-forward evidence
EARLY_EXIT_MIN_GAIN = 0.08      # only consider early exit if position is up >= 8%
EARLY_EXIT_RSTRENGTH_MIN = 3    # resistance must have >=3 touches (get_levels s/r strength)
EARLY_EXIT_NEAR_RESISTANCE = 0.01   # price must be within 1% of that resistance
# "Low" reach probability (%) to the next resistance. NOTE: reach probability is
# now the empirical reach_probability_v2 (distance x vol base-rate table), whose
# output is bounded ~57-77% with base rate ~66% — it is NOT the old analog scan
# that emitted 0-100%. A "low" break-through probability therefore means "below the
# base rate", not "below 40". 40 is unreachable under v2 (would silently disable
# this rule and make the with-early-exit backtest show zero early exits). 60 selects
# the genuinely weak cells (very-close or high-vol resistances that hit ~57-60%).
# Rebuild the table (sr_build_reachtable.py) and re-check this if base_rate shifts.
EARLY_EXIT_REACH_PROB_MAX = 60

# Non-strategy holdings: flag for manual review, never auto-sell via the
# momentum re-qualification gate (gold ETF, delisted/BE-series, manual entries).
EXIT_EXCLUDE_SUFFIXES = ["-BE.NS", "-BE"]
EXIT_EXCLUDE_SYMBOLS = ["GOLDBEES.NS", "HARCR.NS"]
