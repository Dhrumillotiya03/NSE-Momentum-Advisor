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
A CRITIC step (plain code, no LLM) then checks two different things:
  PLUMBING  — the books moved exactly as the orders said, journal rows match.
  ECONOMICS — each executed buy's RUPEE notional against ai_assistant's own
              position_sizes() plan, the book's deployed share against
              REGIME_EXPOSURE, and whether the advice was truncated before
              the trader ever read it.
The economics half was added 2026-09-01 after the 2026-08-25 rebalance
executed with "critic problems: 0" while being wrong by up to 19x — the
advisor rendered position_sizes()'s output as "Weight: 25%" and dropped the
`quantity` field, the trader read the percentage as a SHARE COUNT, and the
orders were applied ADD-TO instead of REBALANCE-TO. Every individual order
looked sane; only the notionals and the resulting exposure did not.

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

# How much of the advisor's answer the trader actually gets to read. A
# month-end review enumerates every name with its weight AND quantity and is
# the longest message of the year — exactly the one that must not be cut. The
# critic now records when this limit was hit (see check_advice_truncated).
ADVICE_CHAR_LIMIT = 6000

# CRITIC TOLERANCES. These exist because the 2026-08-25 rebalance executed
# with `critic problems: 0` while being wrong by up to 19x: the advisor
# rendered position_sizes()'s output as "Weight: 25%" and dropped the
# `quantity` field, the trader read the percentage as a SHARE COUNT and
# ordered 25 shares of each name (HFCL got 6% of its target, RADICO 119%),
# and the orders were executed as ADD-TO rather than REBALANCE-TO, leaving
# the book 54.7% deployed against a 37.5% BEAR mandate. Neither failure was
# detectable by the old critic, which only asked "did a BUY produce a
# position" and "does the journal row count match".
NOTIONAL_TOL = 0.40      # executed rupees vs the plan's rupees, per name
EXPOSURE_TOL = 0.10      # deployed share of equity vs REGIME_EXPOSURE

