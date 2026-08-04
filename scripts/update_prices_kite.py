"""
update_prices_kite.py
---------------------
Daily price update from KITE CONNECT. Replaces download_data.py /
download_index.py / download_etf.py in the nightly pipeline.

WHY. Those three re-download the ENTIRE history from yfinance every night
(2015-01-01 onward for stocks, 2010 for the index). That is why yfinance's
glitches kept coming back: a bar with real Volume but NaN OHLC, or a session
dropped outright, was rewritten nightly no matter how many times it had been
repaired. Kite is the broker's own feed and does not exhibit either failure.

DESIGN — APPEND ONLY, NEVER REWRITE HISTORY.
This script only ever adds bars NEWER than what a file already holds. It never
touches existing rows. That is essential, because the two sources use different
conventions:

    Kite history is UNADJUSTED.  The existing CSVs are yfinance
    split/dividend-ADJUSTED.

Measured on NATIONALUM the kite/csv ratio drifts 1.801 (2016) -> 1.405 (2019)
-> 1.146 (2023) -> 1.000 (today). Neither source is wrong; they answer
different questions. Rewriting history from Kite would silently change every
past price, moving every S/R pivot, altering the momentum scorer's returns, and
invalidating all four P(touch) tables plus every backtest figure on file.

So: yfinance's archive stays as the historical record, Kite supplies everything
from here on, and the two are spliced only where they agree. Before appending,
each symbol's overlapping bars are compared and the symbol is SKIPPED if they
disagree beyond AGREE_TOL — that check is what makes mixing two sources safe
rather than merely convenient. (Recent bars agree to 0.000% because no
corporate action has intervened; that stops being true the further back you go,
which is exactly why history is never rewritten.)

NEVER WRITES A PARTIAL CANDLE. Kite returns the in-progress daily bar during
market hours. Writing it would put a half-formed High/Low/Close into
price_data — the pollution trim_partial.py exists to remove, and it invents
swing points that vanish at the close. cutoff_date() caps writes at the last
CLOSED session (16:00 IST rule, matching drop_partial_candle).

Usage:
    python update_prices_kite.py              # stocks + index + ETFs
    python update_prices_kite.py --dry-run    # report only
    python update_prices_kite.py RELIANCE.NS  # subset
"""
import os
import sys
import time
import datetime as dt

import numpy as np
import pandas as pd

PRICE_DIR = "../data/price_data/"
INDEX_DIR = "../data/index_data/"
ETF_DIR = "../data/etf_data/"

# Index CSVs are keyed by filename, not a tradable .NS symbol.
INDEX_SYMBOLS = {"nifty50.csv": "NIFTY 50", "indiavix.csv": "INDIA VIX"}
ETF_SYMBOLS = ["GOLDBEES", "MON100"]

OHLC = ["Open", "High", "Low", "Close"]
AGREE_TOL = 0.005      # max |kite/csv - 1| on overlapping bars
OVERLAP_BARS = 5
LOOKBACK_DAYS = 40     # enough to cover a long weekend + a missed week

# Kite's documented historical_data rate limit is 3 requests/second. This
# script makes one call per symbol (up to ~500), back to back, with no pacing
# — that WILL throttle partway through a run (seen live: "Too many requests"
# on ~2 calls, plus a cluster of "disagree" verdicts on symbols that likely got
# a partial/retried response rather than a real corporate-action mismatch).
# REQUEST_DELAY paces normal calls; RETRY_DELAY backs off once on a throttle
# instead of giving up and silently skipping the symbol.
REQUEST_DELAY = 0.34   # ~3/sec, matching the documented cap
RETRY_DELAY = 2.0
MAX_RETRIES = 2


def cutoff_date():
    """Newest date safe to write: yesterday while today's session is open."""
    now = dt.datetime.now()
    if now.weekday() <= 4 and now.hour < 16:
        return now.date() - dt.timedelta(days=1)
    return now.date()


def load_csv(path):
    df = pd.read_csv(path)
    df["Date"] = pd.to_datetime(df["Date"], errors="coerce")
    return df[df["Date"].notna()].sort_values("Date")


def last_usable(df, cutoff):
    """Newest bar with real OHLC at or before cutoff.

    Usability, not presence: a NaN-OHLC row (yfinance's signature failure) is
    dropped by every consumer, so a file can look current and be stale. Capping
    at the cutoff stops a partial bar from masking an older missing session.
    """
    cols = [c for c in OHLC if c in df.columns]
    g = df.dropna(subset=cols) if cols else df
    g = g[g["Date"] <= pd.Timestamp(cutoff)]
    return g["Date"].max() if len(g) else None


