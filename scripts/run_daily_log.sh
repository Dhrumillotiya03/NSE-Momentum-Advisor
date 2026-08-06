#!/bin/bash
# Daily download + S/R logging pipeline. Double-click the "Daily SR Log"
# launcher on the Desktop (or run this directly from scripts/).
# Output shows live in the terminal AND appends to ../data/cron_daily_log.log.

cd "$(dirname "$0")" || exit 1

PYTHON=/home/dhrumil/anaconda3/bin/python
LOG=../data/cron_daily_log.log

# Rotate the run log once it passes ~5MB, keeping one previous generation.
# It is append-only across every run and nothing prunes it, so it grows
# without bound; only the recent tail is ever read (to check what a run did).
if [ -f "$LOG" ] && [ "$(stat -c%s "$LOG" 2>/dev/null || echo 0)" -gt 5242880 ]; then
    mv -f "$LOG" "$LOG.1"
fi

# The pipeline is safe to run at ANY hour: the user powers on at no fixed
# time and the systemd timer fires missed runs at boot. A download during
# market hours writes today's PARTIAL candle — trim_partial.py (below,
# right after the downloads) removes it, so downstream steps only ever see
# completed sessions. (Replaced the old skip-during-market-hours guard,
# which silently lost the day for boot-at-10am, shutdown-at-2pm usage.)

{
    echo "===== $(date) ====="

    # PRICE DOWNLOAD MOVED OUT (2026-08-06) — now run_price_update.sh at
    # ~00:30 IST via the stockai-price-update timer, AFTER Kite's Bhavcopy
    # settlement (typically 19:00-20:00+ IST). Running the Kite pull here at
    # 18:15 raced that settlement: historical_data() for the just-closed
    # session wasn't final yet, so ~350/500 symbols got written with a
    # not-yet-settled value that disagreed with Kite's own data the next
    # morning. This step (and everything below) now reads whatever close
    # run_price_update.sh most recently wrote — one calendar day behind the
    # session that just closed, constantly, which nothing downstream needed
    # (the S/R loggers already only ever read the last COMPLETED close, never
    # live — see CLAUDE.md). See memory kite-settlement-lag-2026-08.
    "$PYTHON" data_integrity_check.py || notify-send "stock_ai" "DATA INTEGRITY WARNINGS — check cron_daily_log.log" 2>/dev/null
    "$PYTHON" market_scanner.py eod
    "$PYTHON" sr_daily_logger.py
    "$PYTHON" sr_dynamic_logger.py
    "$PYTHON" exit_engine.py
    "$PYTHON" paper_trader.py
    "$PYTHON" full_advisor.py --log
    "$PYTHON" news_watchdog.py
    "$PYTHON" agent_sim.py
    "$PYTHON" exit_shadow.py
    "$PYTHON" sim_charts.py

    echo "----- done $(date) -----"
} 2>&1 | tee -a "$LOG"

# Keep the window open when launched by double-click
if [ -t 0 ]; then
    read -rp "Done. Press Enter to close..."
fi
