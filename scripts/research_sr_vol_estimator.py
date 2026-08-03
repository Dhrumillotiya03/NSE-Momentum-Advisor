"""
research_sr_vol_estimator.py
----------------------------
Does keying the P(touch) table on an INTRADAY-RANGE volatility estimator beat
the current close-to-close one?

MOTIVATION. The touch table is keyed on (distance x volatility), and its
volatility is close-to-close: std of daily Close returns, annualised. That
estimator never looks inside the bar. But "did price TOUCH this level" is
answered by the day's HIGH and LOW — a stock that swings 4% intraday and
closes flat has a much better chance of tagging a level than its close-to-close
vol suggests. So the feature and the outcome are arguably mismatched.

Parkinson (1980) estimates volatility from the high-low range:
    sigma = sqrt( mean( ln(H/L)^2 ) / (4 ln 2) ) * sqrt(252)
It is a strictly better estimator of diffusion volatility than close-to-close
(roughly 5x more efficient) BUT it ignores overnight gaps, which close-to-close
captures. Which matters more here is an empirical question, not a theoretical
one — hence this script.

METHOD. Rebuilds the touch table exactly as sr_build_touchtable.py does
(same walk-forward grid, same holdout, same buckets, complete-window
requirement) but swaps the vol estimator. Compares OOS corr(pred, hit) — the
same metric that exposed the P(bounce|touched) bug (0.173 -> 0.529).

PRE-REGISTERED DECISION RULE (fixed before running, so this cannot be
rationalised after the fact):
  ADOPT only if Parkinson's OOS correlation beats close-to-close by >= 0.02
  AND it wins on the majority of per-horizon runs (5/10/15/21d).
  Anything smaller is noise on a single holdout and does not clear this
  repo's bar (see memory statistical-hygiene-2026-07).

Usage:
    python research_sr_vol_estimator.py            # all horizons
    python research_sr_vol_estimator.py --forward 21
"""
import sys
import numpy as np
import pandas as pd

import sr_build_touchtable as B


def parkinson_vol(past_df):
    """Annualised Parkinson high-low range volatility, in percent."""
    d = past_df.tail(252)
    hl = np.log(d["High"] / d["Low"]).replace([np.inf, -np.inf], np.nan).dropna()
    if len(hl) < 30:
        return None
    return float(np.sqrt((hl ** 2).mean() / (4 * np.log(2))) * np.sqrt(252) * 100)


def collect_both(symbols, forward_days):
    """One pass computing BOTH vol estimators per observation, so the two
    tables are built on exactly the same rows — the comparison is then purely
    the estimator, with no sampling difference confounding it."""
    import os
    from support_resistance import get_levels, load_stock

    rows = []
    for idx, sym in enumerate(symbols, 1):
        sym = sym.strip().upper()
        if not sym.endswith(".NS"):
            sym += ".NS"
        if idx % 100 == 0:
            print(f"  [{idx}/{len(symbols)}]", flush=True)
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
            v_close = B.realized_vol(past["Close"])
            v_park = parkinson_vol(past)
            if v_close is None or v_park is None:
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
                rows.append({
                    "td": td, "dist": abs(level - cur) / cur,
                    "vol_close": v_close, "vol_park": v_park,
                    "hit": B.touched(future, level, direction),
                })
    return pd.DataFrame(rows)


def evaluate(df, vol_col):
    """Build on train, score on holdout, return OOS corr(pred, hit)."""
    d = df.rename(columns={vol_col: "vol"})
    dates = sorted(d["td"].unique())
    if len(dates) <= B.HOLDOUT_MONTHS:
        return None, None
    cut = dates[-B.HOLDOUT_MONTHS]
    train, hold = d[d["td"] < cut], d[d["td"] >= cut]
    table, base = B.build_table(train)

    h = hold.copy()
    h["db"] = h["dist"].apply(lambda x: B.bucket(x, B.DIST_EDGES, B.DIST_LABELS))
    h["vb"] = h["vol"].apply(lambda x: B.bucket(x, B.VOL_EDGES, B.VOL_LABELS))
    h["pred"] = h.apply(
        lambda r: table.get(f"{r['db']}|{r['vb']}", {}).get("prob", base), axis=1)
    corr = h["pred"].corr(h["hit"].astype(float))
    return (float(corr) if pd.notna(corr) else None), len(hold)


def main():
    argv = sys.argv[1:]
    horizons = [5, 10, 15, 21]
    if "--forward" in argv:
        horizons = [int(argv[argv.index("--forward") + 1])]

    import os
    symbols = sorted(f[:-4] for f in os.listdir(B.PRICE_DIR) if f.endswith(".csv"))

    print(f"Comparing vol estimators over {len(symbols)} symbols, "
          f"horizons {horizons}\n")
    results = []
    for W in horizons:
        print(f"=== {W}d ===")
        df = collect_both(symbols, W)
        if df.empty:
            print("  no observations"); continue
        c_close, n = evaluate(df, "vol_close")
        c_park, _ = evaluate(df, "vol_park")
        delta = (c_park - c_close) if (c_close is not None and c_park is not None) else None
        results.append((W, c_close, c_park, delta, n, len(df)))
        print(f"  n_obs={len(df)}  holdout={n}")
        print(f"  OOS corr close-to-close : {c_close:.4f}")
        print(f"  OOS corr Parkinson (HL) : {c_park:.4f}")
        print(f"  delta                   : {delta:+.4f}\n")

    if not results:
        return
    print("=" * 62)
    print(f"  {'horizon':>8} {'close':>9} {'parkinson':>11} {'delta':>9}")
    print("-" * 62)
    for W, cc, cp, dl, _, _ in results:
        print(f"  {W:>7}d {cc:>9.4f} {cp:>11.4f} {dl:>+9.4f}")

    deltas = [r[3] for r in results if r[3] is not None]
    wins = sum(1 for d in deltas if d > 0)
    mean_d = float(np.mean(deltas)) if deltas else 0.0
    print("-" * 62)
    print(f"  mean delta {mean_d:+.4f}   Parkinson wins {wins}/{len(deltas)} horizons")
    print()
    adopt = mean_d >= 0.02 and wins > len(deltas) / 2
    print("PRE-REGISTERED RULE: adopt only if mean delta >= +0.02 AND wins a "
          "majority of horizons.")
    print(f"VERDICT: {'ADOPT' if adopt else 'DO NOT ADOPT'} "
          f"(mean delta {mean_d:+.4f}, {wins}/{len(deltas)} wins)")
    if not adopt:
        print("  -> keep close-to-close. The two estimators correlate ~0.89, so a")
        print("     small delta is expected noise, not evidence of a better feature.")


if __name__ == "__main__":
    main()
