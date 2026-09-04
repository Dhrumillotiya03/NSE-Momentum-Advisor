# Pre-registration — profit-taking trigger evaluation

Written 2026-09-04 BEFORE any evaluation data exists (profit_exit_log.csv was
created empty the same day). Frozen; amendments dated.

## Status of the mechanism

`profit_watch.py` is DISPLAY-ONLY infrastructure, same class as
chart_analysis.py / news_watchdog.py. It surfaces four profit-taking triggers
(BIG_GAIN, GIVEBACK_IN_PROFIT, RESISTANCE_IN_PROFIT, RSI_EXHAUSTION), all
requiring the position to be green, and logs every fire to
`data/profit_exit_log.csv`. Nothing in it is imported by exit_engine.py,
paper_trader.py or agent_sim.py, and it must stay that way until this
evaluation clears.

## Why the prior is low

Every price-based intra-month exit tested on this strategy has been REJECTED:
tight trailing / 50MA (2x), resistance-fade, and the ratcheting giveback stop
(-5 to -6pp mean CAGR, 4-6/19 windows). The one thing NOT yet tested is an
asymmetric profit-ONLY rule — one that can only pull an exit FORWARD on a name
already in profit, never sell a loser early. That asymmetry is the whole
hypothesis.

## The counterfactual (this is the entire test)

The strategy is laggards-only: a held name is sold at the next last Tuesday if
it drops out of the sector-capped top-N, or immediately if the -18% stop
fires, whichever comes first. A profit-taking trigger has value ONLY IF
exiting at the flag beats that baseline exit — i.e. the name would have given
back gains between the flag and the baseline exit.

For each logged fire, the scorer records:
- `px_5d`, `px_21d` — close 5 / 21 sessions after the flag
- `px_next_rebalance` — close on the next last-Tuesday rebalance date
- `laggards_would_hold` — did the name stay in the top-N at that rebalance
  (i.e. would laggards-only have kept holding it)
- the realised exit the baseline would have taken (rebalance close if sold
  there, else the -18% stop, else still held at eval time = censored)

## Decision rule (frozen)

Evaluate per TRIGGER TYPE, never pooled (they are different mechanisms). For a
trigger type to be promoted from display-only to a live exit signal:

1. **>= 30 independent fires** on distinct (symbol, month) pairs — a name
   re-flagged daily for a week is ONE fire (same clustering error as
   call_report and the exit-flow study).
2. Mean (exit-at-flag return − baseline-exit return) over those fires is
   **positive with a symbol-clustered bootstrap 95% CI excluding zero**
   (n_boot=20000, resample symbols not rows).
3. The result is **not driven by one or two names** — drop the single
   largest-contributing symbol and the CI still excludes zero.
4. It survives a **transaction-cost haircut** of 2 x COST on the position
   (an early exit is an extra round-trip the baseline did not pay).
5. **>= 3 of the 4 timing-luck phases** agree in direction on the
   counterfactual rebalance date (the phase gate — a real effect is
   phase-stable, a null one swings).

If a trigger type fails any of 1-5, it stays display-only. Failing on the
"only 2-3 real events" pattern (criterion 1 or 3) CLOSES that trigger — do not
retune its threshold.

## What promotion would mean

Even a passing trigger becomes an ALERT the user acts on at their discretion,
plus a `run_backtest_laggards_only(exit_signal_fn=...)` walk-forward on the
real last-Tuesday calendar with the frozen threshold — the exit_signal_fn hook
already exists for exactly this. Only if THAT walk-forward also clears the
standard bar does it touch the engine default. Two gates, not one.

## Timeline

profit_exit_log.csv accumulates ~a handful of fires per month at most (4
triggers, ~4-7 held names, dedup per day). 30 independent fires is realistically
6-12 months out. Do not evaluate before then; an underpowered early look is how
this repo's instruments have failed before.
