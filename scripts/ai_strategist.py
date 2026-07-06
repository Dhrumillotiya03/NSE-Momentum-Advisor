import subprocess
from symbol_lookup import build_symbol_list


# ---------- Run System Scripts ----------

def run(script):
    return subprocess.run(
        ["python", script],
        capture_output=True,
        text=True
    ).stdout


# ---------- Gather Context ----------

def gather_context():

    context = {}

    context["market"] = run("adaptive_engine.py")
    context["opportunities"] = run("portfolio_engine.py")
    context["exits"] = run("exit_engine.py")
    context["portfolio"] = run("portfolio_state.py")
    context["available_symbols"] = ", ".join(sorted(build_symbol_list()))
    context["sector_intel"] = run("sector_intelligence.py")
# override with your manually curated sectors
    import json
    with open("../data/sectors.json") as f:
    	context["sector_map"] = json.dumps(json.load(f), indent=2)

    return context


# ---------- Response Generator ----------

import subprocess as sp


def llm(prompt):

    result = sp.run(
        ["ollama", "run", "llama3"],
        input=prompt,
        text=True,
        capture_output=True
    )

    return result.stdout


def generate_response(question, ctx, history=[]):
    history_text = "\n".join(history[-8:]) if history else ""

    system_prompt = f"""
You are an expert NSE trading advisor with deep knowledge of 
SEBI regulations, FII/DII flows, RBI policy, F&O expiry cycles, and sector rotation.
You balance risk management with opportunity.

MARKET DATA (use this to give specific answers):
{ctx['market']}

TOP OPPORTUNITIES:
{ctx['opportunities']}

EXIT SIGNALS:
{ctx['exits']}

PORTFOLIO:
{ctx['portfolio']}

SECTOR INTELLIGENCE:
{ctx['sector_intel']}

VERIFIED SECTOR MAPPING (use this, ignore any sector misclassification):
{ctx['sector_map']}

CONVERSATION HISTORY:
{history_text}

AVAILABLE SYMBOLS IN MY DATA (always use exact symbol from this list):
{ctx['available_symbols']}

USER QUESTION: {question}


RULES — FOLLOW STRICTLY:
1. Answer the EXACT question asked. Never deflect.
2. Even in BEAR market, if a stock has positive momentum and is above 50DMA, 
   it is a valid buy candidate — say so clearly with position size guidance.
3. BEAR market means SMALLER position sizes (5-10% per stock max), NOT zero positions.
4. If user asks about a specific stock that has good signals, recommend it with:
   - Entry price
   - Stop loss level (7% below entry)
   - Suggested position size based on market regime
5. Never repeat the same market warning more than once per conversation.
6. Structure every answer as:
   SIGNAL: (Bullish/Bearish/Neutral for the specific stock/sector asked)
   REASON: (2 lines max, specific to the stock/sector)
   ACTION: (exact recommendation — buy X qty, avoid, or wait for Y level)
   RISK: (one line on what to watch)
"""
    return llm(system_prompt)

# ---------- Chat Loop ----------

def main():

    print("\n==============================")
    print("🤖 AI TRADING STRATEGIST")
    print("==============================")

    ctx = gather_context()

    print("\nType your question (or 'exit')")

    history = []
    while True:
        q = input("\nYou: ")
        if q.lower() in ["exit", "quit"]:
            break
        history.append(f"User: {q}")
        response = generate_response(q, ctx, history)
        history.append(f"Advisor: {response}")
        history = history[-10:]  # keep last 5 exchanges
        print(response)


if __name__ == "__main__":
    main()
