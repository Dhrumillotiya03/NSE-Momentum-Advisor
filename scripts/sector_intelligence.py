import yfinance as yf
import pandas as pd
import os

PRICE_DIR = "../data/price_data/"


# ---------- Sector Index Mapping ----------

SECTOR_INDEX = {
    "IT": "^CNXIT",
    "Banking": "^NSEBANK",
    "FMCG": "^CNXFMCG",
    "Auto": "^CNXAUTO",
    "Pharma": "^CNXPHARMA",
    "Metal": "^CNXMETAL",
    "Realty": "^CNXREALTY",
    "Energy": "^CNXENERGY"
}


# ---------- Stock-to-Sector Mapping ----------

STOCK_SECTOR = {
    "INFY.NS": "IT",
    "TCS.NS": "IT",
    "WIPRO.NS": "IT",

    "HDFCBANK.NS": "Banking",
    "ICICIBANK.NS": "Banking",
    "SBIN.NS": "Banking",

    "MARUTI.NS": "Auto",
    "TATAMOTORS.NS": "Auto",

    "SUNPHARMA.NS": "Pharma",
    "CIPLA.NS": "Pharma",

    "TATASTEEL.NS": "Metal",
    "JSWSTEEL.NS": "Metal",

    "RELIANCE.NS": "Energy",
    "ONGC.NS": "Energy"
}


# ---------- Helpers ----------

def index_momentum(symbol):

    df = yf.download(symbol, period="6mo", progress=False)

    if df.empty:
        return None

    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)

    close = pd.to_numeric(df["Close"], errors="coerce").dropna()

    if len(close) < 2:
        return None

    return float(close.iloc[-1] / close.iloc[0] - 1)


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
    print("🧠 PRO SECTOR INTELLIGENCE")
    print("==============================")

    # ---- Index Trends ----

    index_scores = {}

    for sector, sym in SECTOR_INDEX.items():

        m = index_momentum(sym)

        if m is not None:
            index_scores[sector] = m

    # ---- Stock Strength ----

    sector_strength = {}

    for stock, sector in STOCK_SECTOR.items():

        m = stock_momentum(stock)

        if m is None:
            continue

        sector_strength.setdefault(sector, []).append(m)

    # ---- Combine ----

    results = []

    for sector in SECTOR_INDEX.keys():

        idx = index_scores.get(sector, 0)
        stk = sector_strength.get(sector, [])

        if stk:
            stk_avg = sum(stk) / len(stk)
        else:
            stk_avg = 0

        score = 0.6 * idx + 0.4 * stk_avg

        results.append((sector, idx, stk_avg, score))

    results.sort(key=lambda x: x[3], reverse=True)

    # ---- Output ----

    for sector, idx, stk, score in results:

        if score > 0.15:
            verdict = "🔥 HIGH PRIORITY"
        elif score > 0.05:
            verdict = "🟢 FAVORABLE"
        elif score > -0.05:
            verdict = "🟡 NEUTRAL"
        else:
            verdict = "🔴 AVOID"

        print(f"\n{sector} Sector")
        print(f"  Index Trend:     {idx:.2%}")
        print(f"  Stock Strength:  {stk:.2%}")
        print(f"  Leadership Score:{score:.2%}")
        print(f"  Verdict: {verdict}")


if __name__ == "__main__":
    main()