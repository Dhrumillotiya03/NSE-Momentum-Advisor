# Pre-registration — month-end full liquidation vs laggards-only

Written 2026-09-04 BEFORE running research_month_end_liquidation.py. Frozen.

## The question

The user's mandate, stated 2026-09-04: rebalance on the last Tuesday; sell
intra-month only if profitable; **if no exit has fired by the last Tuesday,
empty the entire holding that day regardless of profit or loss.**

Production does NOT do this. Since 2026-07-12 the engine is *laggards-only*: at
each last Tuesday the book is fully re-evaluated, but a name still in the new
sector-capped top-N is HELD and only re-weighted (COST on the delta, no
realised gain). Only dropouts are sold. The mandate is a full liquidation —
the legacy `run_backtest` hard-close behaviour.

`run_backtest` cannot answer this cleanly: it also still sizes on plain
inverse-vol (`backtest_portfolio.py:64`), while laggards-only uses
`conviction_weights`. Comparing them measures TWO differences. So the test
uses `run_backtest_laggards_only(..., liquidate_all=True)`, which is
byte-identical to laggards-only in every respect except the one switch.

## Why the answer is not obvious

Full liquidation costs:
- **2x COST** on every surviving name (sell at close, rebuy at close) vs COST
  on the rebalancing delta only.
- **A realised STCG event on every holding, every month** — no gain is ever
  deferred. Laggards-only defers tax on a name it keeps until it finally drops
  out of the top-N (which momentum turnover usually forces within a few
  months, but not always).

Full liquidation might gain:
- Nothing on the signal — same names selected. This is purely a
  cost/tax/mechanics question, not an alpha question.
- A marginally tighter risk profile: every position's -18% catastrophic stop
  resets to the current month's entry rather than trailing from an old one.

## Decision rule (frozen)

The mandate is the user's choice; this test decides whether to WARN them about
its cost, not whether to override them. Report:

1. **Walk-forward, 19 windows, real last-Tuesday calendar, daily-curve
   drawdown.** Gross CAGR / Sharpe / maxDD for both, plus the paired
   per-window delta (block bootstrap, BLOCK_LEN=6, N_BOOT=2000, SEED=42).
2. **Net of STCG**, using `research_monthly_close_cost.py`'s lot-level tax
   accounting — NOT `research_net_returns.apply_stcg`, which treats every
   period's MtM move as realised and so is exact for hard-close but
   OVER-taxes laggards-only, erasing the very difference under test.
3. The realised-event count and mean holding period for each.

**Framing of the result:**
- If gross and net deltas are both inside the block-bootstrap CI (i.e. within
  timing-luck noise), report: "the mandate is close to free — laggards-only
  would have been ~Xpp/yr cheaper after tax but not distinguishably so."
- If the net delta CI excludes zero and favours laggards-only by > 1pp/yr,
  report that number prominently to the user as the cost of the discipline,
  and note that discretionary early profit-taking (which the user also wants)
  narrows the gap by pre-empting some month-end forced sales.
- Adopt NOTHING automatically. The engine keeps `liquidate_all=False` as the
  default; the switch exists so the number can be quoted.

## Adversarial checks

1. `liquidate_all=True` with the same top-N every month must still churn the
   whole book (verify realised-event count ≈ n_rebalances x mean book size).
2. `liquidate_all=False` must reproduce the current laggards-only equity to
   ~1e-7 relative (the engine has pre-existing set-iteration nondeterminism at
   ~1e-8 — run under PYTHONHASHSEED=0 for an exact check).
3. The net-of-tax comparison must use the SAME rebalance calendar for both
   arms — a hard-close on the fixed grid vs laggards on the real calendar
   would reintroduce a two-difference comparison.
