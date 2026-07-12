"""
Rank-blend failure autopsy.

Two auxiliary signals (delivery %, F&O PCR_OI) showed real-looking isolated
predictive power but LOST money when rank-blended into top-N entry selection
(see memories delivery-pct-inconclusive / oi-pcr-inconclusive). Before any
signal #3, this script attributes WHY, with three diagnostics:

  A. TAIL DECOMPOSITION of the baseline: how concentrated is portfolio P&L in
     a few extreme position-months, and does forward return decay with entry
     score rank? (If the top picks carry the book, demoting them is expensive.)
  B. SWAP ATTRIBUTION: at each rebalance, compare the baseline top-N with the
     blended top-N. Measure the realized forward return of exactly the names
     the blend swapped OUT vs swapped IN — split by whether the swapped-out
     name was a top-3 scorer. This directly prices the blend's interference.
  C. MARGINAL-ZONE TEST: does the aux signal rank-correlate with forward
     returns among near-cutoff names (where a tiebreaker would act) vs among
     top-3 names (where a blend does its damage)?

Run from scripts/:  python research_blend_autopsy.py
Writes per-position records to ../data/_research/blend_autopsy_records.csv
"""
import os
import numpy as np
import pandas as pd

import strategy_config as sc
import backtest_portfolio as bp

DELIV_DIR = "../data/delivery_data/"
FO_DIR    = "../data/fo_data/"
OUT_DIR   = "../data/_research/"

BLEND_WEIGHTS = [0.15, 0.30]


def build_aux_matrix(dirpath, col, matrix, smooth=None):
    frames = {}
    for sym in matrix.columns:
        p = os.path.join(dirpath, sym + ".csv")
        if not os.path.exists(p):
            continue
        try:
            df = pd.read_csv(p, low_memory=False)
        except Exception:
            continue
        if col not in df.columns:
            continue
        df["Date"] = pd.to_datetime(df["Date"], errors="coerce")
        df[col] = pd.to_numeric(df[col], errors="coerce")
        df = df.dropna(subset=["Date", col]).sort_values("Date")
        s = df.set_index("Date")[col]
        s = s[~s.index.duplicated(keep="last")]
        if len(s) > 20:
            frames[sym] = s
    aux = pd.DataFrame(frames).reindex(matrix.index).ffill(limit=7)
    if smooth:
        aux = aux.rolling(smooth, min_periods=3).median()
    return aux


def eligible_scores_at(matrix, i, gated_symbols):
    """Verbatim scoring block from backtest_portfolio.run_backtest."""
    scores, vols = {}, {}
    for sym in gated_symbols:
        price_now  = matrix[sym].iloc[i]
        price_past = matrix[sym].iloc[i - sc.LOOKBACK]
        price_exit = matrix[sym].iloc[i + sc.HOLD]
        if pd.isna(price_now) or pd.isna(price_past) or pd.isna(price_exit):
            continue
        if price_past == 0:
            continue
        ret = price_now / price_past - 1
        price_3m = matrix[sym].iloc[i - 63]
        if pd.isna(price_3m) or price_3m == 0:
            continue
        ret_3m = price_now / price_3m - 1
        if ret <= 0 or ret_3m <= 0:
            continue
        ma50 = matrix[sym].iloc[i - 50:i].mean()
        if pd.isna(ma50) or price_now < ma50:
            continue
        window = matrix[sym].iloc[i - 63:i].pct_change(fill_method=None).dropna()
        if len(window) < 40:
            continue
        vol = window.std()
        if vol == 0 or np.isnan(vol):
            continue
        scores[sym] = ret / vol
        vols[sym]   = vol
    return scores, vols


def pct_rank(d):
    """dict -> dict of percentile ranks in (0,1], higher value = higher rank."""
    s = pd.Series(d).rank(pct=True)
    return s.to_dict()


