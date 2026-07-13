"""
Low-volatility sleeve study (consultant item #8 — single-factor fragility).

QUESTION: the production book is 100% one alpha (cross-sectional momentum).
Its documented failure mode (momentum crash in post-bear rebounds) is a
single point of failure. Can a second, PRICE-ONLY (honestly backtestable
with data we already have), low-correlation sleeve improve the PORTFOLIO
walk-forward distribution — not by finding a better signal, but by
diversifying across return-generating mechanisms?

CANDIDATE SLEEVES:
  1. LOW-VOL: same F&O-liquid universe gate, same 21-day laggards-only
     rebalance mechanics, same sector cap and -18% stop — but selection is
     the N LOWEST trailing-252d-volatility names instead of the highest
     momentum ones, always fully invested (no regime gating — the sleeve's
     entire purpose is to NOT share momentum's regime-timing failure mode).
     The low-vol anomaly is well documented and unusually strong in India.
  2. GOLD: buy-and-hold GOLDBEES (data/etf_data/), near-zero equity
     correlation.

METHOD (per statistical-hygiene-2026-07 — this must clear a higher bar
than a point-estimate delta):
  A. Low-vol sleeve standalone: full-history + correlation of 21d period
     returns vs the momentum engine and vs gold.
  B. Blend grid (monthly-rebalanced across sleeves, with an approximate
     inter-sleeve rebalancing cost charged): CAGR / Sharpe / MaxDD.
  C. Rolling 3y walk-forward windows on the blended return series vs
     100% momentum — count windows improved, not just the full-history point.
  D. Paired block-bootstrap (same machinery as research_statistical_hygiene)
     on best-blend-vs-baseline Sharpe and CAGR deltas.

REPORT ONLY — no production change. Run from scripts/:
    python research_lowvol_sleeve.py
"""
import numpy as np
import pandas as pd

import strategy_config as sc
import backtest_portfolio as bp

N_LOWVOL = 10          # names in the low-vol sleeve (matches BULL top-N)
VOL_WINDOW = 252       # trailing window for the vol ranking
MIN_OBS = 200          # min valid daily returns inside the window
GOLD_PATH = "../data/etf_data/GOLDBEES.NS.csv"

# blend grid: (momentum, low-vol, gold) weights
BLENDS = [
    (1.00, 0.00, 0.00),   # baseline — current production
    (0.80, 0.20, 0.00),
    (0.60, 0.40, 0.00),
    (0.85, 0.00, 0.15),
    (0.70, 0.15, 0.15),
    (0.60, 0.25, 0.15),
    (0.50, 0.35, 0.15),
]

N_BOOT = 2000
BLOCK_LEN = 6
SEED = 42


def rebalance_grid(matrix):
    return list(range(sc.LOOKBACK + 21, len(matrix) - sc.HOLD, sc.HOLD))


