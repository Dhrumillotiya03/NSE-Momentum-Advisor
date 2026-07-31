#!/bin/bash
# sr_monthly_review.sh — the once-a-month S/R review.
#
# Nothing here runs daily: run_daily_log.sh (systemd timer stockai-daily,
# weekdays 18:15 IST) already collects everything. This script only READS what
# a month of that collection produced, and is safe to re-run any number of
# times — it writes nothing to the books, the logs, or the tables.
#
# Run it after the month's last Tuesday (the rebalance date), from scripts/:
#     ./sr_monthly_review.sh
#
# Rebuilding the P(touch) tables is deliberately NOT here — see the note at
# the end.

cd "$(dirname "$0")" || exit 1
PYTHON=/home/dhrumil/anaconda3/bin/python

echo "################################################################"
echo "#  S/R MONTHLY REVIEW — $(date '+%Y-%m-%d')"
echo "################################################################"

# 1. FIXED PANEL at the production horizon. --to-month-end scores each snapshot
#    against its OWN logged HorizonDays (distance to that month's last
#    Tuesday), i.e. exactly the question the subsystem answers in production.
#    --exclude-day0 drops levels already inside the touch band when logged:
#    guaranteed hits with zero predictive content, worth ~7pp of inflation.
echo
echo "=== 1. FIXED PANEL — production horizon (to month-end) ==="
"$PYTHON" sr_monthend_analysis.py --to-month-end --exclude-day0

# 2. Same panel at a fixed 21d, comparable to the backtested ~65-68% baseline.
#    Only meaningful once snapshots have 21 trading bars after them; before
#    that it correctly reports nothing resolved rather than a fake number.
echo
echo "=== 2. FIXED PANEL — fixed 21d (comparable to the backtest baseline) ==="
"$PYTHON" sr_monthend_analysis.py --window 21 --exclude-day0

# 3. Shorter horizon: resolves sooner, so it is the first honest read on a
#    young log. NOT comparable to the 21d figure (shorter window => strictly
#    fewer touches => reads lower by construction).
echo
echo "=== 3. FIXED PANEL — 10d (resolves earliest; NOT comparable to 21d) ==="
"$PYTHON" sr_monthend_analysis.py --window 10 --exclude-day0

# 4. Dynamic panel: deployment-relevant names (holdings + top momentum), but
#    NOT comparable to the fixed panel — its supports sit much further from
#    price, so its hit rate is lower for reasons of composition, not accuracy.
echo
echo "=== 4. DYNAMIC PANEL — deployment-relevant (composition differs!) ==="
"$PYTHON" sr_monthend_analysis.py --log ../data/sr_dynamic_log.csv \
    --to-month-end --exclude-day0

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

  Calibration (section 3) scores probabilities AS LOGGED. Rows written before
  the P(touch) table replaced the old P(bounce|touched) table carry the OLD
  metric, so calibration only becomes readable once a full month has been
  logged under the new table. Mixed-metric buckets are uninterpretable, NOT
  evidence of miscalibration.

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
