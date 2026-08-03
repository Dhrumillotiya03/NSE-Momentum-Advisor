"""
research_sr_model_sweep.py
--------------------------
Systematic sweep of every plausible improvement to the S/R P(touch) model.

Collects ALL candidate features in ONE pass, so every variant is evaluated on
exactly the same observations — differences are then attributable to the
variant, not to sampling. Evaluation is always OOS corr(pred, hit) on a
time-based holdout, the same metric that exposed the P(bounce|touched) bug
(0.173 -> 0.529).

CANDIDATES
  A. Gap-preserving vol estimators — Garman-Klass, Rogers-Satchell. Parkinson
     was already rejected (research_sr_vol_estimator.py) because it discards
     overnight gaps; GK/RS use full OHLC and keep them.
  B. Distance bucket geometry — distance is the dominant feature, so bucket
     placement matters more here than anywhere else.
  C. Volatility bucket geometry.
  D. MIN_CELL — the thin-cell fallback threshold.
  E. Trend feature — is the level in the direction the stock is already moving?
  F. Level strength — score_level's output is computed and currently discarded.
  G. Continuous model (logistic regression / gradient boosting) vs the bucket
     lookup. Buckets throw away within-bucket information by construction.
  H. ATR-normalised distance — "3 ATRs away" may travel better across stocks
     than "5% away", since percent distance means different things at
     different volatilities.

PRE-REGISTERED DECISION RULE (fixed before any run):
  ADOPT a variant only if it beats the baseline by >= +0.02 OOS correlation
  AND wins at a majority of the tested horizons (5/10/15/21d).
  Rationale: with ~3900 holdout rows, deltas below ~0.02 are within resampling
  noise; and this repo has a documented history (memory
  statistical-hygiene-2026-07) of 1-2pp point estimates failing to replicate.
  A variant that wins on one horizon only is a multiple-comparisons artifact.

Usage:
    python research_sr_model_sweep.py                 # full sweep
    python research_sr_model_sweep.py --forward 21    # single horizon
    python research_sr_model_sweep.py --quick         # 150-symbol subset
"""
import os
import sys
import numpy as np
import pandas as pd

import sr_build_touchtable as B
from support_resistance import get_levels, load_stock, score_level

HOLDOUT_MONTHS = B.HOLDOUT_MONTHS


# ── volatility estimators ─────────────────────────────────────────────
def vol_close(past):
    r = past["Close"].pct_change().dropna().tail(252)
    return float(r.std() * np.sqrt(252) * 100) if len(r) >= 30 else None


def vol_parkinson(past):
    d = past.tail(252)
    hl = np.log(d["High"] / d["Low"]).replace([np.inf, -np.inf], np.nan).dropna()
    if len(hl) < 30:
        return None
    return float(np.sqrt((hl ** 2).mean() / (4 * np.log(2))) * np.sqrt(252) * 100)


def vol_garman_klass(past):
    """Uses O/H/L/C. Keeps intraday range AND the open, so unlike Parkinson it
    is not blind to where the session started relative to the prior close."""
    d = past.tail(252)
    hl = np.log(d["High"] / d["Low"]).replace([np.inf, -np.inf], np.nan)
    co = np.log(d["Close"] / d["Open"]).replace([np.inf, -np.inf], np.nan)
    v = (0.5 * hl ** 2 - (2 * np.log(2) - 1) * co ** 2).dropna()
    if len(v) < 30 or v.mean() <= 0:
        return None
    return float(np.sqrt(v.mean()) * np.sqrt(252) * 100)


def vol_rogers_satchell(past):
    """Drift-independent OHLC estimator — handles trending series better than
    GK, which assumes zero drift (momentum names are trending by selection)."""
    d = past.tail(252)
    ho = np.log(d["High"] / d["Open"]); hc = np.log(d["High"] / d["Close"])
    lo = np.log(d["Low"] / d["Open"]);  lc = np.log(d["Low"] / d["Close"])
    v = (ho * hc + lo * lc).replace([np.inf, -np.inf], np.nan).dropna()
    if len(v) < 30 or v.mean() <= 0:
        return None
    return float(np.sqrt(v.mean()) * np.sqrt(252) * 100)


