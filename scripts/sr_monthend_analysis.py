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
"""

import os, sys
import numpy as np
import pandas as pd

LOG_PATH  = "../data/sr_daily_log.csv"
PRICE_DIR = "../data/price_data/"

TOUCH_PCT = 0.01   # price must come within 1% of level to count as "touched"


# ──────────────────────────────────────────────
# LOAD
# ──────────────────────────────────────────────

def load_log():
    if not os.path.exists(LOG_PATH):
        print(f"❌ No log found at {LOG_PATH}. Run sr_daily_logger.py first.")
        sys.exit(1)
    df = pd.read_csv(LOG_PATH, parse_dates=["Date"])
    return df.sort_values(["Symbol", "Date"])


def load_price(symbol):
    path = os.path.join(PRICE_DIR, f"{symbol}.NS.csv")
    if not os.path.exists(path):
        return None
    df = pd.read_csv(path, parse_dates=["Date"])
    df = df.sort_values("Date").set_index("Date")
    for col in ["High", "Low", "Close"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return df.dropna(subset=["High", "Low", "Close"])


# ──────────────────────────────────────────────
# 1. BASIC HIT-RATE PER LOGGED SNAPSHOT
# ──────────────────────────────────────────────

def check_touch(price_df, log_date, level, direction, window_days=21):
    """
    direction: 'down' = check Low reaches level (support)
               'up'   = check High reaches level (resistance)
    Returns True/False/None (None = not enough future data yet)
    """
    future = price_df[price_df.index > log_date].head(window_days)
    if len(future) < 3:
        return None   # not enough time has passed yet

    if direction == "down":
        touch_line = level * (1 + TOUCH_PCT)
        return bool((future["Low"] <= touch_line).any())
    else:
        touch_line = level * (1 - TOUCH_PCT)
        return bool((future["High"] >= touch_line).any())


def analyse_hit_rates(log_df):
    print(f"\n{'='*70}")
    print(f"  1. HIT-RATE — did price touch S1 / R1 within 21 days of log date?")
    print(f"{'='*70}")

    results = []
    price_cache = {}

    for _, row in log_df.iterrows():
        sym = row["Symbol"]
        if sym not in price_cache:
            price_cache[sym] = load_price(sym)
        pdf = price_cache[sym]
        if pdf is None:
            continue

        log_date = row["Date"]

        if pd.notna(row.get("S1")):
            hit = check_touch(pdf, log_date, row["S1"], "down")
            if hit is not None:
                results.append(("S1", sym, row["S1_prob"], row["S1_n"], hit))

        if pd.notna(row.get("R1")):
            hit = check_touch(pdf, log_date, row["R1"], "up")
            if hit is not None:
                results.append(("R1", sym, row["R1_prob"], row["R1_n"], hit))

        if pd.notna(row.get("S2")):
            hit = check_touch(pdf, log_date, row["S2"], "down")
            if hit is not None:
                results.append(("S2", sym, row["S2_prob"], row["S2_n"], hit))

        if pd.notna(row.get("R2")):
            hit = check_touch(pdf, log_date, row["R2"], "up")
            if hit is not None:
                results.append(("R2", sym, row["R2_prob"], row["R2_n"], hit))

    if not results:
        print("  Not enough time has passed since logging to evaluate any touches yet.")
        print("  (Need at least ~5 trading days after a log date to check.)")
        return pd.DataFrame(columns=["Level", "Symbol", "Prob", "N", "Hit"])

    res_df = pd.DataFrame(results, columns=["Level", "Symbol", "Prob", "N", "Hit"])

    for lvl in ["S1", "R1", "S2", "R2"]:
        sub = res_df[res_df["Level"] == lvl]
        if len(sub) == 0:
            continue
        hit_rate = sub["Hit"].mean() * 100
        print(f"  {lvl}: {hit_rate:.1f}% hit rate  (n={len(sub)} snapshots)")

    overall = res_df["Hit"].mean() * 100
    print(f"\n  Overall hit rate: {overall:.1f}%  (n={len(res_df)})")

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

    if len(res_df) == 0:
        print("  No data yet — need touch results from section 1 first.")
        return

    res_df = res_df.dropna(subset=["Prob"])
    if len(res_df) == 0:
        print("  No probability data available.")
        return

    # Fine buckets in reach_probability_v2's operating range (~57-77%). The old
    # 10-wide buckets from 0-100 left everything crammed into 60-70/70-80 because
    # v2 is an empirical base-rate model, not the 0-100 analog scan. Narrow bins
    # here are what let month-end data tell 57% vs 66% vs 74% cells apart.
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
    global TOUCH_PCT
    if "--touch-pct" in sys.argv:
        idx = sys.argv.index("--touch-pct")
        TOUCH_PCT = float(sys.argv[idx + 1]) / 100

    log_df = load_log()

    print(f"\nLoaded {len(log_df)} log rows across {log_df['Symbol'].nunique()} stocks")
    print(f"Date range: {log_df['Date'].min().date()} → {log_df['Date'].max().date()}")

    res_df = analyse_hit_rates(log_df)
    analyse_drift(log_df)
    analyse_calibration(res_df)
    analyse_n_sensitivity(res_df)
    analyse_distance(log_df, res_df)

    print(f"\n{'='*70}\n")


if __name__ == "__main__":
    main()