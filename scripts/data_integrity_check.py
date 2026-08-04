"""
Nightly data-integrity guard — runs after the download steps in
run_daily_log.sh and prints WARN lines for anything that has historically
corrupted this project's numbers. Data bugs have bitten three times:

  1. yfinance MultiIndex second header row in nifty50.csv → Date parsed NaT
     → is_last_trading_day_of_month() true every day → false month-end
     liquidation signals (July 2026).
  2. SUNDARMFIN.NS.csv truncated last row (missing Date) → literal string in
     the index → sort_index TypeError downstream.
  3. GOLDBEES.NS.csv rows at exactly 1/100th price (failed split
     adjustment) → fake -99%/+10400% returns → one walk-forward window
     showed +330% annualized before it was caught.

Checks per CSV (price_data/, etf_data/, index_data/):
  - unparseable Date rows (catches 1 and 2)
  - decimal-shift glitches: Close deviating >3x from the centered 11-day
    rolling median (catches 3)
  - non-positive Close values
  - stale series: last date > STALE_DAYS calendar days old (only warned for
    files that were recently fresh — permanently dead names are expected in
    a survivorship-aware panel and would just be noise)
  - duplicate dates

Plus a CORPORATE-ACTION check on every held name (real, paper, and agent-sim
books): yfinance serves BACK-ADJUSTED prices, so after a split/bonus it
rewrites the whole history — the books' qty/avg (recorded at raw fill terms)
silently stop matching the data, and the -18% stop watch fires a false STOP.
Detection: snapshot each held name's close at a fixed reference date
(data/held_close_snapshot.json, auto-managed); if the CSV's close AT THAT
SAME PAST DATE later shifts >15%, the history was rewritten -> WARN with the
implied factor and the reconcile command (record_fill.py adjust SYM FACTOR).
The snapshot self-heals: any qty change in a book (fill or adjust) refreshes
that name's reference point.

Exit code 1 if any WARN was emitted (so a wrapper/cron can notice), else 0.
Run from scripts/:  python data_integrity_check.py
"""
import glob
import json
import os
import sys

import pandas as pd

BOOKS = {
    "real":  "../data/portfolio_state.json",
    "paper": "../data/paper_state.json",
    "sim":   "../data/_agent_sim/portfolio_state.json",
}
SNAPSHOT_PATH = "../data/held_close_snapshot.json"
CA_TOLERANCE = 0.15   # history rewrite beyond +/-15% at the reference date


def _close_series(sym):
    for d in ("../data/price_data", "../data/etf_data"):
        path = os.path.join(d, f"{sym}.csv")
        if os.path.exists(path):
            df = pd.read_csv(path, low_memory=False)
            dates = pd.to_datetime(df["Date"], errors="coerce")
            close = pd.to_numeric(df["Close"], errors="coerce")
            ok = dates.notna() & close.notna()
            return pd.Series(close[ok].values, index=dates[ok]).sort_index()
    return None


def check_corporate_actions():
    """WARN when a held name's price history was rewritten (split/bonus)."""
    warns = []
    try:
        snapshot = json.load(open(SNAPSHOT_PATH))
    except Exception:
        snapshot = {}

    seen = set()
    for book, path in BOOKS.items():
        try:
            positions = json.load(open(path)).get("positions", {})
        except Exception:
            continue
        for sym, pos in positions.items():
            key = f"{book}:{sym}"
            seen.add(key)
            close = _close_series(sym)
            if close is None or len(close) < 2:
                continue
            snap = snapshot.get(key)
            if snap is None or snap.get("qty") != pos.get("qty"):
                # new position, or qty changed (fill/adjust) — (re)snapshot
                snapshot[key] = {"date": str(close.index[-1].date()),
                                 "close": float(close.iloc[-1]),
                                 "qty": pos.get("qty")}
                continue
            ref_date = pd.Timestamp(snap["date"])
            if ref_date not in close.index:
                warns.append(f"{book} book {sym}: reference date {snap['date']} "
                             f"vanished from its CSV — history rewritten, verify "
                             f"for a corporate action and reconcile the books")
                continue
            ratio = close.loc[ref_date] / snap["close"]
            if abs(ratio - 1) > CA_TOLERANCE:
                factor = snap["close"] / close.loc[ref_date]
                warns.append(
                    f"{book} book {sym}: close at {snap['date']} changed x{ratio:.3f} "
                    f"since snapshot — LIKELY SPLIT/BONUS (implied factor ~{factor:.2f}). "
                    f"Verify on Zerodha/NSE, then: python record_fill.py adjust "
                    f"{sym.replace('.NS', '')} {factor:.2f}  (real book; fix paper/sim "
                    f"state json manually if flagged there)")

    snapshot = {k: v for k, v in snapshot.items() if k in seen}
    try:
        with open(SNAPSHOT_PATH, "w") as f:
            json.dump(snapshot, f, indent=2)
    except Exception as e:
        warns.append(f"held_close_snapshot.json: could not save ({e})")
    return warns

