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

    for sym in tqdm(symbols):
        out_path = f"{OUTPUT_DIR}{sym}.csv"

        # Skip if already downloaded today
        if os.path.exists(out_path):
            modified = os.path.getmtime(out_path)
            age_days = (datetime.now().timestamp() - modified) / 86400
            if age_days < 1:
                continue

        df = download_symbol(sym)
        if df is not None:
            df.to_csv(out_path, index=False)

    print("\n✅ Data saved successfully.")
    print(f"Files saved in: {OUTPUT_DIR}")


if __name__ == "__main__":
    main()
