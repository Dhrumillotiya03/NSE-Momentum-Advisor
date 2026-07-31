"""
sr_daily_logger.py
-------------------
Logs one row per stock per day: Symbol, Date, CMP, S1, S1_prob, S1_n,
R1, R1_prob, R1_n — plus S2/R2 (+prob+n) only when that level's reach
probability beats the empirical base rate (~66%) from sr_reach_table.json.

WATCHLIST is a FIXED validation panel — deliberately hardcoded so the same
stocks are logged every day and sr_monthend_analysis.py measures model
accuracy on a consistent panel. Do NOT make it dynamic (holdings/top-N);
changing names day-to-day undermines the accuracy analysis.

Run this once daily (after 3:30pm IST, after download_data.py).

Usage:
    python sr_daily_logger.py                      ← uses WATCHLIST below
    python sr_daily_logger.py AARTIIND.NS BEL.NS    ← override watchlist
"""

import os, sys
from datetime import datetime
import pandas as pd

from support_resistance import load_stock, get_all_levels, reach_probability_v2, _load_reach_table
import sr_horizon as H

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
    # Horizon the probabilities refer to: the month's rebalance date (last
    # Tuesday) and the trading days remaining to it. Logged so accuracy can
    # later be scored against the horizon actually quoted, instead of assuming
    # a fixed 21 days. Older rows lack these — readers must treat them as 21d.
    "HorizonEnd", "HorizonDays",
    # Would the base-rate gate have DISPLAYED this S2/R2? The level itself is
    # always logged (it must be, to be measurable); this records the display
    # decision so it stays reconstructible.
    "S2_shown", "R2_shown",
]


def drop_partial_candle(df):
    """Consumer-side twin of trim_partial.py: if the last row is TODAY and the
    session hasn't closed (weekday before 16:00 IST), it is a partial candle —
    drop it. trim_partial cleans the CSVs in the pipeline, but other processes
    (parallel research scripts, ad-hoc downloads) can re-write partial rows
    into price_data at any time, so the logger must not trust file state."""
    now = datetime.now()
    if now.weekday() <= 4 and now.hour < 16 and \
            df.index[-1].date() == now.date():
        return df.iloc[:-1]
    return df


def log_stock(sym):
    df = load_stock(sym)
    if df is None or len(df) < 60:
        print(f"  ⚠️  {sym}: no data, skipped")
        return None
    df = drop_partial_candle(df)
    if len(df) < 60:
        print(f"  ⚠️  {sym}: no completed data, skipped")
        return None

    cur        = float(df["Close"].iloc[-1])
    sups, ress = get_all_levels(df, symbol=sym)

    # Horizon = from this snapshot's DATA date to the month's rebalance date
    # (last Tuesday). Probabilities are quoted for that window, not a fixed 21d.
    data_date = df.index[-1]
    h_end = H.horizon_end(data_date)
    h_cal = H.project_calendar_forward(H.load_trading_calendar(), h_end)
    h_days = H.trading_days_until(data_date, h_end, h_cal)

    def level_and_prob(levels, i, direction):
        if len(levels) <= i:
            return None, None, None
        p = levels[i][0]
        prob, n = reach_probability_v2(df, p, direction, h_days)
        return p, prob, n

    s1, s1_prob, s1_n = level_and_prob(sups, 0, "down")
    r1, r1_prob, r1_n = level_and_prob(ress, 0, "up")

    s2_raw, s2_prob, s2_n = level_and_prob(sups, 1, "down")
    r2_raw, r2_prob, r2_n = level_and_prob(ress, 1, "up")

    # S2/R2 are ALWAYS logged (2026-07-31). They used to be suppressed unless
    # their probability beat the base rate, but this file is the MEASUREMENT
    # record: a level that is never written can never be scored, so the gate
    # was destroying exactly the low-probability observations needed to
    # calibrate the low end — and it biased the logged sample toward levels the
    # model already liked. Filtering is a DISPLAY concern (analyse_table), not
    # a logging one. `S2_shown`/`R2_shown` preserve what the gate would have
    # decided, so display behaviour stays reconstructible from the log.
    gate = _load_reach_table().get("base_rate", 50)
    gate = H.scale_probability_to_horizon(gate, h_days) or gate
    s2, r2 = s2_raw, r2_raw
    s2_shown = bool(s2_prob is not None and s2_prob > gate)
    r2_shown = bool(r2_prob is not None and r2_prob > gate)

    return {
        "Symbol":  sym.replace(".NS", ""),
        # Stamp the DATA date (last completed candle), not the wall-clock date:
        # the pipeline may run at any hour (boot-time catch-up), and after
        # trim_partial a mid-market run's data still ends at yesterday's close.
        # Wall-clock stamping logged that same snapshot under a second date,
        # double-counting it and shifting its forward window in
        # sr_monthend_analysis. Data-date stamping makes runs idempotent.
        "Date":    df.index[-1].strftime("%Y-%m-%d"),
        "CMP":     round(cur, 2),
        "S1":      s1, "S1_prob": s1_prob, "S1_n": s1_n,
        "R1":      r1, "R1_prob": r1_prob, "R1_n": r1_n,
        "S2":      s2, "S2_prob": s2_prob, "S2_n": s2_n,
        "R2":      r2, "R2_prob": r2_prob, "R2_n": r2_n,
        "HorizonEnd":  h_end.strftime("%Y-%m-%d"),
        "HorizonDays": h_days,
        "S2_shown":    s2_shown,
        "R2_shown":    r2_shown,
    }


def merge_log(new_df, log_path):
    """Replace existing rows matching new_df's (Date, Symbol) pairs, keep the rest.
    Rows carry per-symbol DATA dates, so dedupe must be pair-wise, not run-date."""
    if os.path.exists(log_path):
        existing = pd.read_csv(log_path)
        pairs = set(zip(new_df["Date"], new_df["Symbol"]))
        keep = [(d, s) not in pairs
                for d, s in zip(existing["Date"], existing["Symbol"])]
        combined = pd.concat([existing[keep], new_df], ignore_index=True)
    else:
        combined = new_df
    return combined.sort_values(["Symbol", "Date"])


def main():
    symbols = sys.argv[1:] if len(sys.argv) > 1 else WATCHLIST

    print(f"\nLogging S/R snapshot for {len(symbols)} stocks")
    print("─" * 50)

    rows = []
    for sym in symbols:
        sym = sym.strip().upper()
        if not sym.endswith(".NS"):
            sym += ".NS"
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

    print("─" * 50)
    print(f"Logged {len(rows)} rows to {LOG_PATH}")
    print(f"Total rows in log: {len(combined)}")

    # Data-date stamping means a symbol whose price CSV lagged (missed run,
    # yfinance drop for that one name) silently logs under an OLDER date than
    # its panel-mates, with no error — that's how the 2026-07-22 gap happened
    # (13/15 stocks stayed on 07-21 for a day, unnoticed until a manual check).
    # Flag it immediately instead: warn on any row dated behind today's max.
    latest = new_df["Date"].max()
    lagging = new_df[new_df["Date"] != latest]
    if not lagging.empty:
        print(f"\n⚠️  {len(lagging)} symbol(s) logged BEHIND today's latest ({latest}) "
              f"— their price data hasn't updated yet:")
        for _, r in lagging.iterrows():
            print(f"      {r['Symbol']:<14} stuck at {r['Date']}")
        print("   Re-run later, or check that symbol's price_data/etf_data CSV.")


if __name__ == "__main__":
    main()