"""
Per-position exit check, hard monthly-close model.

The book is FULLY LIQUIDATED at every month-end (last trading day): the user's
mandate is that the portfolio goes flat each month and the fresh top-N for the
current regime is bought back the next session. A name that re-qualifies is
sold and immediately re-bought — economically a hold, but executed as two
trades so the journal and broker statements match reality. This is also
exactly what backtest_portfolio.py has always modeled (2 x COST per cycle).

Priority order for intra-month checks (first match wins):
  (a) Catastrophic stop — always on. price < entry * CATASTROPHIC_STOP,
      checked against the LIVE quote when available (live_quotes.py,
      ~15-min delayed), falling back to the last downloaded close.
  (b) Month-end liquidation — on the last trading day, every strategy
      position is a SELL. The report annotates which names re-qualify for
      the new book (sell + re-buy) and which don't (sell, gone).

(An early "good exit" resistance-fade rule was tried and rejected — see
strategy_config.py's Exit engine comment. Intra-month, the only exit is
the catastrophic stop; "a suitable price to sell" is the month-end close.)

Non-strategy holdings (GOLDBEES, RCOM-BE, HARCR, or anything with
entry_price==0) are detected and only FLAGGED for manual review — never
auto-sold.

This script emits SIGNALS only. It never writes trade_history.csv or
portfolio_state.json — actual fills are recorded via record_fill.py after
executing on Zerodha. (It used to journal signals directly, which produced
duplicate rows every day a signal persisted and let the state drift from
the journal.)
"""
import os
import pandas as pd
import numpy as np

from portfolio_state import load_state
import strategy_config as sc
from core import compute_score, market_regime, scan_universe
from live_quotes import get_quote

DATA_DIR = "../data/price_data/"


# ---------- Load Stock Data ----------

def load_stock(symbol):
    path = DATA_DIR + f"{symbol}.csv"
    if not os.path.exists(path):
        return None
    df = pd.read_csv(path, parse_dates=["Date"])
    for col in ["Close", "High", "Low", "Open", "Volume"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.dropna(subset=["Close"]).sort_values("Date")
    return df


# ---------- Non-strategy holding detection ----------

def is_non_strategy_holding(symbol, pos):
    if pos.get("entry_price", 0) == 0:
        return True
    if symbol in sc.EXIT_EXCLUDE_SYMBOLS:
        return True
    for suffix in sc.EXIT_EXCLUDE_SUFFIXES:
        if symbol.endswith(suffix):
            return True
    return False


# ---------- Calendar helper ----------

def is_last_trading_day_of_month(dates, as_of=None):
    """dates: sorted DatetimeIndex/Series of trading days for an index/stock.
    Returns True if the last available date is the last trading day of its month."""
    if len(dates) == 0:
        return False
    last = pd.Timestamp(dates.iloc[-1] if hasattr(dates, "iloc") else dates[-1])
    if as_of is not None:
        last = pd.Timestamp(as_of)
    next_day = last + pd.Timedelta(days=1)
    return next_day.month != last.month


# ---------- Exit checks ----------

def check_catastrophic_stop(df, entry_price, live_price=None):
    """Stop check against the live quote when supplied, else last close."""
    price = live_price if live_price else df["Close"].iloc[-1]
    if entry_price and price < entry_price * sc.CATASTROPHIC_STOP:
        pct = (price / entry_price - 1) * 100
        return f"Catastrophic stop ({pct:.0f}% from entry, price {price:.2f})"
    return None


def check_requalification(symbol, df, regime, eligible_scores, top_n_symbols):
    """Month-end classifier: does this name make the NEW book?
    Returns None if it re-qualifies (sell at close, re-buy next session);
    otherwise a reason string explaining why it drops out."""
    r = compute_score(df)
    if r is None:
        return "does NOT re-qualify: fails the momentum filter (no re-buy)"
    if symbol not in top_n_symbols:
        return (f"does NOT re-qualify: eligible but outside top-{len(top_n_symbols)} "
                f"for current regime ({regime}) (no re-buy)")
    return None


# ---------- Main ----------

def main():
    print("\n==============================")
    print("EXIT ANALYSIS")
    print("==============================")

    state = load_state()
    positions = state["positions"]

    regime, _breadth = market_regime()
    n_names = sc.REGIME_NAMES[regime]

    index_dates = pd.to_datetime(
        pd.read_csv("../data/index_data/nifty50.csv")["Date"], errors="coerce"
    ).dropna().sort_values()
    month_end = is_last_trading_day_of_month(index_dates)

    eligible_scores = None
    top_n_symbols = set()
    if month_end:
        eligible_scores = scan_universe()
        ranked = sorted(eligible_scores.items(), key=lambda kv: kv[1]["score"], reverse=True)
        top_n_symbols = {sym for sym, _ in ranked[:n_names]}
        print(f"\n(MONTH-END: full liquidation — book goes flat, fresh top-{n_names} "
              f"for regime {regime} re-entered next session)")

    exit_list = []   # (symbol, reasons, live_price)
    flag_list = []

    for sym, pos in positions.items():
        if is_non_strategy_holding(sym, pos):
            flag_list.append((sym, "Non-strategy holding — excluded from auto-exit, review manually"))
            continue

        df = load_stock(sym)
        if df is None or len(df) < 60:
            continue

        entry_price = pos.get("entry_price", 0)
        live_price, stale = get_quote(sym)
        reasons = []

        reason = check_catastrophic_stop(df, entry_price, live_price=None if stale else live_price)
        if reason:
            reasons.append(reason)

        if not reasons and month_end:
            requal = check_requalification(sym, df, regime, eligible_scores, top_n_symbols)
            if requal is None:
                reasons.append("Month-end liquidation — RE-QUALIFIES for the new book: sell at close, re-buy next session")
            else:
                reasons.append(f"Month-end liquidation — {requal}")

        if reasons:
            exit_list.append((sym, reasons, live_price))

    if flag_list:
        print("\nFLAGGED FOR MANUAL REVIEW (excluded from auto-exit):")
        for sym, reason in flag_list:
            print(f"  {sym}: {reason}")

    if month_end and eligible_scores:
        ranked = sorted(eligible_scores.items(), key=lambda kv: kv[1]["score"], reverse=True)
        print(f"\nNEW BOOK to enter next session (top-{n_names}, {regime} regime):")
        for sym, r in ranked[:n_names]:
            print(f"  {sym:16s} score {r['score']:.2f}")

    if not exit_list:
        print("\nNo exit signals detected")
        return

    print("\nSELL SIGNALS (signals only — books are NOT updated here):")

    for sym, reasons, live_price in exit_list:
        print(f"\n{sym}")
        for r in reasons:
            print(f"  - {r}")

        pos = positions.get(sym, {})
        qty = pos.get("qty", 0)
        entry = pos.get("entry_price", 0)
        price = live_price
        if price is None:
            df = load_stock(sym)
            price = df["Close"].iloc[-1] if df is not None else 0
        pnl = round((price - entry) * qty, 2) if entry and qty else 0
        print(f"  ~₹{price:.2f} x {qty} (est. P&L ₹{pnl:,.2f})")
        print(f"  After executing on Zerodha, record the ACTUAL fill:")
        print(f"    python record_fill.py sell {sym.replace('.NS', '')} <FILL_PRICE>")


if __name__ == "__main__":
    main()
