"""
Corporate-announcement early-exit veto (the feature the user explicitly
requested 2026-07-12): "if a top-ranked momentum stock has a major negative
news event, exit early even if it's not month-end."

This is NOT a re-attempt of the rejected delivery%/OI exit-overlay tests —
those used slow-moving continuous signals (delivery drift, OI change) that
showed zero within-rebalance predictive power. This is a discrete EVENT
trigger on a small, high-confidence blacklist of NSE corporate-announcement
categories that are unambiguous bad news from the category alone (no PDF/
text parsing, no severity judgment call):

  BLACKLIST = {
    "News Verification"                         # NSE forcing the company
                                                  # to confirm/deny a
                                                  # negative rumor
    "Resignation", "Cessation"                   # director/KMP exit
    "Change in Auditors"
    "Corporate Insolvency Resolution Process"
    "Suspension of Trading"
    "Defaults on Payment of Interest/Principal"
  }
Deliberately EXCLUDED from v1: "Credit Rating" (downgrade vs affirm/upgrade
is only in the linked PDF — no text parsing in this pass, see memory).

POINT-IN-TIME DISCIPLINE: an announcement at timestamp T is only actionable
at the NEXT available close >= T. Same-day exit is only used if an_dt's
time-of-day is before NSE's 15:30 close AND the trading day still has a
recorded close (mirrors what would actually be achievable live via
live_quotes.py's ~15-min-delayed intraday quote, not the settled close).
Post-close announcements act at the NEXT trading day's close.

Method: for every baseline position-month (same top-N/sector-cap/inverse-
vol selection as backtest_portfolio.run_backtest), scan for a blacklisted
announcement during the hold. If found, compare:
  (a) TRIGGERED exit at the point-in-time actionable price
  (b) the ORIGINAL month-end (or -18% stop) outcome for the same position
Report the distribution of (a)-(b) — this is the value of ACTING vs not —
plus false-positive rate (how often the flagged event was a false alarm,
i.e. the position would have been fine or better anyway).

Run from scripts/:  python research_exit_announcements.py
"""
import os
import numpy as np
import pandas as pd

import strategy_config as sc
import backtest_portfolio as bp

ANN_DIR = "../data/announcements/"

BLACKLIST = {
    "News Verification",
    "Resignation",
    "Cessation",
    "Change in Auditors",
    "Corporate Insolvency Resolution Process",
    "Suspension of Trading",
    "Defaults on Payment of Interest/Principal",
}

MARKET_CLOSE_HOUR = 15  # 15:30 IST close; an_dt before this hour treated as same-day actionable


def load_announcements():
    """sym -> DataFrame(date[Timestamp, normalized to trading day], is_close_before)"""
    out = {}
    if not os.path.isdir(ANN_DIR):
        return out
    for f in os.listdir(ANN_DIR):
        if not f.endswith(".csv"):
            continue
        sym = f.replace(".csv", "")
        try:
            df = pd.read_csv(ANN_DIR + f)
        except Exception:
            continue
        if df.empty or "desc" not in df.columns:
            continue
        df = df[df["desc"].isin(BLACKLIST)].copy()
        if df.empty:
            continue
        dt = pd.to_datetime(df["date"], format="%d-%b-%Y %H:%M:%S", errors="coerce")
        df = df[dt.notna()]
        dt = dt[dt.notna()]
        df["trading_day"] = dt.dt.normalize()
        df["same_day_actionable"] = dt.dt.hour < MARKET_CLOSE_HOUR
        df["desc_"] = df["desc"]
        out[sym] = df[["trading_day", "same_day_actionable", "desc_"]].drop_duplicates()
    return out


def eligible_scores_at(matrix, i, gated_symbols):
    scores, vols = {}, {}
    for sym in gated_symbols:
        col = matrix[sym]
        price_now = col.iloc[i]
        price_past = col.iloc[i - sc.LOOKBACK]
        if pd.isna(price_now) or pd.isna(price_past) or price_past == 0:
            continue
        ret = price_now / price_past - 1
        price_3m = col.iloc[i - 63]
        if pd.isna(price_3m) or price_3m == 0:
            continue
        ret_3m = price_now / price_3m - 1
        if ret <= 0 or ret_3m <= 0:
            continue
        ma50 = col.iloc[i - 50:i].mean()
        if pd.isna(ma50) or price_now < ma50:
            continue
        window = col.iloc[i - 63:i].pct_change(fill_method=None).dropna()
        if len(window) < 40:
            continue
        vol = window.std()
        if vol == 0 or np.isnan(vol):
            continue
        scores[sym] = ret / vol
        vols[sym] = vol
    return scores, vols


def find_trigger(ann_df, dates, entry_idx, hold_days):
    """First blacklisted announcement during (entry_idx, entry_idx+hold_days].
    Returns the ACTIONABLE trading-day index (>= announcement's own trading
    day if same-day-actionable, else the next one), or None."""
    if ann_df is None or ann_df.empty:
        return None, None
    window_start = dates[entry_idx]
    window_end = dates[min(entry_idx + hold_days, len(dates) - 1)]
    hits = ann_df[(ann_df["trading_day"] > window_start) & (ann_df["trading_day"] <= window_end)]
    if hits.empty:
        return None, None
    hits = hits.sort_values("trading_day")
    first = hits.iloc[0]
    ann_day = first["trading_day"]
    # find position of ann_day (or the next trading day after it) in `dates`
    pos = dates.searchsorted(ann_day)
    if pos < len(dates) and dates[pos] == ann_day and first["same_day_actionable"]:
        action_idx = pos
    else:
        action_idx = pos if pos < len(dates) else None
        if action_idx is not None and dates[action_idx] == ann_day:
            action_idx += 1  # post-close: next day
    if action_idx is None or action_idx <= entry_idx or action_idx >= len(dates):
        return None, None
    return action_idx, first["desc_"]


