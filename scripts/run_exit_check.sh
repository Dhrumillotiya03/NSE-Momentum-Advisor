#!/bin/bash
# Intra-day exit watcher: runs exit_engine.py (catastrophic -18% stop check,
# month-end liquidation on the last trading day) and raises a desktop
# notification if any SELL signal fires. Installed in cron weekdays 14:40 IST
# so a stop breach is caught with live-ish quotes while the market is open
# (NSE closes 15:30; quotes are ~15-min delayed).
# NOTE: cron silently skips if the machine is off/asleep at 14:40 — the
# evening run_daily_log.sh pipeline re-checks on fresh closes as a backstop.

cd "$(dirname "$0")" || exit 1

PYTHON=/home/dhrumil/anaconda3/bin/python
LOG=../data/exit_check.log

OUT=$( { echo "===== $(date) ====="; "$PYTHON" exit_engine.py; } 2>&1 )
echo "$OUT" >> "$LOG"

if echo "$OUT" | grep -q "SELL SIGNALS"; then
    export DISPLAY=${DISPLAY:-:0}
    export DBUS_SESSION_BUS_ADDRESS=${DBUS_SESSION_BUS_ADDRESS:-unix:path=/run/user/$(id -u)/bus}
    notify-send -u critical "stock_ai: SELL SIGNAL" \
        "exit_engine flagged an exit — see stock_ai/data/exit_check.log and execute on Zerodha, then record_fill.py" \
        2>/dev/null
fi
