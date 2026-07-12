import csv
import os
from datetime import datetime

JOURNAL_FILE = "../data/trade_history.csv"

HEADERS = [
    "date", "symbol", "action", "price", "qty",
    "value", "regime", "sector", "reason", "pnl"
]


def init_journal():
    if not os.path.exists(JOURNAL_FILE):
        with open(JOURNAL_FILE, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=HEADERS)
            writer.writeheader()


def log_trade(symbol, action, price, qty, regime, sector, reason, pnl=None, date=None):
    init_journal()
    value = round(price * qty, 2)
    row = {
        "date":    date or datetime.today().strftime("%Y-%m-%d"),
        "symbol":  symbol,
        "action":  action,        # BUY or SELL
        "price":   round(price, 2),
        "qty":     qty,
        "value":   value,
        "regime":  regime,
        "sector":  sector,
        "reason":  reason,
        "pnl":     round(pnl, 2) if pnl else ""
    }
    with open(JOURNAL_FILE, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=HEADERS)
        writer.writerow(row)
    print(f"📓 Logged: {action} {symbol} @ ₹{price} x {qty}")


def view_journal():
    if not os.path.exists(JOURNAL_FILE):
        print("No trades logged yet.")
        return
    with open(JOURNAL_FILE, "r") as f:
        reader = csv.DictReader(f)
        rows   = list(reader)
    if not rows:
        print("No trades logged yet.")
        return
    print(f"\n{'='*60}")
    print(f"{'DATE':<12} {'SYM':<15} {'ACT':<5} {'PRICE':>8} {'QTY':>5} {'PNL':>10}")
    print(f"{'='*60}")
    for r in rows:
        pnl_str = f"₹{r['pnl']}" if r['pnl'] else "-"
        print(f"{r['date']:<12} {r['symbol']:<15} {r['action']:<5} ₹{r['price']:>7} {r['qty']:>5} {pnl_str:>10}")
    print(f"{'='*60}")


if __name__ == "__main__":
    view_journal()
