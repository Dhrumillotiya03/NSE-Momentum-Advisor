"""
audit_sr_log.py
---------------
Does every row in the S/R measurement record actually match the price archive
for the date it claims? READ-ONLY — writes only to data/_research/.

WHY THIS EXISTS. sr_daily_log.csv is what sr_monthend_analysis scores the S/R
model against, so a row whose CMP is a price that never existed scores the
model on a price that never happened. Two mechanisms produce that:

  1. PRE-SETTLEMENT EVENING RUNS. Kite's historical_data() is not final until
     NSE settlement (~19:00-20:00 IST or later), so a 17:00-23:00 run recorded
     values that were revised overnight. Caught 2026-08-19: the whole 08-17
     panel carried CMPs matching no archive bar — RELIANCE logged 1322.0 when
     08-17 settled at 1316.0 (1322.0 was in fact 08-18's close).
  2. MID-SESSION RUNS under the old live-CMP behaviour: the row was stamped
     with the last COMPLETED bar's date but priced from a LIVE tick of the
     session then in progress, so Date and CMP described different sessions.

Both are prevented going forward by sr_daily_logger.LOG_PREVIOUS_SESSION
(2026-08-19), which logs only completed, settled sessions. This script exists
to measure what the historical record still carries, and to stay runnable as a
periodic check — this repo has now had four measurement-instrument failures of
the same family (fake 100% S/R hit rate, a cash-month scored "consistent", the
advisor ledger's header drift, and this), so measurement scripts are worth
re-running purely to confirm they still tell the truth.

NOTE this reports; it does not repair. Rewriting historical measurement rows
is a judgement call, not a cleanup — the rows do record what the system saw at
the time, and rebuilding them point-in-time (backfill_sr_log.py --date) makes
the record uniform but discards that. Decide deliberately.

Usage (from scripts/):
    python audit_sr_log.py
    python audit_sr_log.py --log ../data/sr_dynamic_log.csv
"""
import os
import sys

import pandas as pd

TOL = 0.0005          # 0.05% — float/rounding noise, not a real disagreement
OUT = "../data/_research/sr_log_cmp_audit.csv"

_cache = {}


def bars(sym):
    """Archive bars for a symbol, from whichever directory holds it."""
    if sym in _cache:
        return _cache[sym]
    path = None
    for d in ("price_data", "etf_data", "index_data"):
        p = f"../data/{d}/{sym}.NS.csv"
        if os.path.exists(p):
            path = p
            break
    if path is None:
        _cache[sym] = None
        return None
    df = pd.read_csv(path)
    df["Date"] = pd.to_datetime(df["Date"], errors="coerce")
    _cache[sym] = df.dropna(subset=["Date"]).set_index("Date").sort_index()
    return _cache[sym]


def audit(log_path):
    log = pd.read_csv(log_path)
    log = log[log["Date"] != "AVG"].copy()      # AVG rows are derived, not snapshots
    log["Date"] = pd.to_datetime(log["Date"], errors="coerce")
    log = log.dropna(subset=["Date"])

    rows, missing = [], 0
    for _, r in log.iterrows():
        df = bars(r["Symbol"])
        if df is None or r["Date"] not in df.index:
            missing += 1
            continue
        bar = df.loc[r["Date"]]
        if isinstance(bar, pd.DataFrame):
            bar = bar.iloc[0]
        try:
            cmp_, close = float(r["CMP"]), float(bar["Close"])
        except (TypeError, ValueError):
            continue
        if not close or abs(cmp_ - close) / close <= TOL:
            continue
        # Does the logged CMP match a NEIGHBOURING session instead? That is
        # the settlement signature — an unsettled value later revised, or the
        # next session's print captured early.
        note = ""
        i = df.index.get_loc(r["Date"])
        for off, name in [(1, "NEXT session"), (-1, "PREV session")]:
            j = i + off
            if 0 <= j < len(df):
                c2 = float(df["Close"].iloc[j])
                if c2 and abs(cmp_ - c2) / c2 <= TOL:
                    note = f"== {name} close"
        rows.append({"Date": r["Date"].date(), "Symbol": r["Symbol"],
                     "logged_CMP": cmp_, "archive_close": close,
                     "dev_pct": round(abs(cmp_ - close) / close * 100, 2),
                     "note": note})
    return log, pd.DataFrame(rows), missing


def main():
    log_path = "../data/sr_daily_log.csv"
    if "--log" in sys.argv:
        log_path = sys.argv[sys.argv.index("--log") + 1]

    log, a, missing = audit(log_path)
    print(f"\n{log_path}")
    print(f"  rows checked           : {len(log)}")
    print(f"  no archive bar for date: {missing}")
    print(f"  CMP != archive close   : {len(a)}"
          f"{f'  ({len(a)/len(log)*100:.0f}% of the record)' if len(log) else ''}")
    if a.empty:
        print("\n  Clean — every row matches the archive bar for its own date.")
        return

    print("\n  BY DATE (a whole-panel count means that day's entire run was affected):")
    g = a.groupby("Date").agg(rows=("Symbol", "size"),
                              max_dev_pct=("dev_pct", "max"),
                              matches_neighbour=("note", lambda s: (s != "").sum()))
    print(g.to_string().replace("\n", "\n  "))

    print("\n  WORST 10 BY DEVIATION:")
    print(a.nlargest(10, "dev_pct").to_string(index=False).replace("\n", "\n  "))

    os.makedirs("../data/_research", exist_ok=True)
    a.to_csv(OUT, index=False)
    print(f"\n  wrote {OUT}")
    print("  Reporting only — see the module docstring before repairing anything.")


if __name__ == "__main__":
    main()
