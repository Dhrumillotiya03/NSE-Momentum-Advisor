"""
research_sr_batch.py  —  H2..H6 of PREREG_sr_improvement_batch.md
-----------------------------------------------------------------
Five conditioning hypotheses for the S/R P(touch)/hold model, run as ONE family
with a shared decision rule and a Holm-Bonferroni correction across all six
tests in the batch (H1 lives in research_sr_exit_side.py).

  H2  volume at the level      — does arrival on fading volume predict holding?
  H3  intraday realised vol    — finer vol axis than close-to-close?
  H4  level confluence         — do multi-method-confirmed levels hold better?
  H5  touch count / level age  — do repeatedly-tested levels hold or break?
  H6  regime conditioning      — do levels behave differently by market regime?

WHY THEY ARE RUN TOGETHER
-------------------------
Testing six ideas and reporting the winner is selection, not research. With six
tests at nominal p=0.05 the chance at least one looks significant by luck is
~26%. So the family is fixed in advance and corrected jointly. A test clearing
raw p<0.05 but failing Holm is reported as NOT SIGNIFICANT.

MEASUREMENT TARGET
------------------
For each candidate level, the outcome is HELD = "price touched the level and
did NOT close beyond it by the end of the horizon". That is the tradeable
question ("is this level meaningful") rather than bare P(touch), which the
production table already answers well.

Each hypothesis adds ONE conditioning feature and asks whether hold-rate
separates across its buckets by more than the pre-registered +5pp, out of
sample. A feature that merely correlates with distance or vol is NOT a finding
— those axes are already in the model — so every test reports the lift AFTER
stratifying on the existing (distance x vol) cell.

Usage:
    python research_sr_batch.py [--limit N] [--horizons 5,10,15,21]
"""
import os
import sys
import json
import itertools

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import research_tradeable_levels as R
from support_resistance import get_all_levels

OUT_DIR = "../data/_research/"
SPLIT_DATE = pd.Timestamp("2025-01-01")
TOUCH_ZONE = 0.01
MIN_CELL = 40

DIST_EDGES = [0.0, 0.02, 0.04, 0.06, 0.08, 0.12, 1.0]
DIST_LABELS = ["0-2%", "2-4%", "4-6%", "6-8%", "8-12%", "12%+"]


def dist_bucket(x):
    for i in range(len(DIST_LABELS)):
        if DIST_EDGES[i] <= x < DIST_EDGES[i + 1]:
            return DIST_LABELS[i]
    return DIST_LABELS[-1]


# ---------------------------------------------------------------- features
def intraday_rv(intra, asof, days=63):
    """Annualised realised vol from 15-min returns + the overnight gap.

    NOT the already-rejected Parkinson estimator: that summarised ONE bar per
    day and lost because it discarded overnight gaps. This samples ~25x finer
    AND keeps the gap as its own term, which is where a real share of NSE moves
    happen. (sr-vol-estimator-rejected-2026-08)
    """
    d = intra[intra["d"] <= asof]
    if len(d) < days * 20:
        return None
    d = d[d["d"] >= sorted(d["d"].unique())[-days]]
    if len(d) < 100:
        return None
    r = np.log(d["close"] / d["close"].shift()).dropna()
    # bars spanning the overnight break: keep them, they ARE the gap
    if len(r) < 100:
        return None
    bars_per_year = 25 * 252
    return float(r.std() * np.sqrt(bars_per_year) * 100)


def volume_at_level(intra, fwd_i, level, side, past_daily):
    """Volume in the touching bars relative to the 20d average bar volume.

    <1 = price arrived on fading participation (exhaustion hypothesis)
    >1 = price arrived on heavy participation (conviction hypothesis)
    """
    if side == "down":
        m = fwd_i["low"] <= level * (1 + TOUCH_ZONE)
    else:
        m = fwd_i["high"] >= level * (1 - TOUCH_ZONE)
    if not m.any():
        return None
    touch_vol = float(fwd_i.loc[m, "volume"].mean())
    base = float(past_daily["Volume"].tail(20).mean() / 25.0)  # per-bar
    if not base or not np.isfinite(base) or base <= 0:
        return None
    return touch_vol / base


