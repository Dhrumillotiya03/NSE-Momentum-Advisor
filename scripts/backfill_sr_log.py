"""
backfill_sr_log.py
------------------
ONE-OFF repair: reconstruct sr_daily_log.csv rows that were never written
because the underlying price bar was missing/corrupt on the day the logger
ran (the 2026-08-04 NaN-OHLC incident, and COFORGE's 08-05..08-10 splice
deadlock — see CLAUDE.md "OHLC-UNIFORMITY TEST").

POINT-IN-TIME BY CONSTRUCTION. sr_daily_logger.log_stock() derives EVERY
field from df.index[-1] — levels, CMP, High/Low, the horizon, and the Date
stamp itself. So truncating the price history to bars <= the target date
makes the whole row reconstruct exactly as it would have on that date, using
only data that existed then.

WHY THAT MATTERS: sr_daily_log.csv is the MEASUREMENT record that
sr_monthend_analysis scores the S/R model against. Reconstructing a row from
the FULL history would let bars from after the target date influence its swing
pivots — look-ahead bias that would silently inflate the measured hit rate.
support_resistance.analyse_table's `--as-of` flag does NOT do this: it only
shifts the horizon arithmetic and still loads the complete series, so it is
the wrong tool for a backfill (it is a horizon-testing flag, not a
time-machine).

Rows written here carry Backfilled=1 so the measurement record stays honest
about which observations were live and which were reconstructed. Live rows
get Backfilled=0 (blank/NaN on pre-existing rows means "live", since the
column did not exist when they were written).

FIXED PANEL ONLY — never point this at sr_dynamic_log.csv. The fixed
WATCHLIST is the same 61 symbols every day, so a missing row is unambiguously
a lost observation. The DYNAMIC panel's membership is recomputed daily from
core.scan_universe (holdings + top-N momentum) and genuinely changes: 2026-08-17
carried BHEL, 08-19 carried LLOYDSME instead, 08-21 SYRMA. scan_universe has no
point-in-time mode, so reconstructing which names the dynamic panel WOULD have
held on a missed date means inventing panel membership — the exact
"fabricate a history the panel never had" failure `missing_rows` guards against
above, and worse, because it would be invisible in the output. The dynamic
panel's two missing August sessions (08-18, 08-20) were left as gaps for this
reason; it is supplementary calibration data, not the measurement record.

Usage (from scripts/):
    python backfill_sr_log.py --dry-run
    python backfill_sr_log.py --apply
"""
import sys

import pandas as pd

import sr_daily_logger as L
import support_resistance as SR

LOG_PATH = L.LOG_PATH
FLAG_COL = "Backfilled"


def missing_rows(log_df, watchlist_syms, since=None, quorum=0.8):
    """(symbol, date) pairs a WATCHLIST member should have but doesn't.

    DELIBERATELY NARROW. A naive "every symbol should have every date the
    panel ever logged" definition produced 1037 candidates on first run —
    because the panel GREW over time (symbols added later legitimately have
    no earlier rows) and because July has documented partial-coverage days
    (2026-07-15 logged 3/15 symbols, 07-17 2/15 — see CLAUDE.md). Filling
    those would FABRICATE a history the panel never had, not repair lost
    observations.

    Two guards keep this to genuine data-loss:
      * `since` — only repair on/after a date you have a known incident for.
      * `quorum` — only repair dates where MOST of the panel did log, i.e.
        the run clearly happened and specific symbols fell out of it. A day
        where almost nothing logged is a day the logger didn't really run,
        which is not this script's business to invent.
    """
    d = log_df[log_df["Date"] != "AVG"].copy()
    d["Date"] = pd.to_datetime(d["Date"], errors="coerce")
    d = d[d["Date"].notna()]
    panel = d[d["Symbol"].isin(watchlist_syms)]
    if since is not None:
        panel = panel[panel["Date"] >= pd.Timestamp(since)]
    if panel.empty:
        return []

    have = set(zip(panel["Symbol"], panel["Date"].dt.normalize()))
    n_panel = len(watchlist_syms)

    out = []
    for dt, grp in panel.groupby(panel["Date"].dt.normalize()):
        covered = grp["Symbol"].nunique()
        if covered < quorum * n_panel:
            print(f"  (skipping {dt.date()}: only {covered}/{n_panel} symbols "
                  f"logged — the run itself was partial, not a per-symbol loss)")
            continue
        for sym in sorted(watchlist_syms):
            if (sym, dt) not in have:
                out.append((sym, pd.Timestamp(dt)))
    return out


