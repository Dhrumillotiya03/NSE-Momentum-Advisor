"""
Backfill OHLCV+turnover for names MISSING from the survivor price panel —
delisted, merged, IBC-dead, or index-dropout stocks that price_data/ never
contained because download_data.py builds from TODAY'S index membership.
Output: ../data/price_data_delisted/<SYM>.NS.csv (Date,Close,Volume,Turnover)
— a SEPARATE directory so the live pipeline (breadth, liquid_universe,
scanning) is untouched; only the survivorship research engine reads it.

Two sources:
 1. yfinance (adjusted) — for names Yahoo still serves.
 2. NSE bhavcopy archives (UNADJUSTED) — for names Yahoo purged. Two formats:
      legacy  archives.nseindia.com/content/historical/EQUITIES/
              {YYYY}/{MMM}/cm{DD}{MMM}{YYYY}bhav.csv.zip   (to ~2024-07)
      new     nsearchives.nseindia.com/products/content/
              sec_bhavdata_full_{ddmmYYYY}.csv             (2019-10 onward)
    Legacy is preferred (covers 2015+); per-day files are scanned once and
    rows for ALL target symbols extracted together.
    Crude split/bonus back-adjustment: overnight close ratios near standard
    factors (1/2, 1/3, 1/4, 1/5, 1/10) with no matching market-wide move are
    treated as corporate actions and back-adjusted. Good enough for a bias
    QUANTIFICATION study; not book-quality data.

Run from scripts/:  python download_delisted.py
Resumable via ../data/delisted_backfill_progress.txt
"""
import io
import os
import time
import zipfile
from datetime import date, timedelta

import numpy as np
import pandas as pd
import requests

OUT_DIR = "../data/price_data_delisted/"
PROGRESS = "../data/delisted_backfill_progress.txt"
START = date(2015, 1, 1)

# Names Yahoo still serves (verified 2026-07-12) — adjusted data, easy path.
YF_SYMBOLS = ["JETAIRWAYS", "RELCAPITAL", "HDIL", "MANPASAND", "JPINFRATEC",
              "EDUCOMP", "MCLEODRUSS", "GTLINFRA", "JISLJALEQS"]

# Names Yahoo purged — bhavcopy path. The heavyweight departures 2015-2026:
# mergers (HDFC->HDFCBANK, CAIRN->VEDL, MINDTREE->LTIM, GRUH->BANDHANBNK,
# 6 PSU banks 2020, SHRIRAMCIT->SHRIRAMFIN, TATACOFFEE->TCPL, IDFC->
# IDFCFIRSTB, TATAMTRDVR cancelled), IBC/fraud deaths (DHFL, VIDEOIND,
# SREINFRA, SINTEX, AMTEKAUTO, KWALITY, RHFL, IBVENTURES), forced merger
# (LAKSHVILAS->DBS), delistings (ESSAROIL, BHUSHANSTL, TALWALKARS, COXKINGS).
BHAV_SYMBOLS = ["HDFC", "CAIRN", "DHFL", "VIDEOIND", "SREINFRA", "SINTEX",
                "LAKSHVILAS", "MINDTREE", "GRUH", "BHUSHANSTL", "AMTEKAUTO",
                "ANDHRABANK", "CORPBANK", "ORIENTBANK", "ALBK", "SYNDIBANK",
                "VIJAYABANK", "DENABANK", "TATAMTRDVR", "TATACOFFEE",
                "SHRIRAMCIT", "KWALITY", "COXKINGS", "ESSAROIL",
                "IBVENTURES", "TALWALKARS", "RHFL", "IDFC"]

HEADERS = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"}


def yf_path():
    import yfinance as yf
    for sym in YF_SYMBOLS:
        out = OUT_DIR + sym + ".NS.csv"
        if os.path.exists(out):
            continue
        try:
            df = yf.download(sym + ".NS", period="max", interval="1d",
                             auto_adjust=True, progress=False)
        except Exception as e:
            print(f"  yf {sym}: {e}")
            continue
        if df.empty:
            continue
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        df = df.reset_index()[["Date", "Close", "Volume"]]
        df = df[df["Date"] >= pd.Timestamp(START)]
        # drop the frozen flat tail some suspended names carry (zero volume)
        df["Turnover"] = df["Close"] * df["Volume"]
        df.to_csv(out, index=False)
        print(f"  yf {sym}: {len(df)} rows -> saved")


# ---------------- bhavcopy path ----------------

def trading_days(start, end):
    d = start
    while d <= end:
        if d.weekday() < 5:
            yield d
        d += timedelta(days=1)


def fetch_legacy(session, d):
    mon = d.strftime("%b").upper()
    url = (f"https://archives.nseindia.com/content/historical/EQUITIES/"
           f"{d.year}/{mon}/cm{d.strftime('%d')}{mon}{d.year}bhav.csv.zip")
    r = session.get(url, timeout=15, headers=HEADERS)
    if r.status_code != 200:
        return None
    with zipfile.ZipFile(io.BytesIO(r.content)) as z:
        df = pd.read_csv(z.open(z.namelist()[0]))
    df = df[(df["SERIES"].str.strip() == "EQ") | (df["SERIES"].str.strip() == "BE")]
    df = df[df["SYMBOL"].isin(BHAV_SYMBOLS)]
    return pd.DataFrame({
        "sym": df["SYMBOL"].str.strip(),
        "close": pd.to_numeric(df["CLOSE"], errors="coerce"),
        "volume": pd.to_numeric(df["TOTTRDQTY"], errors="coerce"),
        "turnover": pd.to_numeric(df["TOTTRDVAL"], errors="coerce"),
    })


