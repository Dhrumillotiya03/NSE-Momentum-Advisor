import requests
import re

OLLAMA_URL = "http://localhost:11434/api/generate"

SUBREDDITS = [
    "IndiaInvestments",
    "IndianStockMarket"
]

FINANCE_KEYWORDS = [
    "stock", "market", "nifty", "sensex",
    "bank", "it", "pharma", "auto",
    "psu", "energy", "metal", "fmcg",
    "rally", "crash", "bull", "bear"
]


def is_relevant(text):
    t = text.lower()
    return any(k in t for k in FINANCE_KEYWORDS)


def clean_text(text):
    text = re.sub(r"[^\w\s]", "", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def fetch_posts(limit=40):
    texts = []
    headers = {"User-Agent": "stock-ai-bot"}

    for sub in SUBREDDITS:
        url = f"https://www.reddit.com/r/{sub}/hot.json?limit={limit}"
        try:
            r = requests.get(url, headers=headers, timeout=10)
            if r.status_code == 429:
                print("⚠️ Reddit rate limited — skipping")
                break
            if r.status_code != 200:
                continue
        except requests.exceptions.Timeout:
            print("⚠️ Reddit timeout — skipping")
            continue

        data = r.json()

        for post in data["data"]["children"]:
            title = post["data"]["title"]

            if is_relevant(title):
                texts.append(clean_text(title))

    # Deduplicate
    texts = list(dict.fromkeys(texts))

    return texts[:25]


def analyze_with_llm(text):
    prompt = f"""
You are analyzing retail investor discussions about the Indian stock market.

Return ONLY concise intelligence:

Overall Retail Mood:
(Bullish / Neutral / Bearish / Euphoric / Fearful)

Sector Sentiment:
List sectors with mood (Bullish / Neutral / Bearish)

Retail Positioning:
Where money/attention seems to be flowing

Crowding Risk:
(Low / Moderate / High)

Key Retail Concerns:
Short bullet points

Posts:
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
    posts = fetch_posts()

    if not posts:
        print("Retail Mood: UNAVAILABLE (Reddit unreachable)")
        print("Sector Sentiment: UNKNOWN")
        print("Crowding Risk: UNKNOWN")
        return

    text_block = "\n".join(posts)

    analysis = analyze_with_llm(text_block)

    print("\n📢 RETAIL SENTIMENT INTELLIGENCE:\n")
    print(analysis)


if __name__ == "__main__":
    main()