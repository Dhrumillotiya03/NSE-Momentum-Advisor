"""
sr_daily_logger.py
-------------------
Logs one row per stock per day: Symbol, Date, CMP, S1, S1_prob, S1_n,
R1, R1_prob, R1_n — plus S2/R2 (+prob+n) only when that level's reach
probability is above 50%.

Run this once daily (after 3:30pm IST, after download_data.py).

Usage:
    python sr_daily_logger.py                      ← uses WATCHLIST below
    python sr_daily_logger.py AARTIIND.NS BEL.NS    ← override watchlist
"""

import os, sys
from datetime import datetime
import pandas as pd

from support_resistance import load_stock, get_all_levels, reach_probability_v2, _load_reach_table

LOG_PATH = "../data/sr_daily_log.csv"

WATCHLIST = [
    "AARTIIND.NS", "BEL.NS", "GOLDBEES.NS", "KALYANKJIL.NS",
    "KFINTECH.NS", "RELIANCE.NS", "VOLTAS.NS", "WIPRO.NS",
    "CONCOR.NS", "COCHINSHIP.NS", "KAYNES.NS", "NATIONALUM.NS", "RECLTD.NS", "SAIL.NS", "TMPV.NS",
]

COLUMNS = [
    "Symbol", "Date", "CMP",
    "S1", "S1_prob", "S1_n",
    "R1", "R1_prob", "R1_n",
    "S2", "S2_prob", "S2_n",
    "R2", "R2_prob", "R2_n",
]


def log_stock(sym):
    df = load_stock(sym)
    if df is None or len(df) < 60:
        print(f"  ⚠️  {sym}: no data, skipped")
        return None

    cur        = float(df["Close"].iloc[-1])
    sups, ress = get_all_levels(df, symbol=sym)

    def level_and_prob(levels, i, direction):
        if len(levels) <= i:
            return None, None, None
        p = levels[i][0]
        prob, n = reach_probability_v2(df, p, direction)
        return p, prob, n

    s1, s1_prob, s1_n = level_and_prob(sups, 0, "down")
    r1, r1_prob, r1_n = level_and_prob(ress, 0, "up")

    s2_raw, s2_prob, s2_n = level_and_prob(sups, 1, "down")
    r2_raw, r2_prob, r2_n = level_and_prob(ress, 1, "up")

    # v2 probs are calibrated empirical base rates. Only surface S2/R2 when the
    # odds genuinely beat the average level (base rate ~66%), not just >50%.
    gate = _load_reach_table().get("base_rate", 50)
    s2 = s2_raw if (s2_prob is not None and s2_prob > gate) else None
    r2 = r2_raw if (r2_prob is not None and r2_prob > gate) else None
    if s2 is None: s2_prob, s2_n = None, None
    if r2 is None: r2_prob, r2_n = None, None

    return {
        "Symbol":  sym.replace(".NS", ""),
        "Date":    datetime.now().strftime("%Y-%m-%d"),
        "CMP":     round(cur, 2),
        "S1":      s1, "S1_prob": s1_prob, "S1_n": s1_n,
        "R1":      r1, "R1_prob": r1_prob, "R1_n": r1_n,
        "S2":      s2, "S2_prob": s2_prob, "S2_n": s2_n,
        "R2":      r2, "R2_prob": r2_prob, "R2_n": r2_n,
    }


def main():
    symbols = sys.argv[1:] if len(sys.argv) > 1 else WATCHLIST
    today   = datetime.now().strftime("%Y-%m-%d")

    print(f"\nLogging S/R snapshot for {len(symbols)} stocks — {today}")
    print("─" * 50)

    rows = []
    for sym in symbols:
        sym = sym.strip().upper()
        if not sym.endswith(".NS"):
            sym += ".NS"
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
    print(f"Logged {len(rows)} rows to {LOG_PATH}")
    print(f"Total rows in log: {len(combined)}")


if __name__ == "__main__":
    main()