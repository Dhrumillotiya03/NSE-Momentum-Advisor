#!/bin/bash
# sr_monthly_review.sh — the once-a-month S/R review.
#
# Nothing here runs daily: run_price_update.sh already collects everything.
# This script only READS what a month of that collection produced, and is safe
# to re-run any number of times — it writes nothing to the books, the logs, or
# the tables.
#
# Run it after the month's last Tuesday (the rebalance date), from scripts/:
#     ./sr_monthly_review.sh              # reviews the current calendar month
#     ./sr_monthly_review.sh 2026-08      # or name the month explicitly
#
# SCOPED TO ONE MONTH (2026-08-31). It used to analyse the WHOLE log at once,
# which silently mixes months that were logged under different code: July 2026
# predates min-separation, so its levels sat a median 1.9-2.3% from spot and
# were near-guaranteed to be touched. Pooling it with August dragged every
# figure toward a regime that no longer exists. One month per review, and each
# month's rows share a single horizon end (its last Tuesday), which is what
# makes the cohort coherent in the first place.
#
# Rebuilding the P(touch) tables is deliberately NOT here — see the note at
# the end.

cd "$(dirname "$0")" || exit 1
PYTHON=/home/dhrumil/anaconda3/bin/python
MONTH="${1:-$(date '+%Y-%m')}"

echo "################################################################"
echo "#  S/R MONTHLY REVIEW — $MONTH   (run $(date '+%Y-%m-%d'))"
echo "################################################################"

# 1. FIXED PANEL at the production horizon. --to-month-end scores each snapshot
#    against its OWN logged HorizonDays (distance to that month's last
#    Tuesday), i.e. exactly the question the subsystem answers in production.
#    --exclude-day0 drops levels already inside the touch band when logged:
#    guaranteed hits with zero predictive content, worth ~7pp of inflation.
echo
echo "=== 1. FIXED PANEL — production horizon (to month-end) ==="
"$PYTHON" sr_monthend_analysis.py --month "$MONTH" --exclude-day0 \
    --exclude-contaminated

# 2. Same panel at a fixed 21d, comparable to the backtested ~65-68% baseline.
#    Only meaningful once snapshots have 21 trading bars after them; before
#    that it correctly reports nothing resolved rather than a fake number.
echo
echo "=== 2. FIXED PANEL — fixed 21d (comparable to the backtest baseline) ==="
echo "    NOTE: needs 21 sessions AFTER each log date, so in the first weeks of"
echo "    the following month this resolves few or no rows of THIS month and"
echo "    falls back to older ones. Check the 'dates' column before reading it."
"$PYTHON" sr_monthend_analysis.py --month "$MONTH" --window 21 --exclude-day0 \
    --exclude-contaminated

# 3. Shorter horizon: resolves sooner, so it is the first honest read on a
#    young log. NOT comparable to the 21d figure (shorter window => strictly
#    fewer touches => reads lower by construction).
echo
echo "=== 3. FIXED PANEL — 10d (resolves earliest; NOT comparable to 21d) ==="
"$PYTHON" sr_monthend_analysis.py --month "$MONTH" --window 10 --exclude-day0 \
    --exclude-contaminated

# 4. Dynamic panel: deployment-relevant names (holdings + top momentum), but
#    NOT comparable to the fixed panel — its supports sit much further from
#    price, so its hit rate is lower for reasons of composition, not accuracy.
echo
echo "=== 4. DYNAMIC PANEL — deployment-relevant (composition differs!) ==="
"$PYTHON" sr_monthend_analysis.py --log ../data/sr_dynamic_log.csv \
    --month "$MONTH" --exclude-day0

echo
echo "################################################################"
echo "#  READING THIS"
echo "################################################################"
cat <<'NOTES'

  Section 0 of each block is DATA QUALITY — read it FIRST. A bad hit rate
  caused by partial days, duplicate rows or a frozen price series is a
  PIPELINE bug, not a miscalibrated model, and the two need opposite fixes.

  "still open" windows are EXCLUDED, never counted as misses. A window that
  has not run its full length cannot produce a miss, and scoring it as one
  understates accuracy. If most windows are still open the resolved set skews
  toward earlier log dates — the script says so explicitly.

  READ SECTION 1c BEFORE SECTION 1. The touch table keys on distance x
  TRAILING 252-day volatility, so it cannot see a volatility regime that
  changes inside the month. August 2026 realised 5.4% annualised on the index
  -- the 0.3rd percentile of every 16-session window since 2010 -- and the
  model duly read ~10pp hot. Conditioned on the regime that actually
  materialised it was calibrated (+5.1pp) wherever vol came in at or above
  what was assumed. A P(touch) table fitted on ~11y of average tape SHOULD
  overpredict in the calmest month of that history: that is an unconditional
  model meeting an unusual month, not decay. Only a gap that persists in the
  "realised >= assumed" row is evidence against the model.

  A LARGE CALIBRATION GAP WITH A HIGH AUC IS A LEVEL PROBLEM, NOT A BROKEN
  MODEL, and it is not a licence to flat-shift the probabilities. Doing that
  fits the month you measured and is wrong in the next one.

  Confidence intervals resample DATES, not rows. Sixty-one symbols logged on
  one session share the market's move; treating them as independent
  understates the interval by roughly sqrt(panel size) -- enough to make an
  ordinary month look like a significant deviation.

  DO NOT retune pivot windows, confluence weights or bucket edges off one
  month. That was tested and is a documented dead end absent a new hypothesis.
  One month of forward data does not clear that bar.

  Rebuilding the P(touch) tables (sr_build_touchtable.py) is intentionally NOT
  part of this review. The tables are built from ~10 years of history, so one
  extra month barely moves them, and rebuilding on the same cadence you
  MEASURE on would let the thing being tested change underneath the test.
  Rebuild only when price history is materially extended -- and re-check the
  monotonicity invariant afterward (P(touch) must be non-decreasing in
  horizon; a nearest-table bug broke this once).

NOTES
