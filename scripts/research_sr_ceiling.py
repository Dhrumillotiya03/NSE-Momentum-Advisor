"""
research_sr_ceiling.py
----------------------
How much headroom is actually left in the S/R P(touch) model?

The sweep (research_sr_model_sweep.py) asks "does variant X beat the current
model". This asks the prior question: how good COULD any model of this shape
get? Without that, a sequence of failed variants is ambiguous — it could mean
the ideas were bad, or it could mean the model is already near the ceiling.

Three reference points, all on the same holdout:

  1. BASELINE   — the production (distance x vol) bucket table.
  2. ORACLE-BUCKET — the same buckets, but fitted on the HOLDOUT itself.
     This is the best any (distance x vol) lookup could possibly do on this
     data: it has seen the answers. The gap baseline->oracle is the total
     headroom available to better *fitting* of these two features.
  3. ORACLE-RICH — a gradient-boosted model with every feature collected
     (distance, all five vol estimators, ATR distance, trend, level strength),
     ALSO fitted on the holdout. The gap oracle-bucket->oracle-rich is the
     headroom available from better *features*, not better fitting.

Interpretation:
  - Small baseline->oracle-bucket gap  => the table is already extracting
    nearly all the signal these two features carry; bucket/threshold tuning is
    a dead end (and would be curve-fitting).
  - Small oracle-bucket->oracle-rich gap => the extra features carry little
    independent information; adding them cannot help much even in principle.

Both oracles are DELIBERATELY overfit — they are upper bounds, not candidate
models. Never ship anything fitted this way.

Usage:
    python research_sr_ceiling.py [--forward 21] [--quick]
"""
import os
import sys
import numpy as np
import pandas as pd

import sr_build_touchtable as B
import research_sr_model_sweep as S


def bucket_fit_predict(fit_df, score_df, dist_edges, vol_edges, min_cell,
                       vol_col="vol_close"):
    dl = [f"d{i}" for i in range(len(dist_edges) - 1)]
    vl = [f"v{i}" for i in range(len(vol_edges) - 1)]
    fit_df = fit_df.copy(); score_df = score_df.copy()
    for d in (fit_df, score_df):
        d["db"] = d["dist"].apply(lambda x: B.bucket(x, dist_edges, dl))
        d["vb"] = d[vol_col].apply(lambda x: B.bucket(x, vol_edges, vl))
    base = fit_df["hit"].mean() * 100
    table = {}
    for db in dl:
        marg = fit_df[fit_df["db"] == db]
        mr = marg["hit"].mean() * 100 if len(marg) >= min_cell else base
        for vb in vl:
            cell = fit_df[(fit_df["db"] == db) & (fit_df["vb"] == vb)]
            table[f"{db}|{vb}"] = (cell["hit"].mean() * 100
                                   if len(cell) >= min_cell else mr)
    pred = score_df.apply(lambda r: table.get(f"{r['db']}|{r['vb']}", base), axis=1)
    c = pred.corr(score_df["hit"].astype(float))
    return float(c) if pd.notna(c) else None


def gbm_fit_predict(fit_df, score_df, features):
    X_fit = fit_df[features].astype(float).replace([np.inf, -np.inf], np.nan)
    X_sc = score_df[features].astype(float).replace([np.inf, -np.inf], np.nan)
    med = X_fit.median()
    X_fit = X_fit.fillna(med); X_sc = X_sc.fillna(med)
    try:
        from sklearn.ensemble import HistGradientBoostingClassifier
        m = HistGradientBoostingClassifier(max_depth=4, max_iter=400,
                                           learning_rate=0.05, random_state=0)
        m.fit(X_fit, fit_df["hit"].astype(int))
        p = m.predict_proba(X_sc)[:, 1]
    except Exception as e:
        print(f"  (gbm unavailable: {e})")
        return None
    c = pd.Series(p).corr(score_df["hit"].astype(float).reset_index(drop=True))
    return float(c) if pd.notna(c) else None


def main():
    argv = sys.argv[1:]
    horizons = [5, 10, 15, 21]
    if "--forward" in argv:
        horizons = [int(argv[argv.index("--forward") + 1])]
    symbols = sorted(f[:-4] for f in os.listdir(B.PRICE_DIR) if f.endswith(".csv"))
    if "--quick" in argv:
        symbols = symbols[:150]

    rich = ["dist", "vol_close", "vol_parkinson", "vol_garman_klass",
            "vol_rogers_satchell", "vol_open_to_open", "atr_dist",
            "trend_toward", "strength"]

    print(f"S/R headroom analysis — {len(symbols)} symbols, horizons {horizons}\n")
    rows = []
    for W in horizons:
        print(f"=== {W}d ===")
        df = S.collect(symbols, W)
        if df.empty:
            print("  no observations"); continue
        tr, ho = S.split(df)
        if tr is None or not len(ho):
            print("  insufficient holdout"); continue

        real = bucket_fit_predict(tr, ho, B.DIST_EDGES, B.VOL_EDGES, B.MIN_CELL)
        orc_b = bucket_fit_predict(ho, ho, B.DIST_EDGES, B.VOL_EDGES, 5)
        orc_r = gbm_fit_predict(ho, ho, rich)
        rows.append((W, real, orc_b, orc_r, len(ho)))
        print(f"  holdout n={len(ho)}")
        print(f"  baseline (honest, fitted on train) : {real:.4f}")
        print(f"  ORACLE bucket  (fitted on holdout) : {orc_b:.4f}")
        print(f"  ORACLE rich-features (on holdout)  : "
              f"{orc_r:.4f}" if orc_r is not None else "  ORACLE rich: n/a")
        print()

    if not rows:
        return
    print("=" * 78)
    print(f"  {'horizon':>7} {'baseline':>10} {'oracle-buckets':>15} "
          f"{'oracle-rich':>12} {'fit gap':>9} {'feat gap':>9}")
    print("-" * 78)
    fit_gaps, feat_gaps = [], []
    for W, real, ob, orr, n in rows:
        fg = ob - real
        rg = (orr - ob) if orr is not None else float("nan")
        fit_gaps.append(fg)
        if orr is not None:
            feat_gaps.append(rg)
        print(f"  {W:>6}d {real:>10.4f} {ob:>15.4f} "
              f"{(orr if orr is not None else float('nan')):>12.4f} "
              f"{fg:>+9.4f} {rg:>+9.4f}")
    print("-" * 78)
    print(f"  mean fitting headroom (same 2 features, perfect fit) : "
          f"{np.mean(fit_gaps):+.4f}")
    if feat_gaps:
        print(f"  mean feature headroom (all features, perfect fit) : "
              f"{np.mean(feat_gaps):+.4f}")
    print()
    print("READING THIS: both oracles are fitted on the data they are scored on,")
    print("so they are UPPER BOUNDS, not models. A small fitting gap means the")
    print("bucket table already extracts what distance x vol carry — retuning")
    print("buckets is curve-fitting, not improvement. A small feature gap means")
    print("the extra features (ATR, trend, strength, other vol estimators) hold")
    print("little independent signal, so no variant built on them can help much.")


if __name__ == "__main__":
    main()
