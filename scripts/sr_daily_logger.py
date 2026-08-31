"""
sr_daily_logger.py
-------------------
Logs one row per stock per day: Symbol, Date, CMP, S1, S1_prob, S1_n,
R1, R1_prob, R1_n — plus S2/R2 (+prob+n) only when that level's reach
probability beats the empirical base rate (~66%) from sr_reach_table.json.

WATCHLIST is a FIXED validation panel — deliberately hardcoded so the same
stocks are logged every day and sr_monthend_analysis.py measures model
accuracy on a consistent panel. Do NOT make it dynamic (holdings/top-N);
changing names day-to-day undermines the accuracy analysis.

Run this once daily (after 3:30pm IST, after download_data.py).

Usage:
    python sr_daily_logger.py                      ← uses WATCHLIST below
    python sr_daily_logger.py AARTIIND.NS BEL.NS    ← override watchlist
"""

import os, sys
from datetime import datetime
import pandas as pd

from support_resistance import (load_stock, get_all_levels, reach_probability_v2,
                                _load_reach_table, INDEX_FILES, is_market_open)
import sr_horizon as H

LOG_PATH = "../data/sr_daily_log.csv"

# Log the last session STRICTLY BEFORE today, whatever time the run happens
# (user spec 2026-08-19). Guarantees every row describes a COMPLETED, SETTLED
# session, so a 17:00 run and a 00:30 run produce the identical row instead of
# the evening one capturing pre-settlement prices that get revised overnight.
# See drop_today_bar() for the evidence that motivated this. Set False to
# restore the old "log whatever the newest bar is" behaviour.
LOG_PREVIOUS_SESSION = True

# TODAY-ONLY snapshot, OVERWRITTEN each run — a convenience view of "where do
# things stand right now". Nothing is lost by overwriting: every day is
# preserved in the monthly file below.
TODAY_PATH = "../data/sr_today.csv"

# One file PER CALENDAR MONTH, appended daily: ../data/sr_month_2026-08.csv.
# Rolls automatically on the 1st, so a month's data never mixes with the next
# and the file stays small enough to open in a spreadsheet.
MONTH_PATH_FMT = "../data/sr_month_{ym}.csv"

# Columns averaged in the end-of-month summary rows. CMP gives the month's
# mean traded level; S1/R1 show where the model put the levels on average,
# which is steadier than any single day's reading.
AVG_COLUMNS = ["CMP", "S1", "R1"]

# Date value marking a summary row in a month file. Not a real date, so it
# sorts last and is trivial to filter out when reading the file back.
AVG_ROW_LABEL = "AVG"

# PANEL CHANGED 2026-08-04 (user): 15 -> 61 names. 13 of the original 15 are
# retained, so their history stays continuous; AARTIIND and GOLDBEES were
# dropped by explicit instruction and their existing rows remain in the log as
# history that simply stops advancing.
#
# This is still a FIXED panel — the point is that it changes only when the user
# says so, never automatically from holdings or momentum rank (see memory
# sr-daily-log-fixed-panel). Any accuracy figure that spans 2026-08-04 pools
# two different panel compositions, so read it with that in mind; the enlarged
# panel stands on its own after ~3 weeks of its own data.
WATCHLIST = [
    "BEL.NS", "KALYANKJIL.NS",
    "KFINTECH.NS", "RELIANCE.NS", "VOLTAS.NS", "WIPRO.NS",
    "CONCOR.NS", "COCHINSHIP.NS", "KAYNES.NS", "NATIONALUM.NS", "RECLTD.NS",
    "SAIL.NS", "TMPV.NS",
    "ETERNAL.NS", "DELHIVERY.NS", "KPITTECH.NS", "BDL.NS", "PNB.NS",
    "SUZLON.NS", "ADANIPOWER.NS", "IREDA.NS", "CDSL.NS", "BSE.NS", "RVNL.NS",
    "NIFTY50", "SHRIRAMFIN.NS", "MCX.NS", "ABCAPITAL.NS", "AXISBANK.NS",
    "JIOFIN.NS", "ADANIENT.NS", "ADANIPORTS.NS", "MAZDOCK.NS", "ASHOKLEY.NS",
    "PAYTM.NS", "RBLBANK.NS", "SBIN.NS", "BHEL.NS", "HAL.NS", "PFC.NS",
    "NAUKRI.NS", "TITAN.NS", "BHARTIARTL.NS", "DIXON.NS", "AMBER.NS",
    "MARUTI.NS", "MOTHERSON.NS", "INFY.NS", "TCS.NS", "COFORGE.NS",
    "MPHASIS.NS", "ANGELONE.NS", "SONACOMS.NS", "LT.NS", "NTPC.NS",
    "ADANIGREEN.NS", "SWIGGY.NS", "HAVELLS.NS", "GAIL.NS", "ITC.NS",
    "CROMPTON.NS",
]

