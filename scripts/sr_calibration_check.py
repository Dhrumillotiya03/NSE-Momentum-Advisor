"""
Retroactive probability calibration for reach_probability().

Compares the CURRENT (overlapping-trials) implementation against a FIXED
implementation that collapses overlapping trials into independent episodes
(spaced > forward_days apart) so 'n' reflects real sample size, not
pseudo-replicated consecutive days.

For each walk-forward test date (same grid as sr_backtest.py) we get S1/R1
level + predicted prob, then check whether the level was actually touched+held
in the following 21d window. No lookahead: predictions see only 'past' data.
"""
import os, sys
import numpy as np
import pandas as pd
from support_resistance import get_levels, load_stock
from sr_backtest import test_support, test_resistance, FORWARD_DAYS, MIN_DATA, TEST_MONTHS, PRICE_DIR


def reach_prob_current(df, level, direction, forward_days=21, lookback_days=504):
    """Verbatim copy of the live reach_probability (overlapping trials)."""
    closes = df["Close"].values; highs = df["High"].values; lows = df["Low"].values
    n = len(closes); cur = closes[-1]
    dist_pct = (cur - level) / cur if direction == "down" else (level - cur) / cur
    if dist_pct <= 0: return None, 0
    tolerance = max(dist_pct * 0.35, 0.008)
    start = max(0, n - lookback_days); end = n - forward_days - 1
    hits = trials = 0
    for i in range(start, end):
        ref = closes[i]
        rd = (ref - level) / ref if direction == "down" else (level - ref) / ref
        if rd < 0 or abs(rd - dist_pct) > tolerance: continue
        trials += 1
        if direction == "down":
            if lows[i+1:i+1+forward_days].min() <= level * 1.005: hits += 1
        else:
            if highs[i+1:i+1+forward_days].max() >= level * 0.995: hits += 1
    if trials < 5: return None, trials
    return int(round(hits / trials * 100)), trials


def reach_prob_fixed(df, level, direction, forward_days=21, lookback_days=504,
                     min_episodes=4):
    """
    Collapse overlapping matches into independent episodes: once a matching
    bar i is counted, skip forward_days ahead so its outcome window can't
    overlap the next counted bar. Gate on independent EPISODES, not raw days.
    """
    closes = df["Close"].values; highs = df["High"].values; lows = df["Low"].values
    n = len(closes); cur = closes[-1]
    dist_pct = (cur - level) / cur if direction == "down" else (level - cur) / cur
    if dist_pct <= 0: return None, 0
    tolerance = max(dist_pct * 0.35, 0.008)
    start = max(0, n - lookback_days); end = n - forward_days - 1
    hits = trials = 0
    i = start
    while i < end:
        ref = closes[i]
        rd = (ref - level) / ref if direction == "down" else (level - ref) / ref
        if rd < 0 or abs(rd - dist_pct) > tolerance:
            i += 1; continue
        trials += 1
        if direction == "down":
            if lows[i+1:i+1+forward_days].min() <= level * 1.005: hits += 1
        else:
            if highs[i+1:i+1+forward_days].max() >= level * 0.995: hits += 1
        i += forward_days   # non-overlapping outcome windows
    if trials < min_episodes: return None, trials
    return int(round(hits / trials * 100)), trials


def run(symbols, prob_fn):
    records = []
    for sym in symbols:
        sym = sym.strip().upper()
        if not sym.endswith(".NS"): sym += ".NS"
        df = load_stock(sym)
        if df is None or len(df) < MIN_DATA: continue
        test_dates = pd.date_range(end=df.index[-1], periods=TEST_MONTHS + 1, freq="ME")[:-1]
        for td in test_dates:
            past = df[df.index <= td]
            future = df[df.index > td].head(FORWARD_DAYS)
            if len(past) < MIN_DATA // 2 or len(future) < 5: continue
            try:
                sup, res_lvl, _, _ = get_levels(past, fast=True)
                s_prob, s_n = prob_fn(past, sup, "down")
                r_prob, r_n = prob_fn(past, res_lvl, "up")
            except Exception:
                continue
            s_hit = test_support(future, sup)
            r_hit = test_resistance(future, res_lvl)
            if s_hit is not None and s_prob is not None:
                records.append(("S", s_prob, s_n, s_hit))
            if r_hit is not None and r_prob is not None:
                records.append(("R", r_prob, r_n, r_hit))
    return pd.DataFrame(records, columns=["Side", "Prob", "N", "Hit"])


def report(df, label):
    print(f"\n{'='*52}\n  {label}   (n={len(df)} predictions)\n{'='*52}")
    buckets = [(0, 50), (50, 60), (60, 70), (70, 80), (80, 90), (90, 101)]
    print(f"  {'Predicted range':<16} {'Actual':>8} {'n':>6}")
    print("  " + "-"*34)
    for lo, hi in buckets:
        sub = df[(df["Prob"] >= lo) & (df["Prob"] < hi)]
        if len(sub) == 0: continue
        actual = sub["Hit"].mean() * 100
        mid = (lo + hi - 1) / 2
        gap = actual - mid
        flag = " OVERCONF" if gap < -15 else (" UNDERCONF" if gap > 15 else "")
        print(f"  {lo}-{hi-1}%{'':<10} {actual:>7.1f}% {len(sub):>6}{flag}")
    if len(df) > 1:
        print(f"\n  Correlation(Prob, Hit) = {df['Prob'].corr(df['Hit'].astype(float)):.3f}")


if __name__ == "__main__":
    symbols = [f.replace(".csv", "") for f in os.listdir(PRICE_DIR) if f.endswith(".csv")]
    print(f"Calibration check on {len(symbols)} stocks (current vs fixed)...")
    cur_df = run(symbols, reach_prob_current)
    report(cur_df, "CURRENT (overlapping trials)")
    fix_df = run(symbols, reach_prob_fixed)
    report(fix_df, "FIXED (independent episodes, min=4)")