def run_lowvol_sleeve(matrix, index, turnover_matrix):
    """Laggards-only mechanics cloned from bp.run_backtest_laggards_only,
    with selection swapped to lowest trailing-252d vol, always fully
    invested (exposure 1.0, no regime gating by design)."""
    dates = matrix.index
    n_dates = len(dates)
    sector_map = bp.load_sector_map()

    capital = float(bp.INITIAL_CAPITAL)
    equity = []
    book = {}

    for i in rebalance_grid(matrix):
        gated = bp.liquid_symbols_at(turnover_matrix, i) & set(matrix.columns)

        scores, vols = {}, {}
        for sym in gated:
            col = matrix[sym]
            price_now = col.iloc[i]
            if pd.isna(price_now) or price_now <= 0:
                continue
            window = col.iloc[max(0, i - VOL_WINDOW):i].pct_change(fill_method=None).dropna()
            if len(window) < MIN_OBS:
                continue
            vol = window.std()
            if vol <= 1e-6 or np.isnan(vol):
                continue
            scores[sym] = 1.0 / vol   # higher score = lower vol
            vols[sym] = vol

        # mark existing book
        for s, pos in book.items():
            px = matrix[s].iloc[i]
            pos["last_price"] = px if not pd.isna(px) else pos["last_price"]
            pos["cur_value"] = pos["shares"] * pos["last_price"]
        book_value = sum(p["cur_value"] for p in book.values())

        if len(scores) < N_LOWVOL:
            equity.append(capital + book_value)
            continue

        top = set(bp.select_top_n_capped(scores, N_LOWVOL, sector_map, sc.MAX_PER_SECTOR))
        held = set(book)
        drop, keep, new_names = held - top, held & top, top - held

        for s in drop:
            pos = book.pop(s)
            capital += pos["cur_value"] * (1 - bp.COST)

        inv = {s: 1.0 / vols[s] for s in top}
        tot = sum(inv.values())
        w = {s: min(v / tot, sc.MAX_WEIGHT) * tot for s, v in inv.items()}
        tot2 = sum(w.values())
        w = {s: v / tot2 for s, v in w.items()}

        total_equity = capital + sum(book[s]["cur_value"] for s in keep)
        invested_target = total_equity * 1.0   # always fully invested

        for s in keep:
            pos = book[s]
            target_val = invested_target * w[s]
            delta = target_val - pos["cur_value"]
            capital -= delta + abs(delta) * bp.COST
            if delta > 0 and pos["last_price"] > 0:
                new_shares = pos["shares"] + delta / pos["last_price"]
                pos["entry_price"] = (pos["shares"] * pos["entry_price"] + delta) / new_shares
                pos["shares"] = new_shares
            elif pos["last_price"] > 0:
                pos["shares"] -= (-delta) / pos["last_price"]
            pos["cur_value"] = pos["shares"] * pos["last_price"]

        for s in new_names:
            target_val = invested_target * w[s]
            px = matrix[s].iloc[i]
            if pd.isna(px) or px <= 0:
                continue
            capital -= target_val * (1 + bp.COST)
            book[s] = {"entry_price": px, "shares": target_val / px,
                       "last_price": px, "cur_value": target_val}

        # hold simulation with -18% stop (same tail circuit breaker)
        for s in list(book):
            pos = book[s]
            col = matrix[s]
            entry_ref = pos["entry_price"]
            stopped = False
            for off in range(1, sc.HOLD + 1):
                idx = i + off
                if idx >= n_dates:
                    break
                p = col.iloc[idx]
                if pd.isna(p):
                    continue
                if p < entry_ref * sc.CATASTROPHIC_STOP:
                    capital += pos["shares"] * p * (1 - bp.COST)
                    del book[s]
                    stopped = True
                    break
            if not stopped:
                final_idx = min(i + sc.HOLD, n_dates - 1)
                fp = col.iloc[final_idx]
                if not pd.isna(fp):
                    pos["last_price"] = fp
                    pos["cur_value"] = pos["shares"] * fp

        equity.append(capital + sum(p["cur_value"] for p in book.values()))

    return np.array(equity)


def load_gold_period_returns(matrix):
    """GOLDBEES marked at each rebalance grid point + HOLD (matching where
    the engines' equity points sit), so gold period returns align 1:1 with
    the engines' equity[k+1]/equity[k] returns."""
    df = pd.read_csv(GOLD_PATH, parse_dates=["Date"], low_memory=False)
    df["Close"] = pd.to_numeric(df["Close"], errors="coerce")
    df = df[pd.to_datetime(df["Date"], errors="coerce").notna()]
    df = df.dropna(subset=["Close"]).sort_values("Date").set_index("Date")["Close"]
    gold = df.reindex(matrix.index).ffill()

    grid = rebalance_grid(matrix)
    marks = []
    for i in grid:
        idx = min(i + sc.HOLD, len(matrix) - 1)
        marks.append(gold.iloc[idx])
    marks = pd.Series(marks)
    if marks.isna().any():
        marks = marks.ffill().bfill()
    return (marks.values[1:] / marks.values[:-1]) - 1


