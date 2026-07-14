"""
LLM news watchdog — ALERT-ONLY early-warning layer on held names.

WHAT THIS IS: every evening, fetch the last few days of NSE corporate
announcements for every name currently held (real book + paper book), have
a LOCAL LLM (Ollama) classify each one for genuine danger (auditor
resignation, fraud/default, insolvency, regulatory action, pledge
invocation, delisting...), and notify-send + print anything HIGH so the
HUMAN can look a day before the -18% stop would react — or before a halt
makes the stop unactionable (see memory concentration-risk-2026-07: a
halted stock has no price to stop out at).

WHAT THIS IS NOT: a trading signal. An automated announcement-based exit
VETO was properly backtested 2026-07-12 and REJECTED — false positives
cost more than true saves (memory exit-announcements-rejected). An alert
has no false-positive trading cost: the human reads it and decides. Do NOT
wire this into exit_engine/paper_trader/record_fill.

Degrades gracefully: if Ollama isn't running (port 11434), falls back to a
narrow keyword blacklist (the same one the rejected veto study used for
its "danger" class). Already-alerted announcements are remembered in
../data/news_watchdog_seen.csv so nothing re-fires daily.

Run from scripts/ (wired into run_daily_log.sh):  python news_watchdog.py
"""
import csv
import hashlib
import json
import os
import subprocess
import time

import pandas as pd
import requests

import strategy_config as sc

SEEN_PATH = "../data/news_watchdog_seen.csv"
LOOKBACK_DAYS = 5
OLLAMA_URL = "http://localhost:11434/api/generate"
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "qwen2.5:7b")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/120 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.nseindia.com/companies-listing/corporate-filings-announcements",
}

# fallback blacklist if Ollama is down — same danger class as the rejected
# veto study; deliberately narrow (alerts, so precision > recall is NOT
# critical, but keyword noise trains the user to ignore alerts)
DANGER_KEYWORDS = [
    "resignation of auditor", "auditor resign", "fraud", "default",
    "insolvency", "nclt", "sebi order", "search and seizure", "raid",
    "pledge invoked", "invocation of pledge", "delisting", "suspension of trading",
    "winding up", "liquidation", "downgrade",
]

PROMPT = """You are a risk officer for an Indian equity portfolio. Below is one NSE corporate announcement for {sym}. Classify its danger to a shareholder.

HIGH means: auditor resignation, fraud/financial irregularity, loan default, insolvency/NCLT, SEBI/ED/regulatory action or raid, promoter pledge invocation, delisting/suspension risk, or a credit-rating downgrade to junk. Routine business (results dates, board meetings, investor calls, dividends, ESOPs, orders won, expansions) is NONE. Ambiguous-but-concerning is MEDIUM.

Announcement (date {date}):
{text}

Reply with ONLY a JSON object: {{"severity": "HIGH"|"MEDIUM"|"NONE", "reason": "<one short sentence>"}}"""


def held_symbols():
    # Includes the agent-sim's sandbox book: alerts on sim-held names feed
    # the HUMAN's month-end review (was a loss news-explainable?). They are
    # NEVER fed to the sim's trader agent — news-driven trading was
    # backtested and rejected; wiring alerts into the trader would
    # contaminate the very test the sim runs.
    syms = set()
    for path in ["../data/portfolio_state.json", "../data/paper_state.json",
                 "../data/_agent_sim/portfolio_state.json"]:
        if not os.path.exists(path):
            continue
        try:
            with open(path) as f:
                state = json.load(f)
        except ValueError:
            continue
        for s, p in state.get("positions", {}).items():
            if p.get("entry_price", 0) <= 0:
                continue
            if s in sc.EXIT_EXCLUDE_SYMBOLS:
                continue
            if any(s.endswith(suf) for suf in sc.EXIT_EXCLUDE_SUFFIXES):
                continue
            syms.add(s.replace(".NS", ""))
        for order in state.get("pending_buys", []):
            syms.add(order["sym"].replace(".NS", ""))
    return sorted(syms)


