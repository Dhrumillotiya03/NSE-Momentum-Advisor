import os
import yfinance as yf
import pandas as pd
from tqdm import tqdm
from datetime import datetime

DATA_DIR = "../data/"
SYMBOL_FILE_200 = DATA_DIR + "nifty200.csv"
SYMBOL_FILE_500 = DATA_DIR + "nifty500.csv"
OUTPUT_DIR = DATA_DIR + "price_data/"

START_DATE = "2015-01-01"
END_DATE = None  # None = today


def load_symbols(filepath):
    with open(filepath, "r") as f:
        symbols = [line.strip() for line in f if line.strip()]
    return symbols


def download_symbol(symbol):
    df = yf.download(
        symbol,
        start=START_DATE,
        end=END_DATE,
        interval="1d",
        auto_adjust=True,
        progress=False
    )

    if df.empty:
        print(f"⚠️ No data for {symbol}")
        return None

    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)

    df = df.reset_index()
    df["Symbol"] = symbol
    return df


def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    # Load full universe — same as backtest
    def load_csv_syms(path):
    	with open(path, "r") as f:
        	symbols = [line.strip() for line in f if line.strip()]
    	return [s + ".NS" if not s.endswith(".NS") else s for s in symbols]

    nifty200 = load_csv_syms(SYMBOL_FILE_200)
    nifty500 = load_csv_syms(SYMBOL_FILE_500)
    symbols  = sorted(set(nifty200 + nifty500))

    print(f"Downloading data for {len(symbols)} stocks...")

    today = datetime.now().date()
    stale_count = 0

    for sym in tqdm(symbols):
        out_path = f"{OUTPUT_DIR}{sym}.csv"

        # Skip only if the file's ACTUAL LAST CANDLE is already today's trading
        # day — mtime is not a reliable proxy: a file fetched at 00:58 (an
        # earlier catch-up run, a parallel session) looks "downloaded today"
        # under a rolling 24h mtime check for the next 24h, even on an
        # EVENING run that should refresh it with today's close. This bit 4
        # symbols on 2026-07-28 (AARTIIND, ADANIENSOL, APOLLOHOSP + 1 more),
        # silently skipped and left a day stale until manually caught via the
        # S/R loggers' lag-detection warning.
        if os.path.exists(out_path):
            try:
                with open(out_path, "rb") as f:
                    f.seek(0, os.SEEK_END)
                    size = f.tell()
                    f.seek(max(0, size - 200))
                    last_line = f.read().decode(errors="ignore").strip().splitlines()[-1]
                last_date = last_line.split(",")[0]
                if pd.Timestamp(last_date).date() >= today:
                    continue
                stale_count += 1
            except Exception:
                pass  # unreadable/malformed — fall through and re-fetch

        df = download_symbol(sym)
        if df is not None:
            df.to_csv(out_path, index=False)

    if stale_count:
        print(f"  ({stale_count} file(s) had a recent mtime but stale content — re-fetched anyway)")

    print("\n✅ Data saved successfully.")
    print(f"Files saved in: {OUTPUT_DIR}")


if __name__ == "__main__":
    main()
