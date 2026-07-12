"""
Non-price EXIT overlay diagnostic (Phase 1).

Question: conditional on HOLDING a momentum name (entered at a rebalance),
does mid-hold deterioration in a non-price signal (delivery %, PCR_OI,
futures OI) predict the REMAINDER-of-month return — beyond what the
position's own price move already says?

Why this design: early exits were tested twice with PRICE-based rules and
lost (whipsaw). The rank-blend autopsy killed entry-side aux mechanisms
(signals carry no info inside the selection zone). This is the one untested
mechanism: aux signals as early-warning on existing holdings. Crucially any
candidate must show INCREMENTAL power after controlling for the price move
itself, else it's just a noisy proxy for the already-rejected price exits.

Method: for every baseline position-month and checkpoints d in {5,10,15}
trading days into the hold (positions stopped out before d excluded):
  - aux delta from entry to day d (delivery 10d-median pp change; PCR_OI
    change; futures OI % change)
  - mid-hold price return r0d (the control)
  - remainder return: day d -> hold end
Report remainder by aux-delta tercile, pooled and DOUBLE-SORTED within
mid-hold-return terciles, with a first-half/second-half consistency split.

Run from scripts/:  python research_exit_overlay.py
"""
import os
import numpy as np
import pandas as pd

import strategy_config as sc
import backtest_portfolio as bp
from research_blend_autopsy import build_aux_matrix, eligible_scores_at

DELIV_DIR = "../data/delivery_data/"
FO_DIR    = "../data/fo_data/"
CHECKPOINTS = [5, 10, 15]


def stopped_before(matrix, sym, i, entry, d):
    for off in range(1, d + 1):
        p = matrix[sym].iloc[i + off]
        if not pd.isna(p) and p < entry * sc.CATASTROPHIC_STOP:
            return True
    return False


def main():
    matrix = bp.load_price_matrix()
    index  = bp.load_index()
    turnover = bp.load_turnover_matrix(matrix)
    breadth  = bp.compute_breadth_series(matrix)
    sector_map = bp.load_sector_map()

    print("Building aux matrices...")
    aux = {
        "deliv": build_aux_matrix(DELIV_DIR, "DelivPer", matrix, smooth=10),
        "pcr":   build_aux_matrix(FO_DIR, "PCR_OI", matrix),
        "futoi": build_aux_matrix(FO_DIR, "FutOI", matrix),
    }

    dates = matrix.index
    rows = []
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
            end_p = matrix[sym].iloc[min(i + sc.HOLD, len(dates) - 1)]
            if pd.isna(entry) or pd.isna(end_p):
                continue
            for d in CHECKPOINTS:
                if stopped_before(matrix, sym, i, entry, d):
                    continue
                p_d = matrix[sym].iloc[i + d]
                if pd.isna(p_d) or p_d == 0:
                    continue
                rec = {
                    "date": date, "sym": sym, "d": d, "regime": regime,
                    "r0d": p_d / entry - 1,
                    "rem": end_p / p_d - 1,
                }
                for name, m in aux.items():
                    v0, vd = m[sym].iloc[i] if sym in m.columns else np.nan, \
                             m[sym].iloc[i + d] if sym in m.columns else np.nan
                    if pd.isna(v0) or pd.isna(vd):
                        rec[name + "_delta"] = np.nan
                    elif name == "futoi":
                        rec[name + "_delta"] = vd / v0 - 1 if v0 else np.nan
                    else:
                        rec[name + "_delta"] = vd - v0
                rows.append(rec)

    df = pd.DataFrame(rows)
    os.makedirs("../data/_research/", exist_ok=True)
    df.to_csv("../data/_research/exit_overlay_records.csv", index=False)
    print(f"\n{len(df)} position-checkpoint observations "
          f"({df['date'].nunique()} rebalances)")

    med_date = df["date"].quantile(0.5)

    for sig in ["deliv_delta", "pcr_delta", "futoi_delta"]:
        sub = df.dropna(subset=[sig]).copy()
        if len(sub) < 200:
            print(f"\n[{sig}] insufficient data (n={len(sub)})")
            continue
        print(f"\n{'='*66}\n[{sig}]  n={len(sub)}\n{'='*66}")
        for d in CHECKPOINTS:
            s = sub[sub["d"] == d].copy()
            if len(s) < 100:
                continue
            s["rem_dm"] = s["rem"] - s.groupby("date")["rem"].transform("mean")
            s["ter"] = pd.qcut(s[sig], 3, labels=["low(deteriorating)", "mid", "high(improving)"])
            g = s.groupby("ter", observed=True)["rem_dm"].agg(["mean", "count"])
            spread = g["mean"].iloc[-1] - g["mean"].iloc[0]
            h1 = s[s["date"] <= med_date]
            h2 = s[s["date"] > med_date]

            def ter_spread(x):
                if len(x) < 60:
                    return np.nan
                t = pd.qcut(x[sig], 3, labels=False)
                return x.loc[t == 2, "rem_dm"].mean() - x.loc[t == 0, "rem_dm"].mean()

            print(f"\n  checkpoint d={d} (n={len(s)}): remainder (date-demeaned) by {sig} tercile")
            for ter, r in g.iterrows():
                print(f"    {ter:22s} mean {r['mean']:+.2%}  n={int(r['count'])}")
            print(f"    spread (improving - deteriorating): {spread:+.2%} | "
                  f"1st half {ter_spread(h1):+.2%} / 2nd half {ter_spread(h2):+.2%}")

            # ---- incremental beyond price: double sort within r0d terciles ----
            s["r0d_ter"] = pd.qcut(s["r0d"], 3, labels=False)
            inc = []
            for q in range(3):
                cell = s[s["r0d_ter"] == q]
                if len(cell) >= 60:
                    inc.append(ter_spread(cell))
            if inc:
                inc_str = " / ".join(f"{v:+.2%}" for v in inc)
                print(f"    within-price-tercile spreads (low/mid/high r0d): {inc_str}")


if __name__ == "__main__":
    main()