def fetch_recent(session, symbol):
    frm = (pd.Timestamp.today() - pd.Timedelta(days=LOOKBACK_DAYS)).strftime("%d-%m-%Y")
    to = pd.Timestamp.today().strftime("%d-%m-%Y")
    url = ("https://www.nseindia.com/api/corporate-announcements"
           f"?index=equities&symbol={symbol}&from_date={frm}&to_date={to}")
    for attempt in range(3):
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
        if r.status_code in (401, 403):
            try:
                session.get("https://www.nseindia.com/", timeout=10)
            except requests.RequestException:
                pass
            time.sleep(2)
            continue
        return None
    return None


def load_seen():
    if not os.path.exists(SEEN_PATH):
        return set()
    with open(SEEN_PATH) as f:
        return {row["hash"] for row in csv.DictReader(f)}


def mark_seen(items):
    new = not os.path.exists(SEEN_PATH)
    with open(SEEN_PATH, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["hash", "date", "symbol", "severity", "desc"])
        if new:
            w.writeheader()
        for it in items:
            w.writerow(it)


def ollama_available():
    try:
        requests.get("http://localhost:11434/api/tags", timeout=3)
        return True
    except requests.RequestException:
        return False


def classify_llm(sym, date, text):
    try:
        r = requests.post(OLLAMA_URL, timeout=120, json={
            "model": OLLAMA_MODEL, "stream": False, "format": "json",
            "prompt": PROMPT.format(sym=sym, date=date, text=text[:1500])})
        body = r.json()
        if "response" not in body:  # e.g. {"error": "model not found"} —
            # must NOT silently classify as NONE; trigger the keyword fallback
            return None, f"llm error: {body.get('error', 'no response field')}"
        out = json.loads(body["response"])
        sev = str(out.get("severity", "NONE")).upper()
        if sev not in ("HIGH", "MEDIUM", "NONE"):
            sev = "NONE"
        return sev, str(out.get("reason", ""))[:200]
    except Exception as e:
        return None, f"llm error: {e}"


def classify_keywords(text):
    t = text.lower()
    for kw in DANGER_KEYWORDS:
        if kw in t:
            return "HIGH", f"keyword match: '{kw}' (Ollama unavailable)"
    return "NONE", ""


def notify(title, body):
    try:
        subprocess.run(["notify-send", title, body], timeout=5)
    except Exception:
        pass


def main():
    syms = held_symbols()
    if not syms:
        print("[watchdog] no held names to watch")
        return
    print(f"[watchdog] watching {len(syms)} names: {', '.join(syms)}")

    use_llm = ollama_available()
    if not use_llm:
        print("[watchdog] Ollama not reachable — keyword fallback only")

    session = requests.Session()
    session.headers.update(HEADERS)
    seen = load_seen()
    new_seen, alerts = [], []

    for sym in syms:
        data = fetch_recent(session, sym)
        if not data:
            continue
        for row in data:
            date = row.get("an_dt", "")
            desc = row.get("desc", "") or ""
            text = f"{desc}. {(row.get('attchmntText') or '')[:1200]}"
            h = hashlib.sha1(f"{sym}|{date}|{desc[:100]}".encode()).hexdigest()[:16]
            if h in seen:
                continue
            if use_llm:
                sev, reason = classify_llm(sym, date, text)
                if sev is None:  # llm broke mid-run — fall back
                    sev, reason = classify_keywords(text)
            else:
                sev, reason = classify_keywords(text)
            new_seen.append({"hash": h, "date": date, "symbol": sym,
                             "severity": sev, "desc": desc[:120]})
            if sev in ("HIGH", "MEDIUM"):
                alerts.append((sev, sym, date, desc, reason))
        time.sleep(0.6)

    if new_seen:
        mark_seen(new_seen)

    if not alerts:
        print(f"[watchdog] {len(new_seen)} new announcement(s), nothing dangerous")
        return

    alerts.sort(key=lambda a: a[0] != "HIGH")
    print(f"\n[watchdog] {len(alerts)} ALERT(S) — review before acting, this is NOT a signal:")
    for sev, sym, date, desc, reason in alerts:
        print(f"  [{sev}] {sym} ({date}): {desc}")
        print(f"         {reason}")
    highs = [a for a in alerts if a[0] == "HIGH"]
    if highs:
        notify("stock_ai NEWS WATCHDOG",
               "\n".join(f"{sym}: {desc[:80]}" for _, sym, _, desc, _ in highs))


if __name__ == "__main__":
    main()
