"""
download_etf.py
---------------
Downloads daily OHLCV for ETFs on the S/R watchlist (currently GOLDBEES)
into ../data/etf_data/ — deliberately SEPARATE from price_data/, which is
globbed as the trading universe by core.market_breadth_pct and
core.liquid_universe. A high-turnover ETF placed in price_data/ would enter
the F&O-liquid top-200 and could get bought by the momentum strategy.

Run daily after 3:30pm IST (wired into run_daily_log.sh before the loggers).
"""
import os
import yfinance as yf
import pandas as pd

ETF_DIR = "../data/etf_data/"

ETF_SYMBOLS = ["GOLDBEES.NS"]

START_DATE = "2015-01-01"


def main():
    os.makedirs(ETF_DIR, exist_ok=True)
    for sym in ETF_SYMBOLS:
        df = yf.download(sym, start=START_DATE, interval="1d",
                         auto_adjust=True, progress=False)
        if df.empty:
            print(f"⚠️ No data for {sym}")
            continue
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        df = df.reset_index()
        df["Symbol"] = sym
        out_path = f"{ETF_DIR}{sym}.csv"
        df.to_csv(out_path, index=False)
        print(f"✅ {sym}: {len(df)} rows -> {out_path}")


if __name__ == "__main__":
    main()
