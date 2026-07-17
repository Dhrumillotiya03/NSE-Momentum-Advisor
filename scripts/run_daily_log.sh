#!/bin/bash
# Daily download + S/R logging pipeline. Double-click the "Daily SR Log"
# launcher on the Desktop (or run this directly from scripts/).
# Output shows live in the terminal AND appends to ../data/cron_daily_log.log.

cd "$(dirname "$0")" || exit 1

PYTHON=/home/dhrumil/anaconda3/bin/python
LOG=../data/cron_daily_log.log

# The pipeline is safe to run at ANY hour: the user powers on at no fixed
# time and the systemd timer fires missed runs at boot. A download during
# market hours writes today's PARTIAL candle — trim_partial.py (below,
# right after the downloads) removes it, so downstream steps only ever see
# completed sessions. (Replaced the old skip-during-market-hours guard,
# which silently lost the day for boot-at-10am, shutdown-at-2pm usage.)

{
    echo "===== $(date) ====="

    "$PYTHON" download_index.py
    "$PYTHON" download_data.py
    "$PYTHON" download_etf.py
    "$PYTHON" trim_partial.py
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
