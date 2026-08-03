"""
research_sr_model_bootstrap.py
------------------------------
Is the continuous model's small edge over the bucket table REAL?

The full sweep (research_sr_model_sweep.py) found that a logistic /
gradient-boosted model on (distance, vol, ATR-distance, trend, level strength)
beats the production bucket table at ALL FOUR horizons — but by only +0.010
mean OOS correlation, half the pre-registered +0.02 threshold. Two readings:

  (a) noise that happened to align across four correlated horizons, or
  (b) a real but small edge.

The deltas GROW with horizon (+0.003 at 5d -> +0.015 at 21d), which is
signal-shaped rather than white-noise-shaped, so a point estimate alone cannot
settle it. This script attaches a confidence interval.

METHOD — paired block bootstrap on the holdout. Both models are scored on the
SAME resampled rows, so the pairing removes sampling variation common to both
and isolates the difference. Resampling is by TEST DATE (block), not by row:
observations on the same date share market conditions and are not independent,
and row-wise resampling would understate the interval — the same
autocorrelation correction applied in research_statistical_hygiene.py.

DECISION RULE (pre-registered):
  ADOPT only if the 95% CI on the delta EXCLUDES ZERO at a majority of
  horizons. A CI that includes zero means the point estimate cannot be
  distinguished from noise, regardless of how consistent the sign looks.

Even if it clears, note what adopting costs: the bucket table is inspectable
(24 cells you can read and sanity-check), while a fitted model is not. That
tradeoff is a judgement call for the user, not something this script decides.

Usage:
    python research_sr_model_bootstrap.py [--forward 21] [--boot 2000] [--quick]
"""
import os
import sys
import numpy as np
import pandas as pd

import sr_build_touchtable as B
import research_sr_model_sweep as S

N_BOOT = 1000
RICH = ["dist", "vol_close", "atr_dist", "trend_toward", "strength"]


def fit_bucket(train, dist_edges, vol_edges, min_cell):
    dl = [f"d{i}" for i in range(len(dist_edges) - 1)]
    vl = [f"v{i}" for i in range(len(vol_edges) - 1)]
    t = train.copy()
    t["db"] = t["dist"].apply(lambda x: B.bucket(x, dist_edges, dl))
    t["vb"] = t["vol_close"].apply(lambda x: B.bucket(x, vol_edges, vl))
    base = t["hit"].mean() * 100
    table = {}
    for db in dl:
        marg = t[t["db"] == db]
        mr = marg["hit"].mean() * 100 if len(marg) >= min_cell else base
        for vb in vl:
            cell = t[(t["db"] == db) & (t["vb"] == vb)]
            table[f"{db}|{vb}"] = (cell["hit"].mean() * 100
                                   if len(cell) >= min_cell else mr)
    return table, base, dist_edges, vol_edges, dl, vl


def predict_bucket(model, df):
    table, base, de, ve, dl, vl = model
    db = df["dist"].apply(lambda x: B.bucket(x, de, dl))
    vb = df["vol_close"].apply(lambda x: B.bucket(x, ve, vl))
    return np.array([table.get(f"{a}|{b}", base) for a, b in zip(db, vb)])


def fit_logit(train):
    from sklearn.linear_model import LogisticRegression
    from sklearn.preprocessing import StandardScaler
    from sklearn.pipeline import make_pipeline
    X = train[RICH].astype(float).replace([np.inf, -np.inf], np.nan)
    med = X.median()
    m = make_pipeline(StandardScaler(), LogisticRegression(max_iter=2000))
    m.fit(X.fillna(med), train["hit"].astype(int))
    return m, med


def predict_logit(model, df):
    m, med = model
    X = df[RICH].astype(float).replace([np.inf, -np.inf], np.nan).fillna(med)
    return m.predict_proba(X)[:, 1]


