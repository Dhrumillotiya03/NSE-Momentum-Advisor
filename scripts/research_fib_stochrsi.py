"""
research_fib_stochrsi.py — PREREG_fib_stochrsi.md, pre-registered 2026-08-31.

Tests whether chart_analysis.fib_retracement and chart_analysis.stoch_rsi
predict a FLOW CHANGE (price moves FLOW_THRESH% the favourable way before the
adverse way), against controls that isolate the SIGNAL from the TAPE — the
same method the August 2026 S/R review used on this system's own S1/R1 levels
and the same method Tsinaslanidis & Guijarro (2021) used against Fibonacci
zones in the published literature.

POINT-IN-TIME. At each monthly test date, only `past = df[df.index <= td]`
is fed to fib_retracement/stoch_rsi — the same convention as sr_backtest.py.
Capped to the trailing CONTEXT_BARS for performance; this does not change
"the last swing pivot" or "the current RSI window" in any way that matters
(a liquid F&O name always has a swing point within the past ~300 bars).

Usage:
    python research_fib_stochrsi.py             # full run, all 3 configs
    python research_fib_stochrsi.py --quick      # fewer test dates, for a smoke test
"""
import sys
import numpy as np
import pandas as pd

import core
import chart_analysis as ca
import strategy_config as sc

CONTEXT_BARS = 300     # trailing window fed to fib/stochrsi at each test date
FORWARD_DAYS = 21      # max bars available for the flow-change race
FLOW_THRESH = 2.0      # % move required to count as a flow change (matches ca.py)
STOCHRSI_HOLD = 5      # bars to judge a StochRSI cross's forward move
TOUCH_PCT = 0.01
MIN_HISTORY = CONTEXT_BARS + 60
COMBINED_WINDOW = 2    # sessions either side to count fib+stochrsi as "combined"

rng = np.random.default_rng(42)


def load_universe():
    syms = sorted(core.liquid_universe())
    print(f"Universe: {len(syms)} F&O-liquid names (today's snapshot — see "
          f"PREREG_fib_stochrsi.md for why a return-backtest point-in-time "
          f"universe isn't used for this signal test)")
    return syms


def test_dates(df):
    if len(df) < MIN_HISTORY:
        return []
    start = df.index[MIN_HISTORY]
    end = df.index[-1] - pd.Timedelta(days=45)   # leave room for a forward window
    if start >= end:
        return []
    return pd.date_range(start=start, end=end, freq="MS")


def _race(after, level, direction, thresh=FLOW_THRESH):
    r = after.iloc[1:]
    if len(r) < 2:
        return None
    for _, b in r.iterrows():
        if direction == "down":
            fav = float(b["High"]) >= level * (1 + thresh / 100)
            adv = float(b["Low"]) <= level * (1 - thresh / 100)
        else:
            fav = float(b["Low"]) <= level * (1 - thresh / 100)
            adv = float(b["High"]) >= level * (1 + thresh / 100)
        if fav or adv:
            return bool(fav and not adv)
    return False


def run_fib(df, sym, dates, pending_by_date):
    """Returns list of dicts: real touches + registers a control candidate
    (cmp, distance, direction, future window) for later cross-symbol
    permutation, keyed by test date."""
    out = []
    for td in dates:
        past = df[df.index <= td].tail(CONTEXT_BARS)
        if len(past) < 60:
            continue
        fib = ca.fib_retracement(past)
        if not fib.get("levels"):
            continue
        future = df[df.index > td].head(FORWARD_DAYS)
        if len(future) < 5:
            continue
        cmp_ = float(fib["price"])
        # TOUCH DIRECTION IS PER-LEVEL, not per-leg. A first version derived it
        # once from the leg's nominal direction and applied it to all 5 ratio
        # levels — wrong whenever price has ALREADY retraced past some of
        # them by the test date (verified live: a DOWN leg with price at
        # 212.32 had its 23.6/38.2/50/61.8% levels sitting at 206.72-211.78,
        # all BELOW spot, while only 78.6% at 214.0 was genuinely still above
        # it). Testing an already-passed level as if it were still ahead asks
        # "will price fall BACK DOWN through a level it already broke", which
        # is a momentum-continuation-unfavourable question by construction —
        # this alone produced a spurious ~-33pp gap vs control on the first
        # run. The correct rule, matching how S1/R1 are classified elsewhere
        # in this codebase: a level ABOVE current price is resistance-like
        # (must rise to touch), BELOW is support-like (must fall to touch).
        for label, level in fib["levels"].items():
            level = float(level)
            # DAY-0 GUARD: a level already inside the touch band at test time
            # is a guaranteed hit with zero predictive content (the exact
            # issue min-separation fixed for S1/R1 in the August S/R review —
            # fib levels have no such filter, so this must be applied here).
            if abs(level / cmp_ - 1) <= TOUCH_PCT:
                continue
            touch_dir = "up" if level > cmp_ else "down"
            tou = (future["Low"] <= level * (1 + TOUCH_PCT)) if touch_dir == "down" \
                else (future["High"] >= level * (1 - TOUCH_PCT))
            if tou.any():
                f = future.index[tou.argmax()]
                after = future.loc[f:]
                res = _race(after, level, touch_dir)
                if res is not None:
                    out.append({"Date": td, "Symbol": sym, "label": label,
                               "flow": res, "level": level, "cmp": cmp_,
                               "dist": abs(level / cmp_ - 1), "dir": touch_dir,
                               "touch_bar": f})
            pending_by_date.setdefault(td, []).append(
                (cmp_, abs(level / cmp_ - 1), touch_dir, future))
    return out


