"""
Intraday watcher — live-quote alerts on REAL held names during market hours.

Runs every 15 minutes (systemd timer stockai-intraday, Mon-Fri 09:00-15:45
IST; the script also self-guards). For every real-book position (sleeves,
RCOM and other EXIT_EXCLUDE names skipped), it fetches a live-ish quote
(yfinance, ~15-min delayed — a "12:00" event is visible ~12:15) and alerts:

  STOP  (HIGH)  price below entry x 0.82 — the -18% catastrophic stop is
                the strategy's ONE validated intra-month exit. Previously
                checked only at 14:40 + evenings; a morning crash waited
                hours. This is the actual fix for intraday blindness.
  DROP  (MED)   down >5% vs yesterday's close — sharp move, human review.
  S/R   (INFO)  price crossed yesterday's logged S1 (support broken) or R1
                (resistance reached) from sr_dynamic_log.csv.

HONESTY NOTE on the S/R alert: an automated sell-at-resistance exit was
backtested and REJECTED (momentum names push THROUGH a strong resistance
~58-60% of the time — see strategy_config's exit-engine comment). These
alerts are INFORMATION for the human's discretionary judgment, not a
signal, and must never be wired into exit_engine/paper_trader/agent_sim.

Alerts dedupe per (day, symbol, type) via ../data/intraday_seen.csv, so a
breach alerts once, not every 15 minutes. All events append to
../data/intraday_watch_log.csv. notify-send fires for STOP and DROP.

Run from scripts/ (normally via the systemd timer):
    python intraday_watch.py
"""
import csv
import os
import subprocess
from datetime import datetime

import pandas as pd

import strategy_config as sc
from portfolio_state import load_state
from live_quotes import get_quote

SEEN_PATH = "../data/intraday_seen.csv"
LOG_PATH = "../data/intraday_watch_log.csv"
SR_LOG = "../data/sr_dynamic_log.csv"
DROP_THRESHOLD = -0.05


def market_open_now():
    now = datetime.now()
    if now.weekday() > 4:
        return False
    hhmm = now.hour * 100 + now.minute
    return 915 <= hhmm <= 1545   # 15-min delayed quotes: worth running to 15:45


def watched_positions(state):
    out = {}
    for sym, pos in state["positions"].items():
        if pos.get("entry_price", 0) <= 0:
            continue
        if sym in sc.EXIT_EXCLUDE_SYMBOLS:
            continue
        if any(sym.endswith(suf) for suf in sc.EXIT_EXCLUDE_SUFFIXES):
            continue
        out[sym] = pos
    return out


def prev_close(sym):
    path = f"../data/price_data/{sym}.csv"
    if not os.path.exists(path):
        return None
    df = pd.read_csv(path, usecols=["Close"], low_memory=False)
    c = pd.to_numeric(df["Close"], errors="coerce").dropna()
    return float(c.iloc[-1]) if len(c) else None


def latest_sr_levels():
    """Most recent logged S1/R1 per symbol from the dynamic S/R log."""
    if not os.path.exists(SR_LOG):
        return {}
    df = pd.read_csv(SR_LOG)
    df = df.sort_values("Date").groupby("Symbol").tail(1)
    out = {}
    for _, r in df.iterrows():
        out[str(r["Symbol"]) + ".NS"] = {
            "S1": pd.to_numeric(r.get("S1"), errors="coerce"),
            "R1": pd.to_numeric(r.get("R1"), errors="coerce"),
        }
    return out


def load_seen():
    if not os.path.exists(SEEN_PATH):
        return set()
    with open(SEEN_PATH) as f:
        return {(r["date"], r["symbol"], r["type"]) for r in csv.DictReader(f)}


def mark_seen(rows):
    new = not os.path.exists(SEEN_PATH)
    with open(SEEN_PATH, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["date", "symbol", "type"])
        if new:
            w.writeheader()
        for r in rows:
            w.writerow(r)


def notify(title, body):
    try:
        subprocess.run(["notify-send", "-u", "critical", title, body], timeout=5)
    except Exception:
        pass


def main():
    if not market_open_now():
        print("[intraday] market closed — nothing to do")
        return

    state = load_state()
    positions = watched_positions(state)
    if not positions:
        print("[intraday] no watched positions")
        return

    today = datetime.now().strftime("%Y-%m-%d")
    now_hm = datetime.now().strftime("%H:%M")
    seen = load_seen()
    sr = latest_sr_levels()
    alerts, new_seen = [], []

    for sym, pos in positions.items():
        price, stale = get_quote(sym)
        if price is None or stale:
            continue   # no live quote — CSV fallback is yesterday's close, useless here
        entry = pos["entry_price"]
        pc = prev_close(sym)

        checks = []
        if price < entry * sc.CATASTROPHIC_STOP:
            checks.append(("STOP", "HIGH",
                           f"-18% STOP BREACHED: ₹{price:.2f} vs entry ₹{entry:.2f} "
                           f"({price / entry - 1:+.1%}). Strategy rule: SELL. "
                           f"After the Zerodha fill: python record_fill.py sell "
                           f"{sym.replace('.NS', '')} <FILL_PRICE>"))
        if pc and price / pc - 1 <= DROP_THRESHOLD:
            checks.append(("DROP", "MED",
                           f"sharp intraday drop {price / pc - 1:+.1%} "
                           f"(₹{pc:.2f} -> ₹{price:.2f}) — review manually"))
        lv = sr.get(sym, {})
        if pd.notna(lv.get("R1")) and price >= lv["R1"]:
            checks.append(("SR_R1", "INFO",
                           f"touched logged resistance R1 ₹{lv['R1']:.2f} (now ₹{price:.2f}). "
                           f"INFO ONLY: backtest says momentum names push through ~60% "
                           f"of the time — auto-selling here was tested and rejected"))
        if pd.notna(lv.get("S1")) and price <= lv["S1"]:
            checks.append(("SR_S1", "INFO",
                           f"broke logged support S1 ₹{lv['S1']:.2f} (now ₹{price:.2f}) — "
                           f"informational"))

        for typ, sev, msg in checks:
            key = (today, sym, typ)
            if key in seen:
                continue
            new_seen.append({"date": today, "symbol": sym, "type": typ})
            alerts.append((sev, sym, typ, msg))
            with open(LOG_PATH, "a", newline="") as f:
                w = csv.DictWriter(f, fieldnames=["date", "time", "symbol", "type",
                                                  "severity", "price", "message"])
                if f.tell() == 0:
                    w.writeheader()
                w.writerow({"date": today, "time": now_hm, "symbol": sym, "type": typ,
                            "severity": sev, "price": round(price, 2), "message": msg})

    if new_seen:
        mark_seen(new_seen)

    if not alerts:
        print(f"[intraday] {now_hm}: {len(positions)} names checked, no alerts")
        return
    for sev, sym, typ, msg in alerts:
        print(f"[intraday] [{sev}] {sym} {typ}: {msg}")
    urgent = [a for a in alerts if a[0] in ("HIGH", "MED")]
    if urgent:
        notify("stock_ai INTRADAY ALERT",
               "\n".join(f"{sym}: {msg[:90]}" for _, sym, _, msg in urgent))


if __name__ == "__main__":
    main()