def build_row(sym, target_date):
    """Reconstruct one row as of target_date, or None if not reconstructable.

    Monkeypatches support_resistance.load_stock (which sr_daily_logger imports
    and calls inside log_stock) to serve a history truncated at target_date.
    That is what makes the result point-in-time rather than today's view
    back-stamped with an old date.
    """
    real_load = SR.load_stock

    def truncated_load(s, *a, **kw):
        df = real_load(s, *a, **kw)
        if df is None:
            return None
        return df[df.index <= target_date]

    # log_stock resolves load_stock through the sr_daily_logger namespace it
    # was imported into, so patch there as well as at the source module.
    SR.load_stock = truncated_load
    had_own = hasattr(L, "load_stock")
    prev = getattr(L, "load_stock", None)
    L.load_stock = truncated_load
    try:
        row = L.log_stock(sym if sym in SR.INDEX_FILES else sym + ".NS")
    except Exception as e:
        print(f"    {sym} {target_date.date()}: FAILED ({str(e)[:60]})")
        return None
    finally:
        SR.load_stock = real_load
        if had_own:
            L.load_stock = prev

    if row is None:
        return None
    # The row must land on the date we asked for. If the symbol has no bar on
    # target_date (genuinely untraded / still missing), log_stock stamps the
    # last bar it DID find — silently writing a duplicate of an earlier date.
    if row.get("Date") != target_date.strftime("%Y-%m-%d"):
        print(f"    {sym} {target_date.date()}: no bar on that date "
              f"(nearest {row.get('Date')}) — skipped")
        return None
    return row


def main():
    argv = sys.argv[1:]
    apply = "--apply" in argv
    if not apply and "--dry-run" not in argv:
        print("specify --dry-run or --apply")
        sys.exit(2)

    # Default scope = the August incidents this script exists for: the
    # 2026-08-04 NaN-OHLC bars and COFORGE's 08-05..08-10 splice deadlock.
    # Override with --since YYYY-MM-DD only if you have a specific, known
    # data-loss incident to repair — never to "fill in history" generally.
    since = "2026-08-01"
    if "--since" in argv:
        since = argv[argv.index("--since") + 1]

    log_df = pd.read_csv(LOG_PATH)
    watchlist = {s.replace(".NS", "") for s in L.WATCHLIST}

    if "--date" in argv:
        # WHOLE-DAY mode: the panel logged NOTHING on this date (the pipeline
        # simply did not run that day), so there is no partial day for
        # missing_rows to find — its quorum loop only iterates dates that
        # already appear in the log. Target every WATCHLIST member directly.
        # Still point-in-time: build_row truncates each symbol's history to
        # bars <= the target date, so a run made days later cannot see bars
        # that did not exist then.
        target = pd.Timestamp(argv[argv.index("--date") + 1])
        d = log_df[log_df["Date"] != "AVG"].copy()
        d["Date"] = pd.to_datetime(d["Date"], errors="coerce")
        have = set(d[d["Date"] == target]["Symbol"])
        gaps = [(s, target) for s in sorted(watchlist) if s not in have]
        print(f"Scope: WHOLE DAY {target.date()} — {len(gaps)} WATCHLIST "
              f"symbol(s) missing ({len(have)} already present).\n")
    else:
        print(f"Scope: WATCHLIST members, dates >= {since}, "
              f"on days the panel mostly logged.\n")
        gaps = missing_rows(log_df, watchlist, since=since)
    if not gaps:
        print("No missing WATCHLIST rows — nothing to backfill.")
        return

    print(f"Missing WATCHLIST rows: {len(gaps)}")
    for sym, dt in gaps:
        print(f"  {sym:12} {dt.date()}")
    print()

    built = []
    for sym, dt in gaps:
        row = build_row(sym, dt)
        if row is None:
            continue
        row[FLAG_COL] = 1
        built.append(row)
        print(f"  built {sym:12} {dt.date()}  CMP={row['CMP']:>10} "
              f"S1={row['S1']} R1={row['R1']} horizon={row['HorizonDays']}d")

    print(f"\nReconstructed {len(built)} of {len(gaps)} missing row(s).")
    if not apply:
        print("Dry run — nothing written. Re-run with --apply.")
        return
    if not built:
        print("Nothing to write.")
        return

    new_df = pd.DataFrame(built)
    combined = L.merge_log(new_df, LOG_PATH)
    if FLAG_COL in combined.columns:
        combined[FLAG_COL] = combined[FLAG_COL].fillna(0).astype(int)
    combined.to_csv(LOG_PATH, index=False)
    print(f"Wrote {len(built)} backfilled row(s) to {LOG_PATH} "
          f"(flagged {FLAG_COL}=1).")

    # The MONTH file is derived from the same rows and must not drift from the
    # cumulative log. Earlier versions of this script wrote only LOG_PATH, so
    # every backfilled date was silently absent from sr_month_YYYY-MM.csv —
    # August showed 08-04 at 56 rows and 08-05..08-07 at 60 against 61 in the
    # cumulative log, and 08-18/08-20 missing outright. write_month dedupes on
    # (Date, Symbol) and rebuilds AVG rows from scratch, so re-feeding rows is
    # safe and idempotent.
    #
    # sr_today.csv is deliberately NOT written: it is the "where do things
    # stand right now" view, and a backfill of a PAST date must not overwrite
    # it with stale rows.
    for path, n, complete in L.write_month(new_df):
        tag = "AVG rows written" if complete else "AVG rows pending last Tuesday"
        print(f"  month file updated: {path} ({n} daily rows, {tag})")


if __name__ == "__main__":
    main()
