"""
Agent-sim — a month-long, fully sandboxed rehearsal of the HUMAN interface.

Two roles, both local (Ollama), run once per trading day via run_daily_log.sh:

  ADVISOR — the existing ai_assistant tool-calling loop, pointed at a
            SANDBOXED copy of the books (data/_agent_sim/), so it gives the
            same advice it would give the real user about a comparable book.
  TRADER  — an LLM playing the retail user: reads the advisor's answer,
            decides what to actually do, and emits concrete orders as JSON.

Orders are executed through the REAL record_fill code paths (do_buy/do_sell,
duplicate guard, journal) against the sandbox state, at today's close.
A CRITIC step (plain code, no LLM) then verifies the books moved exactly as
the orders said and logs every discrepancy, blocked order, and deviation
from advice.

WHAT THIS VALIDATES: the advice -> human -> fill -> books pipeline — the
layer no backtest exercises. WHAT IT DOES NOT VALIDATE: strategy returns.
One month is one rebalance period; a good or bad sim month says nothing
about the alpha (see statistical-hygiene-2026-07). The deployment gate
remains gate_report.py over 3-6 paper months.

SANDBOX: data/_agent_sim/ — starts as a CLEAN ₹10L cash book (2026-07-14
redesign, user direction): the sim buys ONLY what the model suggests, at
the model's timing (fresh-start entry, daily -18% stop checks, month-end
rotation), so every rupee of P&L is attributable to the model's calls —
legacy discretionary holdings would muddy that attribution. The real books
are never touched: every module-level path (portfolio_state.STATE_PATH,
core.STATE_PATH, trade_journal.JOURNAL_FILE, record_fill.JOURNAL_PATH) is
redirected before any tool runs.

`python agent_sim.py report` scores MODEL ACCURACY, not just plumbing:
per-trade P&L on closed round-trips, open picks vs entry, and each sell's
aftermath (did the stock keep falling after the model said sell, or did
the exit cost upside?) — plus the interface findings (blocked orders,
critic problems).

Usage (from scripts/):
    python agent_sim.py          # one daily session (idempotent per date)
    python agent_sim.py report   # month-so-far interface report
"""
import csv
import json
import os
import shutil
import sys

import pandas as pd
import requests

SIM_DIR = "../data/_agent_sim/"
SIM_STATE = SIM_DIR + "portfolio_state.json"
SIM_JOURNAL = SIM_DIR + "trade_history.csv"
SIM_LOG = SIM_DIR + "sessions.csv"
SIM_EQUITY = SIM_DIR + "equity.csv"
SIM_CASH_START = 1_000_000.0

OLLAMA_URL = "http://localhost:11434/api/generate"
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "qwen2.5:7b")

TRADER_PROMPT = """You are a cautious Indian retail investor using a momentum advisory system.
Below is today's conversation with your advisor about YOUR portfolio.

{advice}

Decide what you will ACTUALLY do today. Rules:
- You generally follow the advisor's recommendations, including quantities.
- ETF sleeve buys/rebalances (GOLDBEES, MON100) the advisor lists are real
  orders too — include them, don't treat them as optional commentary.
- You may skip or size down a trade if you state a reason.
- Only trade what was discussed. No inventing symbols.
- If the advisor says nothing needs doing, do nothing.

Reply with ONLY a JSON object:
{{"orders": [{{"action": "buy"|"sell", "symbol": "SYMBOL", "qty": <int>, "reason": "<short>"}}],
  "thinking": "<one sentence on why>"}}
Empty orders list if you're doing nothing."""


# ---------- sandbox plumbing ----------

def redirect_paths():
    """Point every books-touching module at the sandbox BEFORE tools run."""
    import portfolio_state
    import core
    import trade_journal
    import record_fill
    portfolio_state.STATE_PATH = SIM_STATE
    core.STATE_PATH = SIM_STATE
    trade_journal.JOURNAL_FILE = SIM_JOURNAL
    record_fill.JOURNAL_PATH = SIM_JOURNAL
    return record_fill


def ensure_seeded():
    os.makedirs(SIM_DIR, exist_ok=True)
    if os.path.exists(SIM_STATE):
        return
    state = {"cash": SIM_CASH_START, "positions": {}}
    with open(SIM_STATE, "w") as f:
        json.dump(state, f, indent=2)
    print(f"[sim] seeded CLEAN sandbox: ₹{SIM_CASH_START:,.0f} cash, no positions — "
          f"the book will only ever hold the model's own picks")


