"""
Free NSE F&O (futures & options) bhavcopy downloader — daily open interest,
change in OI, and volume per stock, for the F&O conditioning research track.

NSE serves two archive formats depending on date:
  - LEGACY (2015-01 through 2024-07-14): archives.nseindia.com/.../DERIVATIVES/
    fo<DD><MON><YYYY>bhav.csv.zip — columns INSTRUMENT/SYMBOL/OPTION_TYP/
    OPEN_INT/CHG_IN_OI/CONTRACTS.
  - UDIFF (2024-01-01 onward, and the ONLY format after 2024-07-15):
    nsearchives.nseindia.com/content/fo/BhavCopy_NSE_FO_..._<YYYYMMDD>_F_0000.csv.zip
    — columns FinInstrmTp/TckrSymb/OptnTp/OpnIntrst/ChngInOpnIntrst/TtlTradgVol.
Both cover 2024-01 to 2024-07-15 (used as a safe legacy/UDiFF cutover check),
verified 2026-07-08. NSE has served no earlier archive under any tried URL
pattern, so pre-2015 F&O history isn't available — matches price data range.

Output: per-symbol daily aggregates in ../data/fo_data/<SYM>.NS.csv with:
  Date, FutOI, FutOIChg, FutVol, CallOI, PutOI, CallVol, PutVol, PCR_OI, PCR_VOL
PCR = Put/Call ratio. Aggregated across all strikes/expiries for that
underlying on that date (near + far month combined) — a per-date market-wide
positioning snapshot, not a single-contract read.

PER-CONTRACT DATA (2026-09-02): download_fo_date() now returns strike,
expiry, close and settlement price alongside the original 6 columns (see
EXTRA_COLS) — aggregate_by_symbol() ignores the extras, so data/fo_data/'s
output is unchanged. This is what unlocks the options-range-selling research
track (PREREG_options_range_selling.md): NSE's F&O archive is the only
historical source of per-contract option PRICES in this repo, and this
downloader already handles both archive eras. research_vrp_gate.py consumes
download_fo_date() directly (small, sampled — one decision date + one expiry
date per cycle), not this module's aggregate/backfill path.

Usage:
    python download_fo_bhavcopy.py backfill 2015-01-01
    python download_fo_bhavcopy.py backfill 2015-01-01 2020-01-01   # resumable range
    python download_fo_bhavcopy.py 1        # just today (daily cron use)
"""
import io
import os
import sys
import time
import zipfile

import pandas as pd
import requests

OUTPUT_DIR = "../data/fo_data/"
PROGRESS_LOG = "../data/fo_backfill_progress.txt"

LEGACY_LAST_DATE = pd.Timestamp("2024-07-14")   # last date legacy format is available
UDIFF_FIRST_DATE = pd.Timestamp("2024-01-01")   # first date UDiFF format is available
EARLIEST_AVAILABLE = pd.Timestamp("2015-01-01")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/120 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.nseindia.com/",
}
SESSION = requests.Session()
SESSION.headers.update(HEADERS)


def init_nse_session():
    try:
        SESSION.get("https://www.nseindia.com", timeout=10)
        time.sleep(1)
    except Exception as e:
        print(f"Could not init NSE session: {e}")


def _fetch_zip_csv(url, retries=3):
    for attempt in range(retries):
        try:
            r = SESSION.get(url, timeout=20)
        except requests.exceptions.RequestException:
            time.sleep(2 * (attempt + 1))
            continue
        if r.status_code != 200:
            return None
        try:
            z = zipfile.ZipFile(io.BytesIO(r.content))
            with z.open(z.namelist()[0]) as f:
                return pd.read_csv(f)
        except Exception:
            return None
    return None


