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
from datetime import datetime
import pandas as pd

from sr_daily_logger import log_stock, COLUMNS
from portfolio_state import load_state
import strategy_config as sc
from core import scan_universe

LOG_PATH = "../data/sr_dynamic_log.csv"

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
    today = datetime.now().strftime("%Y-%m-%d")

    print(f"\n[dynamic] Logging S/R snapshot for {len(symbols)} stocks — {today}")
    print("─" * 50)

    rows = []
    for sym in symbols:
        row = log_stock(sym)
        if row:
            rows.append(row)
            print(f"  ✅ {row['Symbol']:<14} CMP ₹{row['CMP']}")

    if not rows:
        print("Nothing logged.")
        return

    new_df = pd.DataFrame(rows, columns=COLUMNS)

    if os.path.exists(LOG_PATH):
        existing = pd.read_csv(LOG_PATH)
        existing = existing[
            ~((existing["Date"] == today) & (existing["Symbol"].isin(new_df["Symbol"])))
        ]
        combined = pd.concat([existing, new_df], ignore_index=True)
    else:
        combined = new_df

    combined = combined.sort_values(["Symbol", "Date"])
    combined.to_csv(LOG_PATH, index=False)

    print("─" * 50)
    print(f"[dynamic] Logged {len(rows)} rows to {LOG_PATH}")
    print(f"[dynamic] Total rows in log: {len(combined)}")


if __name__ == "__main__":
    main()
