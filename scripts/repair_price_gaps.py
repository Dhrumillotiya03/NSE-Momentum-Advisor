"""
repair_price_gaps.py
--------------------
Backfill MISSING RECENT BARS in data/price_data/*.csv from Kite Connect.

WHY THIS EXISTS. The nightly downloader uses yfinance, which occasionally drops
a symbol for a day (or several) with no error — the file simply stops
advancing. On 2026-08-04, 5 of 500 files were behind the universe's latest
bar, one by nearly three months. Anything reading those files gets stale levels
and a stale CMP, and the S/R logger's "logged BEHIND today's latest" warning is
the only thing that surfaces it.

WHY NOT JUST RE-DOWNLOAD EVERYTHING FROM KITE. Kite's historical data is
UNADJUSTED; the existing CSVs are yfinance split/dividend-ADJUSTED. Measured on
NATIONALUM the kite/csv ratio drifts 1.801 (2016) -> 1.405 (2019) -> 1.146
(2023) -> 1.000 (today): textbook cumulative dividend adjustment. Neither
source is wrong, they answer different questions. Swapping wholesale would
silently change every historical price, which moves every S/R pivot, changes
the momentum scorer's returns, and invalidates all four P(touch) tables and
every backtest number on file.

This script therefore repairs ONLY the recent tail, where the two conventions
agree (ratio 1.000 today, because no corporate action has happened since).
Before writing anything it VERIFIES that agreement on the overlapping bars and
refuses the symbol if they disagree beyond a tolerance — that check is what
makes splicing two sources safe rather than merely convenient.

Usage:
    python repair_price_gaps.py              # report only, writes nothing
    python repair_price_gaps.py --apply      # repair the files
    python repair_price_gaps.py --apply SAIL.NS WIPRO.NS
    python repair_price_gaps.py --max-gap 30 # only repair gaps <= 30 sessions
"""
import os
import sys
import datetime as dt

import pandas as pd

PRICE_DIR = "../data/price_data/"

# A repaired tail is only trustworthy while the two sources agree. 0.5% allows
# for rounding/venue differences without letting a real adjustment gap through.
AGREE_TOL = 0.005
# Bars to compare when checking agreement.
OVERLAP_BARS = 5
# Refuse very old gaps by default: the further back the gap, the likelier a
# corporate action sits inside it and the unadjusted Kite bars no longer splice
# cleanly onto the adjusted history.
DEFAULT_MAX_GAP = 45


OHLC = ["Open", "High", "Low", "Close"]


def load_csv(path):
    df = pd.read_csv(path)
    df["Date"] = pd.to_datetime(df["Date"], errors="coerce")
    df = df[df["Date"].notna()].sort_values("Date")
    return df


def last_usable_date(df):
    """Last bar with real OHLC.

    yfinance sometimes writes a row with a genuine Volume but NaN OHLC — the
    file looks current while every consumer silently drops that row (core and
    support_resistance both dropna on OHLC). So a file can be 'up to date' by
    max(Date) and still be stale in practice. This is the failure this repo
    already hit once, patched by hand for 2026-07-30; measuring usability
    rather than presence is what makes the repair detect it automatically.
    """
    cols = [c for c in OHLC if c in df.columns]
    if not cols:
        return df["Date"].max() if len(df) else None
    good = df.dropna(subset=cols)
    return good["Date"].max() if len(good) else None


def kite_daily(kite, token, start, end):
    """Daily bars, chunked under Kite's 2000-day per-request cap."""
    out = []
    cur_end = end
    while cur_end > start:
        cur_start = max(start, cur_end - dt.timedelta(days=1900))
        try:
            out += kite.historical_data(token, cur_start, cur_end, "day")
        except Exception as e:
            print(f"      kite error: {str(e)[:70]}")
            break
        if cur_start <= start:
            break
        cur_end = cur_start - dt.timedelta(days=1)
    if not out:
        return None
    k = pd.DataFrame(out)
    k["date"] = pd.to_datetime(k["date"]).dt.tz_localize(None).dt.normalize()
    return k.drop_duplicates("date").sort_values("date").set_index("date")


def agreement(df, k):
    """Max |ratio-1| over the last OVERLAP_BARS shared dates, or None."""
    idx = pd.DatetimeIndex(df["Date"]).normalize().intersection(k.index)
    if len(idx) == 0:
        return None, 0
    idx = idx[-OVERLAP_BARS:]
    worst = 0.0
    for t in idx:
        c = float(df.loc[pd.DatetimeIndex(df["Date"]).normalize() == t, "Close"].iloc[-1])
        kc = float(k.loc[t, "close"])
        if c > 0:
            worst = max(worst, abs(kc / c - 1.0))
    return worst, len(idx)


