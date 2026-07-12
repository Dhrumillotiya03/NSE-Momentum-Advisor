"""
Forward paper-trading loop — out-of-sample-in-time evidence accrual.

Runs a simulated ₹10L book under the EXACT live mandate, daily, using only
data available at run time (evening, after the download pipeline):

  - month-end (last trading day): sell the whole book at close; compute the
    new regime top-N (sector-capped, same greedy rule as the backtest) and
    queue it; queued buys execute at the NEXT session's close ("re-enter
    next session", mirroring exit_engine's live instruction)
  - daily: -18% catastrophic stop vs today's close
  - sizing: inverse-vol weights capped at MAX_WEIGHT, regime exposure —
    identical to backtest_portfolio.run_backtest
  - costs: COST per side embedded in cash movements

Completely SEPARATE from the real books (portfolio_state.json /
trade_history.csv are never touched). State: ../data/paper_state.json.
Logs:  ../data/paper_log.csv (fills), ../data/paper_equity.csv (daily MTM).

Idempotent per trading day — safe to run multiple times; it acts once per
new index date. Wired into run_daily_log.sh so it accrues automatically.

Usage (from scripts/):
    python paper_trader.py          # daily step (called by the pipeline)
    python paper_trader.py report   # tracking summary vs Nifty
"""
import csv
import json
import os
import sys

import numpy as np
import pandas as pd

import strategy_config as sc
from core import scan_universe, market_regime, load_stock, load_index
from backtest_portfolio import select_top_n_capped, load_sector_map
from exit_engine import is_last_trading_day_of_month

STATE_PATH = "../data/paper_state.json"
LOG_PATH = "../data/paper_log.csv"
EQUITY_PATH = "../data/paper_equity.csv"
INITIAL_CAPITAL = 1_000_000.0


# ---------- state ----------

def load_state():
    if os.path.exists(STATE_PATH):
        with open(STATE_PATH) as f:
            return json.load(f)
    return {"cash": INITIAL_CAPITAL, "positions": {}, "pending_buys": [],
            "last_run": None, "start_date": None}


def save_state(state):
    with open(STATE_PATH, "w") as f:
        json.dump(state, f, indent=2)


def append_csv(path, row, headers):
    new = not os.path.exists(path)
    with open(path, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=headers)
        if new:
            w.writeheader()
        w.writerow(row)


def log_fill(date, sym, action, price, qty, reason, pnl=""):
    append_csv(LOG_PATH, {
        "date": date, "symbol": sym, "action": action,
        "price": round(price, 2), "qty": qty,
        "value": round(price * qty, 2), "reason": reason, "pnl": pnl,
    }, ["date", "symbol", "action", "price", "qty", "value", "reason", "pnl"])


# ---------- prices ----------

def close_on(sym, date):
    """Close price for sym on `date` from the freshly-downloaded CSVs.
    None if the stock didn't print that day."""
    df = load_stock(sym)
    if df is None:
        return None
    row = df[df.index == date] if df.index.name == "Date" else df[df["Date"] == date]
    if len(row) == 0:
        return None
    px = float(row["Close"].iloc[-1])
    return px if px > 0 else None


# ---------- daily step ----------