COLUMNS = [
    "Symbol", "Date", "CMP", "High", "Low",
    "S1", "S1_prob",
    "R1", "R1_prob",
    "S2", "S2_prob",
    "R2", "R2_prob",
    # Horizon the probabilities refer to: the month's rebalance date (last
    # Tuesday) and the trading days remaining to it. Logged so accuracy can
    # later be scored against the horizon actually quoted, instead of assuming
    # a fixed 21 days. Older rows lack these — readers must treat them as 21d.
    "HorizonEnd", "HorizonDays",
    # Would the base-rate gate have DISPLAYED this S2/R2? The level itself is
    # always logged (it must be, to be measurable); this records the display
    # decision so it stays reconstructible.
    "S2_shown", "R2_shown",
]


def normalize_symbol(sym):
    """Uppercase, and append .NS only for actual stocks.

    INDICES ARE NOT .NS TICKERS. support_resistance.INDEX_FILES maps names like
    NIFTY50 to their own CSV, so blindly appending the suffix turned "NIFTY50"
    into "NIFTY50.NS", which resolves to nothing — the panel silently logged
    60 of 61 names with no error. Same trap as the documented .NS.NS
    double-suffix bug: the symbol looks plausible and just fails to load.
    """
    s = sym.strip().upper()
    if s in INDEX_FILES:
        return s
    return s if s.endswith(".NS") else s + ".NS"


def drop_partial_candle(df):
    """Consumer-side twin of trim_partial.py: if the last row is TODAY and the
    session hasn't closed (weekday before 16:00 IST), it is a partial candle —
    drop it. trim_partial cleans the CSVs in the pipeline, but other processes
    (parallel research scripts, ad-hoc downloads) can re-write partial rows
    into price_data at any time, so the logger must not trust file state."""
    now = datetime.now()
    if now.weekday() <= 4 and now.hour < 16 and \
            df.index[-1].date() == now.date():
        return df.iloc[:-1]
    return df


def drop_today_bar(df, now=None):
    """Log the last session STRICTLY BEFORE today — never today's own bar.

    WHY (user spec 2026-08-19). drop_partial_candle above only removes today's
    bar before 16:00, so an evening run logged TODAY. That is the window in
    which Kite's historical_data() has not yet settled: official NSE
    settlement (Bhavcopy) typically finishes 19:00-20:00 IST or later, so a
    17:00-23:00 run records prices that are still in flux and get revised
    overnight. Caught in the log itself: the 2026-08-17 rows carried CMP
    values (RELIANCE 1322.0, WIPRO 179.1, TITAN 5049.0) that match NO bar in
    the archive — 1322.0 turned out to be 08-18's settled close. Since
    sr_daily_log.csv is the MEASUREMENT RECORD the S/R model is scored
    against, a row priced at a number that never existed scores the model on
    a price that never happened.

    Logging only completed, settled sessions makes the row identical no
    matter what time of day the pipeline runs. The 00:30 schedule is
    unaffected — at 00:30 there is no bar dated "today" yet, so nothing is
    dropped and behaviour is exactly as before. Only 16:00-24:00 runs change.

    Uses >= rather than == so a future-dated bar (clock skew during an NTP
    sync has produced these here before) is also removed rather than logged.
    """
    if not LOG_PREVIOUS_SESSION:
        return df
    now = now or datetime.now()
    while len(df) and df.index[-1].date() >= now.date():
        df = df.iloc[:-1]
    return df


