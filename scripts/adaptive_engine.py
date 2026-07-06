import subprocess
import pandas as pd

INDEX_PATH = "../data/index_data/nifty50.csv"


# ---------- Helper ----------

def run(script):
    return subprocess.run(
        ["python", script],
        capture_output=True,
        text=True
    ).stdout


# ---------- Volatility ----------

def compute_volatility():

    df = pd.read_csv(INDEX_PATH, parse_dates=["Date"], low_memory=False)

    df["Close"] = pd.to_numeric(df["Close"], errors="coerce")
    df = df.dropna(subset=["Close"])

    returns = df["Close"].pct_change()

    vol = returns.rolling(21).std().iloc[-1]

    if vol > 0.025:
        return "HIGH"
    if vol > 0.015:
        return "MEDIUM"
    return "LOW"


# ---------- Main ----------

def main():

    print("\n==============================")
    print("🛡️ ADAPTIVE RISK ENGINE")
    print("==============================")

    advisor = run("production_advisor.py")
    breadth = run("market_breadth.py")

    volatility = compute_volatility()

    # ---------- Regime ----------

    if "BEAR" in advisor or "HIGH_RISK" in advisor:
        regime = "BEAR"
    elif "SIDEWAYS" in advisor:
        regime = "SIDEWAYS"
    else:
        regime = "BULL"

    # ---------- Internal Strength ----------

    if "WEAK" in breadth:
        internal = "WEAK"
    elif "NEUTRAL" in breadth:
        internal = "NEUTRAL"
    else:
        internal = "STRONG"

    # ---------- Risk Scoring ----------

    risk = 0

    if regime == "BEAR":
        risk += 2
    elif regime == "SIDEWAYS":
        risk += 1

    if internal == "WEAK":
        risk += 2
    elif internal == "NEUTRAL":
        risk += 1

    if volatility == "HIGH":
        risk += 2
    elif volatility == "MEDIUM":
        risk += 1

    # ---------- Posture ----------

    if risk >= 4:
        posture = "DEFENSIVE"
        exposure = "0–20%"
    elif risk >= 2:
        posture = "CAUTIOUS"
        exposure = "20–50%"
    else:
        posture = "AGGRESSIVE"
        exposure = "50–80%"

    print(f"\nMarket Regime: {regime}")
    print(f"Internal Strength: {internal}")
    print(f"Volatility State: {volatility}")
    print(f"Risk Score: {risk}")

    print("\n🎯 ADAPTIVE POSTURE:")
    print(f"Mode: {posture}")
    print(f"Recommended Exposure: {exposure}")

    if posture == "DEFENSIVE":
        print("\nActions:")
        print("• Hold mostly cash")
        print("• Avoid new trades")
        print("• Protect capital")

    elif posture == "CAUTIOUS":
        print("\nActions:")
        print("• Selective trades only")
        print("• Smaller position sizes")
        print("• Prefer strongest setups")

    else:
        print("\nActions:")
        print("• Deploy capital")
        print("• Build diversified portfolio")
        print("• Favor trend trades")


if __name__ == "__main__":
    main()