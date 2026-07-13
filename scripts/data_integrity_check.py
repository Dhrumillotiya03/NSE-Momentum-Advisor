"""
Nightly data-integrity guard — runs after the download steps in
run_daily_log.sh and prints WARN lines for anything that has historically
corrupted this project's numbers. Data bugs have bitten three times:

  1. yfinance MultiIndex second header row in nifty50.csv → Date parsed NaT
     → is_last_trading_day_of_month() true every day → false month-end
     liquidation signals (July 2026).
  2. SUNDARMFIN.NS.csv truncated last row (missing Date) → literal string in
     the index → sort_index TypeError downstream.
  3. GOLDBEES.NS.csv rows at exactly 1/100th price (failed split
     adjustment) → fake -99%/+10400% returns → one walk-forward window
     showed +330% annualized before it was caught.

Checks per CSV (price_data/, etf_data/, index_data/):
  - unparseable Date rows (catches 1 and 2)
  - decimal-shift glitches: Close deviating >3x from the centered 11-day
    rolling median (catches 3)
  - non-positive Close values
  - stale series: last date > STALE_DAYS calendar days old (only warned for
    files that were recently fresh — permanently dead names are expected in
    a survivorship-aware panel and would just be noise)
  - duplicate dates

Exit code 1 if any WARN was emitted (so a wrapper/cron can notice), else 0.
Run from scripts/:  python data_integrity_check.py
"""
import glob
import os
import sys

import pandas as pd

STALE_DAYS = 7        # warn if a previously-fresh series stops updating
RECENT_DAYS = 30      # "previously fresh" = last date within this many days


def check_file(path, today):
    warns = []
    name = os.path.basename(path)
    try:
        df = pd.read_csv(path, low_memory=False)
    except Exception as e:
        return [f"{name}: unreadable ({e})"]

    if "Date" not in df.columns or "Close" not in df.columns:
        return [f"{name}: missing Date/Close column"]

    dates = pd.to_datetime(df["Date"], errors="coerce")
    n_bad_dates = int(dates.isna().sum())
    if n_bad_dates:
        warns.append(f"{name}: {n_bad_dates} row(s) with unparseable Date "
                     f"(truncated write or duplicate header row)")

    close = pd.to_numeric(df["Close"], errors="coerce")
    valid = dates.notna() & close.notna()
    dates, close = dates[valid], close[valid]
    if len(close) < 30:
        return warns  # too short to sanity-check further

    if (close <= 0).any():
        warns.append(f"{name}: {int((close <= 0).sum())} non-positive Close value(s)")

    med = close.rolling(11, center=True, min_periods=3).median()
    ratio = close / med
    glitch = (ratio >= 3) | (ratio <= 1 / 3)
    if glitch.any():
        days = [d.date() for d in dates[glitch]]
        warns.append(f"{name}: {int(glitch.sum())} decimal-shift glitch row(s) at {days} "
                     f"(price >3x off its 11d median — GOLDBEES-class corruption)")

    if dates.duplicated().any():
        warns.append(f"{name}: {int(dates.duplicated().sum())} duplicate date row(s)")

    last = dates.max()
    age = (today - last).days
    if STALE_DAYS < age <= RECENT_DAYS:
        warns.append(f"{name}: stale — last date {last.date()} ({age}d old, was recently fresh)")

    return warns


def main():
    today = pd.Timestamp.today().normalize()
    paths = (sorted(glob.glob("../data/price_data/*.csv"))
             + sorted(glob.glob("../data/etf_data/*.csv"))
             + sorted(glob.glob("../data/index_data/*.csv")))

    all_warns = []
    for p in paths:
        all_warns.extend(check_file(p, today))

    if all_warns:
        print(f"DATA INTEGRITY: {len(all_warns)} warning(s) across {len(paths)} files")
        for w in all_warns:
            print(f"  WARN {w}")
        sys.exit(1)
    print(f"DATA INTEGRITY: OK — {len(paths)} files clean")


if __name__ == "__main__":
    main()
