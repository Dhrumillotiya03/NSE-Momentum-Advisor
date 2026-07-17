"""
Parameter-robustness study: are the production parameters curve-fit to one
market cycle?  (2026-07-17, audit follow-up item 3.)

METHOD (pre-registered before looking at any results — see
memory statistical-hygiene-2026-07 for why the rules come first):

For each of the 5 core parameters, vary ONE at a time (all others at
production values) across a neighborhood grid, run the full-history
production engine (laggards_only, sleeves disabled, cash yield on), and
score each config on the FULL period plus 4 pre-named market-cycle eras:

    E1 2015-2017        small/midcap bull
    E2 2018-2020H1      the grind + COVID crash (the regime that hurts)
    E3 2020H2-2022      post-COVID melt-up + 2022 rate-shock chop
    E4 2023-2026        broad bull + 2025 correction

Grids (production value marked *):
    LOOKBACK  63  84  105  126*  147  189  252
    HOLD      10  15  21*  31  42
    MA_GATE   0(none)  20  50*  100  200
    VOL_WIN   42  63*  84  126
    BULL_N    6  8  10*  12  15

VERDICT RULES (fixed in advance):
  PLATEAU pass: production value's full-history Sharpe >= 0.85 x grid-best
    Sharpe AND its CAGR >= grid-best CAGR - 3pp.  (A spike — production best
    by a wide margin with sharp falloff at neighbors — is the curve-fit
    signature.)
  CYCLE pass: production value ranks in the top HALF of its grid by era
    Sharpe in >= 3 of the 4 eras.  (A value that only wins in one era was
    fit to that era.)
  Both pass  -> param considered robust at this diagnostic tier.
  Either fails -> SUSPECT: escalate that one param to a full 19-window
    walk-forward (walk_forward.py machinery) before touching anything.

ANTI-CURVE-FIT MANDATE: this study is DIAGNOSTIC ONLY.  Do NOT retune any
parameter to the grid winner off this table — selecting the best cell here
is exactly the curve-fitting this study exists to detect.  A config change
still requires the usual bar: causal mechanism + walk-forward distribution.

Implementation notes: the loop warmup is pinned to max(all grids)=252 bars
for every run, so every config (per HOLD value) rebalances on the SAME
calendar dates and eras are comparable.  The scorer is a parameterized
mirror of core.momentum_score (exclude-today convention) monkeypatched into
backtest_portfolio — production code keeps zero research knobs.
"""
import time

import numpy as np
import pandas as pd

import backtest_portfolio as bp
import strategy_config as sc

WARMUP = 252          # uniform loop warmup: max lookback/ma/vol across grids
OUT_CSV = "../data/_research/param_robustness_2026-07-17.csv"

ERAS = [
    ("E1_2015-17",   None,         "2018-01-01"),
    ("E2_2018-20H1", "2018-01-01", "2020-07-01"),
    ("E3_2020H2-22", "2020-07-01", "2023-01-01"),
    ("E4_2023-26",   "2023-01-01", None),
]

GRIDS = {
    "LOOKBACK": ([63, 84, 105, 126, 147, 189, 252], 126),
    "HOLD":     ([10, 15, 21, 31, 42], 21),
    "MA_GATE":  ([0, 20, 50, 100, 200], 50),
    "VOL_WIN":  ([42, 63, 84, 126], 63),
    "BULL_N":   ([6, 8, 10, 12, 15], 10),
}


def make_scorer(lookback, ma_win, vol_win):
    """Parameterized mirror of core.momentum_score (exclude-today convention).
    3m confirmation leg stays fixed at 63d (separate parameter, one variable
    at a time)."""
    need = max(lookback, ma_win, vol_win, 63) + 1
    min_obs = max(20, int(vol_win * 0.63))

    def scorer(close, skip_days=0):
        if len(close) < need:
            return None
        price_now = close.iloc[-1]
        price_past = close.iloc[-1 - lookback]
        if pd.isna(price_now) or pd.isna(price_past) or price_past == 0:
            return None
        ret_6m = price_now / price_past - 1
        price_3m = close.iloc[-64]
        if pd.isna(price_3m) or price_3m == 0:
            return None
        ret_3m = price_now / price_3m - 1
        if ret_6m <= 0 or ret_3m <= 0:
            return None
        if ma_win:
            ma = close.iloc[-(ma_win + 1):-1].mean()
            if pd.isna(ma) or price_now < ma:
                return None
        window = close.iloc[-(vol_win + 1):-1].pct_change(fill_method=None).dropna()
        if len(window) < min_obs:
            return None
        vol = window.std()
        if vol == 0 or np.isnan(vol):
            return None
        return {"score": ret_6m / vol, "ret_6m": ret_6m, "ret_3m": ret_3m,
                "vol_63": vol}

    return scorer