TRADER_PROMPT = """You are a cautious Indian retail investor using a momentum advisory system.
Below is today's conversation with your advisor about YOUR portfolio.

{advice}

Decide what you will ACTUALLY do today. Rules:
- You generally follow the advisor's recommendations, including quantities.
- If (and only if) the advisor explicitly lists ETF sleeve buys/rebalances with
  a non-zero quantity, they are real orders too — include them. If the advisor
  does not list them, do NOT invent ETF orders.
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
        "prompt": TRADER_PROMPT.format(advice=advice_text[:ADVICE_CHAR_LIMIT])})
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


# ---------- critic checks ----------
#
# The plumbing checks (did a BUY land in the book, does the journal row count
# match) verify that record_fill did its job. They cannot see the failure that
# actually matters for a signals-only system: the HUMAN ends up typing the
# wrong number. These three check the ECONOMICS of what was executed against
# what the deterministic tool said to do.

def check_advice_truncated(advice, problems):
    """The trader only ever sees ADVICE_CHAR_LIMIT characters."""
    if len(advice) > ADVICE_CHAR_LIMIT:
        problems.append(
            f"advice was TRUNCATED at {ADVICE_CHAR_LIMIT} chars ({len(advice)} produced) — "
            f"the trader never saw the tail, which on a month-end review is where "
            f"the later names' quantities live")


def check_notional_vs_plan(executed, problems):
    """Compare each executed BUY's rupee notional against ai_assistant's own
    position_sizes() plan — the deterministic tool the advice was built from.

    This is the check that would have caught 2026-08-25. position_sizes()
    returns both `weight` ("25.2%") and `quantity` (394) per name; the advisor
    LLM is free to render that badly, and did. Comparing rupees rather than
    share counts makes the check independent of how the advice was worded.
    """
    buys = [e for e in executed if e["action"] == "buy"]
    if not buys:
        return
    try:
        import ai_assistant as ai
        plan = ai.position_sizes()
    except Exception as e:                                  # noqa: BLE001
        problems.append(f"could not verify notionals against position_sizes(): {e}")
        return
    if not isinstance(plan, dict) or "buy_plan" not in plan:
        problems.append(f"position_sizes() returned no buy_plan ({plan.get('error', plan)}) — "
                        f"executed buys could not be verified")
        return
    want = {p["symbol"]: float(p["rupees"]) for p in plan["buy_plan"]}
    for e in buys:
        got = e["qty"] * e["price"]
        target = want.get(e["symbol"])
        if target is None:
            problems.append(f"{e['symbol']}: BUY ₹{got:,.0f} executed but the name is NOT in "
                            f"today's position_sizes() plan")
            continue
        if target > 0 and abs(got - target) / target > NOTIONAL_TOL:
            problems.append(
                f"{e['symbol']}: executed ₹{got:,.0f} ({e['qty']} sh) vs plan ₹{target:,.0f} "
                f"= {got/target:.0%} of target — the quantity the human acted on is wrong")


def check_exposure(state, equity, problems):
    """Deployed share of equity vs the regime's mandated REGIME_EXPOSURE.

    Catches a month-end executed as ADD-TO instead of REBALANCE-TO: every
    individual order can look sane while the book ends up carrying far more
    risk than the mandate allows.
    """
    if equity <= 0:
        return
    try:
        import core
        import strategy_config as sc
        regime, _ = core.market_regime()
        mandate = sc.REGIME_EXPOSURE[regime]
    except Exception as e:                                  # noqa: BLE001
        problems.append(f"could not check exposure against REGIME_EXPOSURE: {e}")
        return
    deployed = 1.0 - state["cash"] / equity
    if abs(deployed - mandate) > EXPOSURE_TOL:
        problems.append(
            f"book is {deployed:.1%} deployed vs the {mandate:.1%} {regime} mandate "
            f"({'OVER' if deployed > mandate else 'UNDER'}-exposed by "
            f"{abs(deployed - mandate):.1%} of equity)")


# ---------- one session ----------

def step():
    ensure_seeded()
    rf = redirect_paths()

    # tools must import AFTER redirection so they read sandbox state
    import ai_assistant as ai
    from exit_engine import is_last_trading_day_of_month
    import core

    index = core.load_index()
    # Last COMPLETED session, never today's own bar, so the sim's decisions
    # and fills do not depend on what hour the pipeline ran — same rule as
    # paper_trader and sr_daily_logger. See core.last_completed_session.
    today = core.last_completed_session(index.index)
    if today is None:
        print("[sim] no completed session in the index yet — nothing to do")
        return
    today_str = today.strftime("%Y-%m-%d")

    if os.path.exists(SIM_LOG):
        log = pd.read_csv(SIM_LOG)
        if len(log) and log["date"].iloc[-1] == today_str:
            print(f"[sim] already ran for {today_str}")
            return

    month_end = is_last_trading_day_of_month(pd.Series(index.index))

    # Missed-month-end self-heal: the machine may be off on the actual
    # month-end evening (user does not run this daily). If a month boundary
    # passed since the last session and that month's rotation never ran,
    # run it LATE at today's prices — same as a human catching up a day or
    # two later. Logged as month_end="late" so the report can see it.
    if not month_end and os.path.exists(SIM_LOG):
        prev = pd.read_csv(SIM_LOG)
        if len(prev):
            last_dt = pd.Timestamp(prev["date"].iloc[-1])
            if (last_dt.year, last_dt.month) != (today.year, today.month):
                prev_month = prev[pd.to_datetime(prev["date"]).dt.month == last_dt.month]
                if not prev_month["month_end"].astype(str).isin(["True", "late"]).any():
                    month_end = "late"
                    print(f"[sim] month boundary passed with no rotation logged — "
                          f"running LATE month-end at today's prices")

    from portfolio_state import load_state as _ls
    fresh_book = not _ls()["positions"]
    if month_end:
        question = ("It's the month-end review day. Review my whole portfolio: what should "
                    "I sell, what should I buy, and exactly how many shares of each? My "
                    "total capital assumption should be my current portfolio value.")
    elif fresh_book:
        cash_now = _ls()["cash"]
        question = (f"I'm starting fresh with ₹{cash_now:,.0f} in cash and no positions. "
                    f"What exactly should I buy today and how many shares of each?")
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
            if qty < o["qty"]:
                # Silently downsizing a fill hides the reason the book ended
                # up smaller than the advice — record it as a deviation.
                blocked.append({**o, "qty": o["qty"] - qty,
                                "why": f"partially downsized to {qty} sh by available "
                                       f"cash (₹{state['cash']:,.0f})"})
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

    # ECONOMIC checks — see the note above check_advice_truncated. These are
    # what make a "0 problems" report mean something.
    check_advice_truncated(advice, problems)
    if executed:
        check_notional_vs_plan(executed, problems)
    check_exposure(state, equity, problems)

    append_csv(SIM_EQUITY, {"date": today_str, "equity": round(equity, 2),
                            "cash": round(state["cash"], 2),
                            "n_pos": len(state["positions"])},
               ["date", "equity", "cash", "n_pos"])
    append_csv(SIM_LOG, {
        "date": today_str, "month_end": str(month_end),
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
    #
    # MANDATED vs DISCRETIONARY matters and used to be conflated. Under
    # laggards-only, a month-end sell fires because the name dropped out of
    # the sector-capped top-N — it is the MANDATE executing, not a forecast
    # that the stock will fall. Scoring those by "did it keep falling" reads
    # badly forever by construction: momentum names routinely keep running
    # after they stop being the BEST momentum names. Only a sell taken OFF a
    # rebalance day is a discretionary directional call the model owns.
    month_end_dates = set()
    if log is not None and "month_end" in log:
        month_end_dates = set(
            log.loc[log["month_end"].astype(str).isin(["True", "late"]), "date"].astype(str))

    sells = jr[jr["action"] == "SELL"]
    if len(sells):
        print(f"  SELL calls ({len(sells)}):")
        disc_total = disc_good = 0
        for _, t in sells.iterrows():
            now = latest_close(t["symbol"])
            if now is None:
                continue
            after = now / t["price"] - 1
            mandated = str(t["date"]) in month_end_dates
            verdict = ("kept falling" if after < -0.01 else
                       "kept rising" if after > 0.01 else "flat")
            if mandated:
                tag = f"MANDATED rotation ({verdict} after — not a forecast)"
            else:
                tag = ("GOOD EXIT (kept falling)" if after < -0.01 else
                       "cost upside" if after > 0.01 else "neutral")
                disc_total += 1
                disc_good += after < -0.01
            pnl = t.get("pnl", "")
            print(f"    {t['date']} SELL {t['symbol']:16s} @ ₹{t['price']:.2f}, since then "
                  f"{after:+.1%} -> {tag} (realized P&L ₹{pnl})")
        n_mand = len(sells) - disc_total
        if disc_total:
            print(f"    -> {disc_good}/{disc_total} DISCRETIONARY sells vindicated "
                  f"({n_mand} mandated month-end rotations excluded — the mandate "
                  f"chose those, not the model)")
        else:
            print(f"    -> 0 discretionary sells to score; all {n_mand} were mandated "
                  f"month-end rotations, which are not forecasts and are not scored")

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
