import feedparser
import requests
import re

OLLAMA_URL = "http://localhost:11434/api/generate"

NEWS_FEEDS = [
    "https://news.google.com/rss/search?q=Indian+stock+market",
    "https://news.google.com/rss/search?q=NIFTY+Sensex",
    "https://news.google.com/rss/search?q=RBI+India+economy",
    "https://news.google.com/rss/search?q=IT+sector+India",
    "https://news.google.com/rss/search?q=banking+sector+India",
    "https://news.google.com/rss/search?q=oil+prices+India+economy"
]

FINANCE_KEYWORDS = [
    "stock", "market", "nifty", "sensex", "rbi",
    "inflation", "gdp", "earnings", "sector",
    "oil", "bank", "it", "pharma", "auto",
    "policy", "interest", "export", "import"
]


def is_relevant(text):
    t = text.lower()
    return any(k in t for k in FINANCE_KEYWORDS)


def clean_text(text):
    text = re.sub(r"[^\w\s]", "", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def fetch_news(limit=30):
    headlines = []

    for url in NEWS_FEEDS:
        feed = feedparser.parse(url)

        for entry in feed.entries[:limit]:
            title = entry.title

            if is_relevant(title):
                headlines.append(clean_text(title))

    # Remove duplicates
    headlines = list(dict.fromkeys(headlines))

    return headlines[:25]


def analyze_with_llm(text):
    prompt = f"""
You are a financial analyst.

Analyze these Indian market news headlines and extract MARKET IMPACT.

Return ONLY structured output:

Overall Market Impact:
(Bullish / Neutral / Bearish / Risk-Off)

Key Drivers:
- What is actually moving the market?

Affected Sectors:
- Sector : Bullish/Bearish/Neutral

Short-Term Outlook (days–weeks):
- ...

Major Risks:
- ...

Headlines:
{text}
"""

    payload = {
        "model": "llama3",
        "prompt": prompt,
        "stream": False
    }

    response = requests.post(OLLAMA_URL, json=payload)
    result = response.json()["response"]

    return result


def main():
    headlines = fetch_news()

    if not headlines:
        print("Retail Mood: UNAVAILABLE (No headlines found)")
        return

    text_block = "\n".join(headlines)
    analysis = analyze_with_llm(text_block)

    print("\n🧠 MARKET IMPACT ANALYSIS:\n")
    print(analysis)


if __name__ == "__main__":
    main()