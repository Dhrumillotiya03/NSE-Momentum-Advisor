import subprocess
import re
import pandas as pd
from portfolio_state import load_state, latest_price, portfolio_value


# ---------- Helper ----------

def run(script):
    return subprocess.run(
        ["python", script],
        capture_output=True,
        text=True
    ).stdout


# ---------- Parse Exposure ----------

def parse_exposure(text):

    if "0–20%" in text:
        return 0.15
    if "20–50%" in text:
        return 0.4
    return 0.7


# ---------- Extract Top Stocks ----------

def extract_targets(text):

    stocks = []

    for line in text.splitlines():
        if "→" in line and ".NS" in line:
            sym = line.split()[1]
            stocks.append(sym)

    return stocks


# ---------- Main ----------

def main():

    print("\n==============================")
    print("📋 TRADE EXECUTION PLAN")
    print("==============================")

    state = load_state()

    value = portfolio_value(state)

    advisor = run("adaptive_engine.py")
    portfolio_text = run("portfolio_engine.py")

    exposure = parse_exposure(advisor)

    targets = extract_targets(portfolio_text)

    if not targets:
        print("No buy targets")
        return

    investable = value * exposure

    per_position = investable / len(targets)

    print(f"\nPortfolio Value: ₹{value:,.0f}")
    print(f"Target Exposure: {exposure:.0%}")
    print(f"Capital to Deploy: ₹{investable:,.0f}")
    print(f"Per Position: ₹{per_position:,.0f}")

    print("\n🟢 BUY ORDERS:")

    for sym in targets:

        price = latest_price(sym)

        if not price:
            continue

        qty = int(per_position // price)

        cost = qty * price

        if qty > 0:
            print(f"{sym:15s} → Buy {qty} @ ₹{price:.2f}  (~₹{cost:,.0f})")

    print("\n🔴 SELL CHECK:")

    exits = run("exit_engine.py")

    print(exits)


if __name__ == "__main__":
    main()