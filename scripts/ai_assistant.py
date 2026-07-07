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

def market_status():
    regime, breadth = core.market_regime()
    n = sc.REGIME_NAMES[regime]
    exposure = sc.REGIME_EXPOSURE[regime]
    table = core.load_confidence()
    reg_stats = table["regime_stats"].get(regime, {})
    return {
        "regime": regime,
        "breadth_pct_above_200dma": None if np.isnan(breadth) else round(float(breadth), 4),
        "names_to_hold": n,
        "target_exposure": exposure,
        "historical_win_rate_this_regime": reg_stats.get("win_rate"),
        "historical_median_21d_fwd_return": reg_stats.get("median_fwd"),
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
        "score": r["score"],
        "ret_6m": r["ret_6m"],
        "ret_3m": r["ret_3m"],
        "rsi": r["rsi"],
        "overbought": r["rsi"] > sc.RSI_OVERBOUGHT,
        "historical_win_rate_this_decile": decile_stats.get("win_rate"),
        "historical_median_21d_fwd_this_decile": decile_stats.get("median_fwd"),
        "historical_win_rate_this_regime": regime_stats.get("win_rate"),
    })
    return result


def should_i_sell(symbol):
    if not symbol.upper().endswith(".NS"):
        symbol = symbol.upper() + ".NS"
    else:
        symbol = symbol.upper()

    state = core.load_portfolio_state()
    pos = state["positions"].get(symbol)
    if pos is None:
        return {"error": f"No open position in {symbol}"}

    if is_non_strategy_holding(symbol, pos):
        return {"symbol": symbol, "verdict": "MANUAL REVIEW",
                "reason": "non-strategy holding (ETF/BE-series/zero-cost) — excluded from auto-exit"}

    df = exit_load_stock(symbol)
    if df is None or len(df) < 60:
        return {"error": f"Not enough price data for {symbol}"}

    entry_price = pos.get("entry_price", 0)
    regime, _breadth = core.market_regime()

    reason = check_catastrophic_stop(df, entry_price)
    if reason:
        return {"symbol": symbol, "verdict": "SELL", "reason": reason}

    import pandas as pd
    index_dates = pd.read_csv("../data/index_data/nifty50.csv", parse_dates=["Date"])["Date"].sort_values()
    if is_last_trading_day_of_month(index_dates):
        eligible_scores = core.scan_universe()
        n_names = sc.REGIME_NAMES[regime]
        ranked = sorted(eligible_scores.items(), key=lambda kv: kv[1]["score"], reverse=True)
        top_n_symbols = {sym for sym, _ in ranked[:n_names]}
        reason = check_requalification(symbol, df, regime, eligible_scores, top_n_symbols)
        if reason:
            return {"symbol": symbol, "verdict": "SELL", "reason": reason}

    price = float(df["Close"].iloc[-1])
    gain = (price / entry_price - 1) if entry_price else None
    return {"symbol": symbol, "verdict": "HOLD", "current_gain": gain,
            "reason": "no exit condition currently fires"}


def what_to_sell():
    state = core.load_portfolio_state()
    results = []
    for sym in state["positions"]:
        results.append(should_i_sell(sym))
    urgent = [r for r in results if r.get("verdict") == "SELL"]
    review = [r for r in results if r.get("verdict") == "MANUAL REVIEW"]
    holds = [r for r in results if r.get("verdict") == "HOLD"]
    return {"sell": urgent, "manual_review": review, "hold": holds}


def buy_candidates():
    regime, _breadth = core.market_regime()
    n = sc.REGIME_NAMES[regime]
    exposure = sc.REGIME_EXPOSURE[regime]
    results = core.scan_universe()
    ranked = sorted(results.items(), key=lambda kv: kv[1]["score"], reverse=True)[:n]
    sector_map = core.load_sector_map()

    out = []
    for sym, r in ranked:
        decile_stats, regime_stats = core.confidence_for(r["score"], regime)
        out.append({
            "symbol": sym,
            "sector": sector_map.get(sym, "unmapped"),
            "price": r["price"],
            "score": r["score"],
            "rsi": r["rsi"],
            "ret_6m": r["ret_6m"],
            "ret_3m": r["ret_3m"],
            "historical_win_rate_this_decile": decile_stats.get("win_rate"),
            "historical_median_21d_fwd": decile_stats.get("median_fwd"),
        })
    return {"regime": regime, "target_names": n, "target_exposure": exposure, "candidates": out}


def portfolio_summary():
    state = core.load_portfolio_state()
    total = state["cash"]
    positions = []
    for sym, pos in state["positions"].items():
        df = core.load_stock(sym)
        price = float(df["Close"].iloc[-1]) if df is not None else None
        value = price * pos["qty"] if price else None
        pnl = (price / pos["entry_price"] - 1) if price and pos.get("entry_price") else None
        if value:
            total += value
        positions.append({
            "symbol": sym, "qty": pos["qty"], "entry_price": pos.get("entry_price"),
            "current_price": price, "value": value, "pnl_pct": pnl,
        })
    return {"cash": state["cash"], "positions": positions, "total_value": total}


TOOL_IMPLS = {
    "market_status": lambda args: market_status(),
    "stock_status": lambda args: stock_status(args["symbol"]),
    "should_i_sell": lambda args: should_i_sell(args["symbol"]),
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
        "description": "Runs the exit hierarchy (catastrophic stop, early exit, month-end "
                        "re-qualification) for one currently-held symbol and returns a verdict.",
        "parameters": {"type": "object", "properties": {
            "symbol": {"type": "string", "description": "NSE ticker of a held position"},
        }, "required": ["symbol"]},
    }},
    {"type": "function", "function": {
        "name": "what_to_sell",
        "description": "Scans all current holdings and ranks exit urgency — which to sell now, "
                        "which need manual review, which to hold.",
        "parameters": {"type": "object", "properties": {}, "required": []},
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
]

SYSTEM_PROMPT = """You are a trading assistant for an NSE India momentum strategy.
Answer questions by calling the available tools — never guess at prices, regime,
or scores from memory. If a question needs current data (is a stock strong,
what's the regime, should I sell X), call the relevant tool(s) first, then
answer from the tool result. Be direct and specific: cite the actual numbers
the tools return. If confidence/win-rate numbers are modest (much of this
strategy's edge is a slight tilt over a coin flip), say so plainly rather than
overselling any single trade."""


def chat_step(messages):
    resp = requests.post(OLLAMA_URL, json={
        "model": MODEL_NAME,
        "messages": messages,
        "tools": TOOL_SCHEMAS,
        "stream": False,
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
