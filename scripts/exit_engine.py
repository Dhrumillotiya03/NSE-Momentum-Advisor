"""
Per-position exit check, LAGGARDS-ONLY monthly re-evaluation model.

ADOPTED 2026-07-12 (was: hard close — sell EVERYTHING at month-end, re-buy
next session). The book is FULLY RE-EVALUATED at every month-end (last
trading day) — the user's mandate is no INTER-month drift (a position
silently carried 2-3 months with no re-evaluation), not a forced full
liquidation. A name that STILL ranks in the new sector-capped top-N is
HELD (no sell, no re-buy, no realized gain/tax event) — only its target
WEIGHT may need a top-up/trim, which record_fill.py handles as a buy/sell
of the delta. A name that drops OUT of the new top-N is sold. This matches
backtest_portfolio.run_backtest_laggards_only exactly (see
monthly-close-cost-2026-07: saves ~3pp/yr net CAGR, mostly fewer taxable
events, vs the old hard-close engine).

Priority order for intra-month checks (first match wins):
  (a) Catastrophic stop — always on. price < entry * CATASTROPHIC_STOP,
      checked against the LIVE quote when available (live_quotes.py,
      ~15-min delayed), falling back to the last downloaded close.
  (b) Month-end re-evaluation — on the last trading day, every strategy
      position is re-scored. Re-qualifying names are HELD (flagged, not a
      sell signal); non-re-qualifying names are a SELL.

(Early intra-month exits are permitted by the user — the mandate only forbids
holding ACROSS month boundaries; month-end flat is the maximum hold. The
engine still holds to month-end because every tested early-exit rule was
price-based and lost in walk-forward: tight trailing/50MA exits cost ~0.3
Sharpe, a resistance-fade rule was net-negative. Non-price exit overlays
on holdings are untested and open — any new rule needs walk-forward evidence
before it lands here.)

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
    Returns None if it re-qualifies (HOLD — no trade needed);
    otherwise a reason string explaining why it must be sold."""
    r = compute_score(df)
    if r is None:
        return "does NOT re-qualify: fails the momentum filter (SELL, no re-buy)"
    if symbol not in top_n_symbols:
        return (f"does NOT re-qualify: eligible but outside top-{len(top_n_symbols)} "
                f"for current regime ({regime}) (SELL, no re-buy)")
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
        # sector-capped greedy selection — the SAME rule the backtest
        # validates (a plain ranked[:n] here let live books breach the
        # 2-per-sector cap the backtest enforces)
        from backtest_portfolio import select_top_n_capped, load_sector_map
        scores_only = {s: r["score"] for s, r in eligible_scores.items()}
        top_n_symbols = set(select_top_n_capped(
            scores_only, n_names, load_sector_map(), sc.MAX_PER_SECTOR))
        print(f"\n(MONTH-END: full re-evaluation, laggards-only — re-qualifying names "
              f"held, drop-outs sold, fresh top-{n_names} for regime {regime})")

    exit_list = []   # (symbol, reasons, live_price)
    hold_list = []   # (symbol,) — re-qualified, no trade needed
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
                hold_list.append(sym)
            else:
                reasons.append(f"Month-end re-evaluation — {requal}")

        if reasons:
            exit_list.append((sym, reasons, live_price))

    if flag_list:
        print("\nFLAGGED FOR MANUAL REVIEW (excluded from auto-exit):")
        for sym, reason in flag_list:
            print(f"  {sym}: {reason}")

    if month_end and eligible_scores:
        print(f"\nNEW BOOK for next session (top-{n_names} sector-capped, {regime} regime):")
        ranked = sorted(top_n_symbols, key=lambda s: eligible_scores[s]["score"], reverse=True)
        for sym in ranked:
            tag = " [ALREADY HELD — no trade]" if sym in hold_list else " [NEW]"
            print(f"  {sym:16s} score {eligible_scores[sym]['score']:.2f}{tag}")

        new_names = top_n_symbols - set(hold_list)
        if new_names:
            print(f"\n  BUY (new entries, not currently held):")
            for sym in sorted(new_names, key=lambda s: eligible_scores[s]["score"], reverse=True):
                print(f"    python record_fill.py buy {sym.replace('.NS', '')} <QTY> <FILL_PRICE>")

        print(f"\n  All held names still in the new top-N are UNCHANGED (laggards-only —")
        print(f"  no sell+rebuy). If a held name's weight has drifted from its inverse-vol")
        print(f"  target, a manual top-up/trim is optional, not required by the mandate.")

        # ---- Gold sleeve rebalance (GOLD_ALLOC of TOTAL equity, adopted 2026-07-13) ----
        from portfolio_state import portfolio_value
        total_eq = portfolio_value(state)
        gold_target = total_eq * sc.GOLD_ALLOC
        gold_pos = positions.get(sc.GOLD_SYMBOL, {})
        gold_qty = gold_pos.get("qty", 0)
        gold_px, gold_stale = get_quote(sc.GOLD_SYMBOL)
        if gold_px is None:
            etf_path = f"../data/etf_data/{sc.GOLD_SYMBOL}.csv"
            if os.path.exists(etf_path):
                gdf = pd.read_csv(etf_path, parse_dates=["Date"], low_memory=False)
                gdf["Close"] = pd.to_numeric(gdf["Close"], errors="coerce")
                gold_px = gdf.dropna(subset=["Close"]).sort_values("Date")["Close"].iloc[-1]
        print(f"\n  GOLD SLEEVE ({sc.GOLD_SYMBOL}, target {sc.GOLD_ALLOC:.0%} of total equity):")
        if gold_px is None:
            print(f"    no price available for {sc.GOLD_SYMBOL} — resolve manually")
        else:
            gold_val = gold_qty * gold_px
            delta = gold_target - gold_val
            print(f"    total equity ₹{total_eq:,.0f} -> target ₹{gold_target:,.0f} | "
                  f"held {gold_qty} x ₹{gold_px:.2f} = ₹{gold_val:,.0f} | delta ₹{delta:+,.0f}")
            if abs(delta) < 0.01 * total_eq:
                print(f"    within 1% drift band — no trade needed")
            else:
                side = "buy" if delta > 0 else "sell"
                qty = int(abs(delta) / gold_px)
                print(f"    {side.upper()} ~{qty} units, then record the actual fill:")
                print(f"      python record_fill.py {side} {sc.GOLD_SYMBOL.replace('.NS','')} "
                      f"{qty if side == 'buy' else ''} <FILL_PRICE>".rstrip())

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
