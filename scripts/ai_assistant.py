"""
Tool-calling AI trading assistant. Replaces ai_strategist.py's prompt-stuffing
(dumping every script's stdout into one giant prompt) with a real
function-calling loop: the model decides which tool(s) to call based on the
user's question, and every tool is backed directly by core.py / exit_engine.py
so the assistant can never diverge from the canonical regime/scoring/exit
logic.

Requires an Ollama model that supports tool-calling (llama3 does NOT; use
e.g. qwen2.5, llama3.1, mistral-nemo). Set MODEL_NAME below once you've
pulled one — everything else is model-agnostic.

Usage:
    python ai_assistant.py
"""
import json
import numpy as np
import requests

import strategy_config as sc
import core
from exit_engine import (
    check_catastrophic_stop, check_requalification,
    is_non_strategy_holding, is_last_trading_day_of_month, load_stock as exit_load_stock,
)

# ---------- Pluggable model config ----------
MODEL_NAME = "qwen2.5:7b"     # any Ollama model that supports tool-calling
OLLAMA_URL = "http://localhost:11434/api/chat"


# ---------- Tool implementations ----------
# Each returns a small JSON-serializable dict — the model reads this back
# as the tool result and decides what to say / call next.
#
# FORMATTING RULE (2026-07-14): every return/ratio field is pre-formatted as
# an explicit percent STRING ("+220.6%"), never a raw decimal. A real user
# read HFCL's raw ret_6m of 2.206 (i.e. +220.6%) as "2%", told the model the
# return was bad, and the model capitulated and recommended a LOWER-scored
# name as "better". Raw floats invite that whole failure class.


def pct(x, digits=1, signed=True):
    if x is None:
        return None
    try:
        return f"{x:+.{digits}%}" if signed else f"{x:.{digits}%}"
    except (TypeError, ValueError):
        return None

def data_freshness():
    """Age of the downloaded data. The user runs this system irregularly —
    if the nightly pipeline silently died, advice would be based on stale
    prices without anyone noticing."""
    import pandas as pd
    dates = pd.to_datetime(
        pd.read_csv("../data/index_data/nifty50.csv")["Date"], errors="coerce").dropna()
    last = dates.max()
    age_days = (pd.Timestamp.today().normalize() - last.normalize()).days
    return last.date(), age_days


def market_status():
    regime, breadth = core.market_regime()
    n = sc.REGIME_NAMES[regime]
    exposure = sc.REGIME_EXPOSURE[regime]
    table = core.load_confidence()
    reg_stats = table["regime_stats"].get(regime, {})
    last_date, age = data_freshness()
    return {
        "data_as_of": str(last_date),
        "data_staleness_warning": (f"DATA IS {age} DAYS OLD — run the download "
                                   f"pipeline (run_daily_log.sh) before trusting "
                                   f"any number" if age > 4 else None),
        "regime": regime,
        "breadth_pct_above_200dma": None if np.isnan(breadth) else pct(float(breadth), 0, signed=False),
        "names_to_hold": n,
        "target_exposure": pct(exposure, 0, signed=False),
        "historical_win_rate_this_regime": pct(reg_stats.get("win_rate"), 0, signed=False),
        "historical_median_21d_fwd_return": pct(reg_stats.get("median_fwd")),
        "historical_n_setups": reg_stats.get("n"),
    }


def stock_status(symbol):
    if not symbol.upper().endswith(".NS"):
        symbol = symbol.upper() + ".NS"
    else:
        symbol = symbol.upper()

    df = core.load_stock(symbol)
    if df is None:
        return {"error": f"No price data for {symbol}"}
    if len(df) < core.LOOKBACK + 60:
        return {"error": f"Not enough history for {symbol} ({len(df)} rows)"}

    regime, _breadth = core.market_regime()
    r = core.compute_score(df)
    support, resistance, s_str, r_str = core.sr_levels(df, symbol=symbol)
    price = float(df["Close"].iloc[-1])

    result = {
        "symbol": symbol,
        "price": price,
        "regime": regime,
        "support": support,
        "resistance": resistance,
        "support_strength": s_str,
        "resistance_strength": r_str,
        "eligible_momentum_setup": r is not None,
    }
    if r is None:
        result["reason_not_eligible"] = ("fails momentum filter: needs positive 6m AND 3m "
                                          "returns, and price above 50DMA")
        return result

    decile_stats, regime_stats = core.confidence_for(r["score"], regime)
    result.update({
        "momentum_score": round(r["score"], 1),
        "return_6_month": pct(r["ret_6m"]),
        "return_3_month": pct(r["ret_3m"]),
        "rsi": round(r["rsi"], 1),
        "overbought": r["rsi"] > sc.RSI_OVERBOUGHT,
        "historical_win_rate_this_decile": pct(decile_stats.get("win_rate"), 0, signed=False),
        "historical_median_21d_fwd_this_decile": pct(decile_stats.get("median_fwd")),
        "historical_win_rate_this_regime": pct(regime_stats.get("win_rate"), 0, signed=False),
    })
    return result