STALE_DAYS = 7        # warn if a previously-fresh series stops updating
RECENT_DAYS = 30      # "previously fresh" = last date within this many days


def check_file(path, today):
    warns = []
    name = os.path.basename(path)
    try:
        df = pd.read_csv(path, low_memory=False)
    except Exception as e:
        return [f"{name}: unreadable ({e})"]

    if "Date" not in df.columns or "Close" not in df.columns:
        return [f"{name}: missing Date/Close column"]

    dates = pd.to_datetime(df["Date"], errors="coerce")
    n_bad_dates = int(dates.isna().sum())
    if n_bad_dates:
        warns.append(f"{name}: {n_bad_dates} row(s) with unparseable Date "
                     f"(truncated write or duplicate header row)")

    close = pd.to_numeric(df["Close"], errors="coerce")

    # NaN-OHLC rows with real Volume — yfinance's signature failure, and the
    # 4th corruption class this check knows about (added 2026-08-05 after 41
    # files carried one silently). It is invisible to every other test here
    # because the very next line filters NaN closes out, and invisible to
    # consumers because they all dropna: a file looks "up to date" by max(Date)
    # while its newest bar has no price. That is worse than being stale, since
    # nothing reports it. update_prices_kite's agreement() also compared
    # AGAINST such a row and returned NaN, and `NaN > AGREE_TOL` is False, so
    # the splice guard silently approved everything — fixed there too.
    nan_close = dates.notna() & close.isna()
    if nan_close.any():
        bad_days = [d.date() for d in dates[nan_close].tail(5)]
        warns.append(f"{name}: {int(nan_close.sum())} row(s) with a Date but "
                     f"NaN Close at {bad_days} — repair with "
                     f"`python update_prices_kite.py {name[:-4]}`")

    valid = dates.notna() & close.notna()
    dates, close = dates[valid], close[valid]
    if len(close) < 30:
        return warns  # too short to sanity-check further

    if (close <= 0).any():
        warns.append(f"{name}: {int((close <= 0).sum())} non-positive Close value(s)")

    med = close.rolling(11, center=True, min_periods=3).median()
    ratio = close / med
    glitch = (ratio >= 3) | (ratio <= 1 / 3)
    if glitch.any():
        days = [d.date() for d in dates[glitch]]
        warns.append(f"{name}: {int(glitch.sum())} decimal-shift glitch row(s) at {days} "
                     f"(price >3x off its 11d median — GOLDBEES-class corruption)")

    if dates.duplicated().any():
        warns.append(f"{name}: {int(dates.duplicated().sum())} duplicate date row(s)")

    last = dates.max()
    age = (today - last).days
    if STALE_DAYS < age <= RECENT_DAYS:
        warns.append(f"{name}: stale — last date {last.date()} ({age}d old, was recently fresh)")

    return warns


def main():
    today = pd.Timestamp.today().normalize()
    paths = (sorted(glob.glob("../data/price_data/*.csv"))
             + sorted(glob.glob("../data/etf_data/*.csv"))
             + sorted(glob.glob("../data/index_data/*.csv")))

    all_warns = []
    for p in paths:
        all_warns.extend(check_file(p, today))
    all_warns.extend(check_corporate_actions())

    if all_warns:
        print(f"DATA INTEGRITY: {len(all_warns)} warning(s) across {len(paths)} files")
        for w in all_warns:
            print(f"  WARN {w}")
        sys.exit(1)
    print(f"DATA INTEGRITY: OK — {len(paths)} files clean")


if __name__ == "__main__":
    main()