def main():
    matrix = bp.load_price_matrix()
    index = bp.load_index()
    turnover = bp.load_turnover_matrix(matrix)
    breadth = bp.compute_breadth_series(matrix)
    sector_map = bp.load_sector_map()
    ann = load_announcements()
    print(f"loaded blacklisted announcements for {len(ann)} symbols "
          f"(scanning {sum(len(v) for v in ann.values())} events)")

    dates = matrix.index
    records = []

    for i in range(sc.LOOKBACK + 21, len(dates) - sc.HOLD, sc.HOLD):
        date = dates[i]
        regime = bp.get_regime(index, date, breadth)
        gated = bp.liquid_symbols_at(turnover, i) & set(matrix.columns)
        scores, vols = eligible_scores_at(matrix, i, gated)
        n = sc.REGIME_NAMES[regime]
        if len(scores) < n:
            continue
        top = bp.select_top_n_capped(scores, n, sector_map, sc.MAX_PER_SECTOR)

        for sym in top:
            entry = matrix[sym].iloc[i]
            if pd.isna(entry):
                continue
            original_ret = bp.simulate_position_exit(matrix, sym, i, entry, sc.HOLD) - 2 * sc.COST

            action_idx, desc = find_trigger(ann.get(sym), dates, i, sc.HOLD)
            if action_idx is None:
                continue  # no trigger fired for this position

            trig_price = matrix[sym].iloc[action_idx]
            if pd.isna(trig_price):
                continue
            # did the position ALREADY hit the -18% stop before the announcement
            # fired? if so the announcement is moot — the stop already acted.
            already_stopped = False
            for off in range(1, action_idx - i + 1):
                p = matrix[sym].iloc[i + off]
                if not pd.isna(p) and p < entry * sc.CATASTROPHIC_STOP:
                    already_stopped = True
                    break
            if already_stopped:
                continue

            triggered_ret = trig_price / entry - 1 - 2 * sc.COST
            records.append({
                "date": date, "sym": sym, "desc": desc, "regime": regime,
                "days_to_trigger": action_idx - i,
                "triggered_ret": triggered_ret, "original_ret": original_ret,
                "delta": triggered_ret - original_ret,
            })

    df = pd.DataFrame(records)
    os.makedirs("../data/_research/", exist_ok=True)
    df.to_csv("../data/_research/exit_announcements_records.csv", index=False)

    if df.empty:
        print("\nNo blacklisted-announcement triggers found on any held position. "
              "Either the blacklist never fires on this universe/period, or "
              "announcement coverage is too sparse (check download_announcements.py "
              "ran to completion).")
        return

    print(f"\n{len(df)} triggered position-events across {df['date'].nunique()} rebalances")
    print(f"\nby category:")
    print(df.groupby("desc")["delta"].agg(["count", "mean"]).sort_values("count", ascending=False)
             .to_string(formatters={"mean": "{:+.2%}".format}))

    print(f"\n{'='*66}\nOVERALL: acting on the trigger vs riding to month-end/stop\n{'='*66}")
    print(f"  mean delta (triggered - original): {df['delta'].mean():+.2%}")
    print(f"  median delta: {df['delta'].median():+.2%}")
    print(f"  win rate (triggering was BETTER): {(df['delta'] > 0).mean():.1%}")
    print(f"  mean original_ret (if you'd done nothing): {df['original_ret'].mean():+.2%}")
    print(f"  mean triggered_ret (if you exit on the event): {df['triggered_ret'].mean():+.2%}")

    print(f"\n  days-to-trigger distribution: "
          f"median {df['days_to_trigger'].median():.0f}, "
          f"mean {df['days_to_trigger'].mean():.1f} (of {sc.HOLD}-day hold)")

    # false-positive framing: how often would original_ret have been >= 0
    # anyway (i.e. the trigger was "acted on nothing really wrong")
    fp_rate = (df["original_ret"] >= 0).mean()
    print(f"\n  P(original position would have been non-negative anyway): {fp_rate:.1%}")
    print(f"  mean original_ret | original_ret < 0 (the cases the veto is FOR): "
          f"{df.loc[df['original_ret'] < 0, 'original_ret'].mean():+.2%}  (n={ (df['original_ret']<0).sum() })")

    # split by half for stability check (informal, low n makes formal
    # walk-forward windows unreliable here)
    med_date = df["date"].quantile(0.5)
    h1 = df[df["date"] <= med_date]
    h2 = df[df["date"] > med_date]
    print(f"\n  1st half (n={len(h1)}): mean delta {h1['delta'].mean():+.2%}")
    print(f"  2nd half (n={len(h2)}): mean delta {h2['delta'].mean():+.2%}")


if __name__ == "__main__":
    main()