def confluence(levels, level, tol=0.01):
    """How many OTHER detected levels sit within `tol` of this one.

    Uses the levels support_resistance already computes (swing pivots, volume
    nodes, 52w). Free test — nothing new is calculated.
    """
    if not levels:
        return 0
    return sum(1 for L in levels
               if L and np.isfinite(L) and abs(L / level - 1.0) <= tol) - 1


def touch_count(past, level, tol=0.01, lookback=252):
    """How many times price visited this level in the past window."""
    p = past.tail(lookback)
    if not len(p):
        return 0
    near = ((p["Low"] <= level * (1 + tol)) & (p["High"] >= level * (1 - tol)))
    return int(near.sum())


def held(fwd_d, fwd_i, level, side):
    """Touched AND not closed beyond by horizon end. None if never touched.

    None is EXCLUDED, not scored as a miss: this asks "given the level was
    reached, did it hold", which is only defined for levels that were reached.
    (The distinct P(touch) question is what the production table answers, and
    conflating the two is exactly the bug in sr-touch-vs-bounce-metric-bug —
    the difference here is that it is labelled correctly.)
    """
    if side == "down":
        touched = bool((fwd_i["low"] <= level * (1 + TOUCH_ZONE)).any())
        if not touched:
            return None
        return bool(float(fwd_d["Close"].iloc[-1]) > level)
    touched = bool((fwd_i["high"] >= level * (1 - TOUCH_ZONE)).any())
    if not touched:
        return None
    return bool(float(fwd_d["Close"].iloc[-1]) < level)


# ---------------------------------------------------------------- collection
def collect(symbols, horizons, regimes):
    rows = []
    for n, s in enumerate(symbols, 1):
        if n % 25 == 0:
            print(f"    [{n}/{len(symbols)}]", flush=True)
        intra = R.load_intraday(s)
        if intra is None:
            continue
        daily = R.daily_from_intraday(intra)
        if len(daily) < 300:
            continue

        for td in pd.date_range(end=daily.index[-1], periods=40, freq="ME"):
            past = daily[daily.index <= td]
            if len(past) < 280:
                continue
            cur = float(past["Close"].iloc[-1])
            if not cur or not np.isfinite(cur):
                continue
            vol_cc = R.realized_vol(past["Close"])
            if vol_cc is None:
                continue
            vol_id = intraday_rv(intra, td)

            try:
                sup_raw, res_raw = get_all_levels(past, fast=True)
            except Exception:
                continue
            # get_all_levels returns (price, strength, score) tuples, not floats
            def _prices(seq):
                out = []
                for item in (seq or []):
                    p = item[0] if isinstance(item, (list, tuple)) else item
                    p = float(p) if p is not None else None
                    if p and np.isfinite(p) and p > 0:
                        out.append(p)
                return out
            sup_all, res_all = _prices(sup_raw), _prices(res_raw)
            all_levels = list(sup_all) + list(res_all)
            if not all_levels:
                continue

            for H in horizons:
                fwd_d = daily[daily.index > td].head(H)
                if len(fwd_d) < H:
                    continue
                last_day = fwd_d.index[-1]
                fwd_i = intra[(intra["d"] > td) & (intra["d"] <= last_day)]
                if len(fwd_i) < H * 10:
                    continue

                for side, cands in [("down", sup_all[:3]), ("up", res_all[:3])]:
                    for lv in cands:
                        if side == "down" and lv >= cur:
                            continue
                        if side == "up" and lv <= cur:
                            continue
                        hv = held(fwd_d, fwd_i, lv, side)
                        if hv is None:
                            continue
                        rows.append({
                            "sym": s, "td": td, "H": H, "side": side,
                            "dist": abs(lv - cur) / cur,
                            "vol_cc": vol_cc, "vol_id": vol_id,
                            "volume_ratio": volume_at_level(intra, fwd_i, lv,
                                                            side, past),
                            "confluence": confluence(all_levels, lv),
                            "touches": touch_count(past, lv),
                            "regime": regimes.get(td.normalize(), "UNKNOWN"),
                            "held": hv,
                        })
    return pd.DataFrame(rows)


