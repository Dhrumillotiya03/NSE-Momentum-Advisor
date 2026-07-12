"""
India VIX exposure-overlay study.

Mechanism: modify only the EXPOSURE at each rebalance (never stock
selection — entry-side aux mechanisms are dead per the rank-blend autopsy).
All conditioning is point-in-time (trailing percentiles/medians of VIX up to
the rebalance date only).

Pre-registered variants (fixed before results were seen, to avoid
curve-fitting; no leverage — exposure capped at 1.0):
  V1 vol-target : exp *= clip(trailing-1y median VIX / VIX_t, 0.5, 1.0)
  V2 de-risk    : if VIX_t > trailing-3y 75th pctile -> exp *= 0.5
  V3 boost      : if VIX_t < trailing-3y 25th pctile -> exp = min(exp*1.25, 1.0)
  V4 combined   : V2 + V3

Evaluation: full-history run PLUS walk-forward distribution (3y windows,
6mo step) vs baseline — per-window CAGR/Sharpe win rates, not point
estimates.

Run from scripts/:  python research_vix_overlay.py
"""
import numpy as np
import pandas as pd

import backtest_portfolio as bp
from walk_forward import make_windows

VIX_PATH = "../data/index_data/indiavix.csv"


def load_vix():
    df = pd.read_csv(VIX_PATH)
    df["Date"] = pd.to_datetime(df["Date"], errors="coerce")
    df["Close"] = pd.to_numeric(df["Close"], errors="coerce")
    df = df.dropna(subset=["Date", "Close"]).sort_values("Date").set_index("Date")
    return df["Close"]


def make_exposure_fns(vix):
    """Each returns exposure_fn(date, regime, exp). All trailing/point-in-time."""
    med_1y = vix.rolling(252, min_periods=100).median()
    q75_3y = vix.rolling(756, min_periods=252).quantile(0.75)
    q25_3y = vix.rolling(756, min_periods=252).quantile(0.25)

    def at(series, date):
        past = series[series.index <= date]
        return past.iloc[-1] if len(past) else np.nan

    def v1(date, regime, exp):
        v, m = at(vix, date), at(med_1y, date)
        if np.isnan(v) or np.isnan(m) or v <= 0:
            return exp
        return exp * float(np.clip(m / v, 0.5, 1.0))

    def v2(date, regime, exp):
        v, q = at(vix, date), at(q75_3y, date)
        if np.isnan(v) or np.isnan(q):
            return exp
        return exp * 0.5 if v > q else exp

    def v3(date, regime, exp):
        v, q = at(vix, date), at(q25_3y, date)
        if np.isnan(v) or np.isnan(q):
            return exp
        return min(exp * 1.25, 1.0) if v < q else exp

    def v4(date, regime, exp):
        return v3(date, regime, v2(date, regime, exp))

    return {"V1_voltarget": v1, "V2_derisk": v2, "V3_boost": v3, "V4_combined": v4}


def main():
    matrix = bp.load_price_matrix()
    index = bp.load_index()
    turnover = bp.load_turnover_matrix(matrix)
    vix = load_vix()
    fns = make_exposure_fns(vix)

    # ---------- full history ----------
    print("Full-history runs...")
    results = {}
    eq = bp.run_backtest(matrix, index, turnover)
    results["baseline"] = bp.performance(eq)
    for name, fn in fns.items():
        eq = bp.run_backtest(matrix, index, turnover, exposure_fn=fn)
        results[name] = bp.performance(eq)

    print(f"\n{'variant':14s} {'CAGR':>8s} {'Sharpe':>7s} {'MaxDD':>7s}")
    for name, perf in results.items():
        _, cagr, sharpe, dd, _, _ = perf
        print(f"{name:14s} {cagr:8.2%} {sharpe:7.2f} {dd:7.2%}")

    # ---------- walk-forward ----------
    print("\nWalk-forward (3y windows, 6mo step)...")
    windows = make_windows(matrix, 3, 6)
    rows = []
    for (start, end) in windows:
        sub = matrix[(matrix.index >= start) & (matrix.index <= end)]
        sub = sub.loc[:, sub.isna().mean() <= 0.20]
        if len(sub) < 300:
            continue
        sub_turn = turnover.reindex(sub.index)[sub.columns]
        row = {"start": start.date(), "end": end.date()}
        eq = bp.run_backtest(sub, index, sub_turn)
        p = bp.performance(eq)
        if p is None:
            continue
        row["base_cagr"], row["base_sharpe"], row["base_dd"] = p[1], p[2], p[3]
        for name, fn in fns.items():
            eq = bp.run_backtest(sub, index, sub_turn, exposure_fn=fn)
            p = bp.performance(eq)
            row[name + "_cagr"], row[name + "_sharpe"], row[name + "_dd"] = p[1], p[2], p[3]
        rows.append(row)

    wf = pd.DataFrame(rows)
    wf.to_csv("../data/_research/vix_overlay_wf.csv", index=False)
    print(f"{len(wf)} windows\n")

    print(f"{'variant':14s} {'meanCAGR':>9s} {'medCAGR':>8s} {'meanShp':>8s} "
          f"{'meanDD':>7s} {'CAGRwin':>8s} {'Shpwin':>7s}")
    b = wf
    print(f"{'baseline':14s} {b['base_cagr'].mean():9.2%} {b['base_cagr'].median():8.2%} "
          f"{b['base_sharpe'].mean():8.2f} {b['base_dd'].mean():7.2%} {'—':>8s} {'—':>7s}")
    for name in fns:
        cagr_win = (wf[name + "_cagr"] > wf["base_cagr"]).mean()
        shp_win = (wf[name + "_sharpe"] > wf["base_sharpe"]).mean()
        print(f"{name:14s} {wf[name + '_cagr'].mean():9.2%} {wf[name + '_cagr'].median():8.2%} "
              f"{wf[name + '_sharpe'].mean():8.2f} {wf[name + '_dd'].mean():7.2%} "
              f"{cagr_win:8.0%} {shp_win:7.0%}")

    print("\nPer-window CAGR delta vs baseline:")
    for name in fns:
        d = wf[name + "_cagr"] - wf["base_cagr"]
        print(f"  {name:14s} mean {d.mean():+.2%}  min {d.min():+.2%}  max {d.max():+.2%}")


if __name__ == "__main__":
    main()