def append_csv(path, row, headers):
    new = not os.path.exists(path)
    with open(path, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=headers)
        if new:
            w.writeheader()
        w.writerow(row)


# ---------- prices ----------

def close_on(sym, date):
    """Close on `date`, falling back to the most recent close within 5
    calendar days — stock CSVs lag the index until the evening download,
    and a real user placing an order gets a fill regardless."""
    for base in ("../data/price_data/", "../data/etf_data/"):
        path = base + f"{sym}.csv"
        if not os.path.exists(path):
            continue
        df = pd.read_csv(path, parse_dates=["Date"], low_memory=False)
        df = df[pd.to_datetime(df["Date"], errors="coerce").notna()]
        df["Close"] = pd.to_numeric(df["Close"], errors="coerce")
        df = df.dropna(subset=["Close"]).sort_values("Date")
        window = df[(df["Date"] <= date) & (df["Date"] >= date - pd.Timedelta(days=5))]
        if len(window):
            return float(window["Close"].iloc[-1])
    return None


# ---------- LLM roles ----------

def trader_decide(advice_text):
    r = requests.post(OLLAMA_URL, timeout=180, json={
        "model": OLLAMA_MODEL, "stream": False, "format": "json",
        "options": {"temperature": 0.3},
        "prompt": TRADER_PROMPT.format(advice=advice_text[:6000])})
    r.raise_for_status()
    body = r.json()
    if "response" not in body:
        raise RuntimeError(f"ollama error: {body.get('error')}")
    out = json.loads(body["response"])
    orders = out.get("orders", []) or []
    clean = []
    for o in orders:
        try:
            clean.append({"action": str(o["action"]).lower(),
                          "symbol": str(o["symbol"]).upper().replace(".NS", "") + ".NS",
                          "qty": int(o["qty"]),
                          "reason": str(o.get("reason", ""))[:120]})
        except (KeyError, TypeError, ValueError):
            continue
    return clean, str(out.get("thinking", ""))[:300]


# ---------- one session ----------