def step():
    index = load_index()
    index_dates = index.index if hasattr(index, "index") else index
    today = pd.Timestamp(index_dates[-1])
    today_str = today.strftime("%Y-%m-%d")

    state = load_state()
    if state["last_run"] == today_str:
        print(f"[paper] already ran for {today_str}")
        return
    if state["start_date"] is None:
        state["start_date"] = today_str

    # 1. execute queued buys at today's close
    still_pending = []
    for order in state["pending_buys"]:
        px = close_on(order["sym"], today)
        if px is None:
            order["retries"] = order.get("retries", 0) + 1
            if order["retries"] <= 3:
                still_pending.append(order)  # halt/holiday for this name — retry
            continue
        budget = order["rupees"]
        qty = int(budget // (px * (1 + sc.COST)))
        if qty <= 0:
            continue
        cost = qty * px * (1 + sc.COST)
        state["cash"] -= cost
        state["positions"][order["sym"]] = {
            "qty": qty, "entry_price": px, "entry_date": today_str}
        log_fill(today_str, order["sym"], "BUY", px, qty, "month-start entry")
    state["pending_buys"] = still_pending

    # 2. -18% stop check at close
    for sym in list(state["positions"]):
        pos = state["positions"][sym]
        px = close_on(sym, today)
        if px is None:
            continue
        if px < pos["entry_price"] * sc.CATASTROPHIC_STOP:
            proceeds = pos["qty"] * px * (1 - sc.COST)
            pnl = round(proceeds - pos["qty"] * pos["entry_price"], 2)
            state["cash"] += proceeds
            log_fill(today_str, sym, "SELL", px, pos["qty"], "catastrophic stop", pnl)
            del state["positions"][sym]

    # 3. month-end: liquidate everything, queue next book
    month_end = is_last_trading_day_of_month(pd.Series(index_dates))
    if month_end:
        for sym in list(state["positions"]):
            pos = state["positions"][sym]
            px = close_on(sym, today) or pos["entry_price"]
            proceeds = pos["qty"] * px * (1 - sc.COST)
            pnl = round(proceeds - pos["qty"] * pos["entry_price"], 2)
            state["cash"] += proceeds
            log_fill(today_str, sym, "SELL", px, pos["qty"], "month-end liquidation", pnl)
            del state["positions"][sym]

        regime, _ = market_regime()
        n = sc.REGIME_NAMES[regime]
        exposure = sc.REGIME_EXPOSURE[regime]
        eligible = scan_universe()
        if len(eligible) >= n:
            scores = {s: r["score"] for s, r in eligible.items()}
            top = select_top_n_capped(scores, n, load_sector_map(), sc.MAX_PER_SECTOR)
            inv = {s: 1.0 / eligible[s]["vol_63"] for s in top}
            tot = sum(inv.values())
            inv = {s: min(v / tot, sc.MAX_WEIGHT) * tot for s, v in inv.items()}
            tot = sum(inv.values())
            invest = state["cash"] * exposure
            state["pending_buys"] = [
                {"sym": s, "rupees": round(invest * inv[s] / tot, 2)} for s in top]
            print(f"[paper] month-end: queued {len(top)} buys for {regime} "
                  f"(exposure {exposure:.0%})")
        else:
            state["pending_buys"] = []
            print(f"[paper] month-end: only {len(eligible)} eligible < {n}, staying in cash")
        mtm_regime = regime
    else:
        mtm_regime = ""

    # 4. mark-to-market snapshot
    equity = state["cash"]
    for sym, pos in state["positions"].items():
        px = close_on(sym, today)
        equity += pos["qty"] * (px if px else pos["entry_price"])
    append_csv(EQUITY_PATH, {
        "date": today_str, "equity": round(equity, 2),
        "cash": round(state["cash"], 2), "n_pos": len(state["positions"]),
        "pending": len(state["pending_buys"]), "regime": mtm_regime,
    }, ["date", "equity", "cash", "n_pos", "pending", "regime"])

    state["last_run"] = today_str
    save_state(state)
    print(f"[paper] {today_str}: equity ₹{equity:,.0f} | cash ₹{state['cash']:,.0f} "
          f"| {len(state['positions'])} pos | {len(state['pending_buys'])} pending")


# ---------- report ----------

def report():
    if not os.path.exists(EQUITY_PATH):
        print("No paper-trading history yet.")
        return
    eq = pd.read_csv(EQUITY_PATH, parse_dates=["date"])
    idx = load_index()
    start, end = eq["date"].iloc[0], eq["date"].iloc[-1]
    days = (end - start).days
    ret = eq["equity"].iloc[-1] / INITIAL_CAPITAL - 1
    nifty = idx[(idx.index >= start) & (idx.index <= end)]
    nret = nifty.iloc[-1] / nifty.iloc[0] - 1 if len(nifty) > 1 else np.nan

    print(f"\nPAPER BOOK — {start.date()} -> {end.date()}  ({days} days, "
          f"{len(eq)} sessions)")
    print(f"  equity: ₹{eq['equity'].iloc[-1]:,.0f}  ({ret:+.2%})")
    print(f"  Nifty same period: {nret:+.2%}   alpha: {ret - nret:+.2%}")
    peak = eq["equity"].cummax()
    print(f"  max drawdown: {((peak - eq['equity']) / peak).max():.2%}")
    if os.path.exists(LOG_PATH):
        trades = pd.read_csv(LOG_PATH)
        sells = trades[trades["action"] == "SELL"].copy()
        if len(sells):
            sells["pnl"] = pd.to_numeric(sells["pnl"], errors="coerce")
            closed = sells.dropna(subset=["pnl"])
            print(f"  trades: {len(trades)} fills, {len(closed)} closed, "
                  f"hit rate {(closed['pnl'] > 0).mean():.0%}, "
                  f"total P&L ₹{closed['pnl'].sum():,.0f}")
    print("\n  NOTE: 3-6 months of this is the deployment gate — compare monthly")
    print("  returns against walk_forward.py's distribution before real capital.")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "report":
        report()
    else:
        step()