# Per-contract columns retained ON TOP OF the original 6 (2026-09-02, for the
# options-range-selling research track — PREREG_options_range_selling.md).
# aggregate_by_symbol() only ever reads the original 6 by name, so widening
# these does not change a single byte of the existing data/fo_data/ output —
# verified: it groups by Symbol/Instrument/OptionType and sums OI/OIChg/
# Volume, ignoring any other column present.
#   Expiry, Strike : NaN for FUTSTK rows (options-only fields)
#   Close, Settle   : legacy CLOSE is 0.0 for untraded strikes — SETTLE_PR is
#                     NSE's theoretical/settlement price and is what actually
#                     prices a position; prefer it, don't average the two
#   Underlying      : populated ONLY in the UDiFF era (2024-07-15 on) — NSE
#                     started publishing UndrlygPric directly. Legacy has no
#                     equivalent column; the underlying spot for a legacy
#                     date must come from that SAME date's near-month FUTSTK
#                     settle price (self-consistent, unadjusted, no cross-
#                     source join) — see research_vrp_gate.py's
#                     underlying_price(). Do NOT join price_data/ (yfinance-
#                     ADJUSTED) against this (Kite/NSE-UNADJUSTED) archive —
#                     the standing trap (NATIONALUM 1.744x in 2016).
EXTRA_COLS = ["Expiry", "Strike", "Close", "Settle", "Underlying"]


def download_legacy(date):
    mo_str = date.strftime("%b").upper()
    url = (f"https://nsearchives.nseindia.com/content/historical/DERIVATIVES/"
           f"{date.year}/{mo_str}/fo{date.strftime('%d')}{mo_str}{date.year}bhav.csv.zip")
    df = _fetch_zip_csv(url)
    if df is None:
        return None
    df.columns = [c.strip() for c in df.columns]
    # STOCK futures/options only (FUTSTK/OPTSTK) — index contracts (FUTIDX/
    # OPTIDX, e.g. NIFTY/BANKNIFTY) aren't per-stock signals we can attach.
    df = df[df["INSTRUMENT"].isin(["FUTSTK", "OPTSTK"])].copy()
    df = df.rename(columns={
        "SYMBOL": "Symbol", "INSTRUMENT": "Instrument", "OPTION_TYP": "OptionType",
        "OPEN_INT": "OI", "CHG_IN_OI": "OIChg", "CONTRACTS": "Volume",
        "EXPIRY_DT": "Expiry", "STRIKE_PR": "Strike", "CLOSE": "Close",
        "SETTLE_PR": "Settle",
    })
    df["Underlying"] = pd.NA          # not published pre-UDiFF, see EXTRA_COLS note
    return df[["Symbol", "Instrument", "OptionType", "OI", "OIChg", "Volume"]
              + EXTRA_COLS]


def download_udiff(date):
    url = (f"https://nsearchives.nseindia.com/content/fo/"
           f"BhavCopy_NSE_FO_0_0_0_{date.strftime('%Y%m%d')}_F_0000.csv.zip")
    df = _fetch_zip_csv(url)
    if df is None:
        return None
    df.columns = [c.strip() for c in df.columns]
    df = df[df["FinInstrmTp"].isin(["STF", "STO"])].copy()
    inst_map = {"STF": "FUTSTK", "STO": "OPTSTK"}
    df["Instrument"] = df["FinInstrmTp"].map(inst_map)
    df = df.rename(columns={
        "TckrSymb": "Symbol", "OptnTp": "OptionType",
        "OpnIntrst": "OI", "ChngInOpnIntrst": "OIChg", "TtlTradgVol": "Volume",
        "XpryDt": "Expiry", "StrkPric": "Strike", "ClsPric": "Close",
        "SttlmPric": "Settle", "UndrlygPric": "Underlying",
    })
    return df[["Symbol", "Instrument", "OptionType", "OI", "OIChg", "Volume"]
              + EXTRA_COLS]


def download_fo_date(date):
    """Returns the raw per-contract dataframe (stock F&O only) for one date,
    or None if unavailable (holiday, weekend, or outside NSE's archive range)."""
    date = pd.Timestamp(date)
    if date < EARLIEST_AVAILABLE:
        return None
    if date <= LEGACY_LAST_DATE:
        return download_legacy(date)
    return download_udiff(date)


