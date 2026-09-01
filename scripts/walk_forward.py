"""
Multi-window walk-forward validation of the momentum strategy.

Single full-history backtests are not trustworthy evidence on their own (see
memory: the "Sharpe 0.87" full-history number has already been mistaken for
stale/non-reproducing once, when in fact different windows tell very
different stories — trailing 3y currently shows ~8.5% CAGR, trailing 8y shows
~36%). This script slices the full available history into overlapping
windows and reports the DISTRIBUTION of outcomes, not a single point
estimate, so a config can't be judged "good" or "bad" off one lucky/unlucky
slice.

Reuses backtest_portfolio.py's validated engine (load_price_matrix,
run_backtest, performance) unchanged — this is a harness around that engine,
not a new backtest implementation, so it can never silently diverge from the
live-validated formula.

Usage:
    python walk_forward.py                  # default: 3y windows, 6mo step
    python walk_forward.py --years 5 --step 12
"""
import argparse
import numpy as np
import pandas as pd

from backtest_portfolio import (load_price_matrix, load_index, load_turnover_matrix,
                                run_backtest, run_backtest_laggards_only,
                                run_backtest_gold_blend, performance)

ENGINES = {"hard_close": run_backtest, "laggards_only": run_backtest_laggards_only,
           "gold_blend": run_backtest_gold_blend}


def make_windows(matrix, window_years, step_months):
    """Returns list of (start_date, end_date) for overlapping windows spanning
    the full available history, stepped every step_months."""
    start_all = matrix.index[0]
    end_all = matrix.index[-1]
    window_days = pd.DateOffset(years=window_years)
    step = pd.DateOffset(months=step_months)

    windows = []
    w_start = start_all
    while True:
        w_end = w_start + window_days
        if w_end > end_all:
            break
        windows.append((w_start, w_end))
        w_start = w_start + step

    # Always include the most recent full window ending at the last available date,
    # even if it doesn't fall on the step grid — this is the window that matters most
    # for "how has this actually been doing lately."
    last_window = (end_all - window_days, end_all)
    if not windows or windows[-1] != last_window:
        windows.append(last_window)

    return windows


_ENGINE_REQUIRED = object()


def run_window(matrix, index, turnover_matrix, start, end, engine=_ENGINE_REQUIRED):
    """`engine` is REQUIRED — there is deliberately no default.

    It used to default to `run_backtest`, the LEGACY hard-close engine that
    CLAUDE.md keeps only for historical comparison. Four research scripts then
    built their BASELINE with a bare `run_window(matrix, index, turnover, s, e)`
    while printing "Running BASELINE (production run_backtest_laggards_only)"
    and comparing it against candidates that DID pass
    engine=run_backtest_laggards_only. Those studies therefore measured
    "hard_close vs laggards_only + the change under test", not the change under
    test — and laggards_only is worth roughly +1pp CAGR on its own, so every
    candidate got a free ~1pp head start.

    Found 2026-09-01 by an adversarial check that ran the SAME configuration
    down two code paths and got different equity curves (sizing_fn=None
    +28.90% vs an explicit tilt=0.50 sizing_fn +31.76%, which must be
    identical). Impact: PREREG_conviction_sizing.md's +2.89% is +1.85% once
    corrected (still clears its bar); the trend-quality and exit-flow
    REJECTIONS are unaffected in direction, since the confound favoured the
    candidates they rejected anyway.

    A silent wrong default is exactly the failure mode this repo keeps
    re-discovering in its own instruments, so the fix is to make it impossible
    to omit rather than to change which engine is silently assumed.
    """
    if engine is _ENGINE_REQUIRED:
        raise TypeError(
            "run_window() requires an explicit `engine=` — pass "
            "backtest_portfolio.run_backtest_laggards_only for the production "
            "engine, or run_backtest for the legacy hard-close one. It used to "
            "default to the LEGACY engine, which silently invalidated the "
            "baseline of four studies; see this function's docstring.")
    sub_matrix = matrix[(matrix.index >= start) & (matrix.index <= end)]
    sub_matrix = sub_matrix.loc[:, sub_matrix.isna().mean() <= 0.20]
    if len(sub_matrix) < 300:
        return None
    sub_turnover = turnover_matrix.reindex(sub_matrix.index)
    equity = engine(sub_matrix, index, sub_turnover)
    if len(equity) < 2:
        return None
    return performance(equity)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--years", type=int, default=3, help="window length in years")
    parser.add_argument("--step", type=int, default=6, help="step between windows in months")
    parser.add_argument("--engine", choices=list(ENGINES), default="gold_blend",
                        help="hard_close (legacy, full sell+rebuy every rebalance), "
                             "laggards_only (momentum sleeve only, was production 2026-07-12), or "
                             "gold_blend (laggards_only + GOLD_ALLOC gold sleeve — "
                             "production default since 2026-07-13)")
    args = parser.parse_args()
    engine = ENGINES[args.engine]

    print("\n==============================")
    print(f"WALK-FORWARD VALIDATION — {args.years}y windows, {args.step}mo step, engine={args.engine}")
    print("==============================")

    matrix = load_price_matrix()
    index = load_index()
    turnover_matrix = load_turnover_matrix(matrix)
    print(f"Full history: {matrix.index[0].date()} to {matrix.index[-1].date()} ({len(matrix)} days, {matrix.shape[1]} stocks)")

    windows = make_windows(matrix, args.years, args.step)
    print(f"{len(windows)} overlapping {args.years}y windows, stepped every {args.step} months\n")

    rows = []
    for start, end in windows:
        result = run_window(matrix, index, turnover_matrix, start, end, engine=engine)
        if result is None:
            continue
        total, annual, sharpe, dd, vol, yrs = result
        rows.append({
            "start": start.date(), "end": end.date(),
            "total_return": total, "annual_return": annual,
            "sharpe": sharpe, "max_dd": dd,
        })

    if not rows:
        print("No windows had enough data.")
        return

    df = pd.DataFrame(rows)
    pd.set_option("display.width", 120)
    print(df.to_string(index=False, formatters={
        "total_return": lambda x: f"{x:+.1%}",
        "annual_return": lambda x: f"{x:+.1%}",
        "sharpe": lambda x: f"{x:.2f}",
        "max_dd": lambda x: f"{x:.1%}",
    }))

    print(f"\n{'='*60}")
    print("AGGREGATE ACROSS WINDOWS (this is the number to trust, not any single row)")
    print(f"{'='*60}")
    print(f"Annual return:  mean {df['annual_return'].mean():+.1%}   median {df['annual_return'].median():+.1%}   "
          f"min {df['annual_return'].min():+.1%}   max {df['annual_return'].max():+.1%}")
    print(f"Sharpe:         mean {df['sharpe'].mean():.2f}   median {df['sharpe'].median():.2f}   "
          f"min {df['sharpe'].min():.2f}   max {df['sharpe'].max():.2f}")
    print(f"Max drawdown:   mean {df['max_dd'].mean():.1%}   median {df['max_dd'].median():.1%}   "
          f"worst {df['max_dd'].max():.1%}")
    n_negative_sharpe = (df["sharpe"] < 0).sum()
    print(f"\nWindows with negative Sharpe: {n_negative_sharpe}/{len(df)}")
    print(f"Windows with negative total return: {(df['total_return'] < 0).sum()}/{len(df)}")
    print(f"\nMost recent window ({windows[-1][0].date()} to {windows[-1][1].date()}):")
    last = df.iloc[-1]
    print(f"  annual {last['annual_return']:+.1%}  sharpe {last['sharpe']:.2f}  max_dd {last['max_dd']:.1%}")


if __name__ == "__main__":
    main()
