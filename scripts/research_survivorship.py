"""
Survivorship-bias quantification.

price_data/ is built from TODAY'S Nifty200+500 membership, so every name
that delisted / merged / died between 2015 and 2026 is absent from the whole
backtest history. Momentum buys strength, and several of those names had
strong momentum on the way to zero — so the survivor-panel backtest both
inflates returns and amputates the left tail.

This study re-runs the SAME engine on two panels:
  A. survivor panel      (price_data/ only)
  B. extended panel      (price_data/ + price_data_delisted/ — the curated
                          heavyweight departure cohort from download_delisted.py)
and reports the delta. This is a LOWER BOUND on the bias: the cohort covers
only the major departures, not every small index dropout.

Engine differences vs backtest_portfolio.run_backtest (applied to BOTH
panels so the comparison is apples-to-apples):
  1. NO price_exit lookahead: production eligibility requires a valid price
     at i+HOLD (21 days in the future) — which silently excludes any name
     that stops trading mid-hold, i.e. exactly the delisting blow-ups.
     Here eligibility uses only information available at the rebalance date.
  2. Terminal exit handling: if a series ends mid-hold (suspension/delisting/
     merger), the position exits at the LAST TRADED price (fair for mergers,
     where price converges to swap value; optimistic for hard suspensions —
     see TERMINAL_HAIRCUT sensitivity).

Turnover for the liquidity gate: dead names carry a real Turnover column
from bhavcopy; survivors use Close x Volume as in production.

Run from scripts/:  python research_survivorship.py
"""
import os
import numpy as np
import pandas as pd

import strategy_config as sc
import backtest_portfolio as bp
from walk_forward import make_windows

DEAD_DIR = "../data/price_data_delisted/"
TERMINAL_HAIRCUTS = [0.0, 0.25]   # extra loss applied at a terminal (data-end) exit


def load_dead_panel(base_index):
    closes, turns = {}, {}
    if not os.path.isdir(DEAD_DIR):
        return closes, turns
    for f in sorted(os.listdir(DEAD_DIR)):
        if not f.endswith(".csv"):
            continue
        sym = f.replace(".csv", "")
        df = pd.read_csv(DEAD_DIR + f)
        df["Date"] = pd.to_datetime(df["Date"], errors="coerce")
        for c in ["Close", "Volume", "Turnover"]:
            if c in df.columns:
                df[c] = pd.to_numeric(df[c], errors="coerce")
        df = df.dropna(subset=["Date", "Close"]).sort_values("Date").set_index("Date")
        df = df[~df.index.duplicated(keep="last")]
        if len(df) < sc.LOOKBACK + 40:
            continue
        # drop zero-volume frozen tails (suspended tickers quoting flat)
        if "Volume" in df.columns:
            vol = df["Volume"].fillna(0)
            live = vol.rolling(20, min_periods=1).max() > 0
            df = df[live]
        closes[sym] = df["Close"]
        if "Turnover" in df.columns and df["Turnover"].notna().any():
            turns[sym] = df["Turnover"]
        else:
            turns[sym] = df["Close"] * df["Volume"]
    return closes, turns


def build_panels():
    matrix = bp.load_price_matrix()
    turnover = bp.load_turnover_matrix(matrix)
    dead_c, dead_t = load_dead_panel(matrix.index)
    dead_c = {s: v for s, v in dead_c.items() if s not in matrix.columns}
    ext_matrix = matrix.copy()
    ext_turn = turnover.copy()
    for s in dead_c:
        # same ffill(limit=5) survivors get in load_price_matrix; a terminated
        # series still goes NaN 5 days after its last trade, which is what
        # drives terminal exits.
        ext_matrix[s] = dead_c[s].reindex(matrix.index).ffill(limit=5)
        ext_turn[s] = dead_t[s].reindex(matrix.index).ffill(limit=5)
    print(f"dead cohort in panel: {len(dead_c)} names: {sorted(dead_c)}")
    return matrix, turnover, ext_matrix, ext_turn


def simulate_exit_no_lookahead(matrix, sym, i, entry, haircut):
    """Like bp.simulate_position_exit but handles a series that terminates
    mid-hold: exit at last traded price minus haircut."""
    n = len(matrix)
    col = matrix[sym]
    last_price, last_seen = entry, 0
    for off in range(1, sc.HOLD + 1):
        idx = i + off
        if idx >= n:
            break
        p = col.iloc[idx]
        if pd.isna(p):
            continue
        last_price, last_seen = p, off
        if p < entry * sc.CATASTROPHIC_STOP:
            return p / entry - 1
    final_idx = min(i + sc.HOLD, n - 1)
    fp = col.iloc[final_idx]
    if pd.isna(fp):
        # series terminated mid-hold -> delisting/suspension/merger exit
        return (last_price * (1 - haircut)) / entry - 1
    return fp / entry - 1


