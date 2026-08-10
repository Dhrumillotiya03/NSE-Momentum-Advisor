"""
Per-trigger false-positive/true-positive breakdown for H1_deliv_decay_0.50 —
the ONLY config from research_exit_flow_signals.py that passed the aggregate
bar. Required by PREREG_exit_side_flow_signals.md before this can be treated
as adoptable: an aggregate CAGR improvement can still hide a failure mode
where a few large true positives mask many small false-positive losses (the
exact way the corporate-announcements veto looked bad in aggregate detail
once broken down — see memory exit-announcements-rejected).

Method: instrument make_delivery_signal_fn to LOG every trigger event
(symbol, date, price at trigger). For each triggered exit, compare the
FORWARD price path in two counterfactuals over the SAME remaining window:
  (a) exit now (at the trigger price)
  (b) hold to the earlier of: month-end/HOLD-period close, or the
      catastrophic stop firing
A trigger is a TRUE POSITIVE if (a) > (b) (exiting early was the right
call) and a FALSE POSITIVE if (a) < (b) (should have held).

Run from scripts/:  python research_exit_flow_signals_pertrigger.py
"""
import numpy as np
import pandas as pd

import strategy_config as sc
from backtest_portfolio import (load_price_matrix, load_index, load_turnover_matrix,
                                run_backtest_laggards_only)
from research_exit_flow_signals import load_delivery_series

DECAY_THRESHOLD = 0.50
MA_WINDOW = 10
HOLD = sc.HOLD
CATASTROPHIC_STOP = sc.CATASTROPHIC_STOP


def make_logging_delivery_signal_fn(deliv_series, decay_threshold, ma_window, trigger_log):
    """Same logic as make_delivery_signal_fn, but appends (symbol, date) to
    trigger_log every time it fires — so the caller can go back and evaluate
    each firing's forward outcome after the backtest run completes."""
    entry_ma = {}
    last_seen_date = {}

    def signal(sym, date):
        series = deliv_series.get(sym)
        if series is None:
            return False
        window = series[series.index <= date].tail(ma_window)
        if len(window) < ma_window:
            return False
        current_ma = window.mean()

        prev_date = last_seen_date.get(sym)
        is_new_episode = prev_date is None or (date - prev_date).days > 35
        last_seen_date[sym] = date
        if is_new_episode:
            entry_ma[sym] = current_ma
            return False

        baseline = entry_ma.get(sym)
        if baseline is None or baseline <= 0:
            return False
        fired = (current_ma / baseline) < decay_threshold
        if fired:
            trigger_log.append((sym, date))
        return fired

    return signal


def evaluate_trigger(matrix, sym, trigger_date):
    """Compare (a) exit at trigger_date's close vs (b) hold up to HOLD
    trading days forward or the catastrophic stop, whichever comes first —
    using entry price = the price ~HOLD days before trigger_date as a proxy
    for "held this position," since exact entry isn't tracked per-trigger
    here (the backtest's own book is). This intentionally mirrors the
    announcements script's same-position-two-outcomes comparison shape,
    using the price series directly rather than re-deriving the exact
    entry from the backtest's internal book state."""
    if sym not in matrix.columns:
        return None
    col = matrix[sym]
    if trigger_date not in col.index:
        return None
    trigger_idx = col.index.get_loc(trigger_date)
    trigger_price = col.iloc[trigger_idx]
    if pd.isna(trigger_price):
        return None

    # "held" counterfactual: walk forward from trigger_date up to HOLD days,
    # exiting at the catastrophic stop (vs price ~HOLD days before trigger,
    # as an approximation of the position's entry reference) or at the
    # window's end, whichever comes first.
    lookback_idx = max(0, trigger_idx - HOLD)
    entry_ref = col.iloc[lookback_idx]
    if pd.isna(entry_ref) or entry_ref <= 0:
        return None

    end_idx = min(trigger_idx + HOLD, len(col) - 1)
    held_price = trigger_price
    for idx in range(trigger_idx + 1, end_idx + 1):
        p = col.iloc[idx]
        if pd.isna(p):
            continue
        held_price = p
        if p < entry_ref * CATASTROPHIC_STOP:
            break   # catastrophic stop would have fired in the held counterfactual too

    exit_now_return = 0.0   # by definition, exiting now locks in today's price as the reference
    held_return = (held_price / trigger_price) - 1

    # Triggered-exit is BETTER than holding when the forward path from the
    # trigger point is negative (held_return < 0) — i.e. the signal correctly
    # anticipated further downside. It's a FALSE POSITIVE when held_return > 0
    # (price kept going up / recovered after the signal fired).
    return {
        "symbol": sym, "date": trigger_date,
        "trigger_price": trigger_price, "held_forward_return": held_return,
        "true_positive": held_return < 0,
    }


def main():
    print("=" * 70)
    print("PER-TRIGGER BREAKDOWN — H1_deliv_decay_0.50")
    print("=" * 70)

    matrix = load_price_matrix()
    index = load_index()
    turnover = load_turnover_matrix(matrix)

    deliv_series = load_delivery_series(list(matrix.columns))
    trigger_log = []
    signal_fn = make_logging_delivery_signal_fn(deliv_series, DECAY_THRESHOLD, MA_WINDOW, trigger_log)

    print("Running full-history backtest with logging enabled...")
    run_backtest_laggards_only(matrix, index, turnover, exit_signal_fn=signal_fn)
    print(f"Total trigger events fired: {len(trigger_log)}")

    results = [evaluate_trigger(matrix, sym, date) for sym, date in trigger_log]
    results = [r for r in results if r is not None]
    print(f"Evaluable triggers (had enough forward data): {len(results)}/{len(trigger_log)}")

    if not results:
        print("No evaluable triggers — cannot assess false-positive rate.")
        return

    true_pos = [r for r in results if r["true_positive"]]
    false_pos = [r for r in results if not r["true_positive"]]
    print(f"\nTRUE POSITIVES (signal correctly anticipated further downside): {len(true_pos)}/{len(results)} "
          f"({len(true_pos)/len(results):.1%})")
    if true_pos:
        avg_saved = -np.mean([r["held_forward_return"] for r in true_pos])
        print(f"  avg forward loss AVOIDED by exiting early: {avg_saved:+.2%}")

    print(f"\nFALSE POSITIVES (price recovered/kept rising after the signal fired): "
          f"{len(false_pos)}/{len(results)} ({len(false_pos)/len(results):.1%})")
    if false_pos:
        avg_cost = np.mean([r["held_forward_return"] for r in false_pos])
        print(f"  avg forward gain MISSED by exiting early: {avg_cost:+.2%}")

    net = sum(-r["held_forward_return"] for r in results) / len(results)
    print(f"\nNet average value of acting on the signal (positive = good): {net:+.2%}")
    print(f"\n{'PATTERN MATCHES the announcements-veto failure (false positives dominate)' if len(false_pos) > len(true_pos) else 'Does NOT match the announcements-veto failure pattern (true positives dominate by count)'}")


if __name__ == "__main__":
    main()
