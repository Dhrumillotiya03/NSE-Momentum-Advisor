import yfinance as yf
import pandas as pd
import os
import time
from concurrent.futures import ThreadPoolExecutor

DATA_DIR = "../data/"
PRICE_DIR = DATA_DIR + "price_data/"

MIN_VALUE_TRADED = 5e7
HISTORY_PERIOD = "3y"
MAX_WORKERS = 5   # safer for Yahoo


# ---------- SYMBOL HELPERS ----------

def normalize_symbol(sym):
    sym = sym.strip()
    if not sym.endswith(".NS"):
        sym += ".NS"
    return sym


def load_txt_symbols(file):
    with open(file, "r") as f:
        return [normalize_symbol(line) for line in f if line.strip()]


def load_csv_symbols(file):
    df = pd.read_csv(file)
    col = df.columns[0]
    return [normalize_symbol(s) for s in df[col].dropna().tolist()]


# ---------- DOWNLOAD ----------

def download_stock(symbol):

    for attempt in range(3):  # retry mechanism

        try:
            df = yf.download(symbol, period=HISTORY_PERIOD, progress=False)
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)

            if df is None or df.empty:
                return None

            # ---- FIX MULTIINDEX ----
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)

            df = df.reset_index()

            if "Close" not in df.columns or "Volume" not in df.columns:
                return None

            df["Close"] = pd.to_numeric(df["Close"], errors="coerce")
            df["Volume"] = pd.to_numeric(df["Volume"], errors="coerce")

            avg_value = (df["Close"] * df["Volume"]).rolling(20).mean().iloc[-1]

            if pd.isna(avg_value) or avg_value < MIN_VALUE_TRADED:
                return None

            return df

        except Exception:
            time.sleep(1)  # wait before retry

    return None


# ---------- PROCESS ----------

def process_symbol(sym):
    print(f"Processing {sym}...")

    # Prevent double .NS suffix in filename
    filename = sym if sym.endswith(".NS") else sym + ".NS"
    path = PRICE_DIR + f"{filename}.csv"

    # Skip if already exists and is recent
    if os.path.exists(path):
        pass

    df = download_stock(sym)

    if df is None:
        print("  ❌ Skipped")
        return 0

    df.to_csv(path, index=False)
    return 1


# ---------- MAIN ----------

def main():

    os.makedirs(PRICE_DIR, exist_ok=True)

    print("Loading index constituents...")

    nifty50 = load_txt_symbols("../data/nifty50_symbols.txt")
    nifty200 = load_csv_symbols("../data/nifty200.csv")
    nifty500 = load_csv_symbols("../data/nifty500.csv")

    universe = sorted(set(nifty50 + nifty200 + nifty500))

    print(f"Total unique symbols: {len(universe)}")

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        results = executor.map(process_symbol, universe)

    kept = sum(results)

    print(f"\n✅ Tradable universe built: {kept} stocks")


if __name__ == "__main__":
    main()
