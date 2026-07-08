import os
import sys
import time
import requests
import pandas as pd
from datetime import datetime, timedelta

OUTPUT_DIR   = "../data/delivery_data/"
PRICE_DIR    = "../data/price_data/"
PROGRESS_LOG = "../data/bhavcopy_backfill_progress.txt"

# NSE's sec_bhavdata_full CSV format is only reliably available from roughly
# Oct 2019 onward (verified 2026-07: 2015/2018/early-2019 dates 404, Oct 2019+
# works). Don't waste requests probing earlier dates.
EARLIEST_AVAILABLE = datetime(2019, 10, 1)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/120 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.nseindia.com/",
}

SESSION = requests.Session()
SESSION.headers.update(HEADERS)


# ──────────────────────────────────────────────
# NSE SESSION INIT  (NSE needs a cookie first)
# ──────────────────────────────────────────────

def init_nse_session():
    try:
        SESSION.get("https://www.nseindia.com", timeout=10)
        time.sleep(1)
    except Exception as e:
        print(f"⚠️  Could not init NSE session: {e}")


# ──────────────────────────────────────────────
# DOWNLOAD ONE DAY
# ──────────────────────────────────────────────

def download_bhavcopy_date(date):
    """
    Downloads and returns a DataFrame with columns:
    SYMBOL, DELIV_QTY, DELIV_PER, DATE
    Returns None if not available (holiday / weekend).
    """
    date_str = date.strftime("%d%m%Y")
    url = f"https://nsearchives.nseindia.com/products/content/sec_bhavdata_full_{date_str}.csv"

    try:
        resp = SESSION.get(url, timeout=15)
        if resp.status_code != 200:
            return None

        from io import StringIO
        df = pd.read_csv(StringIO(resp.text))

        # Clean column names
        df.columns = [c.strip() for c in df.columns]

        # Keep only equity series
        if "SERIES" in df.columns:
            df = df[df["SERIES"].str.strip() == "EQ"]

        needed = ["SYMBOL", "DELIV_QTY", "DELIV_PER"]
        missing = [c for c in needed if c not in df.columns]
        if missing:
            print(f"  Missing columns {missing} in bhavcopy for {date_str}")
            return None

        df = df[needed].copy()
        df["DATE"] = date.strftime("%Y-%m-%d")
        df["SYMBOL"] = df["SYMBOL"].str.strip()
        df["DELIV_QTY"] = pd.to_numeric(df["DELIV_QTY"], errors="coerce").fillna(0)
        df["DELIV_PER"] = pd.to_numeric(df["DELIV_PER"], errors="coerce").fillna(0)

        return df

    except Exception as e:
        print(f"  Error for {date_str}: {e}")
        return None


# ──────────────────────────────────────────────
# MERGE INTO PER-STOCK FILES
# ──────────────────────────────────────────────

def merge_into_stock_files(daily_df):
    """
    Takes a single day's bhavcopy DataFrame and appends
    delivery data to each stock's CSV in delivery_data/.
    """
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    for _, row in daily_df.iterrows():
        sym  = row["SYMBOL"] + ".NS"
        path = os.path.join(OUTPUT_DIR, f"{sym}.csv")

        new_row = pd.DataFrame([{
            "Date":      row["DATE"],
            "DelivQty":  row["DELIV_QTY"],
            "DelivPer":  row["DELIV_PER"],
        }])

        if os.path.exists(path):
            existing = pd.read_csv(path)
            # avoid duplicates
            if row["DATE"] in existing["Date"].values:
                continue
            combined = pd.concat([existing, new_row], ignore_index=True)
        else:
            combined = new_row

        combined = combined.sort_values("Date")
        combined.to_csv(path, index=False)


# ──────────────────────────────────────────────
# MAIN
# ──────────────────────────────────────────────

# ──────────────────────────────────────────────
# BACKFILL (resumable, batched — for building years of delivery history)
# ──────────────────────────────────────────────

