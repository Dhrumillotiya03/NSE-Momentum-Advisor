"""
research_sr_touch_calibration.py
--------------------------------
Is the P(touch) table CALIBRATED in distance? See
PREREG_sr_touch_calibration.md for the pre-registered design and decision
rule (written before any result was seen).

VERDICT (2026-08-15): **calibrated — 0/6 distance buckets miscalibrated.**
Max mean error ±1.3pp on a table quoting 5-95%. Every bootstrap CI includes
zero; same-sign consistency 50-62% against a 70% bar; 3/6 buckets flip sign
between panel halves; dropping the 3 most extreme dates kills every effect.
Two hypotheses died here — see memory
sr-touch-table-distance-calibration-2026-08. Do not re-test without a NEW
data source.

WHY THIS IS NOT A RE-OPENING OF sr-model-sweep-exhausted-2026-08. That study
closed the search on OOS CORRELATION — ranking quality. This one asks about
CALIBRATION — absolute level. Correlation is invariant to a monotone
distortion, so oos_corr=0.529 says nothing about whether a cell reading 14.6%
comes true 14.6% of the time. Both "at its ceiling" and "well calibrated" are
now established, and they are different claims.

THE METHODOLOGICAL POINT (the part worth carrying forward). A first pass
using ROWS as the unit of observation appeared to find a large, systematic
distance-shape error (11/24 cells outside a Wilson CI). It was an artifact:
~500 symbols on one test date all face the same market, so row-level CIs
assume an independence that does not exist — effective n was nearer 4 than
3,954. Aggregating to per-DATE errors and bootstrapping across dates made the
effect vanish entirely. Same family as the Lo-2002 autocorrelation adjustment
already applied to Sharpe here. Any future S/R calibration work must use
dates (or months) as the unit, never rows.

Read-only: reuses sr_build_touchtable.collect/build_table/bucket VERBATIM
(never reimplement the touch test — that is how this subsystem once produced
a fake 100% hit rate), and writes only to data/_research/.

Usage (from scripts/):
    python research_sr_touch_calibration.py            # analyse cached panel
    python research_sr_touch_calibration.py --collect  # rebuild panel (~35 min)
"""
import os
import sys

import numpy as np
import pandas as pd

import sr_build_touchtable as B

OBS = "../data/_research/calib_obs_wide.csv"
OUT = "../data/_research/calib_wf_results.csv"

TEST_MONTHS = 72      # monthly test dates to collect (production table uses 12)
BURN_IN = 24          # dates used to seed the first table
N_BOOT = 5000
SIGN_THRESH = 0.70    # pre-registered
MIN_PER_CELL = 20     # a date-bucket thinner than this cannot mean anything

rng = np.random.default_rng(0)


def collect_panel():
    """Wide panel: many test DATES, because dates are the unit of inference."""
    B.TEST_MONTHS = TEST_MONTHS
    symbols = sorted(f[:-4] for f in os.listdir(B.PRICE_DIR) if f.endswith(".csv"))
    print(f"Collecting @21d over {len(symbols)} symbols x {TEST_MONTHS} dates")
    df = B.collect(symbols, 21)
    os.makedirs("../data/_research", exist_ok=True)
    df.to_csv(OBS, index=False)
    print(f"collected {len(df)} obs across {df['td'].nunique()} test dates")
    return df


def boot_ci(x, n_boot=N_BOOT):
    x = np.asarray(x, float)
    if len(x) < 3:
        return (float("nan"), float("nan"))
    idx = rng.integers(0, len(x), size=(n_boot, len(x)))
    return tuple(np.percentile(x[idx].mean(axis=1), [2.5, 97.5]))


