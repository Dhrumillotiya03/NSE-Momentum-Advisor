"""
Per-position exit check, monthly-deadline model.

Priority order (first match wins, evaluated in this order):
  (a) Catastrophic stop     - always on. price < entry * CATASTROPHIC_STOP.
  (b) Month-end re-qualification gate - always on, fires only on the last
      trading day of the month. Re-scores the universe; SELLS held names
      that no longer pass the momentum filter or dropped out of the
      regime's current top-N. Winners that still qualify are held (this is
      NOT a blind liquidation).

(An early "good exit" resistance-fade rule was tried and rejected — see
strategy_config.py's Exit engine comment for why.)

Non-strategy holdings (GOLDBEES, RCOM-BE, HARCR, or anything with
entry_price==0) are detected and only FLAGGED for manual review — never
auto-sold by the momentum gate.
"""
import os
import pandas as pd
import numpy as np

from portfolio_state import load_state, save_state
import strategy_config as sc
from core import compute_score, market_regime, scan_universe

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

def check_catastrophic_stop(df, entry_price):
    price = df["Close"].iloc[-1]
    if entry_price and price < entry_price * sc.CATASTROPHIC_STOP:
        pct = (price / entry_price - 1) * 100
        return f"Catastrophic stop ({pct:.0f}% from entry)"
    return None


def check_requalification(symbol, df, regime, eligible_scores, top_n_symbols):
    """Only meaningful on month-end. Sells if the name no longer passes the
    momentum eligibility filter, or fell out of the regime's current top-N."""
    r = compute_score(df)
    if r is None:
        return "Month-end re-qualification: no longer passes momentum filter (eligibility lost)"
    if symbol not in top_n_symbols:
        return (f"Month-end re-qualification: eligible but dropped out of top-{len(top_n_symbols)} "
                f"for current regime ({regime})")
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

    index_dates = pd.read_csv("../data/index_data/nifty50.csv", parse_dates=["Date"])["Date"].sort_values()
    month_end = is_last_trading_day_of_month(index_dates)

    eligible_scores = None
    top_n_symbols = set()
    if month_end:
        eligible_scores = scan_universe()
        ranked = sorted(eligible_scores.items(), key=lambda kv: kv[1]["score"], reverse=True)
        top_n_symbols = {sym for sym, _ in ranked[:n_names]}
        print(f"\n(Month-end re-qualification gate active — regime={regime}, top-{n_names})")

    exit_list = []
    flag_list = []

    for sym, pos in positions.items():
        if is_non_strategy_holding(sym, pos):
            flag_list.append((sym, "Non-strategy holding — excluded from auto-exit, review manually"))
            continue

        df = load_stock(sym)
        if df is None or len(df) < 60:
            continue

        entry_price = pos.get("entry_price", 0)
        reasons = []

        reason = check_catastrophic_stop(df, entry_price)
        if reason:
            reasons.append(reason)

        if not reasons and month_end:
            reason = check_requalification(sym, df, regime, eligible_scores, top_n_symbols)
            if reason:
                reasons.append(reason)

        if reasons:
            exit_list.append((sym, reasons))

    if flag_list:
        print("\nFLAGGED FOR MANUAL REVIEW (excluded from auto-exit):")
        for sym, reason in flag_list:
            print(f"  {sym}: {reason}")

    if not exit_list:
        print("\nNo exit signals detected")
        return

    from trade_journal import log_trade

    print("\nSELL / REDUCE POSITIONS:")

    for sym, reasons in exit_list:
        print(f"\n{sym}")
        for r in reasons:
            print(f"  - {r}")

        pos = positions.get(sym, {})
        qty = pos.get("qty", 0)
        entry = pos.get("entry_price", 0)
        df = load_stock(sym)
        price = df["Close"].iloc[-1] if df is not None else 0
        pnl = round((price - entry) * qty, 2) if entry and qty else 0
        log_trade(sym, "SELL", price, qty, regime, "N/A", ", ".join(reasons), pnl=pnl)


if __name__ == "__main__":
    main()
