import os
import pandas as pd
import numpy as np

DATA_DIR = "../data/"
PRICE_DIR = DATA_DIR + "price_data/"


def load_all_stocks():
    files = [f for f in os.listdir(PRICE_DIR) if f.endswith(".csv")]
    stocks = {}

    for f in files:
        path = PRICE_DIR + f
        df = pd.read_csv(path, parse_dates=["Date"], low_memory=False)
        df = df.sort_values("Date")

        df["Close"] = pd.to_numeric(df["Close"], errors="coerce")
        df = df.dropna(subset=["Close"])

        stocks[f.replace(".csv", "")] = df

    return stocks


def compute_breadth(stocks):

    above_50 = 0
    above_200 = 0
    advancers = 0
    decliners = 0
    total = 0

    for sym, df in stocks.items():

        if len(df) < 210:
            continue

        close = df["Close"]
        price = close.iloc[-1]

        ma50 = close.rolling(50).mean().iloc[-1]
        ma200 = close.rolling(200).mean().iloc[-1]

        r1 = close.iloc[-1] - close.iloc[-2]

        total += 1

        if price > ma50:
            above_50 += 1
        if price > ma200:
            above_200 += 1

        if r1 > 0:
            advancers += 1
        else:
            decliners += 1

    pct_50 = above_50 / total
    pct_200 = above_200 / total
    adv_dec = advancers / max(decliners, 1)

    return pct_50, pct_200, adv_dec


def classify_breadth(p50, p200, adv_dec):

    if p200 > 0.7 and adv_dec > 1.5:
        return "STRONG"
    if p200 < 0.4 and adv_dec < 0.7:
        return "WEAK"
    return "NEUTRAL"


def main():

    stocks = load_all_stocks()

    p50, p200, adv_dec = compute_breadth(stocks)
    state = classify_breadth(p50, p200, adv_dec)

    print("\n📊 MARKET BREADTH")
    print(f"% Above 50DMA: {p50:.2%}")
    print(f"% Above 200DMA: {p200:.2%}")
    print(f"Advance/Decline Ratio: {adv_dec:.2f}")
    print(f"Internal Strength: {state}")


if __name__ == "__main__":
    main()