def _fetch_with_retry(kite, token, start, end):
    """One historical_data call, retrying through a rate-limit response.

    A throttled call must NOT be treated the same as a real API failure — the
    caller used to break the chunk loop and (for the most recent, first-tried
    chunk) return no data at all, silently dropping the symbol for the whole
    run. AFFLE and APLAPOLLO did exactly this. Retrying with backoff turns a
    throttle into a brief pause instead of a missed update.
    """
    for attempt in range(MAX_RETRIES + 1):
        try:
            return kite.historical_data(token, start, end, "day"), None
        except Exception as e:
            msg = str(e)
            throttled = "Too many requests" in msg or "429" in msg
            if throttled and attempt < MAX_RETRIES:
                time.sleep(RETRY_DELAY * (attempt + 1))
                continue
            return None, msg[:70]


def kite_daily(kite, token, start, end):
    """Daily bars, chunked under Kite's 2000-day per-request cap, paced and
    retried to stay under the ~3 req/sec rate limit across a 500-symbol run."""
    rows, cur_end = [], end
    while cur_end > start:
        cur_start = max(start, cur_end - dt.timedelta(days=1900))
        time.sleep(REQUEST_DELAY)
        chunk, err = _fetch_with_retry(kite, token, cur_start, cur_end)
        if err is not None:
            print(f"      kite error: {err}")
            break
        rows += chunk
        if cur_start <= start:
            break
        cur_end = cur_start - dt.timedelta(days=1)
    if not rows:
        return None
    k = pd.DataFrame(rows)
    k["date"] = pd.to_datetime(k["date"]).dt.tz_localize(None).dt.normalize()
    return k.drop_duplicates("date").sort_values("date").set_index("date")


def agreement(df, k):
    """|ratio-1| at the SPLICE POINT — the last bar the two sources share.

    Deliberately the newest common bar rather than an average over a window.
    yfinance back-adjusts prior bars when a dividend goes ex, so an overlap
    window straddling an ex-date shows a step: PFC on 2026-07-31 read 1.0094
    through 07-30 and exactly 1.0000 from 07-31 (a ~0.94% dividend). Averaging
    across that step fails a splice that is perfectly safe, because the
    adjustment only touches bars BEFORE the ex-date — everything from the
    splice point forward already agrees.

    Returns (worst_at_splice, n_compared). Comparing the newest few bars
    guards against a one-off bad print while staying on the correct side of
    any ex-date.
    """
    dates = pd.DatetimeIndex(df["Date"]).normalize()
    # Compare at the newest common bar that has a USABLE close.
    #
    # NaN-OHLC rows must be skipped here (2026-08-05 fix). yfinance's signature
    # failure writes a row with real Volume but NaN OHLC; that row is a real
    # date, so it landed on the splice point and made this function return NaN.
    # `NaN > AGREE_TOL` is False, so the disagreement guard silently PASSED —
    # the safety check that exists to stop a bad splice was comparing against
    # nothing and approving everything. `c <= 0` is also False for NaN, so
    # neither guard caught it. WIPRO/PFC/ANGELONE/SHRIRAMFIN sat with NaN
    # closes while the updater reported them "current".
    good = df[df["Close"].notna()]
    if good.empty:
        return None, 0
    gdates = pd.DatetimeIndex(good["Date"]).normalize()
    idx = gdates.intersection(k.index)
    if len(idx) == 0:
        return None, 0
    t = idx[-1]                       # the splice point itself
    c = good.loc[gdates == t, "Close"]
    if not len(c):
        return None, 0
    c = float(c.iloc[-1])
    if not np.isfinite(c) or c <= 0:
        return None, 0
    kc = float(k.loc[t, "close"])
    if not np.isfinite(kc):
        return None, 0
    return abs(kc / c - 1.0), 1


