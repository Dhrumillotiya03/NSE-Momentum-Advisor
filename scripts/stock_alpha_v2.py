import pandas as pd
import numpy as np
import os

PRICE_DIR = "../data/price_data/"
INDEX_PATH = "../data/index_data/nifty50.csv"


# -----------------------------
# Load NIFTY returns
# -----------------------------
def load_index():

    df = pd.read_csv(INDEX_PATH, parse_dates=["Date"], low_memory=False)

    df["Close"] = pd.to_numeric(df["Close"], errors="coerce")

    return df["Close"].pct_change()


# -----------------------------
# Signal calculations
# -----------------------------
def compute_signals(df, index_returns):

    close = pd.to_numeric(df["Close"], errors="coerce")
    volume = pd.to_numeric(df["Volume"], errors="coerce")

    if len(close) < 252:
        return None

    returns = close.pct_change()

    # ---------------- Momentum ----------------

    if len(close) < 252:
        return None

    # Industry-standard: 12-month return, skip last month (avoids short-term reversal)
    mom_12_1 = close.iloc[-21] / close.iloc[-252] - 1
    mom_3m = close.iloc[-1] / close.iloc[-63] - 1

    momentum = 0.7 * mom_12_1 + 0.3 * mom_3m

    # ---------------- Volatility ----------------

    vol = returns.rolling(63).std().iloc[-1]

    if np.isnan(vol) or vol == 0:
        return None

    vol_adj_momentum = momentum / vol

    # Relative strength over 3 months
    stock_3m = close.iloc[-1] / close.iloc[-63] - 1

    market_close = (1 + index_returns).cumprod()

    market_3m = market_close.iloc[-1] / market_close.iloc[-63] - 1

    rel_strength = stock_3m - market_3m

    # ---------------- Volume Accumulation ----------------

    vol_recent = volume.tail(20).mean()
    vol_hist = volume.tail(60).mean()

    if vol_hist == 0:
        volume_signal = 0
    elif vol_hist > 0:
        volume_signal = np.log(vol_recent / vol_hist)
    else:
        volume_signal = 0

    # ---------------- Trend Filter ----------------

    ma50 = close.rolling(50).mean().iloc[-1]
    ma200 = close.rolling(200).mean().iloc[-1]

    trend = 1 if close.iloc[-1] > ma50 else 0

    return {
        "momentum": momentum,
        "vol_adj": vol_adj_momentum,
        "rel_strength": rel_strength,
        "volume": volume_signal,
        "trend": trend
    }


# -----------------------------
# Alpha score
# -----------------------------
def compute_alpha(sig):

    score = (
        0.40 * sig["momentum"] +
        0.25 * sig["vol_adj"] +
        0.20 * sig["rel_strength"] +
        0.10 * sig["volume"] +
        0.05 * sig["trend"]
    )

    return score


# -----------------------------
# Main
# -----------------------------
def main():

    print("\n==============================")
    print("🚀 STOCK ALPHA v4 (ROBUST)")
    print("==============================")

    index_returns = load_index()

    results = []

    for file in os.listdir(PRICE_DIR):

        if not file.endswith(".csv"):
            continue

        symbol = file.replace(".csv", "")

        path = PRICE_DIR + file

        try:

            df = pd.read_csv(path)

            sig = compute_signals(df, index_returns)

            if sig is None:
                continue

            alpha = compute_alpha(sig)

            results.append((symbol, alpha))

        except:
            continue

    ranked = sorted(results, key=lambda x: x[1], reverse=True)

    print("\n🏆 TOP STOCKS")

    for sym, score in ranked[:20]:

        print(f"{sym:15s}  {score:.4f}")


if __name__ == "__main__":
    main()
