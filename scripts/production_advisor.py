import subprocess
import re

# ---------- Helper ----------

def run(script):
    return subprocess.run(
        ["python", script],
        capture_output=True,
        text=True
    ).stdout


# ---------- Parsers ----------

def parse_regime(text):
    m = re.search(r"Market Regime:\s*(\w+)", text)
    return m.group(1) if m else "UNKNOWN"


def parse_breadth(text):
    m = re.search(r"Internal Strength:\s*(\w+)", text)
    return m.group(1) if m else "UNKNOWN"


def parse_sentiment(text):
    text = text.lower()
    if any(w in text for w in ["bearish", "risk-off", "sell", "caution", "weak"]):
        return -1
    if any(w in text for w in ["bullish", "positive", "buy", "strong", "rally"]):
        return 1
    return 0


# ---------- Main ----------

def main():

    print("\n==============================")
    print("🏆 PRODUCTION ADVISOR REPORT")
    print("==============================")

    # --- Run modules ---

    import sys
    sys.path.insert(0, ".")
    from full_advisor import market_regime
    from market_breadth import compute_breadth, classify_breadth, load_all_stocks

    regime = market_regime()
    stocks_data = load_all_stocks()
    p50, p200, adv_dec = compute_breadth(stocks_data)
    internal = classify_breadth(p50, p200, adv_dec)

    alpha  = run("stock_alpha_v2.py")
    news   = run("news_sentiment.py")
    retail = run("reddit_sentiment.py")

    news_signal   = parse_sentiment(news)
    retail_signal = parse_sentiment(retail)

    # ---------- RISK MODEL ----------

    risk_score = 0

    if regime in ["BEAR", "HIGH_RISK"]:
        risk_score += 2
    elif regime == "SIDEWAYS":
        risk_score += 1

    if internal == "WEAK":
        risk_score += 2
    elif internal == "NEUTRAL":
        risk_score += 1

    # Sentiment adjustment
    if news_signal == -1:
        risk_score += 1
    if retail_signal == -1:
        risk_score += 1
    if news_signal == 1 and retail_signal == 1:
        risk_score -= 1
    risk_score = max(0, risk_score)

    # ---------- Exposure ----------

    if risk_score >= 3:
        exposure   = "LOW (0–25%)"
        confidence = "LOW"
    elif risk_score == 2:
        exposure   = "MODERATE (25–50%)"
        confidence = "MEDIUM"
    else:
        exposure   = "HIGH (50–80%)"
        confidence = "HIGH"

    # ---------- Output ----------

    print(f"\n📊 Market Regime:      {regime}")
    print(f"📉 Internal Strength:  {internal}")
    print(f"📰 News Sentiment:     {'BEARISH' if news_signal == -1 else 'BULLISH' if news_signal == 1 else 'NEUTRAL'}")
    print(f"📢 Retail Sentiment:   {'BEARISH' if retail_signal == -1 else 'BULLISH' if retail_signal == 1 else 'NEUTRAL'}")
    print(f"⚠️  Risk Score:         {risk_score}")
    print(f"💰 Suggested Exposure: {exposure}")
    print(f"🧠 Confidence:         {confidence}")

    print("\n📈 TOP STOCK SIGNALS:")
    print(alpha.strip())

    print("\n📰 NEWS IMPACT:")
    print(news.strip())

    print("\n📢 RETAIL SENTIMENT:")
    print(retail.strip())

    print("\n==============================")
    print("🧭 ACTION GUIDANCE")
    print("==============================")

    if confidence == "HIGH":
        print("• Favor active swing trades")
        print("• Focus on strongest sectors")
        print("• Build positions gradually")
    elif confidence == "MEDIUM":
        print("• Selective trades only")
        print("• Smaller position sizes")
        print("• Prefer market leaders")
    else:
        print("• Defensive stance")
        print("• Avoid aggressive entries")
        print("• Preserve capital")


if __name__ == "__main__":
    main()