def vol_close_gap(past):
    """Close-to-close but computed on OPEN-to-OPEN returns — an explicit
    gap-weighted variant, to isolate whether gaps are the active ingredient."""
    r = past["Open"].pct_change().dropna().tail(252)
    return float(r.std() * np.sqrt(252) * 100) if len(r) >= 30 else None


VOL_FUNCS = {
    "close": vol_close,
    "parkinson": vol_parkinson,
    "garman_klass": vol_garman_klass,
    "rogers_satchell": vol_rogers_satchell,
    "open_to_open": vol_close_gap,
}


def atr(past, n=14):
    d = past.tail(n + 1)
    if len(d) < n + 1:
        return None
    pc = d["Close"].shift(1)
    tr = pd.concat([d["High"] - d["Low"],
                    (d["High"] - pc).abs(),
                    (d["Low"] - pc).abs()], axis=1).max(axis=1).dropna()
    return float(tr.tail(n).mean()) if len(tr) >= n else None


def trend_slope(past, n=63):
    """Normalised slope of Close over the last n bars — the stock's own drift.
    A resistance above a rising stock should be easier to touch than the same
    distance above a falling one; the current model cannot express that."""
    c = past["Close"].tail(n)
    if len(c) < n:
        return None
    x = np.arange(len(c), dtype=float)
    slope = np.polyfit(x, c.values.astype(float), 1)[0]
    mean = float(c.mean())
    return float(slope / mean * 252 * 100) if mean else None


# ── collection ────────────────────────────────────────────────────────
def collect(symbols, forward_days):
    rows = []
    for idx, sym in enumerate(symbols, 1):
        sym = sym.strip().upper()
        if not sym.endswith(".NS"):
            sym += ".NS"
        if idx % 100 == 0:
            print(f"    [{idx}/{len(symbols)}]", flush=True)
        df = load_stock(sym)
        if df is None or len(df) < B.MIN_DATA:
            continue
        test_dates = pd.date_range(end=df.index[-1],
                                   periods=B.TEST_MONTHS + 1, freq="ME")[:-1]
        for td in test_dates:
            past = df[df.index <= td]
            future = df[df.index > td].head(forward_days)
            if len(past) < B.MIN_DATA // 2 or len(future) < forward_days:
                continue

            vols = {k: f(past) for k, f in VOL_FUNCS.items()}
            if any(v is None for v in vols.values()):
                continue
            a = atr(past)
            slope = trend_slope(past)
            if a is None or not a or slope is None:
                continue
            try:
                sup, res_lvl, _, _ = get_levels(past, fast=True)
            except Exception:
                continue
            cur = float(past["Close"].iloc[-1])
            if not cur:
                continue

            for level, direction in [(sup, "down"), (res_lvl, "up")]:
                if level is None or not np.isfinite(level) or level <= 0:
                    continue
                if direction == "down" and level >= cur:
                    continue
                if direction == "up" and level <= cur:
                    continue
                try:
                    strength = float(score_level(
                        past, level, "support" if direction == "down" else "resistance",
                        fast=True))
                except Exception:
                    strength = np.nan
                dist = abs(level - cur) / cur
                rec = {
                    "td": td, "dist": dist,
                    "atr_dist": abs(level - cur) / a,
                    "strength": strength,
                    # Trend TOWARD the level: positive slope helps an upside
                    # level, hurts a downside one.
                    "trend_toward": slope if direction == "up" else -slope,
                    "hit": B.touched(future, level, direction),
                }
                rec.update({f"vol_{k}": v for k, v in vols.items()})
                rows.append(rec)
    return pd.DataFrame(rows)