def main():
    argv = sys.argv[1:]
    horizons = [5, 10, 15, 21]
    if "--forward" in argv:
        horizons = [int(argv[argv.index("--forward") + 1])]
    n_boot = N_BOOT
    if "--boot" in argv:
        n_boot = int(argv[argv.index("--boot") + 1])
    symbols = sorted(f[:-4] for f in os.listdir(B.PRICE_DIR) if f.endswith(".csv"))
    if "--quick" in argv:
        symbols = symbols[:150]

    print(f"Paired block bootstrap — {len(symbols)} symbols, horizons {horizons}, "
          f"{n_boot} resamples")
    print("Rule: adopt only if the 95% CI excludes zero at a majority of horizons.\n")

    rows = []
    for W in horizons:
        print(f"=== {W}d ===")
        df = S.collect(symbols, W)
        if df.empty:
            print("  no observations"); continue
        tr, ho = S.split(df)
        if tr is None or not len(ho):
            print("  insufficient holdout"); continue

        bkt = fit_bucket(tr, B.DIST_EDGES, B.VOL_EDGES, B.MIN_CELL)
        try:
            lgt = fit_logit(tr)
        except Exception as e:
            print(f"  logistic unavailable: {e}"); continue

        ho = ho.reset_index(drop=True)
        p_b = predict_bucket(bkt, ho)
        p_l = predict_logit(lgt, ho)
        y = ho["hit"].astype(float).values

        d_point = (np.corrcoef(p_l, y)[0, 1] - np.corrcoef(p_b, y)[0, 1])

        # Block bootstrap over test dates.
        dates = ho["td"].values
        uniq = pd.unique(dates)
        idx_by_date = {d: np.where(dates == d)[0] for d in uniq}
        rng = np.random.default_rng(0)
        deltas = []
        for _ in range(n_boot):
            pick = rng.choice(uniq, size=len(uniq), replace=True)
            idx = np.concatenate([idx_by_date[d] for d in pick])
            yy = y[idx]
            if yy.std() == 0:
                continue
            cb = np.corrcoef(p_b[idx], yy)[0, 1]
            cl = np.corrcoef(p_l[idx], yy)[0, 1]
            if np.isfinite(cb) and np.isfinite(cl):
                deltas.append(cl - cb)
        if not deltas:
            print("  bootstrap produced no valid resamples"); continue
        lo, hi = np.percentile(deltas, [2.5, 97.5])
        p_better = float(np.mean(np.array(deltas) > 0))
        excl = lo > 0 or hi < 0
        rows.append((W, d_point, lo, hi, p_better, excl))
        print(f"  holdout n={len(ho)}  blocks={len(uniq)}")
        print(f"  delta (logit - bucket) = {d_point:+.4f}")
        print(f"  95% CI [{lo:+.4f}, {hi:+.4f}]   P(logit better) = {p_better:.1%}")
        print(f"  CI excludes zero: {'YES' if excl else 'NO'}\n")

    if not rows:
        return
    print("=" * 76)
    print(f"  {'horizon':>7} {'delta':>9} {'95% CI':>22} {'P(better)':>10} {'excl 0':>7}")
    print("-" * 76)
    for W, d, lo, hi, pb, ex in rows:
        print(f"  {W:>6}d {d:>+9.4f}   [{lo:>+7.4f}, {hi:>+7.4f}] "
              f"{pb:>9.1%} {'YES' if ex else 'no':>7}")
    print("-" * 76)
    n_excl = sum(1 for r in rows if r[5])
    print(f"  CI excludes zero at {n_excl}/{len(rows)} horizons")
    adopt = n_excl > len(rows) / 2
    print()
    print(f"VERDICT: {'SIGNIFICANT — worth considering' if adopt else 'NOT SIGNIFICANT'}")
    if not adopt:
        print("  The edge cannot be distinguished from noise. Keep the bucket")
        print("  table: it is inspectable, has no fitting dependency, and no")
        print("  measurable accuracy cost.")
    else:
        print("  Statistically real, but SMALL. Weigh against losing an")
        print("  inspectable 24-cell table for a fitted model, and re-verify")
        print("  the monotonicity-in-horizon invariant before shipping.")


if __name__ == "__main__":
    main()
