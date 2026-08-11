"""
Forward paper-trading loop — out-of-sample-in-time evidence accrual.

Runs a simulated ₹10L book under the EXACT live mandate, daily, using only
data available at run time (evening, after the download pipeline).

LAGGARDS-ONLY mechanics (adopted 2026-07-12, matches
backtest_portfolio.run_backtest_laggards_only — was: full liquidation
every month-end):
  - month-end (last trading day): re-score the universe. Names still in the
    new sector-capped top-N are HELD (rebalanced to the new target weight,
    cost on the delta only — skipped if drift is <1% of position value).
    Names dropping out are SOLD. New names not already held are queued and
    bought at the NEXT session's close.
  - daily: -18% catastrophic stop vs today's close (fires on ANY held name,
    carried or freshly bought).
  - sizing: conviction-weighted (strategy_config.CONVICTION_TILT, adopted
    2026-08-05) capped at MAX_WEIGHT, regime exposure — identical to
    backtest_portfolio.run_backtest_laggards_only.
  - costs: COST per side, charged only on the actual delta traded.
  - GOLD SLEEVE (adopted 2026-07-13): GOLD_ALLOC of TOTAL equity held in
    GOLD_SYMBOL (GOLDBEES), rebalanced to target each month-end (1% drift
    band); momentum book runs on the remaining (1 - GOLD_ALLOC) sub-capital.
    Gold is exempt from the -18% stop and the momentum re-qualification —
    matches backtest_portfolio.run_backtest_gold_blend.

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
from backtest_portfolio import select_top_n_capped, load_sector_map, conviction_weights
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


def logged_dates():
    """Every date already present in the equity log — the real idempotency
    record (state['last_run'] only remembers the most recent one)."""
    if not os.path.exists(EQUITY_PATH):
        return set()
    out = set()
    with open(EQUITY_PATH, newline="") as f:
        for row in csv.DictReader(f):
            if row.get("date"):
                out.add(row["date"].strip())
    return out


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
    None if the stock didn't print that day. Falls back to etf_data/ for
    the gold sleeve (GOLDBEES lives there, never in price_data/)."""
    df = load_stock(sym)
    if df is None:
        etf_path = f"../data/etf_data/{sym}.csv"
        if os.path.exists(etf_path):
            df = pd.read_csv(etf_path, parse_dates=["Date"], low_memory=False)
            df = df[pd.to_datetime(df["Date"], errors="coerce").notna()]
            df["Close"] = pd.to_numeric(df["Close"], errors="coerce")
            df = df.dropna(subset=["Close"]).sort_values("Date").set_index("Date")
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
    # last_run alone only catches a repeat of the MOST RECENT date. A clock
    # regression or a backfilled index date re-ran an already-logged day and
    # wrote duplicate/out-of-order rows (2026-07-22 twice, with 07-21 between
    # them). Treat any date already in the equity log as done.
    if today_str in logged_dates():
        print(f"[paper] {today_str} already in equity log — skipping (clock/date regression?)")
        state["last_run"] = today_str
        save_state(state)
        return
    if state["last_run"] and today_str < state["last_run"]:
        print(f"[paper] index date {today_str} is BEFORE last run {state['last_run']} "
              f"— refusing to step backwards")
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

    # 2. -18% stop check at close (ETF sleeves exempt — strategic
    # allocations rebalanced monthly, not momentum bets with a stop)
    for sym in list(state["positions"]):
        if sym in (sc.GOLD_SYMBOL, sc.INTL_SYMBOL):
            continue
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

    # 3. month-end: LAGGARDS-ONLY re-evaluation (adopted 2026-07-12, was
    # full liquidation). Names still in the new top-N are HELD (rebalanced
    # to their new target weight, cost on the delta only) — only names that
    # drop out are sold. Non-strategy holdings (entry_price==0, e.g. any
    # manually-added test position) are left untouched.
    month_end = is_last_trading_day_of_month(pd.Series(index_dates))
    if month_end:
        regime, _ = market_regime()
        n = sc.REGIME_NAMES[regime]
        exposure = sc.REGIME_EXPOSURE[regime]
        eligible = scan_universe()

        strategy_syms = {s for s, p in state["positions"].items()
                         if p.get("entry_price", 0) > 0
                         and s not in (sc.GOLD_SYMBOL, sc.INTL_SYMBOL)}

        if len(eligible) >= n:
            scores = {s: r["score"] for s, r in eligible.items()}
            top = set(select_top_n_capped(scores, n, load_sector_map(), sc.MAX_PER_SECTOR))

            # Staleness warning (2026-08-11). full_advisor screens candidates
            # more than MAX_STALE_SESSIONS behind; scan_universe does NOT, so
            # a name whose CSV stopped updating can still be SELECTED here on
            # an old close. It cannot be filled at a fake price — close_on()
            # returns None for a missing bar, so the order just retries and
            # expires — but it can silently consume one of only n slots.
            # Warn rather than drop: dropping would change strategy behaviour
            # on a transient data condition (the 2026-08 cluster was mid
            # dividend/split adjustment and self-resolving), and this is the
            # gate's evidence book, so a silent selection change is worse
            # than a visible warning. See data/corporate_action_watch.json.
            stale_top = []
            for s in top:
                sdf = load_stock(s)
                if sdf is None or len(sdf) == 0:
                    continue
                behind = int(((index_dates > sdf.index[-1])
                              & (index_dates <= today)).sum())
                if behind > 3:
                    stale_top.append((s, sdf.index[-1].date(), behind))
            if stale_top:
                print("[paper] WARNING: selected name(s) have stale prices — "
                      "they may fail to fill and waste a slot:")
                for s, last, behind in stale_top:
                    print(f"[paper]   {s} last bar {last} ({behind} sessions behind)")
            # Same conviction-weighted sizing as backtest_portfolio's
            # production default (strategy_config.CONVICTION_TILT) — do not
            # re-inline plain inverse-vol, the two copies already drifted
            # once before (see core.momentum_score's 2026-07-17 unification).
            vols = {s: eligible[s]["vol_63"] for s in top}
            raw_w = conviction_weights(scores, vols, list(top), sc.CONVICTION_TILT)
            w = {s: min(v, sc.MAX_WEIGHT) for s, v in raw_w.items()}
            tot2 = sum(w.values())
            w = {s: v / tot2 for s, v in w.items()}

            drop = strategy_syms - top
            keep = strategy_syms & top
            new_names = top - strategy_syms

            for sym in drop:
                pos = state["positions"][sym]
                px = close_on(sym, today) or pos["entry_price"]
                proceeds = pos["qty"] * px * (1 - sc.COST)
                pnl = round(proceeds - pos["qty"] * pos["entry_price"], 2)
                state["cash"] += proceeds
                log_fill(today_str, sym, "SELL", px, pos["qty"], "month-end: dropped out of top-N", pnl)
                del state["positions"][sym]

            sleeves = [(sc.GOLD_SYMBOL, sc.GOLD_ALLOC), (sc.INTL_SYMBOL, sc.INTL_ALLOC)]
            sleeve_val = {}
            for ssym, _alloc in sleeves:
                spos = state["positions"].get(ssym)
                spx = close_on(ssym, today) or (spos["entry_price"] if spos else None)
                sleeve_val[ssym] = (spos["qty"] * spx if (spos and spx) else 0.0, spx)

            total_equity = state["cash"] + sum(v for v, _ in sleeve_val.values()) + sum(
                state["positions"][s]["qty"] * (close_on(s, today) or state["positions"][s]["entry_price"])
                for s in keep)

            # ---- ETF sleeve rebalance (gold + intl, % of TOTAL equity, adopted 2026-07-13) ----
            for ssym, alloc in sleeves:
                cur_val, spx = sleeve_val[ssym]
                if alloc <= 0 or not spx:
                    continue
                spos = state["positions"].get(ssym)
                delta = total_equity * alloc - cur_val
                if abs(delta) < 0.01 * total_equity:
                    continue
                if delta > 0:
                    add_qty = int(delta // (spx * (1 + sc.COST)))
                    if add_qty <= 0:
                        continue
                    state["cash"] -= add_qty * spx * (1 + sc.COST)
                    if spos:
                        new_qty = spos["qty"] + add_qty
                        spos["entry_price"] = (spos["qty"] * spos["entry_price"]
                                               + add_qty * spx) / new_qty
                        spos["qty"] = new_qty
                    else:
                        state["positions"][ssym] = {
                            "qty": add_qty, "entry_price": spx, "entry_date": today_str}
                    log_fill(today_str, ssym, "BUY", spx, add_qty,
                             "ETF sleeve rebalance to target")
                elif spos:
                    trim_qty = min(spos["qty"], int((-delta) // spx))
                    if trim_qty <= 0:
                        continue
                    proceeds = trim_qty * spx * (1 - sc.COST)
                    pnl = round(proceeds - trim_qty * spos["entry_price"], 2)
                    state["cash"] += proceeds
                    spos["qty"] -= trim_qty
                    log_fill(today_str, ssym, "SELL", spx, trim_qty,
                             "ETF sleeve rebalance to target", pnl)
                    if spos["qty"] == 0:
                        del state["positions"][ssym]

            # momentum book runs on the remaining sub-capital, regime
            # exposure unchanged — matches backtest_portfolio.run_backtest_gold_blend
            invest_target = total_equity * (1 - sc.GOLD_ALLOC - sc.INTL_ALLOC) * exposure

            for sym in keep:
                pos = state["positions"][sym]
                px = close_on(sym, today) or pos["entry_price"]
                cur_val = pos["qty"] * px
                target_val = invest_target * w[sym]
                delta = target_val - cur_val
                if abs(delta) < 0.01 * cur_val:
                    continue  # negligible drift, skip the noise trade
                if delta > 0:
                    add_qty = int(delta // px)
                    if add_qty <= 0:
                        continue  # rounds to a zero-share trade, nothing to do
                    cost = add_qty * px * (1 + sc.COST)
                    state["cash"] -= cost
                    new_qty = pos["qty"] + add_qty
                    pos["entry_price"] = (pos["qty"] * pos["entry_price"] + add_qty * px) / new_qty
                    pos["qty"] = new_qty
                    log_fill(today_str, sym, "BUY", px, add_qty, "month-end: weight top-up")
                else:
                    trim_qty = min(pos["qty"], int((-delta) // px))
                    if trim_qty <= 0:
                        continue
                    proceeds = trim_qty * px * (1 - sc.COST)
                    state["cash"] += proceeds
                    pos["qty"] -= trim_qty
                    log_fill(today_str, sym, "SELL", px, trim_qty, "month-end: weight trim")

            state["pending_buys"] = [
                {"sym": s, "rupees": round(invest_target * w[s], 2)} for s in new_names]
            print(f"[paper] month-end (laggards-only): {len(keep)} held, {len(drop)} sold, "
                  f"{len(new_names)} new for {regime} (exposure {exposure:.0%})")
        else:
            state["pending_buys"] = []
            print(f"[paper] month-end: only {len(eligible)} eligible < {n}, no new entries queued")
        mtm_regime = regime
    else:
        mtm_regime = ""

    # 3b. idle cash accrues the liquid-ETF yield (CASH_YIELD, adopted
    # 2026-07-13) — once per trading day, mirrors the backtest engines
    state["cash"] *= (1 + sc.CASH_YIELD) ** (1 / 252)

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