# ── evaluation ────────────────────────────────────────────────────────
def split(df):
    dates = sorted(df["td"].unique())
    if len(dates) <= HOLDOUT_MONTHS:
        return None, None
    cut = dates[-HOLDOUT_MONTHS]
    return df[df["td"] < cut], df[df["td"] >= cut]


def eval_buckets(df, dist_col="dist", vol_col="vol_close",
                 dist_edges=None, vol_edges=None, min_cell=None):
    """Build the bucket table on train, score the holdout. Mirrors
    sr_build_touchtable.build_table, including the distance-marginal fallback."""
    dist_edges = dist_edges or B.DIST_EDGES
    vol_edges = vol_edges or B.VOL_EDGES
    min_cell = min_cell or B.MIN_CELL
    dl = [f"d{i}" for i in range(len(dist_edges) - 1)]
    vl = [f"v{i}" for i in range(len(vol_edges) - 1)]

    tr, ho = split(df)
    if tr is None or not len(ho):
        return None
    tr = tr.copy(); ho = ho.copy()
    for d in (tr, ho):
        d["db"] = d[dist_col].apply(lambda x: B.bucket(x, dist_edges, dl))
        d["vb"] = d[vol_col].apply(lambda x: B.bucket(x, vol_edges, vl))
    base = tr["hit"].mean() * 100

    table = {}
    for db in dl:
        marg = tr[tr["db"] == db]
        marg_rate = marg["hit"].mean() * 100 if len(marg) >= min_cell else base
        for vb in vl:
            cell = tr[(tr["db"] == db) & (tr["vb"] == vb)]
            table[f"{db}|{vb}"] = (cell["hit"].mean() * 100
                                   if len(cell) >= min_cell else marg_rate)

    pred = ho.apply(lambda r: table.get(f"{r['db']}|{r['vb']}", base), axis=1)
    c = pred.corr(ho["hit"].astype(float))
    return float(c) if pd.notna(c) else None


def eval_model(df, features, kind="logit"):
    """Continuous model instead of a bucket lookup."""
    tr, ho = split(df)
    if tr is None or not len(ho):
        return None
    X_tr = tr[features].astype(float).replace([np.inf, -np.inf], np.nan)
    X_ho = ho[features].astype(float).replace([np.inf, -np.inf], np.nan)
    med = X_tr.median()
    X_tr = X_tr.fillna(med); X_ho = X_ho.fillna(med)
    y_tr = tr["hit"].astype(int)

    try:
        if kind == "logit":
            from sklearn.linear_model import LogisticRegression
            from sklearn.preprocessing import StandardScaler
            from sklearn.pipeline import make_pipeline
            m = make_pipeline(StandardScaler(),
                              LogisticRegression(max_iter=2000))
        else:
            from sklearn.ensemble import HistGradientBoostingClassifier
            m = HistGradientBoostingClassifier(
                max_depth=3, max_iter=200, learning_rate=0.05,
                min_samples_leaf=50, random_state=0)
        m.fit(X_tr, y_tr)
        p = m.predict_proba(X_ho)[:, 1]
    except Exception as e:
        print(f"      ({kind} unavailable: {e})")
        return None
    c = pd.Series(p).corr(ho["hit"].astype(float).reset_index(drop=True))
    return float(c) if pd.notna(c) else None


