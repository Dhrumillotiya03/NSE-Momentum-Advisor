import subprocess
import re

# --- Helper to run other scripts and capture output ---

def run_script(script_name):
    result = subprocess.run(
        ["python", script_name],
        capture_output=True,
        text=True
    )
    return result.stdout


# ---------- PARSERS ----------

def extract_regime(text):
    match = re.search(r"Market Regime:\s*(\w+)", text)
    return match.group(1) if match else "UNKNOWN"


# ---------- MAIN ----------

def main():

    print("\n==============================")
    print("🚀 UNIFIED AI STOCK ADVISOR v2")
    print("==============================")

    # --- Run core modules ---

    report_quant = run_script("full_advisor.py")
    report_news = run_script("news_sentiment.py")
    report_retail = run_script("reddit_sentiment.py")

    regime = extract_regime(report_quant)

    # --- RISK LEVEL ESTIMATION ---

    if regime in ["BEAR", "HIGH_RISK"]:
        risk_level = "HIGH"
        exposure = "Low (0–20%)"
    elif regime == "SIDEWAYS":
        risk_level = "MODERATE"
        exposure = "Medium (20–50%)"
    else:
        risk_level = "LOW"
        exposure = "High (50–80%)"

    # --- PRINT FINAL REPORT ---

    print(f"\n📊 MARKET REGIME: {regime}")
    print(f"⚠️ RISK LEVEL: {risk_level}")
    print(f"💰 SUGGESTED EXPOSURE: {exposure}")

    print("\n📰 NEWS IMPACT:")
    print(report_news.strip())

    print("\n📢 RETAIL SENTIMENT:")
    print(report_retail.strip())

    print("\n📈 QUANT RECOMMENDATIONS:")
    print(report_quant.strip())

    print("\n==============================")
    print("🧠 FINAL GUIDANCE")
    print("==============================")

    if risk_level == "HIGH":
        print("• Defensive stance recommended")
        print("• Avoid new positions")
        print("• Protect capital")
    elif risk_level == "MODERATE":
        print("• Selective buying only")
        print("• Focus on strongest sectors")
        print("• Smaller position sizes")
    else:
        print("• Favorable environment for swing trades")
        print("• Focus on top-ranked stocks")
        print("• Maintain diversification")

    print("\n✔ Run daily for monitoring")
    print("✔ Run weekly for decisions")


if __name__ == "__main__":
    main()