"""
Backfill NSE corporate announcements — the data source for the early-exit
veto research (research_exit_announcements.py). Per-symbol query, NSE
serves a company's full announcement history in ONE call (no daily paging
needed, unlike bhavcopy) — verified 2026-07: TCS default response spans
2008-2026, and from_date/to_date params are honored.

No cookie warm-up required (the JSON API works even though the NSE
homepage itself 403s from this machine — different from the bhavcopy/
quote endpoints that need it; verified empirically, not documented by NSE).

Scope: symbols currently in price_data/ (the broad universe) — covers any
name that was ever F&O-liquid-gated tradeable, plus enough breadth for
market-wide context. Output: one CSV per symbol,
../data/announcements/<SYM>.NS.csv, columns date,desc,text.

Resumable via ../data/announcements_backfill_progress.txt.
Run from scripts/:  python download_announcements.py
"""
import os
import time
import pandas as pd
import requests

OUT_DIR = "../data/announcements/"
PRICE_DIR = "../data/price_data/"
PROGRESS = "../data/announcements_backfill_progress.txt"
FROM_DATE = "01-01-2015"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/120 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.nseindia.com/companies-listing/corporate-filings-announcements",
}


def to_date_today():
    return pd.Timestamp.today().strftime("%d-%m-%Y")


def fetch(session, symbol, retries=2):
    url = ("https://www.nseindia.com/api/corporate-announcements"
           f"?index=equities&symbol={symbol}&from_date={FROM_DATE}&to_date={to_date_today()}")
    for attempt in range(retries + 1):
        try:
            r = session.get(url, timeout=15)
        except requests.RequestException:
            time.sleep(2)
            continue
        if r.status_code == 200:
            try:
                return r.json()
            except ValueError:
                return None
        if r.status_code == 401 or r.status_code == 403:
            # session got flagged — re-warm and retry once
            try:
                session.get("https://www.nseindia.com/", timeout=10)
            except requests.RequestException:
                pass
            time.sleep(2)
            continue
        return None
    return None


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    done = set()
    if os.path.exists(PROGRESS):
        with open(PROGRESS) as f:
            done = {l.strip() for l in f if l.strip()}

    symbols = sorted(f.replace(".NS.csv", "") for f in os.listdir(PRICE_DIR)
                     if f.endswith(".NS.csv"))
    session = requests.Session()
    session.headers.update(HEADERS)

    n_ok = n_empty = n_fail = 0
    for k, sym in enumerate(symbols):
        if sym in done:
            continue
        data = fetch(session, sym)
        if data is None:
            n_fail += 1
            print(f"  {sym}: FAILED")
        elif len(data) == 0:
            n_empty += 1
        else:
            rows = [{"date": row.get("an_dt"), "desc": row.get("desc"),
                    "text": (row.get("attchmntText") or "")[:200]} for row in data]
            df = pd.DataFrame(rows)
            df.to_csv(OUT_DIR + sym + ".NS.csv", index=False)
            n_ok += 1
        with open(PROGRESS, "a") as f:
            f.write(sym + "\n")
        if (k + 1) % 25 == 0:
            print(f"  [{k+1}/{len(symbols)}] ok={n_ok} empty={n_empty} fail={n_fail}", flush=True)
        time.sleep(0.6)

    print(f"\nDone: {n_ok} saved, {n_empty} empty, {n_fail} failed of {len(symbols)} symbols")


if __name__ == "__main__":
    main()
