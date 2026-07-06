import pandas as pd
import numpy as np

DATA_PATH = "../data/index_data/nifty50.csv"


def load_data():
    df = pd.read_csv(DATA_PATH, parse_dates=["Date"])

    df = df.sort_values("Date")

    # Convert price columns to numeric (robust fix)
    numeric_cols = ["Open", "High", "Low", "Close", "Volume"]

    for col in numeric_cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    # Drop rows with missing Close prices
    df = df.dropna(subset=["Close"])

    return df


def compute_features(df):
    close = df["Close"]

    df["MA50"] = close.rolling(50).mean()
    df["MA200"] = close.rolling(200).mean()

    df["Return20"] = close.pct_change(20)
    df["Volatility"] = close.pct_change().rolling(20).std()

    rolling_max = close.rolling(200).max()
    df["Drawdown"] = (close - rolling_max) / rolling_max

    return df


def classify_regime(df):
    latest = df.iloc[-1]

    price = latest["Close"]
    ma50 = latest["MA50"]
    ma200 = latest["MA200"]
    vol = latest["Volatility"]
    dd = latest["Drawdown"]

    if price > ma50 > ma200 and dd > -0.1:
        return "BULL"

    if price < ma200 and dd < -0.2:
        return "BEAR"

    if vol > df["Volatility"].quantile(0.9):
        return "HIGH_RISK"

    return "SIDEWAYS"


def main():
    df = load_data()
    df = compute_features(df)

    regime = classify_regime(df)

    print("\n📊 CURRENT MARKET REGIME:", regime)


if __name__ == "__main__":
    main()