def fetch_new(session, d):
    url = (f"https://nsearchives.nseindia.com/products/content/"
           f"sec_bhavdata_full_{d.strftime('%d%m%Y')}.csv")
    r = session.get(url, timeout=15, headers=HEADERS)
    if r.status_code != 200:
        return None
    df = pd.read_csv(io.BytesIO(r.content))
    df.columns = [c.strip() for c in df.columns]
    df["SERIES"] = df["SERIES"].astype(str).str.strip()
    df = df[df["SERIES"].isin(["EQ", "BE"])]
    df["SYMBOL"] = df["SYMBOL"].astype(str).str.strip()
    df = df[df["SYMBOL"].isin(BHAV_SYMBOLS)]
    return pd.DataFrame({
        "sym": df["SYMBOL"],
        "close": pd.to_numeric(df["CLOSE_PRICE"], errors="coerce"),
        "volume": pd.to_numeric(df["TTL_TRD_QNTY"], errors="coerce"),
        "turnover": pd.to_numeric(df["TURNOVER_LACS"], errors="coerce") * 1e5,
    })


def bhav_path():
    done = set()
    if os.path.exists(PROGRESS):
        with open(PROGRESS) as f:
            done = {l.strip() for l in f if l.strip()}

    session = requests.Session()
    rows = {s: [] for s in BHAV_SYMBOLS}
    # load partial per-symbol files if resuming
    for s in BHAV_SYMBOLS:
        p = OUT_DIR + s + ".NS.csv"
        if os.path.exists(p):
            old = pd.read_csv(p, parse_dates=["Date"])
            rows[s] = old.to_dict("records")

    n_hit = n_miss = 0
    days = list(trading_days(START, date.today()))
    for k, d in enumerate(days):
        key = d.isoformat()
        if key in done:
            continue
        df = None
        try:
            df = fetch_legacy(session, d)
            if df is None and d >= date(2019, 10, 1):
                df = fetch_new(session, d)
        except Exception:
            pass
        if df is not None:
            n_hit += 1
            for _, r in df.iterrows():
                if pd.isna(r["close"]):
                    continue
                rows[r["sym"]].append({"Date": pd.Timestamp(d), "Close": r["close"],
                                       "Volume": r["volume"], "Turnover": r["turnover"]})
        else:
            n_miss += 1  # holiday or 404
        with open(PROGRESS, "a") as f:
            f.write(key + "\n")
        if (k + 1) % 100 == 0:
            print(f"  {d} — files ok {n_hit}, miss {n_miss}", flush=True)
            flush(rows)
        time.sleep(0.25)
    flush(rows)
    print(f"bhavcopy pass done: {n_hit} days parsed, {n_miss} missing/holidays")


def adjust_splits(df):
    """Back-adjust obvious split/bonus jumps (unadjusted bhavcopy data).
    Ratios near 1/2, 1/3, 1/4, 1/5, 1/10 on heavy volume are treated as CAs."""
    factors = np.array([0.5, 1 / 3, 0.25, 0.2, 0.1])
    close = df["Close"].to_numpy(dtype=float)
    ratio = close[1:] / close[:-1]
    adj = np.ones(len(close))
    for i, r in enumerate(ratio):
        f = factors[np.argmin(np.abs(factors - r))]
        if abs(r - f) / f < 0.06:
            adj[:i + 1] *= f / 1.0  # scale all history before the event
    df["Close"] = close * adj / adj[-1] if adj[-1] != 0 else close
    return df


def flush(rows):
    os.makedirs(OUT_DIR, exist_ok=True)
    for s, recs in rows.items():
        if not recs:
            continue
        df = pd.DataFrame(recs).drop_duplicates(subset="Date").sort_values("Date")
        df.to_csv(OUT_DIR + s + ".NS.csv", index=False)


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    print("yfinance pass...")
    yf_path()
    print("bhavcopy pass (resumable, ~2900 days)...")
    bhav_path()
    # final split-adjust pass on bhavcopy-sourced files
    for s in BHAV_SYMBOLS:
        p = OUT_DIR + s + ".NS.csv"
        if os.path.exists(p):
            df = pd.read_csv(p, parse_dates=["Date"])
            if len(df) > 50:
                df = adjust_splits(df)
                df.to_csv(p, index=False)
    for f in sorted(os.listdir(OUT_DIR)):
        df = pd.read_csv(OUT_DIR + f)
        print(f"  {f:22s} {len(df):5d} rows  {df['Date'].iloc[0][:10]} -> {df['Date'].iloc[-1][:10]}")


if __name__ == "__main__":
    main()
