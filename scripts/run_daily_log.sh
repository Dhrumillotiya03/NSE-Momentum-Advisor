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

    # PRICE DATA COMES FROM KITE (2026-08-04). The old download_*.py scripts
    # re-pulled the ENTIRE history from yfinance every night (2015-onward),
    # which is why its glitches kept returning: a bar with real Volume but NaN
    # OHLC, or a session dropped outright, was rewritten nightly however many
    # times it had been repaired. update_prices_kite.py appends only bars newer
    # than each file already holds, from the broker's own feed, and never
    # rewrites history — yfinance's adjusted archive stays the historical
    # record (Kite's history is UNADJUSTED, so rewriting it would move every
    # S/R pivot and invalidate the P(touch) tables and backtests).
    #
    # If the Kite token has expired the update aborts and the pipeline
    # continues on existing data rather than silently falling back to the
    # source this replaced. Refresh with: python kite_auth.py refresh
    "$PYTHON" update_prices_kite.py || echo "[pipeline] Kite update failed — continuing on existing data (refresh the token)"
    "$PYTHON" trim_partial.py
    # Repair yfinance gaps from Kite before anything reads the CSVs. yfinance
    # intermittently writes a row with real Volume but NaN OHLC — the file
    # looks current while every consumer silently drops that bar. 42 of 500
    # files were affected on 2026-08-04. Only the recent tail is touched, and
    # only when Kite agrees with the existing series on the overlap.
    # Kept as a backstop: update_prices_kite handles the normal daily append,
    # but this also repairs INTERIOR gaps (a NaN-OHLC row or a session missing
    # behind a newer bar) that predate the switch to Kite.
    "$PYTHON" repair_price_gaps.py --apply
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