def era_metrics(equity, dates, hold):
    """Map equity points back to calendar dates, slice into eras."""
    n = len(dates)
    eq_dates = [dates[min(WARMUP + 21 + k * hold + hold, n - 1)]
                for k in range(len(equity))]
    eq = pd.Series(equity, index=pd.DatetimeIndex(eq_dates))
    out = {}
    for name, a, b in ERAS:
        seg = eq
        if a: seg = seg[seg.index >= a]
        if b: seg = seg[seg.index < b]
        if len(seg) < 5:
            out[name] = (np.nan, np.nan)
            continue
        years = (seg.index[-1] - seg.index[0]).days / 365.25
        ann = (seg.iloc[-1] / seg.iloc[0]) ** (1 / years) - 1
        rets = seg.values[1:] / seg.values[:-1] - 1
        volat = np.std(rets) * np.sqrt(252 / hold)
        out[name] = (ann, ann / volat if volat > 0 else np.nan)
    return out


def run_config(matrix, index, turnover, lookback=126, hold=21, ma=50,
               vol_win=63, bull_n=10):
    bp.momentum_score = make_scorer(lookback, ma, vol_win)
    bp.LOOKBACK = WARMUP          # uniform rebalance grid across configs
    bp.HOLD = hold
    sc.REGIME_NAMES["BULL"] = bull_n
    eq = bp.run_backtest_laggards_only(matrix, index, turnover)
    perf = bp.performance(eq)
    eras = era_metrics(eq, matrix.index, hold)
    return perf, eras


def main():
    import core
    orig_score, orig_lb, orig_hold = bp.momentum_score, bp.LOOKBACK, bp.HOLD
    orig_bull = sc.REGIME_NAMES["BULL"]

    matrix = bp.load_price_matrix()
    index = bp.load_index()
    turnover = bp.load_turnover_matrix(matrix)
    print(f"panel: {matrix.shape[1]} names x {len(matrix)} days; "
          f"uniform warmup {WARMUP}")

    rows = []
    for param, (grid, prod) in GRIDS.items():
        for val in grid:
            kw = dict(lookback=126, hold=21, ma=50, vol_win=63, bull_n=10)
            kw[{"LOOKBACK": "lookback", "HOLD": "hold", "MA_GATE": "ma",
                "VOL_WIN": "vol_win", "BULL_N": "bull_n"}[param]] = val
            t0 = time.time()
            perf, eras = run_config(matrix, index, turnover, **kw)
            _, ann, sharpe, dd, _, _ = perf
            row = {"param": param, "value": val, "is_prod": val == prod,
                   "cagr": ann, "sharpe": sharpe, "maxdd": dd}
            for e, (ea, es) in eras.items():
                row[f"{e}_cagr"], row[f"{e}_sharpe"] = ea, es
            rows.append(row)
            print(f"{param}={val}{'*' if val == prod else ' '}  "
                  f"CAGR {ann:+.1%}  Sharpe {sharpe:.2f}  DD {dd:.0%}  "
                  f"[{time.time()-t0:.0f}s]  " +
                  "  ".join(f"{e}:{es:.2f}" if es == es else f"{e}:--"
                            for e, (ea, es) in eras.items()))

    bp.momentum_score, bp.LOOKBACK, bp.HOLD = orig_score, orig_lb, orig_hold
    sc.REGIME_NAMES["BULL"] = orig_bull

    df = pd.DataFrame(rows)
    df.to_csv(OUT_CSV, index=False)
    print(f"\nsaved {OUT_CSV}")

    era_cols = [f"{e}_sharpe" for e, _, _ in ERAS]
    print("\n================ VERDICTS (rules pre-registered in docstring) "
          "================")
    for param, (grid, prod) in GRIDS.items():
        g = df[df.param == param].set_index("value")
        p = g.loc[prod]
        best = g.sharpe.max()
        plateau = (p.sharpe >= 0.85 * best) and (p.cagr >= g.cagr.max() - 0.03)
        wins = 0
        for c in era_cols:
            ranks = g[c].rank(ascending=False)
            if ranks.loc[prod] <= len(g) / 2:
                wins += 1
        cycle = wins >= 3
        verdict = "ROBUST" if (plateau and cycle) else "SUSPECT -> Tier-2 walk-forward"
        print(f"{param}={prod}: plateau {'PASS' if plateau else 'FAIL'} "
              f"(Sharpe {p.sharpe:.2f} vs grid best {best:.2f}), "
              f"cycle {'PASS' if cycle else 'FAIL'} ({wins}/4 eras top-half) "
              f"=> {verdict}")


if __name__ == "__main__":
    main()
