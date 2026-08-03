"""
research_sr_flow_features.py
----------------------------
Do INSTITUTIONAL-FLOW features improve the S/R P(touch) model?

MOTIVATION (user question, 2026-08-04): S/R levels come from price pivots, but
a stock's near-term path also depends on who is buying and selling it. If a
large holder is distributing, the downside level is arguably likelier to be
touched than swing points alone suggest. Price-only features cannot see that.

This is a DIFFERENT test from the delivery-% work already on file. That study
(memory delivery-pct-inconclusive) asked whether delivery % improves MOMENTUM
SCORING for stock selection, and came back inconclusive-to-negative. This asks
whether it improves the P(TOUCH) question — a path/range question over a
1-month horizon, not a cross-sectional ranking question. Different target,
different mechanism, so the earlier verdict does not automatically transfer.

FEATURES TESTED (all from NSE bhavcopy delivery data, the only institutional
-flow proxy available here — no paid ownership/FII-DII feed):
  deliv_level    — delivery % averaged over the last 20 sessions. High =
                   participants taking delivery (conviction) rather than
                   intraday churn.
  deliv_trend    — recent (5d) delivery vs its 60d baseline. Rising delivery
                   into a move is the accumulation/distribution signature.
  deliv_x_dir    — delivery trend SIGNED toward the level being tested.
                   Distribution should raise P(touch) for a level BELOW price
                   and lower it for one above; an unsigned feature cannot
                   express that and would average the two effects to nothing.
  deliv_vol_spike— delivery QUANTITY vs its 60d median. A genuine large-holder
                   exit shows up as an outsized delivery day, which is the
                   closest available proxy to the user's "major shareholder
                   sold" example.

WHAT THIS CANNOT TEST: actual fundamentals (earnings quality, valuation,
promoter pledging) and true ownership flow (FII/DII/bulk-block deals with
counterparty identity). None of that is in this repo, and delivery % is a
coarse proxy — it says shares changed hands for delivery, not WHO traded or
why. A negative result here is therefore evidence about THIS proxy, not proof
that ownership data is worthless.

PRE-REGISTERED RULE: adopt only if OOS corr improves >= +0.02 AND wins a
majority of horizons — the same bar every other S/R variant faced. Context:
the ceiling analysis (research_sr_ceiling.py) showed an oracle fitted on the
answers beats production by only +0.008 using price features, so a genuinely
NEW information source is the one thing that could plausibly clear this.

Usage:
    python research_sr_flow_features.py [--forward 21] [--quick]
"""
import os
import sys
import numpy as np
import pandas as pd

import sr_build_touchtable as B
import research_sr_model_sweep as S
from support_resistance import get_levels, load_stock, load_delivery


def delivery_features(deliv, asof):
    """Flow features computed from data STRICTLY up to `asof` (no lookahead)."""
    if deliv is None or len(deliv) == 0:
        return None
    d = deliv[deliv.index <= asof]
    if len(d) < 60:
        return None
    pct = pd.to_numeric(d["DelivPer"], errors="coerce").dropna()
    qty = pd.to_numeric(d["DelivQty"], errors="coerce").dropna()
    if len(pct) < 60 or len(qty) < 60:
        return None

    lvl = float(pct.tail(20).mean())
    base = float(pct.tail(60).mean())
    trend = lvl - base if np.isfinite(base) else 0.0

    med = float(qty.tail(60).median())
    spike = float(qty.tail(5).max() / med) if med > 0 else 1.0
    return {"deliv_level": lvl, "deliv_trend": trend,
            "deliv_vol_spike": spike}


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
        deliv = load_delivery(sym)
        if deliv is None or len(deliv) < 120:
            continue

        test_dates = pd.date_range(end=df.index[-1],
                                   periods=B.TEST_MONTHS + 1, freq="ME")[:-1]
        for td in test_dates:
            past = df[df.index <= td]
            future = df[df.index > td].head(forward_days)
            if len(past) < B.MIN_DATA // 2 or len(future) < forward_days:
                continue
            v = S.vol_close(past)
            if v is None:
                continue
            fl = delivery_features(deliv, td)
            if fl is None:
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
                rec = {
                    "td": td, "dist": abs(level - cur) / cur, "vol_close": v,
                    "hit": B.touched(future, level, direction),
                }
                rec.update(fl)
                # Signed toward the level: rising delivery (distribution) should
                # help a DOWNSIDE level and hurt an UPSIDE one.
                rec["deliv_x_dir"] = (-fl["deliv_trend"] if direction == "down"
                                      else fl["deliv_trend"])
                rows.append(rec)
    return pd.DataFrame(rows)