def walk_forward(df):
    """Expanding window: for each date, build the table from STRICTLY EARLIER
    dates only, then score that date. No look-ahead at any point."""
    df = df.copy()
    df["db"] = df["dist"].apply(lambda x: B.bucket(x, B.DIST_EDGES, B.DIST_LABELS))
    df["vb"] = df["vol"].apply(lambda x: B.bucket(x, B.VOL_EDGES, B.VOL_LABELS))
    dates = sorted(df["td"].unique())
    print(f"{len(df)} obs across {len(dates)} test dates "
          f"({pd.Timestamp(dates[0]).date()} -> {pd.Timestamp(dates[-1]).date()})")
    print(f"burn-in {BURN_IN} -> {len(dates)-BURN_IN} evaluation dates\n")

    recs = []
    for d in dates[BURN_IN:]:
        train, test = df[df["td"] < d], df[df["td"] == d]
        if len(test) == 0 or len(train) < 500:
            continue
        table, base = B.build_table(train)
        t = test.assign(pred=test.apply(
            lambda r: table.get(f"{r['db']}|{r['vb']}", {}).get("prob", base),
            axis=1))
        for db in B.DIST_LABELS:
            s = t[t["db"] == db]
            if len(s) < MIN_PER_CELL:
                continue
            recs.append({"date": pd.Timestamp(d), "db": db, "n": len(s),
                         "pred": s["pred"].mean(),
                         "actual": s["hit"].mean() * 100,
                         "err": s["hit"].mean() * 100 - s["pred"].mean()})
    return pd.DataFrame(recs)


def report(r):
    print("=" * 78)
    print("  WALK-FORWARD CALIBRATION ERROR BY DISTANCE BUCKET")
    print("  error = actual - predicted, averaged per DATE (dates = unit)")
    print("=" * 78)
    print(f"  {'bucket':<9}{'dates':>6}{'mean err':>10}{'95% CI':>18}"
          f"{'same-sign':>11}   verdict")
    print("  " + "-" * 72)
    verdicts = {}
    for db in B.DIST_LABELS:
        s = r[r["db"] == db]
        if len(s) < 3:
            continue
        e = s["err"].values
        lo, hi = boot_ci(e)
        frac = max((e > 0).mean(), (e < 0).mean())
        ci_excl = (lo > 0) or (hi < 0)
        v = "MISCALIBRATED" if (ci_excl and frac >= SIGN_THRESH) else (
            "—" if not ci_excl else "CI only, sign inconsistent")
        verdicts[db] = v
        print(f"  {db:<9}{len(s):>6}{e.mean():>+9.1f}pp"
              f"{f'[{lo:+.1f},{hi:+.1f}]':>18}{frac*100:>10.0f}%   {v}")

    print(f"\n{'='*78}\n  ADVERSE CHECK — early vs late half (sign stability)\n{'='*78}")
    mid = r["date"].median()
    for db in B.DIST_LABELS:
        s = r[r["db"] == db]
        if len(s) < 6:
            continue
        a, b = s[s["date"] <= mid]["err"].mean(), s[s["date"] > mid]["err"].mean()
        print(f"  {db:<9}{a:>+11.1f}pp{b:>+11.1f}pp   "
              f"{'YES — unstable' if np.sign(a) != np.sign(b) else 'no'}")

    print(f"\n{'='*78}\n  ADVERSE CHECK — drop the 3 most extreme dates\n{'='*78}")
    for db in B.DIST_LABELS:
        s = r[r["db"] == db]
        if len(s) < 8:
            continue
        trimmed = s.reindex(s["err"].abs().sort_values().index)[:-3]
        lo, hi = boot_ci(trimmed["err"].values)
        print(f"  {db:<9} full {s['err'].mean():+6.1f}pp -> trimmed "
              f"{trimmed['err'].mean():+6.1f}pp  CI [{lo:+.1f},{hi:+.1f}]  "
              f"{'holds' if (lo > 0 or hi < 0) else 'GONE'}")

    n_bad = sum(1 for v in verdicts.values() if v == "MISCALIBRATED")
    print(f"\n  VERDICT: {n_bad}/{len(verdicts)} distance bucket(s) miscalibrated "
          f"by the pre-registered rule.")


def main():
    if "--collect" in sys.argv or not os.path.exists(OBS):
        df = collect_panel()
    else:
        df = pd.read_csv(OBS, parse_dates=["td"])
        print(f"Loaded cached panel ({len(df)} obs) — --collect to rebuild.\n")
    r = walk_forward(df)
    r.to_csv(OUT, index=False)
    report(r)
    print(f"\nwrote {OUT}")


if __name__ == "__main__":
    main()