def fib_control(pending_by_date):
    out = []
    for td, grp in pending_by_date.items():
        if len(grp) < 5:
            continue
        dists = rng.permutation([g[1] for g in grp])
        for (cmp_, _, direction, future), dc in zip(grp, dists):
            Lc = cmp_ * (1 - dc) if direction == "down" else cmp_ * (1 + dc)
            tc = (future["Low"] <= Lc * (1 + TOUCH_PCT)) if direction == "down" \
                else (future["High"] >= Lc * (1 - TOUCH_PCT))
            if not tc.any():
                continue
            after = future.loc[future.index[tc.argmax()]:]
            res = _race(after, Lc, direction)
            if res is not None:
                out.append({"Date": td, "flow": res})
    return out


def run_stochrsi(df, sym, dates):
    """Every touch (real) + a within-symbol control of RANDOM dates matched
    1:1 to how many crosses this symbol actually produced."""
    real, cross_dates = [], []
    for td in dates:
        past = df[df.index <= td].tail(CONTEXT_BARS)
        if len(past) < 60:
            continue
        sto = ca.stoch_rsi(past)
        cross = sto.get("cross")
        if not cross:
            continue
        direction = "up" if "BULLISH" in cross else "down"
        future = df[df.index > td].head(STOCHRSI_HOLD)
        if len(future) < STOCHRSI_HOLD:
            continue
        end = float(future["Close"].iloc[-1])
        start = float(past["Close"].iloc[-1])
        ret = (end / start - 1) * 100
        flow = (ret > 0) if direction == "up" else (ret < 0)
        real.append({"Date": td, "Symbol": sym, "dir": direction, "flow": flow,
                     "ret": ret, "touch_bar": td})
        cross_dates.append(td)

    # control: same COUNT of random dates from this symbol's own eligible
    # history, each scored the same way with a RANDOM assigned direction
    # (matches "any 5-day window has some base up/down rate" rather than the
    # symbol's own unconditional drift in one fixed direction)
    ctrl = []
    if cross_dates:
        eligible = [td for td in dates if td not in set(cross_dates)]
        n = min(len(cross_dates), len(eligible))
        if n > 0:
            picks = rng.choice(len(eligible), n, replace=False)
            for i in picks:
                td = eligible[i]
                past = df[df.index <= td].tail(CONTEXT_BARS)
                future = df[df.index > td].head(STOCHRSI_HOLD)
                if len(past) < 5 or len(future) < STOCHRSI_HOLD:
                    continue
                end = float(future["Close"].iloc[-1])
                start = float(past["Close"].iloc[-1])
                ret = (end / start - 1) * 100
                direction = rng.choice(["up", "down"])
                flow = (ret > 0) if direction == "up" else (ret < 0)
                ctrl.append({"Date": td, "flow": flow})
    return real, ctrl


def run_combined(fib_rows, stoch_rows):
    """Fib touch AND same-direction StochRSI cross within COMBINED_WINDOW
    sessions of the touch, SAME symbol."""
    if not fib_rows or not stoch_rows:
        return []
    F = pd.DataFrame(fib_rows)
    S = pd.DataFrame(stoch_rows)
    out = []
    for sym, fg in F.groupby("Symbol"):
        sg = S[S["Symbol"] == sym]
        if not len(sg):
            continue
        for _, fr in fg.iterrows():
            same_dir = sg[sg["dir"] == fr["dir"]]
            if not len(same_dir):
                continue
            gap = (same_dir["touch_bar"] - fr["touch_bar"]).abs().dt.days
            near = same_dir[gap <= COMBINED_WINDOW * 2]   # calendar-day slack for weekends
            if len(near):
                out.append({"Date": fr["Date"], "Symbol": sym, "flow": fr["flow"]})
    return out