def eval_model(df, features, kind="logit"):
    tr, ho = S.split(df)
    if tr is None or not len(ho):
        return None
    X_tr = tr[features].astype(float).replace([np.inf, -np.inf], np.nan)
    X_ho = ho[features].astype(float).replace([np.inf, -np.inf], np.nan)
    med = X_tr.median()
    X_tr, X_ho = X_tr.fillna(med), X_ho.fillna(med)
    try:
        if kind == "logit":
            from sklearn.linear_model import LogisticRegression
            from sklearn.preprocessing import StandardScaler
            from sklearn.pipeline import make_pipeline
            m = make_pipeline(StandardScaler(), LogisticRegression(max_iter=2000))
        else:
            from sklearn.ensemble import HistGradientBoostingClassifier
            m = HistGradientBoostingClassifier(max_depth=3, max_iter=200,
                                               learning_rate=0.05,
                                               min_samples_leaf=50,
                                               random_state=0)
        m.fit(X_tr, tr["hit"].astype(int))
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

    print(f"Institutional-flow feature test — {len(symbols)} symbols, "
          f"horizons {horizons}")
    print("Rule: adopt only if delta >= +0.02 AND majority of horizons.\n")

    price_only = ["dist", "vol_close"]
    flow = ["deliv_level", "deliv_trend", "deliv_x_dir", "deliv_vol_spike"]

    results = {}
    for W in horizons:
        print(f"=== {W}d ===")
        df = collect(symbols, W)
        if df.empty:
            print("  no observations"); continue
        print(f"  n_obs={len(df)}  touch_rate={df['hit'].mean()*100:.1f}%")

        # Raw marginal signal: does any flow feature correlate with the outcome
        # at all, before modelling? If not, no model can extract it.
        print("  raw corr(feature, hit):")
        for f in flow:
            c = df[f].astype(float).corr(df["hit"].astype(float))
            print(f"    {f:<16} {c:+.4f}")

        base = eval_model(df, price_only)
        both = eval_model(df, price_only + flow)
        gbm_b = eval_model(df, price_only, "gbm")
        gbm_f = eval_model(df, price_only + flow, "gbm")
        results.setdefault("logit price-only", {})[W] = base
        results.setdefault("logit + flow", {})[W] = both
        results.setdefault("gbm price-only", {})[W] = gbm_b
        results.setdefault("gbm + flow", {})[W] = gbm_f
        print(f"  logit price-only {base:.4f} -> +flow {both:.4f} "
              f"({both-base:+.4f})")
        print(f"  gbm   price-only {gbm_b:.4f} -> +flow {gbm_f:.4f} "
              f"({gbm_f-gbm_b:+.4f})\n")

    if not results:
        return
    print("=" * 72)
    deltas_l, deltas_g = [], []
    for W in horizons:
        b = results["logit price-only"].get(W)
        f = results["logit + flow"].get(W)
        gb = results["gbm price-only"].get(W)
        gf = results["gbm + flow"].get(W)
        if b is not None and f is not None:
            deltas_l.append(f - b)
        if gb is not None and gf is not None:
            deltas_g.append(gf - gb)
    for name, ds in [("logistic", deltas_l), ("GBM", deltas_g)]:
        if not ds:
            continue
        md = float(np.mean(ds)); wins = sum(1 for d in ds if d > 0)
        adopt = md >= 0.02 and wins > len(ds) / 2
        print(f"  {name:<10} mean delta {md:+.4f}  wins {wins}/{len(ds)}  "
              f"{'ADOPT' if adopt else 'reject'}")
    print()
    print("Reminder: delivery % is a COARSE proxy for institutional flow — it")
    print("says shares were taken for delivery, not who traded or why. A null")
    print("result here is evidence about this proxy, NOT proof that real")
    print("ownership/fundamental data would be useless.")


if __name__ == "__main__":
    main()