def append_new(path, k, cutoff, symbol=None, dry=False):
    """Append Kite bars newer than the file's last usable bar. Returns
    (status, n_added). Existing rows are never modified."""
    df = load_csv(path)
    last = last_usable(df, cutoff)
    if last is None:
        return "empty", 0

    worst, _n = agreement(df, k)
    if worst is None:
        return "no-overlap", 0
    # Explicit NaN check: `NaN > AGREE_TOL` is False, so a NaN would sail past
    # the comparison below and approve a splice nothing had actually validated.
    if not np.isfinite(worst):
        return "no-overlap", 0
    if worst > AGREE_TOL:
        return f"disagree {worst*100:.2f}%", 0

    new = k[(k.index > pd.Timestamp(last)) & (k.index <= pd.Timestamp(cutoff))]
    if new.empty:
        return "current", 0
    if dry:
        return "would-add", len(new)

    cols = list(df.columns)
    rows = []
    for t, r in new.iterrows():
        row = {c: None for c in cols}
        row["Date"] = t
        for src, dst in [("open", "Open"), ("high", "High"), ("low", "Low"),
                         ("close", "Close"), ("volume", "Volume")]:
            if dst in row:
                row[dst] = float(r[src])
        if "Symbol" in row and symbol:
            row["Symbol"] = symbol
        rows.append(row)

    out = pd.concat([df, pd.DataFrame(rows)], ignore_index=True)
    out = out.drop_duplicates("Date", keep="last").sort_values("Date")
    out.to_csv(path, index=False)
    return "added", len(rows)


def main():
    argv = sys.argv[1:]
    dry = "--dry-run" in argv
    only = {a for a in argv if not a.startswith("--")}

    cutoff = cutoff_date()
    print(f"Kite daily update — writing bars up to {cutoff} "
          f"{'(DRY RUN)' if dry else ''}")

    try:
        import kite_auth
        kite = kite_auth.get_kite_client()
    except Exception as e:
        print(f"ABORT: kite unavailable ({e})")
        sys.exit(1)
    if kite is None:
        print("ABORT: no cached Kite token — run: python kite_auth.py refresh")
        sys.exit(1)

    print("Loading instrument maps...")
    nse = {i["tradingsymbol"]: i["instrument_token"] for i in kite.instruments("NSE")}
    try:
        idx_map = {i["tradingsymbol"]: i["instrument_token"]
                   for i in kite.instruments("NSE") if i.get("segment") == "INDICES"}
    except Exception:
        idx_map = {}

    start = cutoff - dt.timedelta(days=LOOKBACK_DAYS)
    counts = {}

    def run(path, token, symbol=None, label=""):
        k = kite_daily(kite, token, start, cutoff)
        if k is None or k.empty:
            counts["no-data"] = counts.get("no-data", 0) + 1
            return
        status, n = append_new(path, k, cutoff, symbol=symbol, dry=dry)
        counts[status.split()[0]] = counts.get(status.split()[0], 0) + 1
        if status in ("added", "would-add") or status.startswith("disagree"):
            print(f"  {label:22} {status}" + (f" +{n}" if n else ""))

    # ---- stocks ----
    files = sorted(f for f in os.listdir(PRICE_DIR) if f.endswith(".csv"))
    if only:
        files = [f for f in files if f in only or f[:-4] in only]
    print(f"\nStocks ({len(files)} files)")
    missing = []
    for i, f in enumerate(files, 1):
        if i % 100 == 0:
            print(f"    [{i}/{len(files)}]", flush=True)
        base = f[:-4].replace(".NS", "")
        token = nse.get(base)
        if not token:
            missing.append(base)
            counts["no-token"] = counts.get("no-token", 0) + 1
            continue
        run(PRICE_DIR + f, token, symbol=f[:-4], label=base)

    # ---- index ----
    if not only:
        print("\nIndices")
        for fn, tsym in INDEX_SYMBOLS.items():
            path = INDEX_DIR + fn
            if not os.path.exists(path):
                continue
            token = idx_map.get(tsym)
            if not token:
                print(f"  {tsym:22} no token (index feed unavailable)")
                continue
            run(path, token, label=tsym)

        # ---- ETFs ----
        print("\nETFs")
        for sym in ETF_SYMBOLS:
            path = ETF_DIR + f"{sym}.NS.csv"
            if not os.path.exists(path):
                continue
            token = nse.get(sym)
            if not token:
                print(f"  {sym:22} no NSE token")
                continue
            run(path, token, symbol=f"{sym}.NS", label=sym)

    print("\nSummary:", ", ".join(f"{k}={v}" for k, v in sorted(counts.items())))
    if missing:
        print(f"No Kite token for {len(missing)} symbol(s) — delisted/renamed, "
              f"left untouched: {', '.join(sorted(missing)[:10])}"
              + (" ..." if len(missing) > 10 else ""))


if __name__ == "__main__":
    main()