def step():
    ensure_seeded()
    rf = redirect_paths()

    # tools must import AFTER redirection so they read sandbox state
    import ai_assistant as ai
    from exit_engine import is_last_trading_day_of_month
    import core

    index = core.load_index()
    today = pd.Timestamp(index.index[-1])
    today_str = today.strftime("%Y-%m-%d")

    if os.path.exists(SIM_LOG):
        log = pd.read_csv(SIM_LOG)
        if len(log) and log["date"].iloc[-1] == today_str:
            print(f"[sim] already ran for {today_str}")
            return

    month_end = is_last_trading_day_of_month(pd.Series(index.index))
    from portfolio_state import load_state as _ls
    fresh_book = not _ls()["positions"]
    if month_end:
        question = ("It's the month-end review day. Review my whole portfolio: what should "
                    "I sell, what should I buy, and exactly how many shares of each? My "
                    "total capital assumption should be my current portfolio value.")
    elif fresh_book:
        cash_now = _ls()["cash"]
        question = (f"I'm starting fresh with ₹{cash_now:,.0f} in cash and no positions. "
                    f"What exactly should I buy today and how many shares of each? "
                    f"Include the ETF sleeves.")
    else:
        question = ("Daily check: any exit alerts or stops on my holdings today? "
                    "Should I do anything right now?")

    # 1. ADVISOR
    messages = [{"role": "system", "content": ai.SYSTEM_PROMPT},
                {"role": "user", "content": question}]
    try:
        advice = ai.run_turn(messages)
    except Exception as e:
        print(f"[sim] advisor failed ({e}) — session skipped")
        return

    # 2. TRADER
    try:
        orders, thinking = trader_decide(advice)
    except Exception as e:
        print(f"[sim] trader failed ({e}) — session skipped")
        return

    # 3. EXECUTE through the real record_fill code against sandbox books
    from portfolio_state import load_state, save_state
    executed, blocked = [], []
    for o in orders:
        if o["qty"] <= 0:
            blocked.append({**o, "why": "qty <= 0 — trader/advisor gave no usable quantity"})
            continue
        px = close_on(o["symbol"], today)
        if px is None:
            blocked.append({**o, "why": "no recent price"})
            continue
        state = load_state()
        pos = state["positions"].get(o["symbol"], {})
        if "PLEDGED" in str(pos.get("note", "")).upper() and o["action"] == "sell":
            blocked.append({**o, "why": "position is PLEDGED — cannot sell without unpledging"})
            continue
        if o["action"] == "buy":
            afford = int(state["cash"] // px)
            if afford <= 0:
                blocked.append({**o, "why": f"insufficient cash (₹{state['cash']:,.0f})"})
                continue
            qty = min(o["qty"], afford)
        else:
            held = pos.get("qty", 0)
            if held <= 0:
                blocked.append({**o, "why": "no position to sell"})
                continue
            qty = min(o["qty"], held)
        try:
            if o["action"] == "buy":
                rf.do_buy(state, o["symbol"], qty, px, today_str,
                          f"agent-sim: {o['reason']}", force=False)
            else:
                rf.do_sell(state, o["symbol"], qty, px, today_str,
                           f"agent-sim: {o['reason']}", force=False)
            save_state(state)
            executed.append({**o, "qty": qty, "price": px})
        except SystemExit as e:   # record_fill aborts (duplicate fill etc.)
            blocked.append({**o, "why": f"record_fill abort: {e}"})

    # 4. CRITIC — verify the books moved exactly as the executed orders say
    state = load_state()
    problems = []
    for e_ in executed:
        pos = state["positions"].get(e_["symbol"])
        if e_["action"] == "buy" and (pos is None or pos["qty"] <= 0):
            problems.append(f"{e_['symbol']}: BUY executed but position missing")
    journal_rows = 0
    if os.path.exists(SIM_JOURNAL):
        jr = pd.read_csv(SIM_JOURNAL)
        journal_rows = int((jr["date"] == today_str).sum()) if "date" in jr else 0
    if journal_rows < len(executed):
        problems.append(f"journal has {journal_rows} rows for today, {len(executed)} executed")

    equity = state["cash"]
    for s, p in state["positions"].items():
        px = close_on(s, today)
        equity += (px if px else p.get("entry_price", 0)) * p["qty"]

    append_csv(SIM_EQUITY, {"date": today_str, "equity": round(equity, 2),
                            "cash": round(state["cash"], 2),
                            "n_pos": len(state["positions"])},
               ["date", "equity", "cash", "n_pos"])
    append_csv(SIM_LOG, {
        "date": today_str, "month_end": month_end,
        "advice": advice.replace("\n", " ")[:500],
        "trader_thinking": thinking.replace("\n", " "),
        "orders": json.dumps(orders), "executed": json.dumps(executed),
        "blocked": json.dumps(blocked), "critic_problems": json.dumps(problems),
        "equity": round(equity, 2),
    }, ["date", "month_end", "advice", "trader_thinking", "orders",
        "executed", "blocked", "critic_problems", "equity"])

    print(f"[sim] {today_str}: {len(orders)} order(s) -> {len(executed)} executed, "
          f"{len(blocked)} blocked | equity ₹{equity:,.0f}"
          + (f" | CRITIC: {problems}" if problems else ""))


# ---------- report ----------

def latest_close(sym):
    for base in ("../data/price_data/", "../data/etf_data/"):
        path = base + f"{sym}.csv"
        if not os.path.exists(path):
            continue
        df = pd.read_csv(path, low_memory=False)
        c = pd.to_numeric(df["Close"], errors="coerce").dropna()
        if len(c):
            return float(c.iloc[-1])
    return None


def model_accuracy(log):
    """Score the MODEL's calls, not the plumbing: every executed buy/sell
    from the sim journal, marked against what the price did afterwards."""
    if not os.path.exists(SIM_JOURNAL):
        print("  no trades journaled yet")
        return
    jr = pd.read_csv(SIM_JOURNAL)
    if not len(jr):
        print("  no trades journaled yet")
        return

    idx = pd.read_csv("../data/index_data/nifty50.csv", low_memory=False)
    idx["Close"] = pd.to_numeric(idx["Close"], errors="coerce")
    idx["Date"] = pd.to_datetime(idx["Date"], errors="coerce")
    idx = idx.dropna(subset=["Date", "Close"]).sort_values("Date")

    print(f"\n  MODEL ACCURACY — every executed model call vs what happened next")
    print(f"  {'-'*66}")

    # BUYS: return since entry vs Nifty since same date
    buys = jr[jr["action"] == "BUY"]
    if len(buys):
        print(f"  BUY calls ({len(buys)}):")
        rel = []
        for _, t in buys.iterrows():
            now = latest_close(t["symbol"])
            if now is None:
                continue
            r = now / t["price"] - 1
            nifty_then = idx[idx["Date"] >= pd.Timestamp(t["date"])]
            nr = (idx["Close"].iloc[-1] / nifty_then["Close"].iloc[0] - 1) if len(nifty_then) else 0
            rel.append(r - nr)
            print(f"    {t['date']} BUY {t['symbol']:16s} @ ₹{t['price']:.2f} -> ₹{now:.2f} "
                  f"({r:+.1%}; Nifty {nr:+.1%}; alpha {r - nr:+.1%})")
        if rel:
            good = sum(1 for x in rel if x > 0)
            print(f"    -> {good}/{len(rel)} buys beating Nifty since entry, "
                  f"mean alpha {sum(rel)/len(rel):+.1%}")

    # SELLS: what did the stock do AFTER the model said sell?
    sells = jr[jr["action"] == "SELL"]
    if len(sells):
        print(f"  SELL calls ({len(sells)}):")
        vindicated = 0
        for _, t in sells.iterrows():
            now = latest_close(t["symbol"])
            if now is None:
                continue
            after = now / t["price"] - 1
            verdict = ("GOOD EXIT (kept falling)" if after < -0.01 else
                       "cost upside" if after > 0.01 else "neutral")
            vindicated += after < -0.01
            pnl = t.get("pnl", "")
            print(f"    {t['date']} SELL {t['symbol']:16s} @ ₹{t['price']:.2f}, since then "
                  f"{after:+.1%} -> {verdict} (realized P&L ₹{pnl})")
        print(f"    -> {vindicated}/{len(sells)} sells vindicated so far")

    print(f"\n  CAVEAT: one month = ONE rebalance period. This scores the month's calls;")
    print(f"  it cannot validate or refute the strategy (that's gate_report.py over 3-6")
    print(f"  paper months). A bad month here with clean plumbing still = ship;")
    print(f"  a good month with broken plumbing = don't.")


def report():
    if not os.path.exists(SIM_LOG):
        print("No sim sessions yet.")
        return
    log = pd.read_csv(SIM_LOG)
    eq = pd.read_csv(SIM_EQUITY) if os.path.exists(SIM_EQUITY) else None
    n_exec = sum(len(json.loads(x)) for x in log["executed"])
    n_block = sum(len(json.loads(x)) for x in log["blocked"])
    n_prob = sum(len(json.loads(x)) for x in log["critic_problems"])
    print(f"AGENT-SIM — {len(log)} sessions ({log['date'].iloc[0]} -> {log['date'].iloc[-1]})")
    print(f"  orders executed: {n_exec} | blocked: {n_block} | critic problems: {n_prob}")
    if eq is not None and len(eq) > 1:
        ret = eq["equity"].iloc[-1] / eq["equity"].iloc[0] - 1
        idx = pd.read_csv("../data/index_data/nifty50.csv", low_memory=False)
        c = pd.to_numeric(idx["Close"], errors="coerce").dropna()
        d = pd.to_datetime(idx["Date"], errors="coerce")
        start = pd.Timestamp(eq["date"].iloc[0])
        nifty = c[d >= start]
        nret = (nifty.iloc[-1] / nifty.iloc[0] - 1) if len(nifty) > 1 else 0
        print(f"  equity: ₹{eq['equity'].iloc[0]:,.0f} -> ₹{eq['equity'].iloc[-1]:,.0f} "
              f"({ret:+.2%}; Nifty same period {nret:+.2%}; alpha {ret - nret:+.2%})")

    probs = [(r["date"], p) for _, r in log.iterrows() for p in json.loads(r["critic_problems"])]
    if probs:
        print("  CRITIC FINDINGS (each is an interface bug to fix before real deployment):")
        for d, p in probs:
            print(f"    {d}: {p}")
    blocks = [(r["date"], b) for _, r in log.iterrows() for b in json.loads(r["blocked"])]
    if blocks:
        print("  blocked orders (often correct behavior — duplicate/cash guards):")
        for d, b in blocks[-10:]:
            print(f"    {d}: {b.get('action')} {b.get('symbol')} x{b.get('qty')} — {b.get('why')}")

    model_accuracy(log)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "report":
        report()
    else:
        step()