def log_stock(sym, live_price=None):
    df = load_stock(sym)
    if df is None or len(df) < 60:
        print(f"  ⚠️  {sym}: no data, skipped")
        return None
    df = drop_partial_candle(df)
    df = drop_today_bar(df)
    if len(df) < 60:
        print(f"  ⚠️  {sym}: no completed data, skipped")
        return None

    # A live tick describes NOW; the bar being logged is a PRIOR session.
    # Using both would date the row to one session and price it from another
    # — the same "two points in time in one row" error that --as-of already
    # suppresses live quotes to avoid. The Date stamp wins; the tick is dropped.
    if live_price is not None and df.index[-1].date() != datetime.now().date():
        live_price = None

    close      = float(df["Close"].iloc[-1])
    # LIVE CMP when the market is open, else the last completed close. Run at
    # 11:00 and again at 14:00 and the levels shift with price, because CMP
    # feeds level selection (see support_resistance.get_all_levels). After
    # 15:30 the live quote IS that day's close, so the evening pipeline row is
    # identical to a close-based one — the end-of-day record is unchanged.
    #
    # Rows still dedupe on (Date, Symbol), so a later run REPLACES an earlier
    # one on the same day rather than accumulating. The file always shows the
    # most recent state; it is not an intraday time series.
    cur = float(live_price) if live_price else close
    # High/Low of the last COMPLETED bar. Deliberately not stretched to include
    # a live price: they describe that bar, and mixing a live tick into them
    # would make High/Low and CMP describe different windows.
    day_high   = float(df["High"].iloc[-1])
    day_low    = float(df["Low"].iloc[-1])
    sups, ress = get_all_levels(df, symbol=sym, cur=cur)

    # Horizon = from this snapshot's DATA date to the month's rebalance date
    # (last Tuesday). Probabilities are quoted for that window, not a fixed 21d.
    data_date = df.index[-1]
    h_end = H.horizon_end(data_date)
    h_cal = H.project_calendar_forward(H.load_trading_calendar(), h_end)
    h_days = H.trading_days_until(data_date, h_end, h_cal)

    def level_and_prob(levels, i, direction):
        if len(levels) <= i:
            return None, None, None
        p = levels[i][0]
        prob, n = reach_probability_v2(df, p, direction, h_days, cur)
        return p, prob, n

    s1, s1_prob, s1_n = level_and_prob(sups, 0, "down")
    r1, r1_prob, r1_n = level_and_prob(ress, 0, "up")

    s2_raw, s2_prob, s2_n = level_and_prob(sups, 1, "down")
    r2_raw, r2_prob, r2_n = level_and_prob(ress, 1, "up")

    # S2/R2 are ALWAYS logged (2026-07-31). They used to be suppressed unless
    # their probability beat the base rate, but this file is the MEASUREMENT
    # record: a level that is never written can never be scored, so the gate
    # was destroying exactly the low-probability observations needed to
    # calibrate the low end — and it biased the logged sample toward levels the
    # model already liked. Filtering is a DISPLAY concern (analyse_table), not
    # a logging one. `S2_shown`/`R2_shown` preserve what the gate would have
    # decided, so display behaviour stays reconstructible from the log.
    gate = _load_reach_table().get("base_rate", 50)
    gate = H.scale_probability_to_horizon(gate, h_days) or gate
    s2, r2 = s2_raw, r2_raw
    s2_shown = bool(s2_prob is not None and s2_prob > gate)
    r2_shown = bool(r2_prob is not None and r2_prob > gate)

    return {
        "Symbol":  sym.replace(".NS", ""),
        # Stamp the DATA date (last completed candle), not the wall-clock date:
        # the pipeline may run at any hour (boot-time catch-up), and after
        # trim_partial a mid-market run's data still ends at yesterday's close.
        # Wall-clock stamping logged that same snapshot under a second date,
        # double-counting it and shifting its forward window in
        # sr_monthend_analysis. Data-date stamping makes runs idempotent.
        "Date":    df.index[-1].strftime("%Y-%m-%d"),
        "CMP":     round(cur, 2),
        # Which basis this row's CMP came from. The month AVG rows average
        # CLOSE rows only, so an intraday snapshot never skews the monthly
        # figure toward whatever time of day you happened to run.
        "CMP_basis": "live" if live_price else "close",
        "High":    round(day_high, 2),
        "Low":     round(day_low, 2),
        "S1":      s1, "S1_prob": s1_prob, "S1_n": s1_n,
        "R1":      r1, "R1_prob": r1_prob, "R1_n": r1_n,
        "S2":      s2, "S2_prob": s2_prob, "S2_n": s2_n,
        "R2":      r2, "R2_prob": r2_prob, "R2_n": r2_n,
        "HorizonEnd":  h_end.strftime("%Y-%m-%d"),
        "HorizonDays": h_days,
        "S2_shown":    s2_shown,
        "R2_shown":    r2_shown,
    }


