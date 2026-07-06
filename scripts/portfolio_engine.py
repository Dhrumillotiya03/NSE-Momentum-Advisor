import subprocess
import re

from pandas.io import json


# ---------- Helper ----------

def run(script):
    return subprocess.run(
        ["python", script],
        capture_output=True,
        text=True
    ).stdout


# ---------- Parse exposure from production advisor ----------

def parse_exposure(text):

    if "HIGH (50–80%)" in text:
        return 0.7, "HIGH"
    if "MODERATE (25–50%)" in text:
        return 0.4, "MEDIUM"
    return 0.15, "LOW"


# ---------- Extract top stocks from alpha output ----------

def extract_top_stocks(text, n):

    lines = text.splitlines()

    stocks = []

    for line in lines:
        if ". " in line and "Score:" in line:
            parts = line.split()
            sym = parts[1]
            stocks.append(sym)

    return stocks[:n]


# ---------- Main ----------

def main():

    print("\n==============================")
    print("💼 PORTFOLIO CONSTRUCTION")
    print("==============================")

    advisor = run("production_advisor.py")
    alpha = run("stock_alpha_v2.py")

    exposure, confidence = parse_exposure(advisor)

    # ---------- Determine portfolio size ----------

    if confidence == "HIGH":
        n_positions = 10
    elif confidence == "MEDIUM":
        n_positions = 6
    else:
        n_positions = 2

    stocks = extract_top_stocks(alpha, n_positions)

    if not stocks:
        print("⚠️ No suitable stocks found")
        return
    import json
    with open("../data/sectors.json") as f:
        sector_map = {sym: sec for sec, syms in json.load(f).items() for sym in syms}

    MAX_PER_SECTOR = 2
    sector_count = {}
    filtered = []
    for sym in stocks:
        sec = sector_map.get(sym, "OTHER")
        if sector_count.get(sec, 0) < MAX_PER_SECTOR:
            filtered.append(sym)
            sector_count[sec] = sector_count.get(sec, 0) + 1
    stocks = filtered

    position_weight = exposure / len(stocks)

    # ---------- Output ----------

    print(f"\nConfidence: {confidence}")
    print(f"Total Market Exposure: {exposure:.0%}")
    print(f"Number of Positions: {len(stocks)}")
    print(f"Position Size: {position_weight:.1%} each")

    print("\n📈 PORTFOLIO ALLOCATION:")

    for s in stocks:
        print(f"• {s:15s} → {position_weight:.1%}")

    print("\n💰 Cash Allocation:")
    print(f"• {(1 - exposure):.0%} held as cash")

    print("\n🧠 Notes:")
    print("• Equal-weight diversified portfolio")
    print("• Based on top-ranked alpha stocks")
    print("• Adjust weekly or on major regime change")


if __name__ == "__main__":
    main()