"""
Daily brief — the ONE command to run after powering on the computer.

    python daily_brief.py        (from scripts/)

Prints, in ~2 seconds, everything that needs human eyes:
  - data freshness + whether the automated pipeline/timers are healthy
  - book analytics: holdings with weights/P&L, sector exposure, and
    book-vs-Nifty since the 2026-07-14 import (closes-based, read-only)
  - intraday alerts on your holdings today/yesterday (stop breaches, drops)
  - news-watchdog hits on held names (last 3 days)
  - scanner QUALIFIES flags (strategy-grade movers) from the last session
  - agent-sim: last session, equity, what it traded
  - next month-end (the strategy's decision day)

Everything else is automatic (systemd timers download data, run the sim,
watch the market). This is a read-only status page — no books touched.
"""
import glob
import json
import os
import subprocess
from datetime import datetime

import pandas as pd


def section(title):
    print(f"\n--- {title} " + "-" * max(0, 50 - len(title)))


IMPORT_DATE = "2026-07-14"   # real Zerodha book imported at this date's closes


def close_at(sym, date=None):
    """Last close on/before `date` (None = latest) from price/etf CSVs."""
    for d in ("../data/price_data", "../data/etf_data"):
        path = f"{d}/{sym}.csv"
        if os.path.exists(path):
            df = pd.read_csv(path, usecols=["Date", "Close"], low_memory=False)
            dates = pd.to_datetime(df["Date"], errors="coerce")
            close = pd.to_numeric(df["Close"], errors="coerce")
            s = pd.Series(close.values, index=dates).dropna().sort_index()
            if date is not None:
                s = s[s.index <= date]
            return float(s.iloc[-1]) if len(s) else None
    return None


def book_analytics():
    """Holdings table, sector exposure, book-vs-Nifty since import."""
    try:
        state = json.load(open("../data/portfolio_state.json"))
    except Exception as e:
        print(f"could not read portfolio_state.json: {e}")
        return
    positions = state.get("positions", {})
    if not positions:
        print("no positions")
        return

    try:
        sec_map = {}
        for sector, syms in json.load(open("../data/sectors.json")).items():
            for s in syms:
                sec_map[s] = sector
    except Exception:
        sec_map = {}

    rows, total_val, total_inv = [], 0.0, 0.0
    for sym, pos in positions.items():
        last = close_at(sym)
        qty, entry = pos.get("qty", 0), pos.get("entry_price", 0)
        val = qty * last if last else qty * entry
        sector = sec_map.get(sym) or (
            "ETF" if os.path.exists(f"../data/etf_data/{sym}.csv") else "—")
        rows.append((sym, qty, entry, last, val, sector, pos.get("note", "")))
        total_val += val
        total_inv += qty * entry

    cash = state.get("cash", 0.0)
    equity = total_val + cash
    print(f"equity ₹{equity:,.0f}  (positions ₹{total_val:,.0f} + cash ₹{cash:,.0f})"
          f"  |  unrealized P&L ₹{total_val - total_inv:+,.0f} "
          f"({total_val / total_inv - 1:+.1%} vs avg entry)")

    rows.sort(key=lambda r: -r[4])
    for sym, qty, entry, last, val, sector, note in rows:
        pnl = f"{last / entry - 1:+.1%}" if last and entry else "   ?"
        flag = f"  [{note[:36]}]" if note else ""
        print(f"  {sym.replace('.NS', ''):12s} {qty:6d} @ ₹{entry:9.2f}  now "
              f"{'₹%9.2f' % last if last else '        ?'}  {val / equity:5.1%}  "
              f"{pnl:>7s}  {sector[:18]}{flag}")

    sec_w = {}
    for sym, qty, entry, last, val, sector, note in rows:
        sec_w[sector] = sec_w.get(sector, 0) + val / equity
    top = sorted(sec_w.items(), key=lambda x: -x[1])
    print("sector exposure: " + "  ".join(f"{s} {w:.0%}" for s, w in top[:6])
          + ("  (strategy cap is 2 names/sector on NEW buys)" if top and top[0][1] > 0.3 else ""))

    # book vs Nifty since the import date (both marked at CSV closes)
    try:
        v0 = sum(pos.get("qty", 0) * (close_at(sym, IMPORT_DATE) or pos.get("entry_price", 0))
                 for sym, pos in positions.items())
        idx = pd.read_csv("../data/index_data/nifty50.csv", low_memory=False)
        d = pd.to_datetime(idx["Date"], errors="coerce")
        c = pd.to_numeric(idx["Close"], errors="coerce")
        s = pd.Series(c.values, index=d).dropna().sort_index()
        n0, n1 = float(s[s.index <= IMPORT_DATE].iloc[-1]), float(s.iloc[-1])
        if v0 > 0:
            print(f"since import {IMPORT_DATE}: book {total_val / v0 - 1:+.2%}  "
                  f"vs Nifty {n1 / n0 - 1:+.2%}")
    except Exception:
        pass


