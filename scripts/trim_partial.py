"""
Drop today's PARTIAL daily candle after an in-market-hours download.

The user powers the machine on at no fixed time. The daily pipeline
therefore must be safe to run at ANY hour (the systemd timer fires its
missed run at boot). But a yfinance daily-interval download during market
hours includes TODAY as a half-formed row (intraday price, partial
volume) — and every downstream consumer (momentum scores, regime,
paper trader, agent-sim, S/R) assumes rows are completed sessions.

This runs right after the download steps: if now is a weekday before
16:00 IST, any CSV whose last row is dated today gets that row removed.
After 16:00 (or on weekends) candles are final and nothing is touched.

Run from scripts/ (wired into run_daily_log.sh):  python trim_partial.py
"""
import glob
import os
from datetime import datetime

import pandas as pd

DIRS = ["../data/price_data/", "../data/etf_data/", "../data/index_data/"]


def last_row_date(path):
    """Cheap last-line Date read without parsing the whole file."""
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - 300))
            tail = f.read().decode(errors="ignore").strip().splitlines()
        if not tail:
            return None
        return tail[-1].split(",")[0]
    except OSError:
        return None


def main():
    now = datetime.now()
    if now.weekday() > 4 or now.hour >= 16:
        print("[trim] after close / weekend — candles are final, nothing to do")
        return

    today = now.strftime("%Y-%m-%d")
    trimmed = 0
    for d in DIRS:
        for path in glob.glob(d + "*.csv"):
            if last_row_date(path) != today:
                continue
            df = pd.read_csv(path, low_memory=False)
            df = df[df["Date"].astype(str).str.slice(0, 10) != today]
            df.to_csv(path, index=False)
            trimmed += 1
    print(f"[trim] market still open — removed today's partial candle from {trimmed} file(s)")


if __name__ == "__main__":
    main()
