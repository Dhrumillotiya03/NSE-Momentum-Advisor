"""
PROFIT-TAKING WATCH — DISPLAY ONLY, LOGGED, NEVER WIRED INTO ANY BOOK.

The user rebalances on the last Tuesday and, per the 2026-09-04 mandate,
empties the whole book that day regardless of P&L. In between, they will sell
a name early ONLY if it is in profit — a discretionary call. This module
surfaces candidate profit-taking moments and records every one so the rule
can be scored later against the laggards-only counterfactual (does exiting at
the flag beat holding to the month-end forced sale or the -18% stop?).

HARD RULE — same status as chart_analysis.py / news_watchdog.py:
nothing here may be imported by exit_engine.py, paper_trader.py or
agent_sim.py, and no threshold here is walk-forward validated. Every
price-based intra-month exit tested on this strategy (tight trailing / 50MA,
resistance-fade, ratcheting giveback stop) has been REJECTED. An asymmetric
profit-ONLY rule has not been tested — that is exactly what the log is for.
See PREREG_profit_taking_trigger.md.

Triggers (all require the position to be GREEN):
  BIG_GAIN            gain >= PROFIT_TAKE_PCT
  GIVEBACK_IN_PROFIT  price >= GIVEBACK_FRAC below its high-since-entry, still green
  RESISTANCE_IN_PROFIT  within RES_NEAR_PCT of logged R1 / ATR target, green
  RSI_EXHAUSTION      RSI >= RSI_OVERBOUGHT, green
"""
import csv
import os
from datetime import datetime

import numpy as np
import pandas as pd

import strategy_config as sc
from core import load_stock, compute_rsi, compute_atr, market_regime

LOG_PATH = "../data/profit_exit_log.csv"

PROFIT_TAKE_PCT = 0.25      # "materially up" — a round, deliberately un-tuned number
GIVEBACK_FRAC = 0.12        # gave back 12% from the high-since-entry
RES_NEAR_PCT = 0.015        # within 1.5% of logged resistance
MIN_GREEN = 0.02            # must be at least +2% to count as "in profit"

LOG_COLUMNS = [
    "date", "symbol", "trigger", "entry_date", "entry_price", "price",
    "gain_pct", "high_since_entry", "giveback_from_high_pct", "rsi", "r1",
    "atr_target", "regime", "days_held_approx",
    # forward-outcome columns, filled later by the scorer — NEVER at log time
    "px_5d", "px_21d", "px_next_rebalance", "laggards_would_hold", "scored",
]


def _entry_date(pos):
    raw = str(pos.get("entry_date", "") or "")
    for tok in (raw, raw.replace("imported-", "")):
        try:
            return pd.Timestamp(tok)
        except (ValueError, TypeError):
            continue
    return None


def profit_signals(sym, entry_price, df, cur=None, regime=None,
                   entry_date=None, r1=None):
    """Return a list of triggered profit-taking conditions for one held name.
    Pure/read-only. `cur` defaults to the last close; `r1` is an optional
    logged resistance level (from sr_daily_logger's latest snapshot)."""
    if df is None or len(df) < 30 or not entry_price or entry_price <= 0:
        return []
    price = float(cur) if cur is not None else float(df["Close"].iloc[-1])
    gain = price / entry_price - 1
    if gain < MIN_GREEN:
        return []   # profit-ONLY by construction

    # high since entry — from the entry date if known, else a 63-session window
    if entry_date is not None and entry_date in df.index:
        seg = df.loc[df.index >= entry_date, "High"]
    else:
        seg = df["High"].tail(63)
    hi = float(seg.max()) if len(seg) else price
    giveback = (hi - price) / hi if hi > 0 else 0.0
    days_held = int((df.index[-1] - entry_date).days) if entry_date is not None else -1

    rsi = float(compute_rsi(df["Close"]))
    atr = float(compute_atr(df))
    atr_target = entry_price + 3 * atr

    out = []
    ctx = dict(price=price, gain_pct=gain, high_since_entry=hi,
               giveback_from_high_pct=giveback, rsi=rsi, r1=r1,
               atr_target=atr_target, days_held_approx=days_held)

    if gain >= PROFIT_TAKE_PCT:
        out.append(dict(trigger="BIG_GAIN",
                        detail=f"up {gain:+.0%} since entry — a discretionary trim "
                               f"locks this in ahead of the month-end forced sale",
                        **ctx))
    if giveback >= GIVEBACK_FRAC and gain >= MIN_GREEN + 0.03:
        out.append(dict(trigger="GIVEBACK_IN_PROFIT",
                        detail=f"gave back {giveback:.0%} from its high (₹{hi:.2f}) "
                               f"but still {gain:+.0%} green",
                        **ctx))
    near_res = [lvl for lvl in (r1, atr_target)
               if lvl and abs(price / lvl - 1) <= RES_NEAR_PCT and price <= lvl * (1 + RES_NEAR_PCT)]
    if near_res:
        out.append(dict(trigger="RESISTANCE_IN_PROFIT",
                        detail=f"at resistance ~₹{min(near_res):.2f} while {gain:+.0%} green "
                               f"(momentum names push through ~60% of the time — "
                               f"info, not a rule)",
                        **ctx))
    if rsi >= sc.RSI_OVERBOUGHT:
        out.append(dict(trigger="RSI_EXHAUSTION",
                        detail=f"RSI {rsi:.0f} (>= {sc.RSI_OVERBOUGHT}) while {gain:+.0%} green",
                        **ctx))
    return out


