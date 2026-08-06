#!/bin/bash
# Price-only pipeline step, run separately from run_daily_log.sh (2026-08-06).
#
# WHY SPLIT OUT. Kite's historical_data() for a session is not final at 18:15
# IST — the official settlement (Bhavcopy) typically processes after 19:00-20:00
# IST, sometimes later. Running update_prices_kite.py at 18:15 caught many
# symbols' bars mid-settlement: the overlap-agreement check (AGREE_TOL) passed
# because the in-flux value was still close enough to the prior close, but
# Kite's OWN historical_data() for that same date returned a materially
# different (now-final) print the next morning — a mass, ~350/500-symbol
# version of the narrower stale-write bug fix_stale_bar.py was built for.
# Running this after midnight instead means Bhavcopy has settled by the time
# this pulls "yesterday's" bar, so it should be written correctly the first
# time — no post-hoc correction needed.
#
# run_daily_log.sh (still 18:15 IST) keeps everything downstream (S/R logging,
# integrity checks, scanner, agent_sim, paper trader, news watchdog) — they
# now read whatever close this script most recently wrote, which lags the
# session that just closed by one calendar day, constantly. Nothing downstream
# needed same-day prices (the S/R subsystem already documents that its loggers
# read the last COMPLETED close, never live) — see CLAUDE.md.

cd "$(dirname "$0")" || exit 1

PYTHON=/home/dhrumil/anaconda3/bin/python
LOG=../data/cron_price_update_log.log

if [ -f "$LOG" ] && [ "$(stat -c%s "$LOG" 2>/dev/null || echo 0)" -gt 5242880 ]; then
    mv -f "$LOG" "$LOG.1"
fi

{
    echo "===== $(date) ====="
    "$PYTHON" update_prices_kite.py || echo "[price-update] Kite update failed — refresh the token (python kite_auth.py refresh)"
    "$PYTHON" trim_partial.py
    "$PYTHON" repair_price_gaps.py --apply
    echo "----- done $(date) -----"
} 2>&1 | tee -a "$LOG"