def period_returns(equity):
    return equity[1:] / equity[:-1] - 1


def metrics(returns):
    q = 252 / sc.HOLD
    eq = np.cumprod(1 + returns)
    yrs = len(returns) / q
    cagr = eq[-1] ** (1 / yrs) - 1
    sharpe = returns.mean() / returns.std(ddof=1) * np.sqrt(q) if returns.std(ddof=1) > 0 else 0
    peak = np.maximum.accumulate(eq)
    dd = np.max((peak - eq) / peak)
    return cagr, sharpe, dd


def blend_returns(sleeves, weights):
    """Monthly-rebalanced blend with an approximate inter-sleeve
    rebalancing cost: each period the blend trades roughly
    sum_i w_i*|r_i - r_blend| of NAV to restore weights; charge COST on it."""
    r = sum(w * s for w, s in zip(weights, sleeves))
    turnover = sum(w * np.abs(s - r) for w, s in zip(weights, sleeves))
    return r - turnover * bp.COST


def rolling_windows(returns, window_periods=36, step=6):
    out = []
    for start in range(0, len(returns) - window_periods + 1, step):
        out.append(returns[start:start + window_periods])
    return out


def paired_bootstrap_delta(ret_a, ret_b, n_boot=N_BOOT, block_len=BLOCK_LEN, seed=SEED):
    q = 252 / sc.HOLD
    rng = np.random.default_rng(seed)
    n = len(ret_a)
    n_blocks = int(np.ceil(n / block_len))
    starts = np.arange(n - block_len + 1)
    sharpe_d = np.empty(n_boot)
    cagr_d = np.empty(n_boot)
    for b in range(n_boot):
        chosen = rng.choice(starts, size=n_blocks, replace=True)
        idx = np.concatenate([np.arange(s, s + block_len) for s in chosen])[:n]
        ra, rb = ret_a[idx], ret_b[idx]
        sa = ra.mean() / ra.std(ddof=1) * np.sqrt(q) if ra.std(ddof=1) > 0 else 0
        sb = rb.mean() / rb.std(ddof=1) * np.sqrt(q) if rb.std(ddof=1) > 0 else 0
        sharpe_d[b] = sb - sa
        yrs = len(ra) / q
        cagr_d[b] = (np.prod(1 + rb) ** (1 / yrs) - 1) - (np.prod(1 + ra) ** (1 / yrs) - 1)
    return sharpe_d, cagr_d


