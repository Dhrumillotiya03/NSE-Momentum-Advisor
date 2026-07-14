"""
Daily brief — the ONE command to run after powering on the computer.

    python daily_brief.py        (from scripts/)

Prints, in ~2 seconds, everything that needs human eyes:
  - data freshness + whether the automated pipeline/timers are healthy
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
