import os
import pandas as pd
import numpy as np

DATA_DIR = "../data/price_data/"
INDEX_PATH = "../data/index_data/nifty50.csv"

LOOKBACK = 126   # days for signal (~6 months)
HOLD = 21        # holding period (~1 month)
TOP_N = 10       # portfolio size


# ---------- Load Stock ----------

def load_stock(symbol):

    path = DATA_DIR + f"{symbol}.csv"

    if not os.path.exists(path):
        return None

    df = pd.read_csv(path, parse_dates=["Date"])

    df["Close"] = pd.to_numeric(df["Close"], errors="coerce")

    df = df.dropna(subset=["Close"])

    df = df.sort_values("Date")

    return df[["Date", "Close"]]


# ---------- Compute Momentum ----------

def momentum(df, i):
    if i < LOOKBACK:
        return None

    return df["Close"].iloc[i] / df["Close"].iloc[i - LOOKBACK] - 1


# ---------- Load All Stocks ----------

def load_universe():

    stocks = {}

    for f in os.listdir(DATA_DIR):
        if not f.endswith(".csv"):
            continue

        sym = f.replace(".csv", "")

        df = load_stock(sym)

        if df is not None and len(df) > LOOKBACK + HOLD:
            stocks[sym] = df

    return stocks


# ---------- Backtest ----------

def run_backtest(stocks):

    dates = stocks[list(stocks.keys())[0]]["Date"]

    portfolio_returns = []

    for i in range(LOOKBACK, len(dates) - HOLD, HOLD):

        scores = {}

        for sym, df in stocks.items():

            if i >= len(df):
                continue

            m = momentum(df, i)

            if m is not None:
                scores[sym] = m

        if len(scores) < TOP_N:
            continue

        top = sorted(scores, key=scores.get, reverse=True)[:TOP_N]

        returns = []

        for sym in top:
            df = stocks[sym]

            if i + HOLD < len(df):
                r = df["Close"].iloc[i + HOLD] / df["Close"].iloc[i] - 1
                returns.append(r)

        if returns:
            portfolio_returns.append(np.mean(returns))

    return np.array(portfolio_returns)


# ---------- Performance Metrics ----------

def performance(returns):

    cumulative = np.cumprod(1 + returns)

    total_return = cumulative[-1] - 1

    annual_return = (1 + total_return) ** (252 / len(returns)) - 1

    volatility = np.std(returns) * np.sqrt(252)

    sharpe = annual_return / volatility if volatility > 0 else 0

    drawdown = np.max(np.maximum.accumulate(cumulative) - cumulative)

    return total_return, annual_return, sharpe, drawdown


# ---------- Main ----------

def main():

    print("\n==============================")
    print("📊 BACKTEST RESULTS")
    print("==============================")

    stocks = load_universe()

    returns = run_backtest(stocks)

    if len(returns) == 0:
        print("⚠️ Not enough data")
        return

    total, annual, sharpe, dd = performance(returns)

    print(f"\nTotal Return: {total:.2%}")
    print(f"Annual Return: {annual:.2%}")
    print(f"Sharpe Ratio: {sharpe:.2f}")
    print(f"Max Drawdown: {dd:.2%}")


if __name__ == "__main__":
    main()