def run(matrix, index, turnover, breadth, sector_map, haircut, collect=None):
    dates = matrix.index
    capital = float(bp.INITIAL_CAPITAL)
    equity = []
    for i in range(sc.LOOKBACK + 21, len(dates) - sc.HOLD, sc.HOLD):
        date = dates[i]
        regime = bp.get_regime(index, date, breadth)
        gated = bp.liquid_symbols_at(turnover, i) & set(matrix.columns)
        scores, vols = {}, {}
        for sym in gated:
            col = matrix[sym]
            price_now = col.iloc[i]
            price_past = col.iloc[i - sc.LOOKBACK]
            price_3m = col.iloc[i - 63]
            if pd.isna(price_now) or pd.isna(price_past) or pd.isna(price_3m):
                continue
            if price_past == 0 or price_3m == 0:
                continue
            ret = price_now / price_past - 1
            ret3 = price_now / price_3m - 1
            if ret <= 0 or ret3 <= 0:
                continue
            ma50 = col.iloc[i - 50:i].mean()
            if pd.isna(ma50) or price_now < ma50:
                continue
            w = col.iloc[i - 63:i].pct_change(fill_method=None).dropna()
            if len(w) < 40:
                continue
            v = w.std()
            if v == 0 or np.isnan(v):
                continue
            scores[sym] = ret / v
            vols[sym] = v
        n = sc.REGIME_NAMES[regime]
        exp = sc.REGIME_EXPOSURE[regime]
        if len(scores) < n:
            equity.append(capital)
            continue
        top = bp.select_top_n_capped(scores, n, sector_map, sc.MAX_PER_SECTOR)
        if not top:
            equity.append(capital)
            continue
        inv = {s: 1.0 / vols[s] for s in top}
        tot = sum(inv.values())
        inv = {s: min(v / tot, sc.MAX_WEIGHT) * tot for s, v in inv.items()}
        tot = sum(inv.values())
        invested = capital * exp
        new_capital = capital - invested
        for s in top:
            r = simulate_exit_no_lookahead(matrix, s, i, matrix[s].iloc[i], haircut)
            r -= 2 * sc.COST
            new_capital += invested * (inv[s] / tot) * (1 + r)
            if collect is not None:
                collect.append({"date": date, "sym": s, "ret": r, "regime": regime})
        capital = new_capital
        equity.append(capital)
    return np.array(equity)


def main():
    survivor_m, survivor_t, ext_m, ext_t = build_panels()
    index = bp.load_index()
    sector_map = bp.load_sector_map()

    print("\n================ FULL HISTORY ================")
    print(f"{'panel':28s} {'CAGR':>7s} {'Sharpe':>7s} {'MaxDD':>7s}")
    results = {}
    for hc in TERMINAL_HAIRCUTS:
        for label, (m, t) in {
            "survivor": (survivor_m, survivor_t),
            "extended": (ext_m, ext_t),
        }.items():
            breadth = bp.compute_breadth_series(m)
            picks = [] if (label == "extended" and hc == 0.0) else None
            eq = run(m, index, t, breadth, sector_map, hc, collect=picks)
            p = bp.performance(eq)
            key = f"{label} (haircut {hc:.0%})"
            results[key] = p
            print(f"{key:28s} {p[1]:7.2%} {p[2]:7.2f} {p[3]:7.2%}")
            if picks is not None:
                dfp = pd.DataFrame(picks)
                dead = dfp[dfp["sym"].isin(
                    set(ext_m.columns) - set(survivor_m.columns))]
                os.makedirs("../data/_research/", exist_ok=True)
                dead.to_csv("../data/_research/survivorship_dead_picks.csv", index=False)
                print(f"    dead-cohort selections: {len(dead)} position-months, "
                      f"mean ret {dead['ret'].mean():+.2%}" if len(dead) else
                      "    dead-cohort selections: none")

    print("\n================ WALK-FORWARD (3y/6mo) ================")
    rows = []
    for (s, e) in make_windows(survivor_m, 3, 6):
        row = {"start": s.date()}
        ok = True
        dead_syms = set(ext_m.columns) - set(survivor_m.columns)
        for label, (m, t) in {"surv": (survivor_m, survivor_t),
                              "ext": (ext_m, ext_t)}.items():
            sub = m[(m.index >= s) & (m.index <= e)]
            # production 20%-missing filter for survivors; dead names are
            # legitimately mostly-NaN (they died) and must be exempt from it
            keep = [c for c in sub.columns
                    if c in dead_syms or sub[c].isna().mean() <= 0.20]
            sub = sub[keep]
            sub_t = t.reindex(sub.index)[sub.columns]
            breadth = bp.compute_breadth_series(sub)
            eq = run(sub, index, sub_t, breadth, sector_map, 0.0)
            p = bp.performance(eq)
            if p is None:
                ok = False
                break
            row[label + "_cagr"], row[label + "_sharpe"], row[label + "_dd"] = p[1], p[2], p[3]
        if ok:
            rows.append(row)
    wf = pd.DataFrame(rows)
    wf.to_csv("../data/_research/survivorship_wf.csv", index=False)
    d = wf["ext_cagr"] - wf["surv_cagr"]
    print(f"windows: {len(wf)}")
    print(f"survivor: mean CAGR {wf['surv_cagr'].mean():7.2%}  mean Sharpe {wf['surv_sharpe'].mean():5.2f}  mean DD {wf['surv_dd'].mean():6.2%}")
    print(f"extended: mean CAGR {wf['ext_cagr'].mean():7.2%}  mean Sharpe {wf['ext_sharpe'].mean():5.2f}  mean DD {wf['ext_dd'].mean():6.2%}")
    print(f"CAGR delta (ext - surv): mean {d.mean():+.2%}  min {d.min():+.2%}  "
          f"max {d.max():+.2%}  windows worse: {(d < 0).sum()}/{len(wf)}")


if __name__ == "__main__":
    main()