def main():
    matrix = bp.load_price_matrix()
    index = bp.load_index()
    turnover = bp.load_turnover_matrix(matrix)

    print(f"{'='*74}\nPART A — sleeves standalone + correlation structure\n{'='*74}")
    eq_mom = bp.run_backtest_laggards_only(matrix, index, turnover)
    eq_lv = run_lowvol_sleeve(matrix, index, turnover)
    r_mom = period_returns(eq_mom)
    r_lv = period_returns(eq_lv)
    r_gold = load_gold_period_returns(matrix)
    n = min(len(r_mom), len(r_lv), len(r_gold))
    r_mom, r_lv, r_gold = r_mom[:n], r_lv[:n], r_gold[:n]

    for name, r in [("momentum (production)", r_mom),
                    ("low-vol sleeve", r_lv),
                    ("gold (GOLDBEES b&h)", r_gold)]:
        c, s, d = metrics(r)
        print(f"  {name:24s} CAGR {c:7.2%}  Sharpe {s:5.2f}  MaxDD {d:6.2%}")

    corr = np.corrcoef(np.vstack([r_mom, r_lv, r_gold]))
    print(f"\n  21d-period return correlations:")
    print(f"    momentum vs low-vol: {corr[0,1]:+.2f}")
    print(f"    momentum vs gold:    {corr[0,2]:+.2f}")
    print(f"    low-vol  vs gold:    {corr[1,2]:+.2f}")

    print(f"\n{'='*74}\nPART B — blend grid (monthly-rebalanced, inter-sleeve rebal cost charged)\n{'='*74}")
    print(f"  {'mom':>5s} {'lowvol':>7s} {'gold':>5s}   {'CAGR':>8s} {'Sharpe':>7s} {'MaxDD':>8s}")
    sleeves = [r_mom, r_lv, r_gold]
    blend_series = {}
    for wts in BLENDS:
        r = blend_returns(sleeves, wts)
        blend_series[wts] = r
        c, s, d = metrics(r)
        print(f"  {wts[0]:5.0%} {wts[1]:7.0%} {wts[2]:5.0%}   {c:8.2%} {s:7.2f} {d:8.2%}")

    print(f"\n{'='*74}\nPART C — rolling 3y walk-forward windows vs 100% momentum baseline\n{'='*74}")
    base_windows = rolling_windows(blend_series[BLENDS[0]])
    print(f"  ({len(base_windows)} overlapping 3y windows, step 6 periods)")
    print(f"  {'blend':>22s} {'wfSharpe mean':>14s} {'wfCAGR mean':>12s} "
          f"{'Sharpe>base':>12s} {'DD<base':>8s}")
    for wts in BLENDS[1:]:
        wins = rolling_windows(blend_series[wts])
        sh_w, cg_w, sh_better, dd_better = [], [], 0, 0
        for wb, wx in zip(base_windows, wins):
            cb, sb, db = metrics(wb)
            cx, sx, dx = metrics(wx)
            sh_w.append(sx); cg_w.append(cx)
            sh_better += sx > sb
            dd_better += dx < db
        label = f"{wts[0]:.0%}/{wts[1]:.0%}/{wts[2]:.0%}"
        print(f"  {label:>22s} {np.mean(sh_w):14.2f} {np.mean(cg_w):12.2%} "
              f"{sh_better:>3d}/{len(base_windows):<3d}    {dd_better:>3d}/{len(base_windows)}")
    bsh, bcg = [], []
    for wb in base_windows:
        cb, sb, db = metrics(wb)
        bsh.append(sb); bcg.append(cb)
    print(f"  {'baseline 100/0/0':>22s} {np.mean(bsh):14.2f} {np.mean(bcg):12.2%}")

    print(f"\n{'='*74}\nPART D — paired block-bootstrap: each blend vs 100% momentum\n{'='*74}")
    for wts in BLENDS[1:]:
        sh_d, cg_d = paired_bootstrap_delta(blend_series[BLENDS[0]], blend_series[wts])
        label = f"{wts[0]:.0%}/{wts[1]:.0%}/{wts[2]:.0%}"
        sig_s = np.percentile(sh_d, 2.5) > 0 or np.percentile(sh_d, 97.5) < 0
        sig_c = np.percentile(cg_d, 2.5) > 0 or np.percentile(cg_d, 97.5) < 0
        print(f"  {label:>16s}  Sharpe delta {sh_d.mean():+.2f} "
              f"[{np.percentile(sh_d,2.5):+.2f},{np.percentile(sh_d,97.5):+.2f}] "
              f"P(better)={(sh_d>0).mean():4.0%} {'SIG' if sig_s else '   '} | "
              f"CAGR delta {cg_d.mean():+.2%} "
              f"[{np.percentile(cg_d,2.5):+.2%},{np.percentile(cg_d,97.5):+.2%}] "
              f"P={(cg_d>0).mean():4.0%} {'SIG' if sig_c else ''}")

    print(f"\nNOTES: blends imply monthly inter-sleeve rebalancing (charged above at")
    print(f"COST on approximate turnover) which also creates extra taxable events —")
    print(f"small since inter-sleeve drift per month is small, but nonzero. Gold sleeve")
    print(f"gains are taxed differently (non-equity ETF slab rules pre-2026 regime /")
    print(f"12.5% LTCG after 24m holding under current rules) — flag for the net-return")
    print(f"model if a gold blend is adopted.")


if __name__ == "__main__":
    main()
