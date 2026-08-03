"""
sr_dynamic_logger.py
--------------------
SEPARATE from sr_daily_logger.py — do not merge the two.

sr_daily_logger.py logs the user's FIXED hardcoded validation panel to
sr_daily_log.csv (consistent panel = clean accuracy measurement, user mandate).

This script logs a DYNAMIC watchlist — current strategy holdings + top-N
momentum names from the F&O-liquidity-gated universe — to its own file,
sr_dynamic_log.csv. Purpose: accumulate forward S/R + reach-probability
observations on the names the strategy actually trades, giving a larger and
deployment-relevant sample for calibrating/improving reach_probability_v2
(sr_monthend_analysis.py can be pointed at this file via --log argument
once enough history accrues).

Run once daily after download_data.py (wired into run_daily_log.sh).
"""
import os, sys
import pandas as pd

from sr_daily_logger import (log_stock, merge_log, COLUMNS,
                             write_today, write_month)
from portfolio_state import load_state
import strategy_config as sc
from core import scan_universe

LOG_PATH = "../data/sr_dynamic_log.csv"
# Distinct filenames from the fixed panel's — the two panels must never share
# a file (this one rotates names by momentum rank).
TODAY_PATH = "../data/sr_dynamic_today.csv"
MONTH_PATH_FMT = "../data/sr_dynamic_month_{ym}.csv"

# Fixed at the BULL book size (not the current regime's) so panel size stays
# stable across regime flips — steadier forward sample.
TOP_N_WATCH = 10


def build_watchlist():
    state = load_state()
    syms = []
    for s in state.get("positions", {}):
        if s in sc.EXIT_EXCLUDE_SYMBOLS:
            continue
        if any(s.endswith(suf) for suf in sc.EXIT_EXCLUDE_SUFFIXES):
            continue
        syms.append(s)

    scores = scan_universe()
    top = sorted(scores, key=lambda s: scores[s]["score"], reverse=True)[:TOP_N_WATCH]
    for s in top:
        if s not in syms:
            syms.append(s)
    return syms


def main():
    symbols = build_watchlist()

    print(f"\n[dynamic] Logging S/R snapshot for {len(symbols)} stocks")
    print("─" * 50)

    rows = []
    for sym in symbols:
        row = log_stock(sym)
        if row:
            rows.append(row)
            print(f"  ✅ {row['Symbol']:<14} {row['Date']}  CMP ₹{row['CMP']}")

    if not rows:
        print("Nothing logged.")
        return

    new_df = pd.DataFrame(rows, columns=COLUMNS)
    combined = merge_log(new_df, LOG_PATH)
    combined.to_csv(LOG_PATH, index=False)

    # Same today/month split as the fixed panel, under DIFFERENT filenames —
    # this panel rotates its names by momentum rank, so its rows must never be
    # mixed with the fixed validation panel's (see memory
    # sr-daily-log-fixed-panel).
    today_path = write_today(new_df, TODAY_PATH)
    month_written = write_month(new_df, MONTH_PATH_FMT)

    print("─" * 50)
    print(f"[dynamic] Logged {len(rows)} rows to {LOG_PATH}")
    print(f"[dynamic] Total rows in log: {len(combined)}")
    print(f"[dynamic] Today snapshot: {today_path} ({len(new_df)} rows, overwritten)")
    for path, n, complete in month_written:
        tag = ("month COMPLETE — AVG rows written"
               if complete else "month in progress — AVG rows pending last Tuesday")
        print(f"[dynamic] Month file   : {path} ({n} daily rows, {tag})")

    # Data-date stamping means a symbol whose price CSV lagged (yfinance drop
    # for that one name, stale cache) silently logs under an OLDER date than
    # its watchlist-mates, with no error — same failure mode caught in
    # sr_daily_logger.py's fixed panel on 2026-07-22/23 (13/15 stocks stuck a
    # day behind, unnoticed for two days). Flag it here too.
    latest = new_df["Date"].max()
    lagging = new_df[new_df["Date"] != latest]
    if not lagging.empty:
        print(f"\n⚠️  [dynamic] {len(lagging)} symbol(s) logged BEHIND today's "
              f"latest ({latest}) — their price data hasn't updated yet:")
        for _, r in lagging.iterrows():
            print(f"      {r['Symbol']:<14} stuck at {r['Date']}")
        print("   Re-run later, or check that symbol's price_data/etf_data CSV.")


if __name__ == "__main__":
    main()