def merge_log(new_df, log_path):
    """Replace existing rows matching new_df's (Date, Symbol) pairs, keep the rest.
    Rows carry per-symbol DATA dates, so dedupe must be pair-wise, not run-date."""
    if os.path.exists(log_path):
        existing = pd.read_csv(log_path)
        pairs = set(zip(new_df["Date"], new_df["Symbol"]))
        keep = [(d, s) not in pairs
                for d, s in zip(existing["Date"], existing["Symbol"])]
        combined = pd.concat([existing[keep], new_df], ignore_index=True)
    else:
        combined = new_df
    return combined.sort_values(["Symbol", "Date"])


def month_path_for(date_str):
    """../data/sr_month_YYYY-MM.csv for a row's DATA date.

    Keyed on the data date, not wall-clock: a late/catch-up run logging the
    31st's data on the 1st belongs in the OLD month's file, or the month
    boundary would silently split one session across two files.
    """
    return MONTH_PATH_FMT.format(ym=str(date_str)[:7])


def month_is_complete(daily, ym):
    """Has this month's data collection finished?

    True once a logged DATA date reaches the month's rebalance day (the last
    Tuesday). Uses >= rather than == on purpose: if that Tuesday is an NSE
    holiday no row will ever fall exactly on it, and an equality test would
    silently never write the averages for that month.
    """
    year, month = int(ym[:4]), int(ym[5:7])
    last_tue = H.last_tuesday_of_month(year, month)
    dates = pd.to_datetime(daily["Date"], errors="coerce").dropna()
    return bool(len(dates)) and dates.max() >= last_tue


def build_avg_rows(daily):
    """One AVG summary row per symbol: the mean of each AVG_COLUMN over the
    month's logged days.

    Computed from the DAILY rows only — any existing AVG rows are stripped by
    the caller first, so re-running never averages an average back into itself.
    """
    # Average CLOSE rows only. Including intraday snapshots would weight a day
    # you happened to run five times five times as heavily, so the average
    # would describe your run schedule rather than the market. Falls back to
    # all rows for legacy files written before CMP_basis existed.
    if "CMP_basis" in daily.columns:
        closes = daily[daily["CMP_basis"].fillna("close") == "close"]
        if len(closes):
            daily = closes

    out = []
    for sym, grp in daily.groupby("Symbol", sort=True):
        row = {"Symbol": sym, "Date": AVG_ROW_LABEL}
        for col in AVG_COLUMNS:
            if col in grp.columns:
                vals = pd.to_numeric(grp[col], errors="coerce").dropna()
                row[col] = round(float(vals.mean()), 2) if len(vals) else None
        row["Days"] = int(len(grp))   # how many sessions the average covers
        out.append(row)
    return pd.DataFrame(out)


def split_daily_rows(df):
    """(daily_rows, had_avg_rows) — AVG summary rows are never treated as data."""
    if "Date" not in df.columns:
        return df, False
    is_avg = df["Date"].astype(str).str.upper() == AVG_ROW_LABEL
    return df[~is_avg].copy(), bool(is_avg.any())


