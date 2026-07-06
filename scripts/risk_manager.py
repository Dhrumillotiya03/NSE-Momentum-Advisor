import os
import pandas as pd
import numpy as np

DATA_DIR = "../data/"
PRICE_DIR = DATA_DIR + "price_data/"

import yaml
with open("../config.yaml") as f:
    cfg = yaml.safe_load(f)
CAPITAL = cfg["capital"]
RISK_PER_TRADE = cfg["risk"]["risk_per_trade"]
MAX_POSITIONS = cfg["portfolio"]["top_n"]


def load_stock(symbol):
    path = PRICE_DIR + f"{symbol}.csv"
    if not os.path.exists(path):
        return None

    df = pd.read_csv(path, parse_dates=["Date"])
    df = df.sort_values("Date")

    df["Close"] = pd.to_numeric(df["Close"], errors="coerce")
    df["High"] = pd.to_numeric(df["High"], errors="coerce")
    df["Low"] = pd.to_numeric(df["Low"], errors="coerce")

    df = df.dropna(subset=["Close", "High", "Low"])

    return df


# --- ATR-like volatility measure ---

def compute_atr(df, period=14):
    high = df["High"]
    low = df["Low"]
    close = df["Close"]

    tr1 = high - low
    tr2 = abs(high - close.shift())
    tr3 = abs(low - close.shift())

    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)

    return tr.rolling(period).mean().iloc[-1]


def position_size(price, atr):
    stop_distance = 2 * atr   # 2 ATR stop

    risk_amount = CAPITAL * RISK_PER_TRADE

    shares = risk_amount / stop_distance
    shares = int(shares)

    position_value = shares * price

    return shares, position_value, price - stop_distance


def main():
    print("\n💰 RISK MANAGER READY")
    print(f"Capital: ₹{CAPITAL}")
    print(f"Risk per trade: {RISK_PER_TRADE*100:.1f}%")
    print(f"Max positions: {MAX_POSITIONS}")

    # Example usage — replace with BUY list later
    example_stocks = ["TCS.NS", "INFY.NS", "RELIANCE.NS"]

    print("\n📊 POSITION SIZING EXAMPLES:\n")

    for sym in example_stocks:
        df = load_stock(sym)
        if df is None:
            continue

        price = df["Close"].iloc[-1]
        atr = compute_atr(df)

        if np.isnan(atr) or atr == 0:
            continue

        shares, value, stop = position_size(price, atr)

        print(f"{sym}")
        print(f"  Price: ₹{price:.2f}")
        print(f"  Shares: {shares}")
        print(f"  Position Value: ₹{value:.2f}")
        print(f"  Stop Loss: ₹{stop:.2f}")
        print()


if __name__ == "__main__":
    main()