# ---------------------------------------------------------------- analysis
def stratified_lift(df, feat, n_bins=3):
    """Hold-rate spread across feature buckets, AFTER stratifying on the
    existing (distance x vol) cell.

    Stratification is the whole point: a feature that merely proxies distance
    or volatility adds nothing, because those axes are already in the model.
    Returns (lift_pp, n, detail) where lift is high-bucket minus low-bucket.
    """
    d = df.dropna(subset=[feat, "held"]).copy()
    if len(d) < MIN_CELL * 2:
        return None
    d["db"] = d["dist"].apply(dist_bucket)
    d["vb"] = d["vol_cc"].apply(R.vol_bucket)
    try:
        d["fb"] = pd.qcut(d[feat].rank(method="first"), n_bins,
                          labels=["low", "mid", "high"])
    except Exception:
        return None

    # within-cell deviation from the cell mean, then averaged across cells
    d["cell"] = d["db"] + "|" + d["vb"]
    cell_mean = d.groupby("cell")["held"].transform("mean")
    d["resid"] = d["held"].astype(float) - cell_mean
    g = d.groupby("fb", observed=True)["resid"].agg(["mean", "size"])
    if len(g) < 2 or g["size"].min() < MIN_CELL:
        return None
    lift = float((g.loc["high", "mean"] - g.loc["low", "mean"]) * 100)
    raw = d.groupby("fb", observed=True)["held"].mean() * 100
    return lift, len(d), raw.round(1).to_dict()


def perm_pvalue(df, feat, n_perm=1000, seed=0):
    """Permutation p-value for the stratified lift, shuffling BY DATE.

    Shuffling rows would break the same-date dependence structure and give an
    optimistically small p. Dates are the exchangeable unit here.
    """
    base = stratified_lift(df, feat)
    if base is None:
        return None, None
    obs = abs(base[0])
    rng = np.random.default_rng(seed)
    d = df.dropna(subset=[feat, "held"]).copy()
    dates = d["td"].unique()
    cnt = 0
    for _ in range(n_perm):
        mapping = dict(zip(dates, rng.permutation(dates)))
        d2 = d.copy()
        d2[feat] = d.groupby("td")[feat].transform(
            lambda s: s.sample(frac=1, random_state=rng.integers(1e6)).values)
        r = stratified_lift(d2, feat)
        if r is not None and abs(r[0]) >= obs:
            cnt += 1
    return base[0], (cnt + 1) / (n_perm + 1)