def ordered_columns(df):
    """COLUMNS order first, then anything extra (e.g. the AVG rows' Days)."""
    out = [c for c in COLUMNS if c in df.columns]
    out += [c for c in df.columns if c not in out]
    return out


def write_today(new_df, path=None):
    """Overwrite the today-only snapshot. One row per stock, no averages —
    a single day has nothing to average."""
    path = path or TODAY_PATH
    snap = new_df.sort_values("Symbol")
    snap = snap[[c for c in COLUMNS if c in snap.columns]]
    snap.to_csv(path, index=False)
    return path


def write_month(new_df, path_fmt=None):
    """Append today's rows to this month's file, recomputing the running
    averages. Rows are deduped on (Date, Symbol) exactly as the main log is,
    so a re-run replaces its own rows rather than double-counting them into
    the averages.

    Rows are grouped by their own DATA date, so a run that straddles a month
    boundary (a late catch-up logging the 31st alongside the 1st) files each
    row under the right month instead of lumping both into one.
    """
    path_fmt = path_fmt or MONTH_PATH_FMT
    written = []
    for ym, grp in new_df.groupby(new_df["Date"].astype(str).str[:7]):
        path = path_fmt.format(ym=ym)
        combined = merge_log(grp, path)

        # Drop any AVG rows already in the file before doing anything else:
        # they are derived output, and letting one survive into the next
        # merge would feed an average back into the next average.
        daily, _ = split_daily_rows(combined)
        daily = daily.sort_values(["Symbol", "Date"])

        complete = month_is_complete(daily, ym)
        if complete:
            out = pd.concat([daily, build_avg_rows(daily)], ignore_index=True)
        else:
            out = daily

        out = out[ordered_columns(out)]
        out.to_csv(path, index=False)
        written.append((path, len(daily), complete))
    return written