def should_i_sell(symbol):
    if not symbol or not symbol.strip():
        return {"error": "symbol is required. To review ALL holdings at once, "
                          "call the what_to_sell tool instead (it takes no arguments)."}
    if not symbol.upper().endswith(".NS"):
        symbol = symbol.upper() + ".NS"
    else:
        symbol = symbol.upper()

    state = core.load_portfolio_state()
    pos = state["positions"].get(symbol)
    if pos is None:
        held = list(state["positions"].keys())
        return {"error": f"No open position in {symbol}", "currently_held_symbols": held}

    if is_non_strategy_holding(symbol, pos):
        return {"symbol": symbol, "verdict": "MANUAL REVIEW",
                "reason": "non-strategy holding (ETF/BE-series/zero-cost) — excluded from auto-exit"}

    df = exit_load_stock(symbol)
    if df is None or len(df) < 60:
        return {"error": f"Not enough price data for {symbol}"}

    entry_price = pos.get("entry_price", 0)
    regime, _breadth = core.market_regime()
    from live_quotes import get_quote
    live_price, stale = get_quote(symbol)

    reason = check_catastrophic_stop(df, entry_price, live_price=None if stale else live_price)
    if reason:
        return {"symbol": symbol, "verdict": "SELL", "reason": reason}

    import pandas as pd
    index_dates = pd.to_datetime(
        pd.read_csv("../data/index_data/nifty50.csv")["Date"], errors="coerce"
    ).dropna().sort_values()
    if is_last_trading_day_of_month(index_dates):
        # LAGGARDS-ONLY month-end (production since 2026-07-12, matches
        # exit_engine.py): a name still in the new sector-capped top-N is
        # HELD (no sell, no tax event) — only drop-outs are sold.
        eligible_scores = core.scan_universe()
        n_names = sc.REGIME_NAMES[regime]
        from backtest_portfolio import select_top_n_capped, load_sector_map
        scores_only = {s: r["score"] for s, r in eligible_scores.items()}
        top_n_symbols = set(select_top_n_capped(
            scores_only, n_names, load_sector_map(), sc.MAX_PER_SECTOR))
        requal = check_requalification(symbol, df, regime, eligible_scores, top_n_symbols)
        if requal is None:
            return {"symbol": symbol, "verdict": "HOLD",
                    "reason": "Month-end re-evaluation: still in the new top-N — KEEP it "
                              "(laggards-only rebalance: no sell/re-buy, no tax event; "
                              "only its target weight may need a small top-up/trim)"}
        return {"symbol": symbol, "verdict": "SELL",
                "reason": f"Month-end re-evaluation — {requal}"}

    price = live_price if live_price else float(df["Close"].iloc[-1])
    gain = (price / entry_price - 1) if entry_price else None
    return {"symbol": symbol, "verdict": "HOLD", "current_price": round(price, 2),
            "price_is_live": not stale, "gain_since_entry": pct(gain),
            "reason": "no exit condition fires; intra-month the only exit is the -18% "
                      "catastrophic stop — otherwise positions run to the month-end review"}


def what_to_sell():
    state = core.load_portfolio_state()
    results = []
    for sym in state["positions"]:
        results.append(should_i_sell(sym))
    urgent = [r for r in results if r.get("verdict") == "SELL"]
    review = [r for r in results if r.get("verdict") == "MANUAL REVIEW"]
    holds = [r for r in results if r.get("verdict") == "HOLD"]
    # Key names must be unambiguous for the LLM: an empty list under a key
    # like "hold" was misread as "no holdings at all" in testing.
    return {
        "total_open_positions": len(results),
        "positions_to_SELL_now": urgent,
        "positions_needing_manual_review": review,
        "positions_fine_to_keep_holding": holds,
    }


