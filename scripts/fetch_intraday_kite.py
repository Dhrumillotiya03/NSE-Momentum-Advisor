"""
fetch_intraday_kite.py
----------------------
Bulk-download 15-minute bars from Kite Connect into ../data/intraday_data/.

RESEARCH DATA ONLY. This directory is NOT part of the trading pipeline.

WHY IT MUST NOT GO IN price_data/
---------------------------------
core.market_breadth_pct() and core.liquid_universe() GLOB price_data/ to define
the tradable universe. Anything written there becomes eligible to be bought.
Worse, Kite history is UNADJUSTED while price_data/ is yfinance-ADJUSTED, so a
stray file there would feed the momentum scorer prices on a different scale.
Measured kite/csv close ratio on NATIONALUM: 1.744 (2016) -> 1.405 (2019) ->
1.110 (2023) -> 1.000 (today). See update_prices_kite.py's header for the same
hazard on daily bars.

THE ADJUSTMENT RULE THAT MAKES THIS SAFE
----------------------------------------
Every study built on this data must take levels, current price AND forward path
from the SAME source. P(touch) and containment are pure DISTANCE RATIOS, so
mixing an adjusted level with an unadjusted path computes a distance across two
price scales — a 74% error on NATIONALUM in 2016, concentrated in exactly the
far-distance cells the touch table exists to get right.

VERIFIED CAPABILITY (probed 2026-08-04, not assumed from docs):
  interval    max span/request    reach
  minute      60 days             Aug 2015
  15minute    200 days            Aug 2015   <- what this script pulls
  60minute    400 days            Aug 2015
  (empty before ~Aug 2014 on every symbol probed)
Uniform across 10 probed symbols incl. recent listings — not a megacap artifact.
Measured ~1.98 s/symbol for a 200-day pull; 200 symbols x 3y ~= 36 min.

Rate limit: documented 3 req/s. A burst test achieved 7 req/s without error, but
this script paces at REQUEST_DELAY anyway — update_prices_kite.py already
learned that lesson (throttling mid-run silently dropped symbols).

Usage:
    python fetch_intraday_kite.py --years 3            # F&O-liquid universe
    python fetch_intraday_kite.py --years 3 --limit 50 # quick subset
    python fetch_intraday_kite.py RELIANCE TCS         # explicit symbols
    python fetch_intraday_kite.py --resume             # skip existing files
"""
import os
import sys
import time
import datetime as dt

import pandas as pd

OUT_DIR = "../data/intraday_data/"
PRICE_DIR = "../data/price_data/"

INTERVAL = "15minute"
MAX_SPAN_DAYS = 190          # under the verified 200-day cap, with margin
REQUEST_DELAY = 0.34         # ~3/sec, the documented cap
RETRY_DELAY = 2.0
MAX_RETRIES = 2


def _fetch_chunk(kite, token, start, end):
    """One historical_data call, retrying through a rate-limit response.

    A throttle is NOT a real failure. Treating the two alike is what made
    update_prices_kite silently drop AFFLE/APLAPOLLO for a whole run.
    """
    for attempt in range(MAX_RETRIES + 1):
        try:
            return kite.historical_data(token, start, end, INTERVAL), None
        except Exception as e:
            msg = str(e)
            if ("Too many requests" in msg or "429" in msg) and attempt < MAX_RETRIES:
                time.sleep(RETRY_DELAY * (attempt + 1))
                continue
            return None, msg[:80]


def fetch_symbol(kite, token, start, end):
    """Walk backwards in <=MAX_SPAN_DAYS chunks (Kite's per-request cap)."""
    rows, cur_end = [], end
    while cur_end > start:
        cur_start = max(start, cur_end - dt.timedelta(days=MAX_SPAN_DAYS))
        time.sleep(REQUEST_DELAY)
        chunk, err = _fetch_chunk(kite, token, cur_start, cur_end)
        if err is not None:
            return None, err
        rows += chunk
        if cur_start <= start:
            break
        cur_end = cur_start - dt.timedelta(days=1)
    if not rows:
        return None, "no data"

    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["date"])
    # tz-naive IST. Kite returns +05:30; dropping the tz AFTER conversion keeps
    # session boundaries intact (a naive UTC cast would shift bars across days).
    df["date"] = df["date"].dt.tz_localize(None)
    df = df.drop_duplicates("date").sort_values("date")
    return df, None


def liquid_symbols(limit=None):
    """Trading-universe names, by 20d median turnover — same proxy as core."""
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import core
        syms = core.liquid_universe()
        if syms:
            out = [s.replace(".NS", "") for s in syms]
            return out[:limit] if limit else out
    except Exception as e:
        print(f"  (core.liquid_universe unavailable: {str(e)[:60]})")

    files = sorted(f[:-4].replace(".NS", "")
                   for f in os.listdir(PRICE_DIR) if f.endswith(".csv"))
    return files[:limit] if limit else files


def main():
    argv = sys.argv[1:]
    years = 3
    if "--years" in argv:
        i = argv.index("--years")
        years = float(argv[i + 1]); argv = argv[:i] + argv[i + 2:]
    limit = None
    if "--limit" in argv:
        i = argv.index("--limit")
        limit = int(argv[i + 1]); argv = argv[:i] + argv[i + 2:]
    resume = "--resume" in argv
    explicit = [a.upper().replace(".NS", "") for a in argv if not a.startswith("--")]

    os.makedirs(OUT_DIR, exist_ok=True)

    try:
        import kite_auth
        kite = kite_auth.get_kite_client()
    except Exception as e:
        print(f"ABORT: kite unavailable ({e})"); sys.exit(1)
    if kite is None:
        print("ABORT: no cached token — run: python kite_auth.py refresh"); sys.exit(1)

    print("Loading NSE instrument map...")
    nse = {i["tradingsymbol"]: i["instrument_token"] for i in kite.instruments("NSE")}

    symbols = explicit if explicit else liquid_symbols(limit)
    end = dt.date.today()
    start = end - dt.timedelta(days=int(years * 365))

    print(f"\n15-minute bars: {len(symbols)} symbols, {start} -> {end} "
          f"({years}y)")
    print(f"Output: {OUT_DIR}  (research only — never read by the pipeline)\n")

    stats = {"ok": 0, "skip": 0, "no-token": 0, "fail": 0}
    t0 = time.time()

    for i, sym in enumerate(symbols, 1):
        path = os.path.join(OUT_DIR, f"{sym}.csv")
        if resume and os.path.exists(path):
            stats["skip"] += 1
            continue
        token = nse.get(sym)
        if not token:
            stats["no-token"] += 1
            continue

        df, err = fetch_symbol(kite, token, start, end)
        if df is None:
            print(f"  {sym:<14} FAIL {err}")
            stats["fail"] += 1
            continue

        df.to_csv(path, index=False)
        stats["ok"] += 1
        if i % 20 == 0 or i == len(symbols):
            el = time.time() - t0
            rate = i / el if el else 0
            eta = (len(symbols) - i) / rate / 60 if rate else 0
            print(f"  [{i}/{len(symbols)}] {sym:<14} bars={len(df):<7} "
                  f"{el/60:.1f}min elapsed, ~{eta:.0f}min left", flush=True)

    print(f"\nDone in {(time.time()-t0)/60:.1f} min — "
          + ", ".join(f"{k}={v}" for k, v in stats.items()))


if __name__ == "__main__":
    main()
