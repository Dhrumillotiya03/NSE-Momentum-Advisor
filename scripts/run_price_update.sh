#!/bin/bash
# Single nightly pipeline: price update + everything that reads price data.
# Merged 2026-08-07 (previously two pipelines: this one at ~00:30 IST for
# prices only, run_daily_log.sh at 18:15 IST for S/R/exit/advisor/paper/sim).
#
# WHY ONE PIPELINE, WHY 00:30 IST. Kite's historical_data() for a session is
# not final at 18:15 IST — official settlement (Bhavcopy) typically processes
# after 19:00-20:00 IST, sometimes later. Running the price pull at 18:15 used
# to race that settlement: the overlap-agreement check (AGREE_TOL) passed
# because the in-flux value was still close enough to the prior close, but
# Kite's OWN historical_data() for that same date returned a materially
# different (now-final) print the next morning — a mass, ~350/500-symbol
# version of the narrower stale-write bug fix_stale_bar.py was built for.
# Running the price pull after midnight avoids that race entirely. Since every
# downstream step (S/R loggers, integrity checks, scanner, exit engine, paper
# trader, advisor, agent-sim) only ever reads the last COMPLETED close and
# never needed same-day prices, there is no reason to run them separately in
# the evening — merging into one pipeline removes the two-schedule ordering
# to reason about, at the cost of everything (incl. paper trading / advisor
# calls / agent-sim) now landing after midnight instead of the evening.
# See memory kite-settlement-lag-2026-08 and the pipeline-merge memory.
#
# Retains run_daily_log.sh's original behavior of being safe to run at ANY
# hour (a download during market hours writes today's PARTIAL candle;
# trim_partial.py removes it, so downstream steps only ever see completed
# sessions) — relevant if the machine was off overnight and this fires late
# at next boot (Persistent=true) instead of exactly at 00:30.

cd "$(dirname "$0")" || exit 1

PYTHON=/home/dhrumil/anaconda3/bin/python
LOG=../data/cron_daily_log.log

# Rotate the run log once it passes ~5MB, keeping one previous generation.
if [ -f "$LOG" ] && [ "$(stat -c%s "$LOG" 2>/dev/null || echo 0)" -gt 5242880 ]; then
    mv -f "$LOG" "$LOG.1"
fi

{
    echo "===== $(date) ====="
    
    # ABORT, don't limp on. Run manually (2026-08-14): kite_auth.py refresh is
    # INTERACTIVE by design — Kite has no programmatic password login, so a
    # human must paste a request_token. If that step fails (no stdin, bad
    # paste, Ctrl-C, expired token), every price call below fails too and the
    # rest of the pipeline would run happily against a FROZEN archive:
    # S/R rows, advisor calls, paper-trader steps all written from stale bars,
    # and each is idempotent per date, so the bad run consumes the date slot a
    # later good run needs. That is exactly what happened on 2026-08-13/14 —
    # a full, normal-looking log while 201 of 500 symbols sat frozen at 08-12.
    # A pipeline whose failure mode is "looks fine" is worse than one that stops.
    if ! "$PYTHON" kite_auth.py refresh; then
        echo "[pipeline] ABORT: Kite token refresh failed — nothing downstream ran."
        echo "[pipeline] Fix: python kite_auth.py refresh   (then re-run this script)"
        notify-send "stock_ai" "Pipeline ABORTED — Kite token refresh failed" 2>/dev/null
        exit 1
    fi
    if ! "$PYTHON" update_prices_kite.py; then
        echo "[pipeline] ABORT: price update failed — nothing downstream ran."
        echo "[pipeline] Downstream steps are idempotent per date; running them"
        echo "[pipeline] on a stale archive would burn today's slot."
        notify-send "stock_ai" "Pipeline ABORTED — price update failed" 2>/dev/null
        exit 1
    fi
    "$PYTHON" trim_partial.py
    "$PYTHON" repair_price_gaps.py --apply
    "$PYTHON" data_integrity_check.py || notify-send "stock_ai" "DATA INTEGRITY WARNINGS — check cron_daily_log.log" 2>/dev/null
    "$PYTHON" market_scanner.py eod
    "$PYTHON" sr_daily_logger.py
    "$PYTHON" sr_dynamic_logger.py
    "$PYTHON" exit_engine.py
    "$PYTHON" paper_trader.py
    "$PYTHON" full_advisor.py --log
    # One-row-per-stock sheet of tradeable levels for the watchlist + held +
    # today's strategy top-N, so the 60-name "what do I trade this at" question
    # doesn't need full_advisor run per symbol. Read-only apart from its own
    # ../data/trade_sheet.csv. ~15s.
    "$PYTHON" trade_sheet.py --csv-only
    "$PYTHON" news_watchdog.py
    "$PYTHON" agent_sim.py
    "$PYTHON" exit_shadow.py
    "$PYTHON" sim_charts.py
    # Live-vs-backtest selection agreement. The gate (gate_report.py) scores
    # the paper book's RETURN; this checks the paper book is running the
    # strategy the backtest validated at all. Without it, a bottom-decile
    # gate month cannot be attributed to market vs code-path drift — and
    # this repo has shipped that drift three times (sector cap, duplicated
    # momentum_score, three inlined inverse-vol copies). Read-only.
    "$PYTHON" divergence_check.py

    echo "----- done $(date) -----"
} 2>&1 | tee -a "$LOG"

# Propagate the pipeline's real status. `exit 1` inside the { ... } | tee block
# above only leaves the SUBSHELL — without this the script would fall through
# and exit 0, reporting success for a run that aborted. PIPESTATUS[0] is the
# brace block's status (tee's own status is not what we care about).
STATUS=${PIPESTATUS[0]}

# Keep the window open when launched by double-click
if [ -t 0 ]; then
    if [ "$STATUS" -ne 0 ]; then
        read -rp "ABORTED (see messages above). Press Enter to close..."
    else
        read -rp "Done. Press Enter to close..."
    fi
fi

exit "$STATUS"
