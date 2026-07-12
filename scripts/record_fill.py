"""
Record an ACTUAL executed trade (a fill from Zerodha) into both books at once:
  - ../data/portfolio_state.json  (positions + cash)
  - ../data/trade_history.csv     (journal, via trade_journal.log_trade)

This is the ONLY supported way to update portfolio state after a trade.
exit_engine.py / full_advisor.py print SIGNALS — they never touch the books.
After executing an order on Zerodha, record it here with the real fill price.

Usage (from scripts/):
  python record_fill.py buy  SYMBOL QTY PRICE  [--date YYYY-MM-DD] [--reason "..."]
  python record_fill.py sell SYMBOL PRICE      [--qty N] [--date YYYY-MM-DD] [--reason "..."]
  python record_fill.py show

SELL defaults to the full position. Symbols are normalised to .NS
(aartiind -> AARTIIND.NS). An identical fill (same date/symbol/action/qty/price)
already in the journal aborts unless --force is given.
"""
import argparse
import json
import sys
from datetime import datetime

from portfolio_state import load_state, save_state, show_portfolio
from trade_journal import log_trade

SECTORS_PATH = "../data/sectors.json"
JOURNAL_PATH = "../data/trade_history.csv"


def normalise(symbol):
    s = symbol.upper()
    if not s.endswith(".NS"):
        s += ".NS"
    return s


def sector_of(symbol):
    try:
        with open(SECTORS_PATH) as f:
            sectors = json.load(f)
        for sector, syms in sectors.items():
            if symbol in syms:
                return sector
    except Exception:
        pass
    return "N/A"


def current_regime():
    try:
        from core import market_regime
        regime, _breadth = market_regime()
        return regime
    except Exception:
        return "N/A"


def already_journaled(date, symbol, action, qty, price):
    import os
    import pandas as pd
    if not os.path.exists(JOURNAL_PATH):
        return False
    try:
        df = pd.read_csv(JOURNAL_PATH)
        return bool(((df["date"] == date) & (df["symbol"] == symbol)
                     & (df["action"] == action) & (df["qty"] == qty)
                     & (df["price"].round(2) == round(price, 2))).any())
    except Exception:
        return False


def do_buy(state, symbol, qty, price, date, reason, force):
    if already_journaled(date, symbol, "BUY", qty, price) and not force:
        sys.exit(f"ABORT: identical BUY already journaled for {date} — use --force if this is a real second fill.")

    pos = state["positions"].get(symbol)
    if pos:
        old_qty, old_entry = pos["qty"], pos["entry_price"]
        new_qty = old_qty + qty
        pos["entry_price"] = (old_qty * old_entry + qty * price) / new_qty
        pos["qty"] = new_qty
        pos["high_since_entry"] = max(pos.get("high_since_entry", 0), price)
        print(f"Added to existing position: {old_qty} @ ₹{old_entry:.2f} + {qty} @ ₹{price:.2f} "
              f"-> {new_qty} @ avg ₹{pos['entry_price']:.2f}")
    else:
        state["positions"][symbol] = {
            "qty": qty,
            "entry_price": price,
            "entry_date": date,
            "high_since_entry": price,
        }

    cost = qty * price
    state["cash"] -= cost
    if state["cash"] < 0:
        print(f"⚠️  WARNING: cash is now NEGATIVE (₹{state['cash']:,.0f}) — "
              f"check qty/price, or update the cash figure if capital was added.")

    log_trade(symbol, "BUY", price, qty, current_regime(), sector_of(symbol),
              reason or "Manual fill", pnl=None, date=date)
    return None


def do_sell(state, symbol, qty, price, date, reason, force):
    pos = state["positions"].get(symbol)
    if pos is None:
        sys.exit(f"ABORT: no open position in {symbol} — nothing to sell. "
                 f"(Check `python record_fill.py show`.)")

    if qty is None:
        qty = pos["qty"]
    if qty > pos["qty"]:
        sys.exit(f"ABORT: trying to sell {qty} but position is only {pos['qty']}.")

    if already_journaled(date, symbol, "SELL", qty, price) and not force:
        sys.exit(f"ABORT: identical SELL already journaled for {date} — use --force if this is a real second fill.")

    entry = pos["entry_price"]
    pnl = round((price - entry) * qty, 2)
    state["cash"] += qty * price

    if qty == pos["qty"]:
        del state["positions"][symbol]
        print(f"Closed {symbol}: {qty} @ ₹{price:.2f} (entry ₹{entry:.2f}) — P&L ₹{pnl:,.2f}")
    else:
        pos["qty"] -= qty
        print(f"Partial sell {symbol}: {qty} @ ₹{price:.2f}, {pos['qty']} remaining — P&L ₹{pnl:,.2f}")

    log_trade(symbol, "SELL", price, qty, current_regime(), sector_of(symbol),
              reason or "Manual fill", pnl=pnl, date=date)
    return pnl


def main():
    p = argparse.ArgumentParser(description="Record an executed fill into portfolio state + trade journal.")
    sub = p.add_subparsers(dest="cmd", required=True)

    b = sub.add_parser("buy", help="record a buy fill")
    b.add_argument("symbol")
    b.add_argument("qty", type=int)
    b.add_argument("price", type=float)

    s = sub.add_parser("sell", help="record a sell fill (defaults to full position)")
    s.add_argument("symbol")
    s.add_argument("price", type=float)
    s.add_argument("--qty", type=int, default=None)

    for sp in (b, s):
        sp.add_argument("--date", default=datetime.today().strftime("%Y-%m-%d"),
                        help="fill date, default today")
        sp.add_argument("--reason", default=None)
        sp.add_argument("--force", action="store_true",
                        help="allow a fill identical to one already journaled today")

    sub.add_parser("show", help="show current portfolio state")

    args = p.parse_args()

    if args.cmd == "show":
        show_portfolio()
        return

    symbol = normalise(args.symbol)
    state = load_state()

    if args.cmd == "buy":
        do_buy(state, symbol, args.qty, args.price, args.date, args.reason, args.force)
    else:
        do_sell(state, symbol, args.qty, args.price, args.date, args.reason, args.force)

    save_state(state)
    print(f"Cash: ₹{state['cash']:,.2f} | Open positions: {len(state['positions'])}")
    print("✅ portfolio_state.json and trade_history.csv updated.")


if __name__ == "__main__":
    main()
