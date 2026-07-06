import json
import os
import requests
from datetime import datetime

STATE_PATH  = "../data/portfolio_state.json"
KITE_URL    = "https://api.kite.trade"

# ---- Paste your Zerodha API key and access token here ----
# Get access token daily from: https://kite.zerodha.com/connect/login
API_KEY      = "your_api_key_here"
ACCESS_TOKEN = "your_access_token_here"


def get_zerodha_holdings():
    """Fetch holdings from Zerodha Kite API"""
    headers = {
        "X-Kite-Version": "3",
        "Authorization": f"token {API_KEY}:{ACCESS_TOKEN}"
    }
    r = requests.get(f"{KITE_URL}/portfolio/holdings", headers=headers)
    if r.status_code != 200:
        print(f"❌ Zerodha API error: {r.status_code} — {r.text}")
        return None
    return r.json()["data"]


def get_zerodha_funds():
    """Fetch available cash from Zerodha"""
    headers = {
        "X-Kite-Version": "3",
        "Authorization": f"token {API_KEY}:{ACCESS_TOKEN}"
    }
    r = requests.get(f"{KITE_URL}/user/margins", headers=headers)
    if r.status_code != 200:
        return None
    data = r.json()["data"]
    return float(data["equity"]["available"]["live_balance"])


def sync_to_portfolio_state():
    """Pull Zerodha holdings and overwrite portfolio_state.json"""

    print("\n🔄 Syncing Zerodha holdings to portfolio_state.json...")

    holdings = get_zerodha_holdings()
    if holdings is None:
        return

    try:
        with open(STATE_PATH) as f:
            existing_state = json.load(f)
    except FileNotFoundError:
        existing_state = {}
    existing_positions = existing_state.get("positions", {})

    cash = get_zerodha_funds()
    if cash is None:
        # Load existing cash if API fails
        cash = existing_state.get("cash", 0)

    positions = {}
    for h in holdings:
        if h["quantity"] <= 0:
            continue

        # Convert BSE/NSE symbol to yfinance format
        sym = h["tradingsymbol"] + ".NS"
        entry_price = round(float(h["average_price"]), 2)

        # Preserve the trailing-stop high-water mark across syncs — it must
        # only ratchet up, never reset, or the trailing stop can loosen.
        prior = existing_positions.get(sym, {})
        high_since_entry = max(prior.get("high_since_entry", entry_price), entry_price)

        positions[sym] = {
            "qty":               h["quantity"],
            "entry_price":       entry_price,
            "entry_date":        prior.get("entry_date") or datetime.today().strftime("%Y-%m-%d"),
            "high_since_entry":  high_since_entry,
        }

        print(f"  ✅ {sym}: {h['quantity']} shares @ ₹{h['average_price']:.2f}")

    state = {
        "cash":      round(cash, 2),
        "positions": positions
    }

    with open(STATE_PATH, "w") as f:
        json.dump(state, f, indent=2)

    print(f"\n✅ Synced {len(positions)} holdings")
    print(f"💰 Available cash: ₹{cash:,.2f}")
    print(f"📁 Saved to: {STATE_PATH}")


if __name__ == "__main__":
    sync_to_portfolio_state()
