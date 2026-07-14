#!/bin/bash
# Daily download + S/R logging pipeline. Double-click the "Daily SR Log"
# launcher on the Desktop (or run this directly from scripts/).
# Output shows live in the terminal AND appends to ../data/cron_daily_log.log.

cd "$(dirname "$0")" || exit 1

PYTHON=/home/dhrumil/anaconda3/bin/python
LOG=../data/cron_daily_log.log

# Market-hours guard: this pipeline downloads DAILY candles — running while
# NSE is open (Mon-Fri ~09:00-15:40 IST) would write a partial candle for
# today into the CSVs and let downstream steps trade on it. The systemd
# timer is Persistent=true (fires at next boot if the machine was off at
# 18:15), so a morning boot would otherwise trigger exactly that.
dow=$(date +%u); hhmm=$(date +%H%M)
if [ "$dow" -le 5 ] && [ "$hhmm" -ge 0900 ] && [ "$hhmm" -le 1540 ]; then
    echo "[guard] NSE market hours — skipping pipeline (will run at 18:15 timer)"
    exit 0
fi

{
    echo "===== $(date) ====="

    "$PYTHON" download_index.py
    "$PYTHON" download_data.py
    "$PYTHON" download_etf.py
    "$PYTHON" data_integrity_check.py || notify-send "stock_ai" "DATA INTEGRITY WARNINGS — check cron_daily_log.log" 2>/dev/null
    "$PYTHON" sr_daily_logger.py
    "$PYTHON" sr_dynamic_logger.py
    "$PYTHON" exit_engine.py
    "$PYTHON" paper_trader.py
    "$PYTHON" news_watchdog.py
    "$PYTHON" agent_sim.py
    "$PYTHON" sim_charts.py

    echo "----- done $(date) -----"
} 2>&1 | tee -a "$LOG"

# Keep the window open when launched by double-click
if [ -t 0 ]; then
    read -rp "Done. Press Enter to close..."
fi
