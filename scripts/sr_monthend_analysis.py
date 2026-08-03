"""
sr_monthend_analysis.py
------------------------
Reads ../data/sr_daily_log.csv (built by sr_daily_logger.py) and runs
month-end accuracy + calibration analysis.

Analyses performed:
  1. Basic hit-rate: did price touch S1 / R1 within the logged period?
  2. Level drift: how much did S1/R1 move day-to-day for each stock?
  3. Probability calibration: bucketed predicted-prob vs actual hit-rate
  4. Sample size (n) sensitivity: does higher n predict better accuracy?
  5. Distance vs accuracy: does closer S/R hit more often than far S/R?

Usage:
    python sr_monthend_analysis.py
    python sr_monthend_analysis.py --touch-pct 1.0   ← override touch tolerance
    python sr_monthend_analysis.py --window 10       ← shorter forward horizon
    python sr_monthend_analysis.py --exclude-day0    ← drop already-touching levels

WINDOW NOTE: a 21-day hit rate needs 21 trading bars after each log date. On a
log shorter than that, NO window resolves and the rate is unmeasurable at 21d.
--window 7/10 measures a genuinely resolvable shorter horizon instead. A 10d
rate is NOT comparable to the 21d backtested ~65-68% figure — shorter window,
strictly fewer touches, so it reads lower by construction.
"""

import os, sys
import numpy as np
import pandas as pd

LOG_PATH  = "../data/sr_daily_log.csv"
PRICE_DIR = "../data/price_data/"

TOUCH_PCT = 0.01   # price must come within 1% of level to count as "touched"

# Forward window in trading days. 21 is the validated/backtested horizon, but a
# short forward log cannot RESOLVE a 21d window (needs 21 bars after the log
# date), and an unresolved window is not a miss. Shortening the window to
# something the data can actually close (--window 7/10) trades horizon for
# resolvability and yields a real, if shorter-horizon, hit rate.
WINDOW_DAYS = 21

# --to-month-end: use each row's own logged HorizonDays (distance to that
# month's last Tuesday) instead of one fixed window. This measures exactly the
# production question — "will it touch before the rebalance date?" — where the
# window differs per row. Rows logged before HorizonDays existed fall back to
# WINDOW_DAYS.
USE_LOGGED_HORIZON = False

# Exclude levels already inside the touch band on the log date. Those are
# arithmetically guaranteed hits carrying zero predictive content, and they
# inflate S1/R1 by ~7pp on this sample. Off by default (keeps the historical
# definition); --exclude-day0 turns it on.
EXCLUDE_DAY0 = False


# ──────────────────────────────────────────────
# LOAD
# ──────────────────────────────────────────────

def load_log():
    if not os.path.exists(LOG_PATH):
        print(f"❌ No log found at {LOG_PATH}. Run sr_daily_logger.py first.")
        sys.exit(1)
    df = pd.read_csv(LOG_PATH)
    # Month files (sr_month_YYYY-MM.csv) carry AVG summary rows whose Date is
    # the literal "AVG". Those are derived output, not observations — drop them
    # before anything tries to parse dates, or they become NaT and (worse) get
    # scored as if they were a logged snapshot.
    if "Date" in df.columns:
        df = df[df["Date"].astype(str).str.upper() != "AVG"]
    df["Date"] = pd.to_datetime(df["Date"], errors="coerce", format="mixed")
    # Guard the malformed-row class that has bitten this repo before (a
    # truncated download leaves a row whose Date won't parse). Those survive as
    # NaT and break any date comparison downstream.
    df = df[df["Date"].notna()]
    return df.sort_values(["Symbol", "Date"])


# v2 went live 2026-07-04; rows before it came from the old analog scan whose
# probabilities are on a different scale and would corrupt calibration.
#
# This is deliberately DATE-anchored, not range-anchored. An earlier version
# keyed on v2's 57-78 output band, which broke the moment the P(touch) table
# replaced the bounce table — P(touch) legitimately spans ~5-95%, so a band
# test would have silently discarded most valid modern rows. The cutover date
# is a fixed historical fact and cannot drift with the scorer.
V2_START_DATE = pd.Timestamp("2026-07-04")