def main():
    argv = sys.argv[1:]
    horizons = [5, 10, 15, 21]
    if "--horizons" in argv:
        horizons = [int(x) for x in argv[argv.index("--horizons") + 1].split(",")]
    limit = None
    if "--limit" in argv:
        limit = int(argv[argv.index("--limit") + 1])
    n_perm = 400
    if "--perm" in argv:
        n_perm = int(argv[argv.index("--perm") + 1])

    os.makedirs(OUT_DIR, exist_ok=True)
    symbols = sorted(f[:-4] for f in os.listdir(R.INTRA_DIR) if f.endswith(".csv"))
    if limit:
        symbols = symbols[:limit]

    # regime series, computed once from the index (H6)
    regimes = {}
    try:
        import core
        idx = core.load_index()
        # load_index returns a SERIES of closes, not a frame
        close = idx if isinstance(idx, pd.Series) else idx["Close"]
        ma50 = close.rolling(50).mean()
        ma200 = close.rolling(200).mean()
        for dt_, c, a, b in zip(close.index, close, ma50, ma200):
            if not np.isfinite(a) or not np.isfinite(b):
                continue
            regimes[dt_.normalize()] = ("BULL" if c > a and a > b
                                        else "BEAR" if c < a and a < b
                                        else "SIDEWAYS")
    except Exception as e:
        print(f"  (regime unavailable: {str(e)[:60]})")

    print("=" * 76)
    print("S/R IMPROVEMENT BATCH — H2..H6")
    print("Protocol: PREREG_sr_improvement_batch.md (frozen before running)")
    print("=" * 76)

    df = collect(symbols, horizons, regimes)
    if df.empty:
        print("No observations."); return
    print(f"\nn={len(df)}  symbols={df['sym'].nunique()}  "
          f"dates={df['td'].nunique()}  hold_rate={df['held'].mean()*100:.1f}%")

    train = df[df["td"] < SPLIT_DATE]
    hold = df[df["td"] >= SPLIT_DATE]
    print(f"train={len(train)}  holdout={len(hold)}")
    df.to_csv(os.path.join(OUT_DIR, "sr_batch_raw.csv"), index=False)

    tests = [
        ("H2 volume-at-level", "volume_ratio"),
        ("H3 intraday RV", "vol_id"),
        ("H4 confluence", "confluence"),
        ("H5 touch count", "touches"),
    ]

    print("\n" + "=" * 76)
    print("HOLDOUT — stratified hold-rate lift (high vs low bucket)")
    print("  stratified on the EXISTING (distance x vol) cell, so a feature")
    print("  that merely proxies those two scores ~0 by construction")
    print("=" * 76)
    results = []
    for name, feat in tests:
        r = stratified_lift(hold, feat)
        if r is None:
            print(f"  {name:<22} insufficient data")
            continue
        lift, n, raw = r
        print(f"  {name:<22} lift {lift:+6.2f}pp   n={n:<6} raw={raw}")
        results.append((name, feat, lift, n))

    # H6 regime is categorical, reported separately
    print()
    if "regime" in hold.columns and hold["regime"].nunique() > 1:
        g = hold.groupby("regime")["held"].agg(["mean", "size"])
        g["mean"] = (g["mean"] * 100).round(1)
        spread = float(g["mean"].max() - g["mean"].min())
        print(f"  H6 regime              spread {spread:+6.2f}pp   "
              f"{g['mean'].to_dict()}")
        results.append(("H6 regime", "regime", spread, len(hold)))

    # ---- permutation p-values + Holm across the family ----
    print("\n" + "=" * 76)
    print("SIGNIFICANCE — permutation (shuffled BY DATE) + Holm-Bonferroni")
    print("  family = 6 tests (H1 in research_sr_exit_side.py)")
    print("=" * 76)
    pvals = []
    for name, feat, lift, n in results:
        if feat == "regime":
            pvals.append((name, lift, None))
            continue
        _, p = perm_pvalue(hold, feat, n_perm=n_perm)
        pvals.append((name, lift, p))

    scored = [(n, l, p) for n, l, p in pvals if p is not None]
    scored.sort(key=lambda x: x[2])
    m = 6
    print(f"  {'test':<22} {'lift':>8} {'p':>8} {'Holm thresh':>12} {'verdict':>10}")
    for i, (name, lift, p) in enumerate(scored):
        thresh = 0.05 / (m - i)
        sig = p <= thresh and abs(lift) >= 5.0
        print(f"  {name:<22} {lift:+7.2f}pp {p:>8.4f} {thresh:>12.4f} "
              f"{'SIGNIFICANT' if sig else 'not sig':>10}")
    for name, lift, p in pvals:
        if p is None:
            print(f"  {name:<22} {lift:+7.2f}pp {'—':>8} {'—':>12} "
                  f"{'descriptive':>10}")

    print("\n  PREREG bar: lift >= +5pp AND p <= Holm threshold.")
    print("  Anything failing either is NOT adopted, regardless of how it looks.")


if __name__ == "__main__":
    main()