def main():
    symbols = sys.argv[1:] if len(sys.argv) > 1 else WATCHLIST

    print(f"\nLogging S/R snapshot for {len(symbols)} stocks")
    print("─" * 50)

    # Live quotes drive CMP (and therefore level selection) so a mid-session
    # run reflects where price actually is.
    #
    # MARKET-HOURS GUARD (added 2026-08-05, after a real corruption). The
    # earlier comment claimed "after the close the live quote is that day's
    # close, so the evening row is unchanged". That is FALSE in practice:
    # Kite keeps serving a last-traded price after 15:30 which need not equal
    # the official close, and yfinance can serve a delayed or pre-open tick.
    # On 2026-08-04 that wrote CMP 420.00 for ABCAPITAL whose close was 417.00
    # (and whose PREVIOUS close was 424.35) — so the row matched neither day,
    # and because CMP drives level selection, S1/R1 came out nearly identical
    # to 08-03's, looking like duplicated data.
    #
    # The log is the MEASUREMENT RECORD. A row must describe one point in time:
    # during the session, the live price; outside it, that session's CLOSE.
    # Never a stale tick pretending to be either.
    live = {}
    if LOG_PREVIOUS_SESSION:
        # Rows describe the last COMPLETED session, so a live tick has nothing
        # to contribute — log_stock would discard it anyway (a row cannot be
        # dated to one session and priced from another). Skipping the fetch
        # avoids a pointless Kite call on every mid-session run.
        print("  ⓘ Logging the last COMPLETED session — CMP = that session's "
              "close (no live fetch).")
    elif is_market_open():
        syms = [s for s in (normalize_symbol(x) for x in symbols)
                if s not in INDEX_FILES]
        try:
            from support_resistance import fetch_live_prices
            live_prices, _ = fetch_live_prices(syms)
            live = live_prices or {}
        except Exception as e:
            print(f"  ⚠️  live quotes unavailable ({e}) — using last close.")
    else:
        print("  ⓘ Market closed — CMP = last completed close (no live fetch).")

    rows = []
    for sym in symbols:
        sym = normalize_symbol(sym)
        row = log_stock(sym, live_price=live.get(sym.replace(".NS", "")))
        if row:
            rows.append(row)
            print(f"  ✅ {row['Symbol']:<14} {row['Date']}  CMP ₹{row['CMP']}")

    if not rows:
        print("Nothing logged.")
        return

    new_df = pd.DataFrame(rows, columns=COLUMNS)
    combined = merge_log(new_df, LOG_PATH)
    combined.to_csv(LOG_PATH, index=False)

    # Today-only snapshot (overwritten) + this month's appending file with
    # running per-stock averages. The cumulative LOG_PATH above stays as the
    # long-run measurement record that sr_monthend_analysis reads.
    today_path = write_today(new_df)
    month_written = write_month(new_df)

    print("─" * 50)
    print(f"Logged {len(rows)} rows to {LOG_PATH}")
    print(f"Total rows in log: {len(combined)}")
    print(f"Today snapshot   : {today_path} ({len(new_df)} rows, overwritten)")
    for path, n, complete in month_written:
        tag = ("month COMPLETE — AVG rows written"
               if complete else "month in progress — AVG rows pending last Tuesday")
        print(f"Month file       : {path} ({n} daily rows, {tag})")

    # Data-date stamping means a symbol whose price CSV lagged (missed run,
    # yfinance drop for that one name) silently logs under an OLDER date than
    # its panel-mates, with no error — that's how the 2026-07-22 gap happened
    # (13/15 stocks stayed on 07-21 for a day, unnoticed until a manual check).
    # Flag it immediately instead: warn on any row dated behind today's max.
    #
    # SPLIT INTO TWO CATEGORIES (2026-08-05). A lagging symbol used to get one
    # generic "hasn't updated yet, re-run later" message regardless of cause —
    # which is actively wrong advice for a symbol mid dividend/split
    # adjustment (re-running does nothing; it clears on its own over a few
    # sessions) and made a real, fixable glitch indistinguishable from that
    # normal, harmless case. update_prices_kite.py now writes its diagnosis
    # to corporate_action_watch.json; read it here rather than re-deriving the
    # same answer with a second set of Kite calls.
    latest = new_df["Date"].max()
    lagging = new_df[new_df["Date"] != latest]
    if not lagging.empty:
        corp_watch = set()
        try:
            import json
            with open("../data/corporate_action_watch.json") as f:
                watch = json.load(f)
            if watch.get("updated") == str(datetime.now().date()):
                corp_watch = set(watch.get("symbols", []))
        except Exception:
            pass

        corp = lagging[lagging["Symbol"].isin(corp_watch)]
        other = lagging[~lagging["Symbol"].isin(corp_watch)]

        if len(other):
            print(f"\n⚠️  {len(other)} symbol(s) logged BEHIND today's latest "
                  f"({latest}) — investigate, this is NOT expected:")
            for _, r in other.iterrows():
                print(f"      {r['Symbol']:<14} stuck at {r['Date']}")
            print("   Re-run `python update_prices_kite.py`, or check that "
                  "symbol's price_data/etf_data CSV.")

        if len(corp):
            # This block used to say "NOT an error, nothing to fix ...
            # self-resolves in a few sessions". It does NOT self-resolve once
            # the gap exceeds update_prices_kite.AGREE_TOL: the refused bar
            # stays the newest bar and therefore stays the splice point, so
            # the symbol freezes for good. Five symbols have now frozen this
            # way under a "nothing to fix" banner (CHENNPETRO/INDUSTOWER/
            # HINDPETRO in August, BATAINDIA/CESC for 9 sessions to 2026-08-31)
            # and the missing sessions are unrecoverable if the name is needed
            # live. Telling the operator the exact repair command matters more
            # than reassuring them.
            print(f"\n⚠️  {len(corp)} symbol(s) behind after a dividend/split: "
                  + ", ".join(corp["Symbol"].tolist()))
            print("   Kite's price and the archive disagree because the "
                  "archive has not applied the")
            print("   adjustment. A SMALL gap does clear by itself; a large "
                  "one never does — the refused")
            print("   bar stays the splice point and the symbol freezes. If "
                  "these are still behind")
            print("   tomorrow, repair with:")
            print("       python readjust_archive.py "
                  + " ".join(corp["Symbol"].tolist()[:6]) + " --apply")
            print("       python update_prices_kite.py")


if __name__ == "__main__":
    main()
