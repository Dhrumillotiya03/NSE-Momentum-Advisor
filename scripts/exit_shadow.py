"""
Early-exit SHADOW log — testing "sell within the month" without corrupting
the sim.

THE QUESTION IT ANSWERS: the user's standing instinct is that holdings
should sometimes be sold intra-month (take profit at resistance, exit on
trend turn), not only at the month-end re-evaluation. Every price-based
early-exit rule tested so far LOST in walk-forward (tight trailing/50MA
stops ~-0.3 Sharpe, resistance-fade net-negative, announcement veto
rejected) — but those were specific rules on backtest data. This logger
runs FORWARD, on the live sim book, and records every day a holding looks
like a textbook early-exit candidate:

    OVERBOUGHT   RSI(14) > RSI_OVERBOUGHT (80)
    AT_R1        close within 1% of / above the logged S/R resistance R1
    TREND_BREAK  close below the 50DMA
    BIG_GAIN     +10% or more since the sim's entry

It NEVER sells — the sim keeps trading the validated strategy (that's what
the month measures). At month-end, `python exit_shadow.py report` compares
each signal day's price against the position's ACTUAL eventual exit (or
latest close): if "selling on signal" systematically beats holding to the
rotation, that's forward evidence the user's instinct deserves a designed,
walk-forward-tested rule. If not, the instinct is priced and settled with
his own live data.

Run from scripts/ (wired into run_daily_log.sh after agent_sim):
    python exit_shadow.py          # log today's signals
    python exit_shadow.py report   # signal-vs-actual-exit evaluation
"""
import csv
import json
import os
import sys

import numpy as np
import pandas as pd

import strategy_config as sc
from core import compute_rsi

SIM_STATE = "../data/_agent_sim/portfolio_state.json"
SIM_JOURNAL = "../data/_agent_sim/trade_history.csv"
SHADOW_LOG = "../data/_agent_sim/exit_shadow.csv"
SR_LOG = "../data/sr_dynamic_log.csv"
PRICE_DIR = "../data/price_data/"

SLEEVES = {sc.GOLD_SYMBOL, sc.INTL_SYMBOL}
BIG_GAIN = 0.10


def rsi14(close):
    # consolidated to the single canonical RSI (core.compute_rsi) 2026-07-17
    return float(compute_rsi(close))


def latest_r1():
    if not os.path.exists(SR_LOG):
        return {}
    df = pd.read_csv(SR_LOG).sort_values("Date").groupby("Symbol").tail(1)
    return {str(r["Symbol"]) + ".NS": pd.to_numeric(r.get("R1"), errors="coerce")
            for _, r in df.iterrows()}


def step():
    if not os.path.exists(SIM_STATE):
        print("[exit-shadow] no sim book yet")
        return
    with open(SIM_STATE) as f:
        state = json.load(f)
    holdings = {s: p for s, p in state["positions"].items() if s not in SLEEVES}
    if not holdings:
        print("[exit-shadow] no momentum holdings in sim book")
        return

    r1 = latest_r1()
    rows = []
    for sym, pos in holdings.items():
        path = PRICE_DIR + f"{sym}.csv"
        if not os.path.exists(path):
            continue
        df = pd.read_csv(path, usecols=["Date", "Close"], low_memory=False)
        df = df[pd.to_datetime(df["Date"], errors="coerce").notna()]
        df["Close"] = pd.to_numeric(df["Close"], errors="coerce")
        df = df.dropna().sort_values("Date")
        if len(df) < 60:
            continue
        close = df["Close"]
        px = float(close.iloc[-1])
        date = str(df["Date"].iloc[-1])[:10]
        gain = px / pos["entry_price"] - 1

        signals = []
        r = rsi14(close)
        if r > sc.RSI_OVERBOUGHT:
            signals.append("OVERBOUGHT")
        lvl = r1.get(sym)
        if pd.notna(lvl) and px >= 0.99 * lvl:
            signals.append("AT_R1")
        if px < close.tail(50).mean():
            signals.append("TREND_BREAK")
        if gain >= BIG_GAIN:
            signals.append("BIG_GAIN")

        if signals:
            rows.append({"date": date, "symbol": sym, "price": round(px, 2),
                         "gain_since_entry": round(gain, 4),
                         "signals": "+".join(signals)})

    if not rows:
        print("[exit-shadow] no early-exit signals on current holdings")
        return
    # dedupe: one row per (date, symbol)
    seen = set()
    if os.path.exists(SHADOW_LOG):
        prev = pd.read_csv(SHADOW_LOG)
        seen = set(zip(prev["date"], prev["symbol"]))
    new_rows = [r for r in rows if (r["date"], r["symbol"]) not in seen]
    if not new_rows:
        print("[exit-shadow] signals already logged for this date")
        return
    new_file = not os.path.exists(SHADOW_LOG)
    with open(SHADOW_LOG, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["date", "symbol", "price",
                                          "gain_since_entry", "signals"])
        if new_file:
            w.writeheader()
        for r in new_rows:
            w.writerow(r)
    for r in new_rows:
        print(f"[exit-shadow] {r['date']} {r['symbol']}: {r['signals']} at ₹{r['price']} "
              f"({r['gain_since_entry']:+.1%} since entry) — LOGGED, not acted on")


def report():
    if not os.path.exists(SHADOW_LOG):
        print("No early-exit signals logged yet.")
        return
    log = pd.read_csv(SHADOW_LOG)
    journal = pd.read_csv(SIM_JOURNAL) if os.path.exists(SIM_JOURNAL) else pd.DataFrame()

    print(f"EARLY-EXIT SHADOW REPORT — {len(log)} signal-day(s)")
    print("Would selling on the signal have beaten the strategy's actual exit?\n")
    verdicts = []
    for _, s in log.iterrows():
        sells = journal[(journal["symbol"] == s["symbol"]) &
                        (journal["action"] == "SELL") &
                        (journal["date"] > s["date"])] if len(journal) else pd.DataFrame()
        if len(sells):
            exit_px, exit_note = float(sells.iloc[0]["price"]), f"actual exit {sells.iloc[0]['date']}"
        else:
            path = PRICE_DIR + f"{s['symbol']}.csv"
            c = pd.to_numeric(pd.read_csv(path, usecols=["Close"], low_memory=False)["Close"],
                              errors="coerce").dropna()
            exit_px, exit_note = float(c.iloc[-1]), "still held (latest close)"
        edge = s["price"] / exit_px - 1   # >0: selling at signal beat the actual exit
        verdicts.append(edge)
        print(f"  {s['date']} {s['symbol'].replace('.NS',''):12s} [{s['signals']}] "
              f"signal ₹{s['price']:.2f} vs {exit_note} ₹{exit_px:.2f} -> "
              f"early exit {'WON' if edge > 0.005 else 'LOST' if edge < -0.005 else 'neutral'} "
              f"({edge:+.1%})")
    if verdicts:
        v = np.array(verdicts)
        print(f"\n  SUMMARY: early exit won {int((v > 0.005).sum())}/{len(v)}, "
              f"mean edge {v.mean():+.1%}")
        print("  (Forward evidence only — a designed rule still needs walk-forward")
        print("   before it touches the strategy. History says holding wins; this")
        print("   is the user's instinct getting a fair, live-data hearing.)")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "report":
        report()
    else:
        step()
