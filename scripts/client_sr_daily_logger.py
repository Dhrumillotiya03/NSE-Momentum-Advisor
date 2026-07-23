"""
client_sr_daily_logger.py
--------------------------
CLIENT-BOUND FILE — not used by this codebase's own pipeline. This is the
version to copy over to the client's machine, replacing their
sr_daily_logger.py. Do not wire this into run_daily_log.sh or anything else
here; the production panel lives in sr_daily_logger.py.

Built from the client's own pasted copy of sr_daily_logger.py (their
watchlist edits preserved: AARTIIND dropped, 12 symbols added) brought up to
the current logger interface (data-date stamping, merge_log, partial-candle
guard) and paired with run_client_sr_log.py.

"NIFTY 50.NS" in their original list was not a valid yfinance ticker (it
would have failed every download and logged nothing) — replaced with
"NIFTY50", which support_resistance.load_stock resolves from
data/index_data/nifty50.csv via the INDEX_FILES map. That map, plus
ETF_DIR routing (GOLDBEES), only exist in the CURRENT support_resistance.py —
their machine is still on the initial-commit version without either, so
support_resistance.py must be copied over too. See the handoff notes
in run_client_sr_log.py's docstring / the accompanying chat summary for the
full file list.

Logs one row per stock per day: Symbol, Date, CMP, S1, S1_prob, S1_n,
R1, R1_prob, R1_n — plus S2/R2 (+prob+n) only when that level's reach
probability beats the empirical base rate (~66%) from sr_reach_table.json.

WATCHLIST is a FIXED validation panel — deliberately hardcoded so the same
stocks are logged every day and sr_monthend_analysis.py measures model
accuracy on a consistent panel. Do NOT make it dynamic (holdings/top-N);
changing names day-to-day undermines the accuracy analysis. Adding/removing
individual symbols by hand is fine — that's how the client got here.

Index entries (e.g. NIFTY50) go in WITHOUT a ".NS" suffix — they're not
yfinance-downloadable tickers, support_resistance.load_stock reads them
straight from data/index_data/ (see that file's INDEX_FILES map).

Run this once daily (after 3:30pm IST) via run_client_sr_log.py — do not
run this file directly on a machine that also needs the .xlsx output.

Usage:
    python client_sr_daily_logger.py                      ← uses WATCHLIST below
    python client_sr_daily_logger.py AARTIIND.NS BEL.NS    ← override watchlist
"""

import os, sys
from datetime import datetime
import pandas as pd

from support_resistance import load_stock, get_all_levels, reach_probability_v2, _load_reach_table, INDEX_FILES

LOG_PATH = "../data/sr_daily_log.csv"

WATCHLIST = [
    "BEL.NS", "KALYANKJIL.NS",
    "KFINTECH.NS", "RELIANCE.NS", "VOLTAS.NS", "WIPRO.NS",
    "CONCOR.NS", "COCHINSHIP.NS", "KAYNES.NS", "NATIONALUM.NS", "RECLTD.NS", "SAIL.NS", "TMPV.NS",
    "ETERNAL.NS", "DELHIVERY.NS", "KPITTECH.NS", "BDL.NS", "PNB.NS", "SUZLON.NS",
    "ADANIPOWER.NS", "IREDA.NS", "CDSL.NS", "BSE.NS", "RVNL.NS", "NIFTY50",
]

COLUMNS = [
    "Symbol", "Date", "CMP",
    "S1", "S1_prob", "S1_n",
    "R1", "R1_prob", "R1_n",
    "S2", "S2_prob", "S2_n",
    "R2", "R2_prob", "R2_n",
]


def drop_partial_candle(df):
    """Consumer-side twin of trim_partial.py: if the last row is TODAY and the
    session hasn't closed (weekday before 16:00 IST), it is a partial candle —
    drop it. A download running during market hours can leave a half-formed
    candle in price_data, so the logger must not trust file state blindly."""
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
        # Stamp the DATA date (last completed candle), not the wall-clock date:
        # a run at any hour of day would otherwise double-log the same
        # snapshot under two dates. Data-date stamping makes runs idempotent.
        "Date":    df.index[-1].strftime("%Y-%m-%d"),
        "CMP":     round(cur, 2),
        "S1":      s1, "S1_prob": s1_prob, "S1_n": s1_n,
        "R1":      r1, "R1_prob": r1_prob, "R1_n": r1_n,
        "S2":      s2, "S2_prob": s2_prob, "S2_n": s2_n,
        "R2":      r2, "R2_prob": r2_prob, "R2_n": r2_n,
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
        # Index entries (NIFTY50, INDIAVIX) are not .NS-suffixed tickers.
        if sym not in INDEX_FILES and not sym.endswith(".NS"):
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


if __name__ == "__main__":
    main()