def clustered_ci(rows, val="flow", n_boot=4000):
    df = pd.DataFrame(rows)
    if not len(df) or "Date" not in df.columns:
        return (np.nan, np.nan, len(df), 0)
    dates = df["Date"].unique()
    if len(dates) < 2:
        return (np.nan, np.nan, len(df), len(dates))
    groups = [df.loc[df["Date"] == d, val].to_numpy(dtype=float) for d in dates]
    means = np.empty(n_boot)
    for i in range(n_boot):
        pick = rng.integers(0, len(groups), len(groups))
        means[i] = np.concatenate([groups[j] for j in pick]).mean()
    return (float(np.percentile(means, 2.5)), float(np.percentile(means, 97.5)),
            len(df), len(dates))


def diff_ci(real, ctrl, val="flow", n_boot=4000):
    a = pd.DataFrame(real)[val].to_numpy(dtype=float) if real else np.array([])
    b = pd.DataFrame(ctrl)[val].to_numpy(dtype=float) if ctrl else np.array([])
    if len(a) < 10 or len(b) < 10:
        return None
    diffs = np.empty(n_boot)
    for i in range(n_boot):
        diffs[i] = (rng.choice(a, len(a), True).mean()
                   - rng.choice(b, len(b), True).mean())
    return (diffs.mean(), np.percentile(diffs, 2.5), np.percentile(diffs, 97.5),
            float((diffs > 0).mean()))


def oos_split(rows, ctrl_rows):
    """First 70% / last 30% of test dates, by DATE not row count."""
    df = pd.DataFrame(rows)
    if not len(df):
        return rows, [], ctrl_rows, []
    dates = sorted(df["Date"].unique())
    cut = dates[int(len(dates) * 0.7)]
    is_r = [r for r in rows if r["Date"] <= cut]
    oos_r = [r for r in rows if r["Date"] > cut]
    is_c = [r for r in ctrl_rows if r["Date"] <= cut]
    oos_c = [r for r in ctrl_rows if r["Date"] > cut]
    return is_r, oos_r, is_c, oos_c


def report(name, real, ctrl):
    lo, hi, n, nd = clustered_ci(real)
    clo, chi, cn, cnd = clustered_ci(ctrl)
    print(f"\n  {name}")
    print(f"    real : n={n:5d} dates={nd:4d}  flow={pd.DataFrame(real)['flow'].mean()*100 if n else float('nan'):5.1f}%"
          f"  95%CI [{lo*100:5.1f},{hi*100:5.1f}]" if n else f"    real : n=0")
    print(f"    ctrl : n={cn:5d} dates={cnd:4d}  flow={pd.DataFrame(ctrl)['flow'].mean()*100 if cn else float('nan'):5.1f}%"
          f"  95%CI [{clo*100:5.1f},{chi*100:5.1f}]" if cn else f"    ctrl : n=0")
    d = diff_ci(real, ctrl)
    if d:
        m, lo2, hi2, pb = d
        excludes_zero = not (lo2 <= 0 <= hi2)
        edge_5pp = m * 100 >= 5.0
        print(f"    diff : {m*100:+5.1f}pp  95%CI [{lo2*100:+5.1f},{hi2*100:+5.1f}]  "
              f"P(real>ctrl)={pb*100:4.0f}%")
        print(f"    criterion 1 (CI excludes 0, favourable) : {'PASS' if excludes_zero and m>0 else 'FAIL'}")
        print(f"    criterion 2 (edge >= 5pp)               : {'PASS' if edge_5pp else 'FAIL'}")
        return excludes_zero and m > 0 and edge_5pp
    else:
        print("    (insufficient n for a bootstrap)")
        return False