def aggregate_by_symbol(raw_df, date):
    """Collapses all strikes/expiries for a date into one row per underlying
    symbol: futures OI/OIChg/volume, and options OI/volume split by call/put
    (summed across strikes+expiries — a market-wide positioning snapshot)."""
    rows = []
    for sym, g in raw_df.groupby("Symbol"):
        fut = g[g["Instrument"] == "FUTSTK"]
        opt = g[g["Instrument"] == "OPTSTK"]
        calls = opt[opt["OptionType"] == "CE"]
        puts = opt[opt["OptionType"] == "PE"]

        fut_oi = fut["OI"].sum()
        fut_oi_chg = fut["OIChg"].sum()
        fut_vol = fut["Volume"].sum()
        call_oi = calls["OI"].sum()
        put_oi = puts["OI"].sum()
        call_vol = calls["Volume"].sum()
        put_vol = puts["Volume"].sum()

        pcr_oi = (put_oi / call_oi) if call_oi > 0 else None
        pcr_vol = (put_vol / call_vol) if call_vol > 0 else None

        rows.append({
            "Date": date.strftime("%Y-%m-%d"), "Symbol": sym,
            "FutOI": fut_oi, "FutOIChg": fut_oi_chg, "FutVol": fut_vol,
            "CallOI": call_oi, "PutOI": put_oi, "CallVol": call_vol, "PutVol": put_vol,
            "PCR_OI": pcr_oi, "PCR_VOL": pcr_vol,
        })
    return pd.DataFrame(rows)


# ---------- Backfill (resumable, batched) ----------

def _load_progress():
    if not os.path.exists(PROGRESS_LOG):
        return set()
    with open(PROGRESS_LOG) as f:
        return set(line.strip() for line in f if line.strip())


def _mark_done(date_str):
    with open(PROGRESS_LOG, "a") as f:
        f.write(date_str + "\n")


def backfill(start_date, end_date=None, flush_every=60):
    end_date = end_date or pd.Timestamp.today()
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    init_nse_session()

    done = _load_progress()
    dates = pd.bdate_range(start_date, end_date)
    dates = [d for d in dates if d.strftime("%Y-%m-%d") not in done]
    print(f"{len(dates)} business days remaining ({len(done)} already done per {PROGRESS_LOG})")

    pending = {}
    fetched_since_flush = 0
    ok, missing = 0, 0

    def flush():
        nonlocal pending
        for sym, rows in pending.items():
            path = os.path.join(OUTPUT_DIR, f"{sym}.NS.csv")
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
        date_str = d.strftime("%Y-%m-%d")
        try:
            raw = download_fo_date(d)
        except Exception as e:
            print(f"  {date_str}: unexpected error, treating as missing ({e})")
            raw = None
        if raw is None or len(raw) == 0:
            missing += 1
        else:
            daily = aggregate_by_symbol(raw, d)
            for _, row in daily.iterrows():
                sym = row["Symbol"]
                pending.setdefault(sym, []).append(row.to_dict())
            ok += 1
            fetched_since_flush += 1
        _mark_done(date_str)

        if fetched_since_flush >= flush_every:
            flush()
            fetched_since_flush = 0
            print(f"  [{i+1}/{len(dates)}] flushed through {date_str} — {ok} ok, {missing} missing so far")

        time.sleep(0.4)

    flush()
    print(f"\nBackfill done. {ok} days fetched, {missing} missing (holidays/weekends/no-archive).")


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "backfill":
        start = sys.argv[2] if len(sys.argv) > 2 else EARLIEST_AVAILABLE.strftime("%Y-%m-%d")
        end = sys.argv[3] if len(sys.argv) > 3 else None
        backfill(pd.Timestamp(start), pd.Timestamp(end) if end else None)
        return

    # Daily-use mode: fetch just today (or --days-back N)
    days_back = int(sys.argv[1]) if len(sys.argv) > 1 else 1
    init_nse_session()
    today = pd.Timestamp.today()
    for i in range(days_back):
        d = today - pd.Timedelta(days=i)
        if d.weekday() >= 5:
            continue
        raw = download_fo_date(d)
        if raw is None or len(raw) == 0:
            print(f"{d.date()}: not available")
            continue
        daily = aggregate_by_symbol(raw, d)
        os.makedirs(OUTPUT_DIR, exist_ok=True)
        for _, row in daily.iterrows():
            path = os.path.join(OUTPUT_DIR, f"{row['Symbol']}.NS.csv")
            new_row = pd.DataFrame([row])
            if os.path.exists(path):
                existing = pd.read_csv(path)
                if row["Date"] in existing["Date"].values:
                    continue
                combined = pd.concat([existing, new_row], ignore_index=True)
            else:
                combined = new_row
            combined.sort_values("Date").to_csv(path, index=False)
        print(f"{d.date()}: {len(daily)} symbols updated")


if __name__ == "__main__":
    main()