def log_signal(sym, sig, entry_price, entry_date, regime):
    os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
    today = datetime.now().strftime("%Y-%m-%d")
    # dedupe on (date, symbol, trigger)
    if os.path.exists(LOG_PATH):
        try:
            ex = pd.read_csv(LOG_PATH)
            if ((ex["date"] == today) & (ex["symbol"] == sym)
                    & (ex["trigger"] == sig["trigger"])).any():
                return False
        except Exception:
            pass
    row = {c: "" for c in LOG_COLUMNS}
    row.update(date=today, symbol=sym, trigger=sig["trigger"],
               entry_date=str(entry_date.date()) if entry_date is not None else "",
               entry_price=round(entry_price, 2), price=round(sig["price"], 2),
               gain_pct=round(sig["gain_pct"], 4),
               high_since_entry=round(sig["high_since_entry"], 2),
               giveback_from_high_pct=round(sig["giveback_from_high_pct"], 4),
               rsi=round(sig["rsi"], 1),
               r1=round(sig["r1"], 2) if sig.get("r1") else "",
               atr_target=round(sig["atr_target"], 2),
               regime=regime or "", days_held_approx=sig["days_held_approx"],
               scored=0)
    write_header = not os.path.exists(LOG_PATH) or os.path.getsize(LOG_PATH) == 0
    with open(LOG_PATH, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=LOG_COLUMNS)
        if write_header:
            w.writeheader()
        w.writerow(row)
    return True


def check_book(positions, sr_levels=None, quote_fn=None, do_log=True):
    """positions: {sym: {entry_price, entry_date, ...}} (portfolio_state schema).
    quote_fn(sym)->(price, stale) optional (live); falls back to last close.
    Returns a list of (sym, sig) for display. Logs each new one."""
    regime = market_regime()[0]
    sr_levels = sr_levels or {}
    hits = []
    for sym, pos in positions.items():
        entry = float(pos.get("entry_price") or 0)
        if entry <= 0:
            continue
        df = load_stock(sym)
        if df is None:
            continue
        cur = None
        if quote_fn is not None:
            p, stale = quote_fn(sym)
            if p is not None and not stale:
                cur = p
        edt = _entry_date(pos)
        r1 = sr_levels.get(sym, {}).get("R1")
        r1 = float(r1) if r1 is not None and pd.notna(r1) else None
        for sig in profit_signals(sym, entry, df, cur=cur, regime=regime,
                                  entry_date=edt, r1=r1):
            hits.append((sym, sig))
            if do_log:
                log_signal(sym, sig, entry, edt, regime)
    return hits


def main():
    from portfolio_state import load_state
    state = load_state()
    positions = state.get("positions", {})
    try:
        from intraday_watch import latest_sr_levels
        sr = latest_sr_levels()
    except Exception:
        sr = {}
    hits = check_book(positions, sr_levels=sr, do_log=True)
    if not hits:
        print(f"[profit_watch] {len(positions)} held names, no profit-taking flags")
        return
    print(f"[profit_watch] {len(hits)} flag(s) — DISCRETIONARY, display only:\n")
    for sym, s in hits:
        print(f"  {sym.replace('.NS',''):<12} {s['trigger']:<22} {s['detail']}")
    print(f"\n  logged to {LOG_PATH} — scored later vs the laggards-only "
          f"counterfactual (PREREG_profit_taking_trigger.md). Not a signal.")


if __name__ == "__main__":
    main()
