import yfinance as yf
import pandas as pd
import os
from collections import defaultdict
import portfolio_state

PRICE_DIR = "../data/price_data/"


# REPLACE lines 2698-2707 with:
SECTOR_CACHE_FILE = "../data/sector_cache.json"

def get_sector(symbol):
    import json, time, os

    # Load cache
    if os.path.exists(SECTOR_CACHE_FILE):
        with open(SECTOR_CACHE_FILE) as f:
            cache = json.load(f)
        # Use cache if less than 7 days old
        if cache.get("_updated", 0) > time.time() - 7 * 86400:
            return cache.get(symbol, "Unknown")
    else:
        cache = {}

    # Fetch fresh
    try:
        ticker = yf.Ticker(symbol)
        sector = ticker.info.get("sector", "Unknown")
    except:
        sector = "Unknown"

    cache[symbol] = sector
    cache["_updated"] = time.time()

    with open(SECTOR_CACHE_FILE, "w") as f:
        json.dump(cache, f)

    return sector


# ---------- Stock Momentum ----------

def stock_momentum(symbol):

    path = PRICE_DIR + f"{symbol}.csv"

    if not os.path.exists(path):
        return None

    df = pd.read_csv(path)

    close = pd.to_numeric(df["Close"], errors="coerce").dropna()

    if len(close) < 63:
        return None

    r1 = close.iloc[-1] / close.iloc[-21] - 1
    r3 = close.iloc[-1] / close.iloc[-63] - 1

    return 0.6 * r1 + 0.4 * r3


# ---------- Main ----------

def main():

    print("\n==============================")
    print("🧠 AUTO SECTOR INTELLIGENCE")
    print("==============================")

    sector_data = defaultdict(list)

    # ---- Scan your universe ----

    for file in os.listdir(PRICE_DIR):

        if not file.endswith(".csv"):
            continue

        symbol = file.replace(".csv", "")

        sector = get_sector(symbol)

        m = stock_momentum(symbol)

        if m is None:
            continue

        sector_data[sector].append(m)

    # ---- Compute Sector Strength ----

    results = []

    for sector, values in sector_data.items():

        avg_strength = sum(values) / len(values)

        results.append((sector, avg_strength, len(values)))

    results.sort(key=lambda x: x[1], reverse=True)

    # ---- Output ----

    for sector, strength, count in results:

        if strength > 0.15:
            verdict = "🔥 STRONG"
        elif strength > 0.05:
            verdict = "🟢 FAVORABLE"
        elif strength > -0.05:
            verdict = "🟡 NEUTRAL"
        else:
            verdict = "🔴 WEAK"

        print(f"\n{sector}")
        print(f"  Strength: {strength:.2%}")
        print(f"  Stocks: {count}")
        print(f"  Verdict: {verdict}")

    from portfolio_state import load_state

    print("\n==============================")
    print("💼 PORTFOLIO SECTOR ALIGNMENT")
    print("==============================")

    state = load_state()

    for sym in state["positions"].keys():

        sector = get_sector(sym)

        print(f"{sym} → {sector}")


if __name__ == "__main__":
    main()