import os
import json

PRICE_DIR   = "../data/price_data/"
SECTOR_FILE = "../data/sectors.json"


def build_symbol_list():
    """All symbols available in price_data/"""
    return [f.replace(".csv", "") for f in os.listdir(PRICE_DIR) 
            if f.endswith(".csv")]


def find_symbol(query):
    """
    Match a plain English name to the closest NSE symbol.
    e.g. 'adani enterprise' → 'ADANIENT.NS'
         'hdfc bank'        → 'HDFCBANK.NS'
         'tata motors'      → 'TATAMOTORS.NS'
    """
    query   = query.lower().strip()
    symbols = build_symbol_list()

    # Direct match first (case-insensitive)
    for sym in symbols:
        if query.upper() == sym.replace(".NS", ""):
            return sym

    # Keyword match — strip spaces and common words
    query_clean = query.replace(" ", "").replace("limited", "")\
                       .replace("ltd", "").replace("industries", "")\
                       .replace("enterprise", "").replace("enterprises", "")\
                       .replace("bank", "bank").replace("finance", "fin")

    matches = []
    for sym in symbols:
        sym_clean = sym.replace(".NS", "").lower()
        if query_clean in sym_clean or sym_clean in query_clean:
            matches.append(sym)

    if len(matches) == 1:
        return matches[0]

    if len(matches) > 1:
        print(f"\n⚠️ Multiple matches found for '{query}':")
        for i, m in enumerate(matches, 1):
            print(f"  {i}. {m}")
        print("Please use exact symbol from above.")
        return None

    print(f"❌ No symbol found for '{query}'")
    print("Tip: Use exact NSE symbol e.g. ADANIENT.NS, HDFCBANK.NS")
    return None


def main():
    print("\n🔍 SYMBOL LOOKUP")
    print("Type company name to find NSE symbol (or 'exit')\n")

    while True:
        query = input("Search: ").strip()
        if query.lower() in ["exit", "quit"]:
            break
        result = find_symbol(query)
        if result:
            print(f"✅ Symbol: {result}\n")


if __name__ == "__main__":
    main()