def buy_candidates():
    regime, _breadth = core.market_regime()
    n = sc.REGIME_NAMES[regime]
    exposure = sc.REGIME_EXPOSURE[regime]
    results = core.scan_universe()
    sector_map = core.load_sector_map()
    # sector-capped selection — the SAME rule backtest/exit_engine enforce
    # (a plain ranked[:n] here could recommend a book the strategy would never hold)
    from backtest_portfolio import select_top_n_capped
    scores_only = {s: r["score"] for s, r in results.items()}
    top = select_top_n_capped(scores_only, n, sector_map, sc.MAX_PER_SECTOR)

    out = []
    for rank, sym in enumerate(sorted(top, key=scores_only.get, reverse=True), 1):
        r = results[sym]
        decile_stats, regime_stats = core.confidence_for(r["score"], regime)
        out.append({
            "rank": rank,
            "symbol": sym,
            "sector": sector_map.get(sym, "unmapped"),
            "price": round(r["price"], 2),
            "momentum_score": round(r["score"], 1),
            "rsi": round(r["rsi"], 1),
            "return_6_month": pct(r["ret_6m"]),
            "return_3_month": pct(r["ret_3m"]),
            "historical_win_rate_this_decile": pct(decile_stats.get("win_rate"), 0, signed=False),
            "historical_median_21d_fwd": pct(decile_stats.get("median_fwd")),
        })
    return {"regime": regime, "target_names": n,
            "target_exposure": pct(exposure, 0, signed=False),
            "ranking_rule": "candidates are ranked by momentum_score (return/volatility); "
                            "rank 1 is the strategy's strongest pick",
            "candidates": out}