def main():
    os.makedirs(OUT_DIR, exist_ok=True)

    matrix = bp.load_price_matrix()
    index  = bp.load_index()
    turnover = bp.load_turnover_matrix(matrix)
    breadth  = bp.compute_breadth_series(matrix)
    sector_map = bp.load_sector_map()

    print("Building aux matrices (delivery 10d-median, PCR_OI)...")
    deliv = build_aux_matrix(DELIV_DIR, "DelivPer", matrix, smooth=10)
    pcr   = build_aux_matrix(FO_DIR, "PCR_OI", matrix)
    aux_matrices = {"delivery": deliv, "pcr": pcr}
    print(f"  delivery coverage: {deliv.shape[1]} syms | pcr coverage: {pcr.shape[1]} syms")

    dates = matrix.index
    fwd_cache = {}

    def fwd_ret(sym, i):
        key = (sym, i)
        if key not in fwd_cache:
            entry = matrix[sym].iloc[i]
            fwd_cache[key] = bp.simulate_position_exit(matrix, sym, i, entry, sc.HOLD) - 2 * sc.COST
        return fwd_cache[key]

    pos_records = []     # Part A
    swap_records = []    # Part B
    zone_records = []    # Part C

    n_reb = 0
    for i in range(sc.LOOKBACK + 21, len(dates) - sc.HOLD, sc.HOLD):
        date = dates[i]
        regime = bp.get_regime(index, date, breadth)
        gated = bp.liquid_symbols_at(turnover, i) & set(matrix.columns)
        scores, vols = eligible_scores_at(matrix, i, gated)
        n = sc.REGIME_NAMES[regime]
        if len(scores) < n:
            continue
        n_reb += 1

        ranked = sorted(scores, key=scores.get, reverse=True)
        score_rank = {s: k + 1 for k, s in enumerate(ranked)}
        base = bp.select_top_n_capped(scores, n, sector_map, sc.MAX_PER_SECTOR)

        # ---- A: per-position records with inverse-vol weights ----
        inv = {s: 1.0 / vols[s] for s in base}
        tot = sum(inv.values())
        w = {s: min(inv[s] / tot, sc.MAX_WEIGHT) for s in base}
        tot2 = sum(w.values())
        for k, s in enumerate(base):
            pos_records.append({
                "date": date, "regime": regime, "sym": s,
                "pick_order": k + 1, "score_rank": score_rank[s],
                "weight": w[s] / tot2, "fwd": fwd_ret(s, i),
            })

        # ---- B: swap attribution per aux per weight ----
        score_pct = pct_rank(scores)
        for aux_name, aux_m in aux_matrices.items():
            row = aux_m.iloc[i]
            aux_vals = {s: row.get(s, np.nan) for s in scores}
            have = {s: v for s, v in aux_vals.items() if not pd.isna(v)}
            if len(have) < max(10, len(scores) // 4):
                continue
            aux_pct = pct_rank(have)
            for s in scores:
                aux_pct.setdefault(s, 0.5)   # neutral for missing aux
            for wgt in BLEND_WEIGHTS:
                comp = {s: (1 - wgt) * score_pct[s] + wgt * aux_pct[s] for s in scores}
                blend = bp.select_top_n_capped(comp, n, sector_map, sc.MAX_PER_SECTOR)
                outs = sorted(set(base) - set(blend))
                ins  = sorted(set(blend) - set(base))
                for s in outs:
                    swap_records.append({
                        "date": date, "aux": aux_name, "w": wgt, "side": "out",
                        "sym": s, "score_rank": score_rank[s], "fwd": fwd_ret(s, i),
                    })
                for s in ins:
                    swap_records.append({
                        "date": date, "aux": aux_name, "w": wgt, "side": "in",
                        "sym": s, "score_rank": score_rank[s], "fwd": fwd_ret(s, i),
                    })

        # ---- C: zone-wise aux predictiveness (pooled later) ----
        for aux_name, aux_m in aux_matrices.items():
            row = aux_m.iloc[i]
            zone = ranked[:n + 8]
            for s in zone:
                v = row.get(s, np.nan)
                if pd.isna(v):
                    continue
                zone_records.append({
                    "date": date, "aux": aux_name, "sym": s,
                    "score_rank": score_rank[s], "n_req": n,
                    "aux_val": v, "fwd": fwd_ret(s, i),
                })

    pos = pd.DataFrame(pos_records)
    swaps = pd.DataFrame(swap_records)
    zone = pd.DataFrame(zone_records)
    pos.to_csv(OUT_DIR + "blend_autopsy_records.csv", index=False)

    # ================= REPORT =================
    print(f"\n{'='*66}\nA. TAIL DECOMPOSITION — baseline, {n_reb} rebalances, "
          f"{len(pos)} position-months\n{'='*66}")
    r = pos["fwd"]
    print(f"position fwd returns: mean {r.mean():+.2%}  median {r.median():+.2%}  "
          f"skew {r.skew():.2f}")
    contrib = pos["weight"] * pos["fwd"]
    pos_sorted = contrib.sort_values(ascending=False)
    total_pos = pos_sorted[pos_sorted > 0].sum()
    k5 = max(1, int(len(pos_sorted) * 0.05))
    k10 = max(1, int(len(pos_sorted) * 0.10))
    print(f"top 5% of position-months = {pos_sorted.head(k5).sum() / total_pos:.1%} "
          f"of all positive P&L; top 10% = {pos_sorted.head(k10).sum() / total_pos:.1%}")
    net = contrib.sum()
    print(f"net sum of weighted contributions: {net:+.3f} "
          f"(top 5% alone: {pos_sorted.head(k5).sum():+.3f})")
    print("\nmean fwd return by SCORE RANK at entry (pooled):")
    pos["rank_bucket"] = pd.cut(pos["score_rank"], [0, 1, 2, 3, 5, 10, 999],
                                labels=["1", "2", "3", "4-5", "6-10", "11+"])
    print(pos.groupby("rank_bucket", observed=True)["fwd"]
             .agg(["mean", "median", "count"]).to_string(
                 formatters={"mean": "{:+.2%}".format, "median": "{:+.2%}".format}))

    print(f"\n{'='*66}\nB. SWAP ATTRIBUTION — what the blend actually traded away\n{'='*66}")
    for aux_name in swaps["aux"].unique():
        for wgt in BLEND_WEIGHTS:
            sub = swaps[(swaps["aux"] == aux_name) & (swaps["w"] == wgt)]
            if sub.empty:
                continue
            o = sub[sub["side"] == "out"]
            i_ = sub[sub["side"] == "in"]
            print(f"\n[{aux_name} w={wgt}]  swaps: {len(o)} out / {len(i_)} in "
                  f"across {sub['date'].nunique()} rebalances")
            print(f"  swapped-OUT fwd: mean {o['fwd'].mean():+.2%} median {o['fwd'].median():+.2%}")
            print(f"  swapped-IN  fwd: mean {i_['fwd'].mean():+.2%} median {i_['fwd'].median():+.2%}")
            print(f"  per-swap delta (in - out): {i_['fwd'].mean() - o['fwd'].mean():+.2%}")
            top3 = o[o["score_rank"] <= 3]
            rest = o[o["score_rank"] > 3]
            if len(top3):
                print(f"  swapped-out TOP-3 scorers: n={len(top3)}, "
                      f"their fwd mean {top3['fwd'].mean():+.2%}")
            if len(rest):
                print(f"  swapped-out rank>3:        n={len(rest)}, "
                      f"their fwd mean {rest['fwd'].mean():+.2%}")

    print(f"\n{'='*66}\nC. MARGINAL-ZONE PREDICTIVENESS — where does the aux work?\n{'='*66}")
    for aux_name in zone["aux"].unique():
        sub = zone[zone["aux"] == aux_name].copy()
        top = sub[sub["score_rank"] <= 3]
        marg = sub[sub["score_rank"] > 3]

        def pooled_spearman(df):
            # de-mean fwd within each rebalance date to kill common regime moves
            if len(df) < 50:
                return np.nan, len(df)
            d = df.copy()
            d["fwd_dm"] = d["fwd"] - d.groupby("date")["fwd"].transform("mean")
            d["aux_dm"] = d.groupby("date")["aux_val"].rank(pct=True)
            return d["aux_dm"].corr(d["fwd_dm"], method="spearman"), len(d)

        c_top, n_top = pooled_spearman(top)
        c_marg, n_marg = pooled_spearman(marg)
        print(f"\n[{aux_name}] pooled within-rebalance spearman(aux, fwd):")
        print(f"  among TOP-3 scorers:      rho={c_top:+.3f}  (n={n_top})")
        print(f"  among marginal (rank>3):  rho={c_marg:+.3f}  (n={n_marg})")


if __name__ == "__main__":
    main()