def main():
    argv = sys.argv[1:]
    horizons = [5, 10, 15, 21]
    if "--forward" in argv:
        horizons = [int(argv[argv.index("--forward") + 1])]
    symbols = sorted(f[:-4] for f in os.listdir(B.PRICE_DIR) if f.endswith(".csv"))
    if "--quick" in argv:
        symbols = symbols[:150]

    print(f"S/R model sweep — {len(symbols)} symbols, horizons {horizons}")
    print("Pre-registered: adopt only if delta >= +0.02 AND majority of horizons.\n")

    results = {}     # variant -> {horizon: corr}
    for W in horizons:
        print(f"=== collecting {W}d ===")
        df = collect(symbols, W)
        if df.empty:
            print("  no observations"); continue
        print(f"  n_obs={len(df)}  touch_rate={df['hit'].mean()*100:.1f}%")

        def rec(name, val):
            results.setdefault(name, {})[W] = val

        rec("BASELINE (dist x vol_close)", eval_buckets(df))

        # A. vol estimators
        for v in ["parkinson", "garman_klass", "rogers_satchell", "open_to_open"]:
            rec(f"A. vol={v}", eval_buckets(df, vol_col=f"vol_{v}"))

        # B. distance buckets
        rec("B. dist finer (9 buckets)", eval_buckets(
            df, dist_edges=[0, .01, .02, .03, .04, .06, .08, .12, .20, 1.0]))
        rec("B. dist coarser (4)", eval_buckets(
            df, dist_edges=[0, .03, .06, .12, 1.0]))
        rec("B. dist log-spaced (8)", eval_buckets(
            df, dist_edges=[0, .015, .025, .04, .06, .09, .14, .22, 1.0]))

        # C. vol buckets
        rec("C. vol finer (6)", eval_buckets(
            df, vol_edges=[0, 20, 27, 34, 42, 55, 1000]))
        rec("C. vol coarser (3)", eval_buckets(df, vol_edges=[0, 28, 42, 1000]))

        # D. min_cell
        for mc in (50, 100):
            rec(f"D. min_cell={mc}", eval_buckets(df, min_cell=mc))

        # H. ATR-normalised distance
        rec("H. ATR-normalised distance", eval_buckets(
            df, dist_col="atr_dist",
            dist_edges=[0, 0.5, 1.0, 1.5, 2.5, 4.0, 100.0]))

        # G. continuous models (E/F folded in as features)
        rec("G. logistic (dist+vol)", eval_model(df, ["dist", "vol_close"]))
        rec("G. logistic (+atr,trend,strength)", eval_model(
            df, ["dist", "vol_close", "atr_dist", "trend_toward", "strength"]))
        rec("G. GBM (dist+vol)", eval_model(df, ["dist", "vol_close"], "gbm"))
        rec("G. GBM (+atr,trend,strength)", eval_model(
            df, ["dist", "vol_close", "atr_dist", "trend_toward", "strength"], "gbm"))
        print()

    # ── report ────────────────────────────────────────────────────────
    base = results.get("BASELINE (dist x vol_close)", {})
    print("=" * 84)
    print(f"  {'variant':<38} " + " ".join(f"{w:>7}d" for w in horizons) + f" {'mean Δ':>9} {'wins':>6}")
    print("-" * 84)
    verdicts = []
    for name, byh in results.items():
        cells = " ".join(f"{byh.get(w):>8.4f}" if byh.get(w) is not None else f"{'—':>8}"
                         for w in horizons)
        if name.startswith("BASELINE"):
            print(f"  {name:<38} {cells} {'':>9} {'':>6}")
            continue
        deltas = [byh[w] - base[w] for w in horizons
                  if byh.get(w) is not None and base.get(w) is not None]
        if not deltas:
            continue
        md = float(np.mean(deltas)); wins = sum(1 for d in deltas if d > 0)
        adopt = md >= 0.02 and wins > len(deltas) / 2
        verdicts.append((name, md, wins, len(deltas), adopt))
        flag = "  <== ADOPT" if adopt else ""
        print(f"  {name:<38} {cells} {md:>+9.4f} {wins:>3}/{len(deltas)}{flag}")

    print("=" * 84)
    winners = [v for v in verdicts if v[4]]
    if winners:
        print("VARIANTS CLEARING THE PRE-REGISTERED BAR:")
        for n, md, w, t, _ in winners:
            print(f"  {n}: mean delta {md:+.4f}, wins {w}/{t}")
    else:
        print("NO variant cleared the bar (delta >= +0.02 AND majority of horizons).")
        best = max(verdicts, key=lambda v: v[1]) if verdicts else None
        if best:
            print(f"Best was {best[0]} at {best[1]:+.4f} — below the noise threshold; "
                  f"keep the current model.")


if __name__ == "__main__":
    main()
