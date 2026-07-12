import os
import yfinance as yf
import pandas as pd

DATA_DIR = "../data/"
OUTPUT_DIR = DATA_DIR + "index_data/"
SYMBOL = "^NSEI"   # NIFTY 50 index

START_DATE = "2010-01-01"


def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    df = yf.download(
        SYMBOL,
        start=START_DATE,
        interval="1d",
        auto_adjust=True,
        progress=False
    )

    if df.empty:
        print("❌ Failed to download index data")
        return

    # newer yfinance returns MultiIndex columns -> to_csv would emit a second
    # ",^NSEI,^NSEI,..." header row whose Date parses as NaT and (sorted last)
    # made exit_engine's is_last_trading_day_of_month() ALWAYS True
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)

    df = df.reset_index()
    df.to_csv(OUTPUT_DIR + "nifty50.csv", index=False)

    print("✅ NIFTY 50 data saved.")


if __name__ == "__main__":
    main()