def main():
    argv = sys.argv[1:]
    apply = "--apply" in argv
    max_gap = DEFAULT_MAX_GAP
    if "--max-gap" in argv:
        i = argv.index("--max-gap")
        max_gap = int(argv[i + 1])
        argv = argv[:i] + argv[i + 2:]
    only = [a for a in argv if not a.startswith("--")]

    all_files = sorted(f for f in os.listdir(PRICE_DIR) if f.endswith(".csv"))
    files = all_files
    if only:
        want = {s if s.endswith(".csv") else s + ".csv" for s in only}
        files = [f for f in all_files if f in want]

    # The universe's newest bar is the reference for what "current" means, and
    # it must be computed over the WHOLE universe even when repairing a subset.
    # Scoping it to the named files made two equally-stale symbols look current
    # relative to each other, so an explicit repair request silently did
    # nothing.
    latest = {}
    for f in files:
        try:
            latest[f] = last_usable_date(load_csv(PRICE_DIR + f))
        except Exception:
            continue
    latest = {f: d for f, d in latest.items() if pd.notna(d)}
    if not latest:
        print("no readable price files"); return

    universe_dates = []
    for f in all_files:
        try:
            m = last_usable_date(load_csv(PRICE_DIR + f))
            if pd.notna(m):
                universe_dates.append(m)
        except Exception:
            continue
    universe_latest = max(universe_dates) if universe_dates else max(latest.values())
    behind = {f: d for f, d in latest.items() if d < universe_latest}

    print(f"Universe latest bar: {universe_latest.date()}")
    print(f"Files behind it: {len(behind)}/{len(latest)}")
    if not behind:
        print("Nothing to repair."); return
    for f, d in sorted(behind.items(), key=lambda x: x[1]):
        print(f"  {f[:-4]:18} last bar {d.date()}  ({(universe_latest-d).days}d behind)")

    if not apply:
        print("\n(report only — pass --apply to repair)")
        return

    try:
        import kite_auth
        kite = kite_auth.get_kite_client()
    except Exception as e:
        print(f"\nABORT: kite unavailable ({e})"); return
    if kite is None:
        print("\nABORT: no cached Kite token — run: python kite_auth.py refresh"); return

    print("\nLoading NSE instrument map...")
    inst = {i["tradingsymbol"]: i["instrument_token"] for i in kite.instruments("NSE")}

    repaired = skipped = 0
    for f, last in sorted(behind.items(), key=lambda x: x[1]):
        sym = f[:-4]
        base = sym.replace(".NS", "")
        gap_days = (universe_latest - last).days
        print(f"\n  {base}: gap {last.date()} -> {universe_latest.date()}")

        if gap_days > max_gap:
            print(f"      SKIP: gap {gap_days}d exceeds --max-gap {max_gap} "
                  f"(a corporate action may sit inside it)")
            skipped += 1
            continue
        token = inst.get(base)
        if not token:
            print("      SKIP: no NSE instrument token"); skipped += 1; continue

        path = PRICE_DIR + f
        df = load_csv(path)
        k = kite_daily(kite, token,
                       (last - dt.timedelta(days=30)).date(),
                       universe_latest.date())
        if k is None or k.empty:
            print("      SKIP: kite returned no data"); skipped += 1; continue

        worst, n_cmp = agreement(df, k)
        if worst is None or n_cmp == 0:
            print("      SKIP: no overlapping bars to verify against")
            skipped += 1; continue
        if worst > AGREE_TOL:
            print(f"      SKIP: sources disagree by {worst*100:.2f}% on the "
                  f"overlap — unadjusted Kite bars would corrupt this series")
            skipped += 1; continue

        new = k[k.index > pd.Timestamp(last).normalize()]
        if new.empty:
            print("      nothing new from kite"); skipped += 1; continue

        cols = list(df.columns)
        rows = []
        for t, r in new.iterrows():
            row = {c: None for c in cols}
            row["Date"] = t
            for src, dst in [("open", "Open"), ("high", "High"),
                             ("low", "Low"), ("close", "Close"),
                             ("volume", "Volume")]:
                if dst in row:
                    row[dst] = float(r[src])
            if "Symbol" in row:
                row["Symbol"] = sym
            rows.append(row)

        out = pd.concat([df, pd.DataFrame(rows)], ignore_index=True)
        out = out.drop_duplicates("Date", keep="last").sort_values("Date")
        out.to_csv(path, index=False)
        print(f"      repaired: +{len(rows)} bar(s), agreement {worst*100:.3f}%, "
              f"now ends {out['Date'].max().date()}")
        repaired += 1

    print(f"\nRepaired {repaired}, skipped {skipped}.")
    if repaired:
        print("Re-run data_integrity_check.py to confirm the series are clean.")


if __name__ == "__main__":
    main()
