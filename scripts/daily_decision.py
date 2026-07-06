import subprocess
from portfolio_state import load_state, portfolio_value


def run(script):
    return subprocess.run(
        ["python", script],
        capture_output=True,
        text=True
    ).stdout


def main():

    print("\n==============================")
    print("📊 DAILY TRADING DECISION")
    print("==============================")

    state = load_state()

    value = portfolio_value(state)

    print(f"\nPortfolio Value: ₹{value:,.0f}")
    print(f"Cash: ₹{state['cash']:,.0f}")

    print("\n--- MARKET ENVIRONMENT ---")
    print(run("adaptive_engine.py"))

    print("\n--- TOP OPPORTUNITIES ---")
    print(run("portfolio_engine.py"))

    print("\n--- EXIT SIGNALS ---")
    print(run("exit_engine.py"))

    print("\n==============================")
    print("🧠 ACTION SUMMARY")
    print("==============================")

    if state["cash"] > 0:
        print("• Capital available for new positions")

    if state["positions"]:
        print("• Review exit signals for held stocks")
    else:
        print("• No open positions")

    print("• Follow adaptive exposure guidance")


if __name__ == "__main__":
    main()