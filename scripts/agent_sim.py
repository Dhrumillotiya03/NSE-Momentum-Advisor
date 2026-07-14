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

SANDBOX: data/_agent_sim/ — seeded on first run from the real
portfolio_state.json (same positions, sim cash ₹5,00,000). The real books
are never touched: every module-level path (portfolio_state.STATE_PATH,
core.STATE_PATH, trade_journal.JOURNAL_FILE, record_fill.JOURNAL_PATH) is
redirected before any tool runs.

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
SIM_CASH_START = 500_000.0

REAL_STATE = "../data/portfolio_state.json"

OLLAMA_URL = "http://localhost:11434/api/generate"
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "qwen2.5:7b")

TRADER_PROMPT = """You are a cautious Indian retail investor using a momentum advisory system.
Below is today's conversation with your advisor about YOUR portfolio.

{advice}

Decide what you will ACTUALLY do today. Rules:
- You generally follow the advisor's recommendations, including quantities.
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
    with open(REAL_STATE) as f:
        state = json.load(f)
    state["cash"] = SIM_CASH_START
    state.pop("cash_note", None)
    with open(SIM_STATE, "w") as f:
        json.dump(state, f, indent=2)
    print(f"[sim] seeded sandbox from real book: {len(state['positions'])} positions, "
          f"cash ₹{SIM_CASH_START:,.0f}")


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
    question = ("It's the month-end review day. Review my whole portfolio: what should "
                "I sell, what should I buy, and exactly how many shares of each? My "
                "total capital assumption should be my current portfolio value."
                if month_end else
                "Daily check: any exit alerts or stops on my holdings today? "
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
        print(f"  equity: ₹{eq['equity'].iloc[0]:,.0f} -> ₹{eq['equity'].iloc[-1]:,.0f} ({ret:+.2%})")
        print(f"  (interface test, not an alpha test — one month proves nothing about returns)")
    probs = [(r["date"], p) for _, r in log.iterrows() for p in json.loads(r["critic_problems"])]
    if probs:
        print("  CRITIC FINDINGS (each is an interface bug to fix before real deployment):")
        for d, p in probs:
            print(f"    {d}: {p}")
    blocks = [(r["date"], b) for _, r in log.iterrows() for b in json.loads(r["blocked"])]
    if blocks:
        print("  blocked orders (often correct behavior — pledged/duplicate/cash guards):")
        for d, b in blocks[-10:]:
            print(f"    {d}: {b.get('action')} {b.get('symbol')} x{b.get('qty')} — {b.get('why')}")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "report":
        report()
    else:
        step()
