import yfinance as yf
import sys
import os

PRICE_DIR = "../data/price_data/"


def resolve_symbol(query):
    """Match plain name to exact symbol from your data"""
    query = query.strip()

    # If already a valid symbol in your data, use it directly
    if os.path.exists(PRICE_DIR + f"{query}.csv"):
        return query
    if os.path.exists(PRICE_DIR + f"{query}.NS.csv"):
        return query + ".NS"

    # Search by name
    query_clean = query.lower().replace(" ", "")
    symbols = [f.replace(".csv", "") for f in os.listdir(PRICE_DIR)
               if f.endswith(".csv")]

    matches = []
    for sym in symbols:
        sym_clean = sym.replace(".NS", "").lower()
        if query_clean in sym_clean or sym_clean in query_clean:
            matches.append(sym)

    if len(matches) == 1:
        return matches[0]

    if len(matches) > 1:
        print(f"\n⚠️ Multiple matches for '{query}':")
        for i, m in enumerate(matches, 1):
            print(f"  {i}. {m}")
        choice = input("Enter number: ").strip()
        try:
            return matches[int(choice) - 1]
        except:
            return None

    print(f"❌ No symbol found for '{query}'")
    return None

def get_price(query):
    symbol = resolve_symbol(query)
    if symbol is None:
        return

    # Try NSE real-time first (0 lag, free)
    sym_clean = symbol.replace(".NS", "")
    try:
        url     = f"https://www.nseindia.com/api/quote-equity?symbol={sym_clean}"
        headers = {
            "User-Agent": "Mozilla/5.0",
            "Accept":     "application/json",
            "Referer":    "https://www.nseindia.com"
        }
        session = requests.Session()
        session.get("https://www.nseindia.com", headers=headers, timeout=5)
        r       = session.get(url, headers=headers, timeout=5)
        data    = r.json()
        price   = float(data["priceInfo"]["lastPrice"])
        prev    = float(data["priceInfo"]["previousClose"])
        change  = ((price - prev) / prev) * 100

        print(f"\n{symbol}  [NSE Live]")
        print(f"  Price:      ₹{price:.2f}")
        print(f"  Change:     {change:+.2f}%")
        print(f"  Prev Close: ₹{prev:.2f}")
        return

    except:
        pass  # fall through to yfinance

    # Fallback — yfinance (15 min delay)
    try:
        t      = yf.Ticker(symbol)
        hist   = t.history(period="2d")
        price  = hist["Close"].iloc[-1]
        prev   = hist["Close"].iloc[-2]
        change = ((price - prev) / prev) * 100

        print(f"\n{symbol}  [15-min delayed]")
        print(f"  Price:      ₹{price:.2f}")
        print(f"  Change:     {change:+.2f}%")
        print(f"  Prev Close: ₹{prev:.2f}")

    except Exception as e:
        print(f"❌ Could not fetch {symbol}: {e}")


if __name__ == "__main__":
    if len(sys.argv) > 1:
        # e.g. python price_check.py RELIANCE
        # or   python price_check.py Adani Enterprise
        get_price(" ".join(sys.argv[1:]))
    else:
        print("Enter stock name or symbol (or 'exit'):")
        while True:
            q = input("\nStock: ").strip()
            if q.lower() in ["exit", "quit"]:
                break
            get_price(q)