def cost_adjusted_note(flow_rate, n_trials, cost_per_side=sc.COST):
    """Rough sanity check: a round-trip trade pays 2*cost. FLOW_THRESH=2% is
    the reward being raced for; at COST=0.1%/side, round-trip cost is 0.2%,
    i.e. 10% of the reward threshold. Report what fraction of the apparent
    edge that consumes -- NOT a full backtest, just the sanity check named
    in the pre-registration's decision rule #4."""
    rt_cost_pct = cost_per_side * 2 * 100
    print(f"    txn-cost check: round-trip cost {rt_cost_pct:.2f}% vs "
          f"{FLOW_THRESH:.0f}% flow threshold "
          f"({rt_cost_pct/FLOW_THRESH*100:.0f}% of the threshold consumed by cost alone)")


def main():
    quick = "--quick" in sys.argv
    syms = load_universe()
    if quick:
        syms = syms[:30]
        print("--quick: first 30 symbols only")

    all_fib, all_stoch, fib_pending = [], [], {}
    stoch_ctrl_all = []

    for i, sym in enumerate(syms, 1):
        if i % 25 == 0:
            print(f"  [{i}/{len(syms)}]", flush=True)
        df = core.load_stock(sym)   # liquid_universe() already returns "SYM.NS"
        if df is None or len(df) < MIN_HISTORY:
            continue
        dates = test_dates(df)
        if not len(dates):
            continue
        all_fib.extend(run_fib(df, sym, dates, fib_pending))
        r, c = run_stochrsi(df, sym, dates)
        all_stoch.extend(r)
        stoch_ctrl_all.extend(c)

    fib_ctrl = fib_control(fib_pending)
    combined = run_combined(all_fib, all_stoch)
    # combined's control: same permutation logic as fib, restricted to the
    # touches that WERE part of a combined signal (apples to apples)
    combined_pending = {td: g for td, g in fib_pending.items()
                        if any(r["Date"] == td for r in combined)}
    combined_ctrl = fib_control(combined_pending) if combined_pending else []

    print(f"\n{'='*74}")
    print(f"  PREREG_fib_stochrsi.md — RESULTS")
    print(f"{'='*74}")
    print(f"  fib touches: {len(all_fib)}  |  stochrsi crosses: {len(all_stoch)}  |  "
          f"combined: {len(combined)}")

    verdicts = {}
    for name, real, ctrl in [("1. FIB-ALONE", all_fib, fib_ctrl),
                             ("2. STOCHRSI-ALONE", all_stoch, stoch_ctrl_all),
                             ("3. COMBINED", combined, combined_ctrl)]:
        passed_full = report(name, real, ctrl)
        cost_adjusted_note(None, len(real))

        # criterion 3: out-of-sample split
        is_r, oos_r, is_c, oos_c = oos_split(real, ctrl)
        print(f"    in-sample (first 70% of dates)  : n={len(is_r)}")
        print(f"    out-of-sample (last 30%)        : n={len(oos_r)}")
        oos_pass = False
        if len(oos_r) >= 10 and len(oos_c) >= 10:
            d = diff_ci(oos_r, oos_c)
            if d:
                m, lo, hi, pb = d
                oos_pass = not (lo <= 0 <= hi) and m > 0
                print(f"    OOS diff: {m*100:+.1f}pp  CI [{lo*100:+.1f},{hi*100:+.1f}]  "
                      f"criterion 3 (OOS confirms): {'PASS' if oos_pass else 'FAIL'}")
            else:
                print("    OOS: insufficient n")
        else:
            print("    OOS: insufficient n")

        verdicts[name] = passed_full and oos_pass

    print(f"\n{'='*74}")
    print("  VERDICT (per PREREG_fib_stochrsi.md's decision rule — all four")
    print("  criteria must pass; #4 txn-cost is a sanity note above, not a")
    print("  separate pass/fail gate since FLOW_THRESH already exceeds it)")
    print(f"{'='*74}")
    for name, v in verdicts.items():
        print(f"  {name:20s} {'CLEARS THE BAR' if v else 'REJECTED'}")
    if not any(verdicts.values()):
        print(f"\n  0/3 configs cleared. Per the pre-registration: this line of work")
        print(f"  CLOSES, recorded as the 7th confirmation of the auxiliary-overlay")
        print(f"  pattern. chart_analysis.py's fib_retracement/stoch_rsi remain")
        print(f"  permanently display-only.")
    elif verdicts.get("3. COMBINED") and not (verdicts.get("1. FIB-ALONE") or verdicts.get("2. STOCHRSI-ALONE")):
        print(f"\n  COMBINED cleared but neither standalone config did — per the")
        print(f"  pre-registration this is treated as likely noise from the extra")
        print(f"  degree of freedom, NOT chased with further combined variants.")
    print()


if __name__ == "__main__":
    main()
