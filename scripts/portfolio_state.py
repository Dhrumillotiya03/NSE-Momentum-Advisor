import json
import os
import pandas as pd

STATE_PATH = "../data/portfolio_state.json"
PRICE_DIR = "../data/price_data/"


# ---------- Load / Save ----------

def load_state():
    with open(STATE_PATH, "r") as f:
        return json.load(f)


def save_state(state):
    with open(STATE_PATH, "w") as f:
        json.dump(state, f, indent=2)


# ---------- Latest Price ----------

def latest_price(symbol):
    """Live-ish price via live_quotes (yfinance ~15min delayed, cached,
    CSV-close fallback). Returns None if no price is available at all."""
    from live_quotes import get_quote
    price, _stale = get_quote(symbol)
    return price


# ---------- Portfolio Valuation ----------

def portfolio_value(state):

    total = state["cash"]

    for sym, pos in state["positions"].items():
        price = latest_price(sym)
        if price:
            total += price * pos["qty"]

    return total


# ---------- Display ----------

def show_portfolio():

    state = load_state()

    print("\n==============================")
    print("📒 PORTFOLIO STATE")
    print("==============================")

    total = portfolio_value(state)

    print(f"\nCash: ₹{state['cash']:,.0f}")

    if not state["positions"]:
        print("No open positions")
        return

    print("\nPositions:")

    for sym, pos in state["positions"].items():

        price = latest_price(sym)

        if price:
            value = price * pos["qty"]
            pnl = (price - pos["entry_price"]) / pos["entry_price"]

            print(f"\n{sym}")
            print(f"  Qty: {pos['qty']}")
            print(f"  Entry: ₹{pos['entry_price']:.2f}")
            print(f"  Current: ₹{price:.2f}")
            print(f"  Value: ₹{value:,.0f}")
            print(f"  P&L: {pnl:.2%}")

    print(f"\nTotal Portfolio Value: ₹{total:,.0f}")


if __name__ == "__main__":
    show_portfolio()