def _load_progress():
    if not os.path.exists(PROGRESS_LOG):
        return set()
    with open(PROGRESS_LOG) as f:
        return set(line.strip() for line in f if line.strip())


def _mark_done(date_str):
    with open(PROGRESS_LOG, "a") as f:
        f.write(date_str + "\n")


def backfill(start_date, end_date=None, flush_every=60):
    """Downloads bhavcopy for every business day in [start_date, end_date],
    merging into per-symbol CSVs. Resumable via PROGRESS_LOG — re-running
    skips already-done dates (including ones that came back empty/holiday).
    Batches writes: accumulates rows per symbol in memory and flushes to
    disk every `flush_every` successful days (not one open+read+write per
    symbol per day, which doesn't scale to a multi-year backfill)."""
    end_date = end_date or datetime.today()
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    init_nse_session()

    done = _load_progress()
    dates = pd.bdate_range(start_date, end_date)
    dates = [d for d in dates if d.strftime("%Y-%m-%d") not in done]
    print(f"{len(dates)} business days remaining to fetch "
          f"({len(done)} already done per {PROGRESS_LOG})")

    pending = {}   # symbol -> list of row dicts, flushed periodically
    fetched_since_flush = 0
    ok, missing = 0, 0

    def flush():
        nonlocal pending
        for sym, rows in pending.items():
            path = os.path.join(OUTPUT_DIR, f"{sym}.csv")
            new_df = pd.DataFrame(rows)
            if os.path.exists(path):
                existing = pd.read_csv(path)
                combined = pd.concat([existing, new_df], ignore_index=True)
                combined = combined.drop_duplicates(subset=["Date"], keep="last")
            else:
                combined = new_df
            combined = combined.sort_values("Date")
            combined.to_csv(path, index=False)
        pending = {}

    for i, d in enumerate(dates):
        df = download_bhavcopy_date(d)
        date_str = d.strftime("%Y-%m-%d")
        if df is None:
            missing += 1
        else:
            for _, row in df.iterrows():
                sym = row["SYMBOL"] + ".NS"
                pending.setdefault(sym, []).append({
                    "Date": row["DATE"], "DelivQty": row["DELIV_QTY"], "DelivPer": row["DELIV_PER"],
                })
            ok += 1
            fetched_since_flush += 1
        _mark_done(date_str)

        if fetched_since_flush >= flush_every:
            flush()
            fetched_since_flush = 0
            print(f"  [{i+1}/{len(dates)}] flushed through {date_str} — {ok} ok, {missing} missing so far")

        time.sleep(0.4)

    flush()
    print(f"\nBackfill done. {ok} days fetched, {missing} missing (holidays/weekends/pre-format).")


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "backfill":
        start = sys.argv[2] if len(sys.argv) > 2 else EARLIEST_AVAILABLE.strftime("%Y-%m-%d")
        backfill(pd.Timestamp(start))
        return

    days_back = int(sys.argv[1]) if len(sys.argv) > 1 else 1

    print(f"Initialising NSE session...")
    init_nse_session()

    dates_to_fetch = []
    today = datetime.today()
    for i in range(days_back):
        d = today - timedelta(days=i)
        if d.weekday() < 5:   # skip weekends
            dates_to_fetch.append(d)

    print(f"Fetching bhavcopy for {len(dates_to_fetch)} trading day(s)...")

    success = 0
    for d in dates_to_fetch:
        print(f"  {d.strftime('%d-%b-%Y')}...", end=" ", flush=True)
        df = download_bhavcopy_date(d)
        if df is None:
            print("not available (holiday or not uploaded yet)")
            continue
        merge_into_stock_files(df)
        print(f"✅  {len(df)} stocks updated")
        success += 1
        time.sleep(0.5)   # be polite to NSE servers

    print(f"\nDone. {success}/{len(dates_to_fetch)} days downloaded.")
    print(f"Delivery data saved in: {OUTPUT_DIR}")


if __name__ == "__main__":
    main()