def position_sizes(capital=None):
    """Exact quantities the strategy would buy right now — the same
    inverse-vol / MAX_WEIGHT / regime-exposure / sleeve math as
    backtest_portfolio and paper_trader, so 'how much quantity' can never
    be improvised by the LLM. capital: total account value in rupees; if
    omitted, uses the recorded portfolio's total value."""
    regime, _breadth = core.market_regime()
    n = sc.REGIME_NAMES[regime]
    exposure = sc.REGIME_EXPOSURE[regime]

    if capital is None:
        state = core.load_portfolio_state()
        from live_quotes import get_quote
        capital = state["cash"]
        for s, p in state["positions"].items():
            price, _ = get_quote(s)
            if price:
                capital += price * p["qty"]
    capital = float(capital)

    results = core.scan_universe()
    sector_map = core.load_sector_map()
    from backtest_portfolio import select_top_n_capped
    scores_only = {s: r["score"] for s, r in results.items()}
    top = select_top_n_capped(scores_only, n, sector_map, sc.MAX_PER_SECTOR)
    if len(results) < n or not top:
        return {"error": f"only {len(results)} eligible names for regime {regime} (need {n})"}

    inv = {s: 1.0 / results[s]["vol_63"] for s in top}
    tot = sum(inv.values())
    w = {s: min(v / tot, sc.MAX_WEIGHT) * tot for s, v in inv.items()}
    tot2 = sum(w.values())
    w = {s: v / tot2 for s, v in w.items()}

    momentum_capital = capital * (1 - sc.GOLD_ALLOC - sc.INTL_ALLOC) * exposure
    plan = []
    for s in sorted(top, key=scores_only.get, reverse=True):
        rupees = momentum_capital * w[s]
        px = results[s]["price"]
        plan.append({"symbol": s, "weight": pct(w[s], 1, signed=False),
                     "rupees": round(rupees), "price": round(px, 2),
                     "quantity": int(rupees // px)})

    return {
        "total_capital_assumed": round(capital),
        "regime": regime,
        "momentum_budget": round(momentum_capital),
        "momentum_budget_explained": (
            f"{1 - sc.GOLD_ALLOC - sc.INTL_ALLOC:.0%} momentum sleeve x "
            f"{exposure:.0%} {regime}-regime exposure of total capital"),
        "buy_plan": plan,
        "also_maintain_sleeves": {
            sc.GOLD_SYMBOL: f"{sc.GOLD_ALLOC:.0%} of total = ₹{capital * sc.GOLD_ALLOC:,.0f}",
            sc.INTL_SYMBOL: f"{sc.INTL_ALLOC:.0%} of total = ₹{capital * sc.INTL_ALLOC:,.0f}",
        },
        "uninvested_cash_note": "remaining cash should sit in a liquid ETF "
                                "(LIQUIDCASE-type), not idle — the strategy's "
                                "returns assume ~6% on idle cash",
    }


def sleeve_status():
    """Current vs target for the two permanent ETF sleeves (15% GOLDBEES
    gold, 10% MON100 Nasdaq-100), plus the policy rationale so the model
    can answer 'why do I hold gold?' from facts, not improvisation."""
    from live_quotes import get_quote
    state = core.load_portfolio_state()
    total = state["cash"]
    prices = {}
    for s, p in state["positions"].items():
        price, _ = get_quote(s)
        prices[s] = price
        if price:
            total += price * p["qty"]

    sleeves = []
    for sym, alloc, why in [
            (sc.GOLD_SYMBOL, sc.GOLD_ALLOC,
             "diversifier: ~0 correlation to the momentum book; cut backtest max "
             "drawdown 40%->31%; NOT a return bet"),
            (sc.INTL_SYMBOL, sc.INTL_ALLOC,
             "diversifier: second equity market + USD exposure (INR weakens in "
             "Indian risk-off); correlation to momentum book only +0.10")]:
        pos = state["positions"].get(sym)
        px = prices.get(sym) or (get_quote(sym)[0])
        held_val = pos["qty"] * px if (pos and px) else 0.0
        target_val = total * alloc
        delta = target_val - held_val
        sleeves.append({
            "symbol": sym, "target_pct_of_total": pct(alloc, 0, signed=False),
            "target_value": round(target_val), "held_value": round(held_val),
            "rebalance_delta_rupees": round(delta),
            "action": ("within 1% drift band — no trade needed"
                       if abs(delta) < 0.01 * total else
                       f"{'BUY' if delta > 0 else 'SELL'} ~{int(abs(delta) // px) if px else '?'} units at month-end"),
            "why_held": why,
        })
    return {"total_portfolio_value": round(total),
            "policy": "75% momentum / 15% gold / 10% international, ETF sleeves "
                      "rebalanced to target each month-end; sleeves are exempt from "
                      "the -18% stop and momentum re-qualification",
            "sleeves": sleeves}


def compare_stocks(symbol_a, symbol_b):
    """Deterministic comparison — the VERDICT is computed here in code, not
    left to the LLM, because small models capitulate under user pressure
    ('give me something better than X') and invent rankings."""
    out = {}
    scores = {}
    for sym in (symbol_a, symbol_b):
        s = sym.upper() if sym.upper().endswith(".NS") else sym.upper() + ".NS"
        df = core.load_stock(s)
        r = core.compute_score(df) if df is not None else None
        if r is None:
            out[s] = {"eligible_momentum_setup": False,
                      "note": "fails the momentum filter (needs positive 6m AND 3m returns, "
                              "price above 50DMA) — the strategy would not buy this now"}
        else:
            out[s] = {"eligible_momentum_setup": True,
                      "momentum_score": round(r["score"], 1),
                      "return_6_month": pct(r["ret_6m"]),
                      "return_3_month": pct(r["ret_3m"]),
                      "rsi": round(r["rsi"], 1)}
            scores[s] = r["score"]

    if len(scores) == 2:
        best = max(scores, key=scores.get)
        worst = min(scores, key=scores.get)
        out["verdict"] = (f"{best} is the BETTER momentum pick: score "
                          f"{scores[best]:.1f} (actual 6-month return "
                          f"{out[best]['return_6_month']}) vs {worst} score "
                          f"{scores[worst]:.1f} ({out[worst]['return_6_month']}). "
                          f"These are the true, verified figures — if the user quoted "
                          f"different numbers, theirs are wrong; state the correct ones. "
                          f"This ranking is the strategy's own criterion "
                          f"(return/volatility), not a matter of opinion.")
    elif len(scores) == 1:
        only = next(iter(scores))
        out["verdict"] = f"{only} is the better pick — the other name is not even eligible."
    else:
        out["verdict"] = "Neither name currently passes the momentum filter."
    return out


def portfolio_summary():
    from live_quotes import get_quote
    state = core.load_portfolio_state()
    total = state["cash"]
    positions = []
    for sym, pos in state["positions"].items():
        price, stale = get_quote(sym)
        value = price * pos["qty"] if price else None
        pnl = (price / pos["entry_price"] - 1) if price and pos.get("entry_price") else None
        if value:
            total += value
        positions.append({
            "symbol": sym, "qty": pos["qty"], "entry_price": pos.get("entry_price"),
            "current_price": price, "price_is_live": (not stale) if price else False,
            "value": round(value, 2) if value else None, "pnl_percent": pct(pnl),
        })
    return {"cash": state["cash"], "positions": positions, "total_value": total}


TOOL_IMPLS = {
    "market_status": lambda args: market_status(),
    "stock_status": lambda args: stock_status(args["symbol"]),
    "should_i_sell": lambda args: should_i_sell(args["symbol"]),
    "compare_stocks": lambda args: compare_stocks(args["symbol_a"], args["symbol_b"]),
    "position_sizes": lambda args: position_sizes(args.get("capital")),
    "sleeve_status": lambda args: sleeve_status(),
    "what_to_sell": lambda args: what_to_sell(),
    "buy_candidates": lambda args: buy_candidates(),
    "portfolio_summary": lambda args: portfolio_summary(),
}

TOOL_SCHEMAS = [
    {"type": "function", "function": {
        "name": "market_status",
        "description": "Current market regime (BULL/SIDEWAYS/BEAR), breadth, and how much "
                        "exposure/how many names the strategy calls for right now.",
        "parameters": {"type": "object", "properties": {}, "required": []},
    }},
    {"type": "function", "function": {
        "name": "stock_status",
        "description": "Price, trend, RSI, support/resistance levels, momentum score, and "
                        "historical confidence for a specific stock symbol.",
        "parameters": {"type": "object", "properties": {
            "symbol": {"type": "string", "description": "NSE ticker, e.g. TCS or TCS.NS"},
        }, "required": ["symbol"]},
    }},
    {"type": "function", "function": {
        "name": "should_i_sell",
        "description": "Sell verdict for ONE specific, named holding (runs the exit "
                        "hierarchy: catastrophic stop, month-end re-qualification). Only "
                        "use when the user names a specific stock. If the user asks about "
                        "their holdings in general ('what should I sell?', 'review my "
                        "portfolio'), use what_to_sell instead.",
        "parameters": {"type": "object", "properties": {
            "symbol": {"type": "string", "description": "NSE ticker of a held position, e.g. AARTIIND"},
        }, "required": ["symbol"]},
    }},
    {"type": "function", "function": {
        "name": "what_to_sell",
        "description": "USE THIS for any general 'which of my holdings should I sell / "
                        "review my portfolio for exits' question. Scans ALL current "
                        "holdings and returns which to sell now, which need manual review, "
                        "and which are fine to keep. Takes no arguments.",
        "parameters": {"type": "object", "properties": {}, "required": []},
    }},
    {"type": "function", "function": {
        "name": "compare_stocks",
        "description": "Definitive head-to-head comparison of two stocks on the strategy's "
                        "momentum criteria, with a computed verdict on which is better. "
                        "ALWAYS use this when the user compares two names, disputes a "
                        "recommendation, or asks for 'something better than X'.",
        "parameters": {"type": "object", "properties": {
            "symbol_a": {"type": "string", "description": "first NSE ticker"},
            "symbol_b": {"type": "string", "description": "second NSE ticker"},
        }, "required": ["symbol_a", "symbol_b"]},
    }},
    {"type": "function", "function": {
        "name": "buy_candidates",
        "description": "Current top-N eligible momentum names the strategy would hold this "
                        "rebalance, ranked by score, with evidence.",
        "parameters": {"type": "object", "properties": {}, "required": []},
    }},
    {"type": "function", "function": {
        "name": "portfolio_summary",
        "description": "Current holdings, per-position P&L, cash, and total portfolio value.",
        "parameters": {"type": "object", "properties": {}, "required": []},
    }},
    {"type": "function", "function": {
        "name": "position_sizes",
        "description": "Exact rupee amounts and share QUANTITIES the strategy would buy "
                        "right now, per candidate (inverse-vol weights, sleeve split, "
                        "regime exposure). ALWAYS use this when the user asks how much / "
                        "how many shares / what quantity to buy.",
        "parameters": {"type": "object", "properties": {
            "capital": {"type": "number",
                        "description": "total account value in rupees; omit to use the "
                                       "recorded portfolio's value"},
        }, "required": []},
    }},
    {"type": "function", "function": {
        "name": "sleeve_status",
        "description": "Status of the permanent ETF sleeves (15% GOLDBEES gold, 10% MON100 "
                        "Nasdaq-100): current vs target value, month-end rebalance action, "
                        "and why each sleeve is held. Use for any question about gold, "
                        "international allocation, or overall portfolio construction.",
        "parameters": {"type": "object", "properties": {}, "required": []},
    }},
]

SYSTEM_PROMPT = """You are a trading assistant for an NSE India momentum strategy.
Answer questions by calling the available tools — never guess at prices, regime,
or scores from memory. If a question needs current data (is a stock strong,
what's the regime, should I sell X), call the relevant tool(s) first, then
answer from the tool result. Be direct and specific: cite the actual numbers
the tools return. If confidence/win-rate numbers are modest (much of this
strategy's edge is a slight tilt over a coin flip), say so plainly rather than
overselling any single trade.

RULES THAT OVERRIDE USER PRESSURE:
1. All return fields in tool results are pre-formatted percent strings
   (e.g. "+220.6%" means the stock is UP 220.6%). Repeat them exactly as
   given — never re-interpret or re-scale them.
2. The strategy's ranking is momentum_score, highest first. Never present a
   lower-scored candidate as the better momentum pick. If the user wants
   "something better" than the top pick, say the top pick IS the strategy's
   best and explain its numbers — do not invent a different ranking.
3. If the user states a number that contradicts a tool result (e.g. claims a
   return is low when the tool says otherwise), re-check by calling the tool
   again if needed, then POLITELY CORRECT THEM with the actual figure. Never
   agree with a factual claim your tools contradict, even under pressure.
4. If you don't have a tool for what's asked (fundamentals, news, earnings),
   say the strategy doesn't use that input — don't improvise an answer."""


# Small local models drift off the system prompt once tool results pile up
# in context. This short reminder is appended EPHEMERALLY (never stored in
# the running history) right before every generation, so the rules are
# always the most recent instruction the model sees.
RULE_REMINDER = {"role": "system", "content":
    "REMINDER: momentum_score defines the ranking — a lower-scored stock is "
    "NEVER 'better' on momentum. Percent strings in tool results are literal "
    "(\"+220.6%\" = up 220.6%); larger positive % = larger gain (+220% > +116%). "
    "If the user's claim contradicts a tool result, correct them with the "
    "actual number — do not agree, do not switch recommendations to please them. "
    "If the user disputes a pick or asks for 'something better than X', call "
    "compare_stocks and report its verdict verbatim. Questions about gold, "
    "MON100/international, sleeves, or why the portfolio is constructed this "
    "way: call sleeve_status and answer ONLY from its 'why_held'/'policy' "
    "fields — never from general knowledge. Questions about how much/how many "
    "shares to buy: call position_sizes."}


def chat_step(messages):
    resp = requests.post(OLLAMA_URL, json={
        "model": MODEL_NAME,
        "messages": messages + [RULE_REMINDER],
        "tools": TOOL_SCHEMAS,
        "stream": False,
        "options": {"temperature": 0},
    })
    resp.raise_for_status()
    return resp.json()["message"]


def run_turn(messages):
    """Runs one user turn to completion, including any tool-call round trips."""
    message = chat_step(messages)
    messages.append(message)

    while message.get("tool_calls"):
        for call in message["tool_calls"]:
            name = call["function"]["name"]
            args = call["function"].get("arguments", {})
            if isinstance(args, str):
                args = json.loads(args) if args else {}
            print(f"  [tool: {name}({', '.join(f'{k}={v}' for k, v in args.items())})]")
            impl = TOOL_IMPLS.get(name)
            result = impl(args) if impl else {"error": f"unknown tool {name}"}
            messages.append({"role": "tool", "content": json.dumps(result, default=str)})
        message = chat_step(messages)
        messages.append(message)

    return message.get("content", "")


def main():
    print("\n==============================")
    print("AI TRADING ASSISTANT (tool-calling)")
    print(f"Model: {MODEL_NAME}")
    print("==============================")
    try:
        last_date, age = data_freshness()
        if age > 4:
            print(f"⚠️  DATA IS {age} DAYS OLD (last: {last_date}) — run "
                  f"./run_daily_log.sh first, or every answer below uses stale prices")
        else:
            print(f"data as of {last_date} ({age}d old)")
    except Exception:
        print("⚠️  could not determine data freshness")
    print("Type your question (or 'exit')\n")

    messages = [{"role": "system", "content": SYSTEM_PROMPT}]

    while True:
        q = input("You: ")
        if q.lower() in ("exit", "quit"):
            break
        messages.append({"role": "user", "content": q})
        try:
            answer = run_turn(messages)
        except requests.exceptions.ConnectionError:
            print("\n[Ollama not reachable at localhost:11434 — is it running?]\n")
            continue
        except requests.exceptions.HTTPError as e:
            print(f"\n[Model error: {e}. Does {MODEL_NAME} support tool-calling? "
                  f"Try 'ollama pull {MODEL_NAME}' first.]\n")
            continue
        print(f"\nAssistant: {answer}\n")


if __name__ == "__main__":
    main()