def drop_legacy_rows(df):
    """Drop pre-v2 rows (logged before V2_START_DATE)."""
    if "Date" not in df.columns:
        return df, 0
    legacy = df["Date"] < V2_START_DATE
    return df[~legacy], int(legacy.sum())


def load_price(symbol):
    path = os.path.join(PRICE_DIR, f"{symbol}.NS.csv")
    if not os.path.exists(path):
        # ETFs (GOLDBEES) live in etf_data/, not price_data/ — see
        # support_resistance.load_stock for why they must stay separate.
        path = os.path.join("../data/etf_data/", f"{symbol}.NS.csv")
        if not os.path.exists(path):
            return None
    df = pd.read_csv(path, parse_dates=["Date"])
    df = df.sort_values("Date").set_index("Date")
    for col in ["High", "Low", "Close"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return df.dropna(subset=["High", "Low", "Close"])


# ──────────────────────────────────────────────
# 0. LOG DATA QUALITY
# ──────────────────────────────────────────────

def coverage_report(log_df):
    """Surface pipeline defects BEFORE any accuracy number is read.

    A bad hit rate caused by a broken pipeline is a completely different
    finding from a miscalibrated model, and the three failure modes below have
    all actually occurred in this repo: duplicate (date,symbol) pairs from
    wall-clock stamping, partial days from a stale-data/freshness bug, and
    repeated identical snapshots from a symbol's CSV silently not updating.
    """
    print(f"\n{'='*70}")
    print(f"  0. LOG DATA QUALITY")
    print(f"{'='*70}")

    dup = log_df.duplicated(subset=["Date", "Symbol"]).sum()
    print(f"  Duplicate (Date,Symbol) pairs: {dup}" + ("  ⚠️" if dup else "  ✅"))

    per_day = log_df.groupby("Date")["Symbol"].nunique()
    if len(per_day):
        panel = int(per_day.max())
        partial = per_day[per_day < panel * 0.8]
        print(f"  Panel size (max symbols on a day): {panel}")
        if len(partial):
            print(f"  ⚠️  {len(partial)} partial day(s) — under 80% of the panel "
                  f"(stale-data / missed-run class):")
            for d, c in partial.items():
                print(f"      {pd.Timestamp(d).date()}  {c}/{panel} symbols")
        else:
            print("  ✅ No partial days")

    # Frozen series: identical CMP on consecutive log dates means that
    # symbol's price CSV did not update — the freshness bug. Levels recomputed
    # from an unchanged file are a duplicate observation, not new evidence.
    frozen = []
    for sym, grp in log_df.sort_values("Date").groupby("Symbol"):
        if len(grp) < 3:
            continue
        cmp_ = pd.to_numeric(grp["CMP"], errors="coerce")
        streak = int((cmp_.diff() == 0).astype(int).groupby(
            (cmp_.diff() != 0).cumsum()).sum().max() or 0)
        if streak >= 2:
            frozen.append((sym, streak))
    if frozen:
        print(f"  ⚠️  {len(frozen)} symbol(s) with an unchanged CMP across "
              f"consecutive log dates (possible stale CSV):")
        for s, k in sorted(frozen, key=lambda x: -x[1])[:8]:
            print(f"      {s}: {k+1} consecutive identical closes")
    else:
        print("  ✅ No frozen price series")


# ──────────────────────────────────────────────
# 1. BASIC HIT-RATE PER LOGGED SNAPSHOT
# ──────────────────────────────────────────────

def check_touch(price_df, log_date, level, direction, window_days=None):
    """
    direction: 'down' = check Low reaches level (support)
               'up'   = check High reaches level (resistance)

    Returns True (touched inside the window), False (RESOLVED miss — the full
    window elapsed without a touch), or None (window still open; outcome
    genuinely unknown, must not be scored either way).

    The True/None distinction is the whole ballgame: scoring an open window as
    a miss deflates the rate, and dropping open windows while keeping every hit
    (the previous `resolved = FwdDays>=21 | Hit` filter) forces exactly 100%.
    """
    W = window_days or WINDOW_DAYS
    future = price_df[price_df.index > log_date]
    n_bars = len(future)

    # SYMMETRY REQUIREMENT: score a snapshot only when the full W-bar window
    # exists. Returning True the moment a touch happens (even at bar 3) while
    # misses must wait the full W biases the sample catastrophically — hits
    # resolve early, misses never resolve, and the rate pins at 100%. That is
    # exactly the bug this replaced. Both outcomes must face the same bar count.
    if n_bars < W:
        return None

    window = future.head(W)
    if direction == "down":
        return bool((window["Low"] <= level * (1 + TOUCH_PCT)).any())
    return bool((window["High"] >= level * (1 - TOUCH_PCT)).any())


def already_touching(cmp_, level, direction):
    """True if the level sits inside the touch band at log time — a guaranteed
    hit with no predictive content."""
    if cmp_ is None or not cmp_ or pd.isna(cmp_):
        return False
    if direction == "down":
        return float(cmp_) <= float(level) * (1 + TOUCH_PCT)
    return float(cmp_) >= float(level) * (1 - TOUCH_PCT)


def analyse_hit_rates(log_df):
    # Does the log actually carry per-row horizons? --to-month-end silently
    # falls back to WINDOW_DAYS without them, which would misreport what was
    # measured — the same class of silent-fallback bug that produced the fake
    # 100%. Say plainly which horizon is in force.
    have_horizon = ("HorizonDays" in log_df.columns
                    and log_df["HorizonDays"].notna().any())
    horizon_desc = f"within {WINDOW_DAYS} days of log date"
    if USE_LOGGED_HORIZON:
        if have_horizon:
            hd = pd.to_numeric(log_df["HorizonDays"], errors="coerce").dropna()
            horizon_desc = (f"by each row's own month-end "
                            f"({int(hd.min())}-{int(hd.max())} days, per row)")
        else:
            horizon_desc = (f"within {WINDOW_DAYS} days "
                            f"[--to-month-end IGNORED: log has no HorizonDays yet]")

    print(f"\n{'='*70}")
    print(f"  1. HIT-RATE — did price touch S1 / R1 {horizon_desc}?")
    if EXCLUDE_DAY0:
        print(f"     (excluding levels already inside the {TOUCH_PCT*100:.0f}% touch band at log time)")
    print(f"{'='*70}")

    results = []
    price_cache = {}
    adjusted_skips = {}
    open_windows = {}
    day0_skips = {}

    for _, row in log_df.iterrows():
        sym = row["Symbol"]
        if sym not in price_cache:
            price_cache[sym] = load_price(sym)
        pdf = price_cache[sym]
        if pdf is None:
            continue

        log_date = row["Date"]
        fwd_days = min(len(pdf[pdf.index > log_date]), 21)

        # Corporate-action guard: the logged CMP is the close of log_date. If
        # the CSV's close for that same date has since shifted materially,
        # yfinance back-adjusted the series after we logged (split/bonus) —
        # every logged level is in pre-adjustment price terms and would score
        # garbage against the adjusted forward bars. Skip those rows. (Same
        # >15% threshold as data_integrity_check's held-name detector, which
        # does NOT cover panel names nobody holds — this guard is S/R's own.)
        if pd.notna(row.get("CMP")) and log_date in pdf.index:
            ref = float(pdf.loc[log_date, "Close"])
            if ref > 0 and not 0.85 < float(row["CMP"]) / ref < 1.15:
                adjusted_skips[sym] = float(row["CMP"]) / ref
                continue

        for lvl, direction in [("S1", "down"), ("R1", "up"),
                               ("S2", "down"), ("R2", "up")]:
            if not pd.notna(row.get(lvl)):
                continue
            if EXCLUDE_DAY0 and already_touching(row.get("CMP"), row[lvl], direction):
                day0_skips[lvl] = day0_skips.get(lvl, 0) + 1
                continue
            # Per-row horizon when requested and available, else the flag/default.
            W = WINDOW_DAYS
            if USE_LOGGED_HORIZON and pd.notna(row.get("HorizonDays")):
                W = int(row["HorizonDays"])
            if W <= 0:
                continue
            hit = check_touch(pdf, log_date, row[lvl], direction, W)
            if hit is not None:   # None = window still open, not scoreable
                results.append((lvl, sym, row[f"{lvl}_prob"], row[f"{lvl}_n"],
                                hit, fwd_days))
            else:
                open_windows[lvl] = open_windows.get(lvl, 0) + 1

    if adjusted_skips:
        print("  ⚠️  Skipped symbols whose price history was back-adjusted AFTER "
              "logging (split/bonus — logged levels are in pre-adjustment terms):")
        for s, r in sorted(adjusted_skips.items()):
            print(f"      {s}: logged CMP is {r:.2f}x the CSV's close on the log date")

    if not results:
        print("  Not enough time has passed since logging to evaluate any touches yet.")
        print("  (Need at least ~5 trading days after a log date to check.)")
        return pd.DataFrame(columns=["Level", "Symbol", "Prob", "N", "Hit", "FwdDays"])

    if day0_skips:
        print(f"  Excluded {sum(day0_skips.values())} level(s) already inside the "
              f"touch band at log time (guaranteed hits): "
              + ", ".join(f"{k} {v}" for k, v in sorted(day0_skips.items())))

    res_df = pd.DataFrame(results, columns=["Level", "Symbol", "Prob", "N", "Hit", "FwdDays"])

    # Every row here is RESOLVED by construction: check_touch returned None for
    # any window that hadn't completed, and those never entered `results`. So a
    # False is a real miss and the mean is an honest rate — no filter needed.
    # (The previous `FwdDays>=21 | Hit` filter kept all hits but dropped every
    # unresolved miss, which forced 100% whenever no window had closed.)
    for lvl in ["S1", "R1", "S2", "R2"]:
        sub = res_df[res_df["Level"] == lvl]
        line = f"  {lvl}: "
        if len(sub):
            line += f"{sub['Hit'].mean()*100:.1f}% hit rate  (n={len(sub)} resolved)"
        else:
            line += "no resolved snapshots yet"
        pending = open_windows.get(lvl, 0)
        if pending:
            line += f"  [{pending} window(s) still open — excluded, not scored]"
        print(line)

    if len(res_df):
        print(f"\n  Overall hit rate: {res_df['Hit'].mean()*100:.1f}%  "
              f"(n={len(res_df)} resolved, {sum(open_windows.values())} still open)")
    else:
        print(f"\n  No {WINDOW_DAYS}-day window has completed yet. Re-run with "
              f"--window 7 or --window 10 for a shorter but resolvable horizon.")

    if sum(open_windows.values()) > len(res_df):
        print(f"  ⚠️  More windows are open than resolved — the resolved set is "
              f"skewed toward EARLIER log dates. Treat as provisional.")

    return res_df


# ──────────────────────────────────────────────
# 2. LEVEL DRIFT
# ──────────────────────────────────────────────

def analyse_drift(log_df):
    print(f"\n{'='*70}")
    print(f"  2. LEVEL DRIFT — how much did S1/R1 move over the logged period?")
    print(f"{'='*70}")
    print(f"  {'Symbol':<14} {'S1 first':>10} {'S1 last':>10} {'S1 drift%':>10}   "
          f"{'R1 first':>10} {'R1 last':>10} {'R1 drift%':>10}")
    print("  " + "─"*84)

    for sym, grp in log_df.groupby("Symbol"):
        grp = grp.sort_values("Date")
        s1_first, s1_last = grp["S1"].iloc[0], grp["S1"].iloc[-1]
        r1_first, r1_last = grp["R1"].iloc[0], grp["R1"].iloc[-1]

        s1_drift = round((s1_last - s1_first) / s1_first * 100, 1) if pd.notna(s1_first) and s1_first else None
        r1_drift = round((r1_last - r1_first) / r1_first * 100, 1) if pd.notna(r1_first) and r1_first else None

        s1_flag = " ⚠" if s1_drift is not None and abs(s1_drift) >= 5 else ""
        r1_flag = " ⚠" if r1_drift is not None and abs(r1_drift) >= 5 else ""

        print(f"  {sym:<14} {s1_first:>10.2f} {s1_last:>10.2f} {str(s1_drift)+'%':>9}{s1_flag}   "
              f"{r1_first:>10.2f} {r1_last:>10.2f} {str(r1_drift)+'%':>9}{r1_flag}")

    print(f"\n  ⚠ = level moved 5%+ over the period (less stable — treat with caution)")


# ──────────────────────────────────────────────
# 3. PROBABILITY CALIBRATION
# ──────────────────────────────────────────────

def analyse_calibration(res_df):
    print(f"\n{'='*70}")
    print(f"  3. PROBABILITY CALIBRATION — predicted prob vs actual hit-rate")
    print(f"{'='*70}")
    print(f"  ⓘ Scores the probabilities AS LOGGED. Rows written before the")
    print(f"    P(touch) table replaced the P(bounce|touched) table carry the")
    print(f"    OLD metric, so this section cannot judge the new table until a")
    print(f"    fresh month has been logged under it. Mixed-metric rows make")
    print(f"    the buckets uninterpretable — not evidence of miscalibration.")

    if len(res_df) == 0:
        print("  No data yet — need touch results from section 1 first.")
        return

    res_df = res_df.dropna(subset=["Prob"])
    if len(res_df) == 0:
        print("  No probability data available.")
        return

    # Bucket edges must match the ACTIVE table's output range. The bounce table
    # was compressed into ~57-77 so it needed narrow bins there; the P(touch)
    # table legitimately spans ~5-95%, where those bins would leave most rows
    # in a single "0-57" bucket. Pick the geometry from the data itself.
    span = res_df["Prob"].max() - res_df["Prob"].min() if len(res_df) else 0
    if span > 30:      # wide-range table (P(touch))
        buckets = [(0, 20), (20, 40), (40, 60), (60, 75), (75, 90), (90, 101)]
    else:              # compressed legacy band (P(bounce|touched))
        buckets = [(0, 58), (58, 62), (62, 66), (66, 70), (70, 74), (74, 101)]
    print(f"  {'Predicted range':<18} {'Actual hit rate':>16} {'n':>6}")
    print("  " + "─"*44)

    for lo, hi in buckets:
        sub = res_df[(res_df["Prob"] >= lo) & (res_df["Prob"] < hi)]
        if len(sub) == 0:
            continue
        actual = sub["Hit"].mean() * 100
        label  = f"{lo}-{hi-1}%"
        gap    = actual - (lo + hi - 1) / 2
        flag   = " ⚠ overconfident" if gap < -10 else (" ⚠ underconfident" if gap > 10 else "")
        print(f"  {label:<18} {actual:>15.1f}% {len(sub):>6}{flag}")

    print(f"\n  Well-calibrated = actual hit-rate roughly matches the predicted range.")
    print(f"  Overconfident   = model says high % but reality is lower — discount high scores.")


# ──────────────────────────────────────────────
# 4. SAMPLE SIZE (n) SENSITIVITY
# ──────────────────────────────────────────────

def analyse_n_sensitivity(res_df):
    print(f"\n{'='*70}")
    print(f"  4. CELL SUPPORT (n) — hit rate by reach-table cell sample size")
    print(f"{'='*70}")
    print(f"  NOTE: under reach_probability_v2, n is the TRAINING-CELL size of the")
    print(f"  (distance x vol) bucket, not a per-signal trial count. Low n means the")
    print(f"  prediction came from a thin/fallback cell — trust it less. This is a")
    print(f"  data-coverage check, not the old 'more history = better' test.")

    if len(res_df) == 0:
        print("  No data yet.")
        return

    res_df = res_df.dropna(subset=["N"])
    if len(res_df) == 0:
        print("  No sample-size data available.")
        return

    buckets = [(0, 30), (30, 100), (100, 250), (250, 100000)]
    print(f"  {'n range':<14} {'Hit rate':>10} {'count':>8}")
    print("  " + "─"*36)
    for lo, hi in buckets:
        sub = res_df[(res_df["N"] >= lo) & (res_df["N"] < hi)]
        if len(sub) == 0:
            continue
        hr = sub["Hit"].mean() * 100
        label = f"{lo}-{hi if hi < 100000 else '+'}"
        print(f"  {label:<14} {hr:>9.1f}% {len(sub):>8}")

    print(f"\n  n<30 = thin/fallback cell (prob defaulted to base rate) — low confidence.")


# ──────────────────────────────────────────────
# 5. DISTANCE VS ACCURACY
# ──────────────────────────────────────────────

def analyse_distance(log_df, res_df):
    print(f"\n{'='*70}")
    print(f"  5. DISTANCE vs ACCURACY — do closer levels hit more often?")
    print(f"{'='*70}")

    if len(res_df) == 0:
        print("  No data yet.")
        return

    # merge distance info back in from log_df
    merged = []
    for _, row in res_df.iterrows():
        log_rows = log_df[(log_df["Symbol"] == row["Symbol"])]
        if len(log_rows) == 0:
            continue
        # approximate: use first log entry's CMP and level to get distance
        first = log_rows.iloc[0]
        cmp_  = first["CMP"]
        lvl_col = row["Level"]
        if lvl_col not in first or pd.isna(first[lvl_col]) or cmp_ == 0:
            continue
        level = first[lvl_col]
        if row["Level"] in ("S1", "S2"):
            dist = (cmp_ - level) / cmp_ * 100
        else:
            dist = (level - cmp_) / cmp_ * 100
        merged.append((dist, row["Hit"]))

    if not merged:
        print("  Not enough matched data.")
        return

    dist_df = pd.DataFrame(merged, columns=["Distance", "Hit"])
    buckets = [(0, 5), (5, 10), (10, 15), (15, 100)]
    print(f"  {'Distance range':<16} {'Hit rate':>10} {'count':>8}")
    print("  " + "─"*38)
    for lo, hi in buckets:
        sub = dist_df[(dist_df["Distance"] >= lo) & (dist_df["Distance"] < hi)]
        if len(sub) == 0:
            continue
        hr = sub["Hit"].mean() * 100
        label = f"{lo}-{hi if hi < 100 else '+'}%"
        print(f"  {label:<16} {hr:>9.1f}% {len(sub):>8}")

    print(f"\n  If accuracy drops sharply beyond 10-15%, tighten your proximity filter.")


# ──────────────────────────────────────────────
# MAIN
# ──────────────────────────────────────────────

def main():
    global TOUCH_PCT, LOG_PATH, WINDOW_DAYS, EXCLUDE_DAY0
    if "--touch-pct" in sys.argv:
        idx = sys.argv.index("--touch-pct")
        TOUCH_PCT = float(sys.argv[idx + 1]) / 100
    # --window N: shorten the forward horizon so windows actually resolve on a
    # short log. Default 21 = the validated backtest horizon.
    if "--window" in sys.argv:
        idx = sys.argv.index("--window")
        WINDOW_DAYS = int(sys.argv[idx + 1])
    # --to-month-end: score each snapshot against ITS OWN logged horizon (the
    # HorizonDays column) rather than one fixed window — i.e. measure exactly
    # the question the subsystem answers in production.
    if "--to-month-end" in sys.argv:
        global USE_LOGGED_HORIZON
        USE_LOGGED_HORIZON = True
    if "--exclude-day0" in sys.argv:
        EXCLUDE_DAY0 = True
    # --log <path>: analyse an alternate log, e.g. ../data/sr_dynamic_log.csv
    # (built by sr_dynamic_logger.py). Default stays the fixed-panel log.
    if "--log" in sys.argv:
        idx = sys.argv.index("--log")
        LOG_PATH = sys.argv[idx + 1]

    log_df = load_log()

    if "--keep-legacy" not in sys.argv:
        log_df, n_legacy = drop_legacy_rows(log_df)
        if n_legacy:
            print(f"\nDropped {n_legacy} row(s) logged before {V2_START_DATE.date()} "
                  f"(pre-v2 scorer, different probability scale; "
                  f"--keep-legacy to retain)")

    print(f"\nLoaded {len(log_df)} log rows across {log_df['Symbol'].nunique()} stocks")
    print(f"Date range: {log_df['Date'].min().date()} → {log_df['Date'].max().date()}")

    coverage_report(log_df)

    res_df = analyse_hit_rates(log_df)
    analyse_drift(log_df)
    analyse_calibration(res_df)
    analyse_n_sensitivity(res_df)
    analyse_distance(log_df, res_df)

    print(f"\n{'='*70}\n")


if __name__ == "__main__":
    main()