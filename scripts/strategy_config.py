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