def main():
    now = datetime.now()
    print(f"STOCK_AI DAILY BRIEF — {now.strftime('%A %d %b %Y, %H:%M')}")

    # data freshness + automation health
    section("system")
    try:
        d = pd.to_datetime(pd.read_csv("../data/index_data/nifty50.csv",
                                       low_memory=False)["Date"], errors="coerce").dropna()
        age = (pd.Timestamp.today().normalize() - d.max().normalize()).days
        flag = "" if age <= 4 else "  <-- STALE: pipeline hasn't run; check timers"
        print(f"data as of {d.max().date()} ({age}d old){flag}")
    except Exception as e:
        print(f"could not read index data: {e}")
    try:
        out = subprocess.run(["systemctl", "--user", "list-timers", "--no-pager"],
                             capture_output=True, text=True, timeout=5).stdout
        for line in out.splitlines():
            if "stockai" in line:
                print("timer: " + " ".join(line.split()[:4]) + "  " + line.split()[-2])
    except Exception:
        print("(could not query systemd timers)")

    # book analytics (weights, sectors, vs Nifty)
    section("your book — analytics")
    book_analytics()

    # intraday alerts on holdings
    section("your holdings — intraday alerts (today/yesterday)")
    path = "../data/intraday_watch_log.csv"
    shown = 0
    if os.path.exists(path):
        log = pd.read_csv(path)
        recent = log[pd.to_datetime(log["date"]) >= now.strftime("%Y-%m-%d")]
        if not len(recent):
            recent = log.tail(5)
        for _, r in recent.iterrows():
            print(f"[{r['severity']}] {r['date']} {r['time']} {r['symbol']}: {r['message'][:90]}")
            shown += 1
    if not shown:
        print("none — no stop breaches or sharp drops")

    # news watchdog
    section("news watchdog — held names (last 3 days)")
    path = "../data/news_watchdog_seen.csv"
    shown = 0
    if os.path.exists(path):
        log = pd.read_csv(path)
        log = log[log["severity"].isin(["HIGH", "MEDIUM"])]
        cutoff = now - pd.Timedelta(days=3)
        for _, r in log.tail(50).iterrows():
            try:
                if pd.to_datetime(r["date"], dayfirst=True, errors="coerce") < cutoff:
                    continue
            except Exception:
                pass
            print(f"[{r['severity']}] {r['symbol']} ({r['date']}): {str(r['desc'])[:80]}")
            shown += 1
    if not shown:
        print("nothing dangerous flagged")

    # scanner finds
    section("scanner — strategy-grade movers (last session)")
    path = "../data/scanner_log.csv"
    shown = 0
    if os.path.exists(path):
        log = pd.read_csv(path)
        last_day = log["date"].max()
        best = log[(log["date"] == last_day) & (log["verdict"] == "QUALIFIES")]
        for _, r in best.iterrows():
            print(f"{r['symbol'].replace('.NS', ''):12s} {r['day_change']:+.1%} "
                  f"[{r['flags']}] score {r['score']} (cutoff {r['topn_cutoff']})")
            shown += 1
        if not shown and len(log[log["date"] == last_day]):
            print(f"({len(log[log['date'] == last_day])} flags on {last_day}, "
                  f"none strategy-grade — see data/scanner_log.csv)")
            shown = 1
    if not shown:
        print("no flags yet")

    # agent-sim
    section("agent-sim (the 1-month model test)")
    spath = "../data/_agent_sim/sessions.csv"
    if os.path.exists(spath):
        log = pd.read_csv(spath)
        last = log.iloc[-1]
        eq0 = pd.read_csv("../data/_agent_sim/equity.csv")["equity"]
        print(f"{len(log)} sessions | last {last['date']} "
              f"(month_end={last['month_end']}) | equity ₹{eq0.iloc[-1]:,.0f} "
              f"({eq0.iloc[-1] / eq0.iloc[0] - 1:+.2%} since start)")
        ex = json.loads(last["executed"])
        if ex:
            for o in ex:
                print(f"  last session traded: {o['action'].upper()} {o['symbol']} "
                      f"x{o['qty']} @ ₹{o['price']:.2f}")
        else:
            print("  last session: no trades (as advised)")
        probs = json.loads(last["critic_problems"])
        if probs:
            print(f"  !! critic problems: {probs}")
    else:
        print("no sessions yet")

    # next decision day
    section("next strategy decision")
    try:
        d = pd.to_datetime(pd.read_csv("../data/index_data/nifty50.csv",
                                       low_memory=False)["Date"], errors="coerce").dropna()
        last = d.max()
        nxt = (last + pd.offsets.BMonthEnd(0 if last != last + pd.offsets.BMonthEnd(0) else 1))
        print(f"month-end rebalance (sells + new book): ~{nxt.date()} — the sim handles")
        print(f"itself; for YOUR real book, run 'python exit_engine.py' that evening")
    except Exception:
        pass

    print("\nOptional deep-dives: python agent_sim.py report | python market_scanner.py report")
    print("Ask anything:        python ai_assistant.py")


if __name__ == "__main__":
    main()
