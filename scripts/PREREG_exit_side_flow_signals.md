# PRE-REGISTRATION — delivery%/OI as EXIT-side warning signals

**Written 2026-08-10, BEFORE running any test below. Frozen. Amendments
appended with reason, never silently edited.**

## RESULT (2026-08-10, research_exit_flow_signals.py +
research_exit_flow_signals_pertrigger.py) — REJECTED, mechanism family CLOSED

36-window walk-forward:

| config | mean delta | 95% CI | wins | DD delta | aggregate verdict |
|---|---|---|---|---|---|
| H1 deliv decay 0.70 | -0.23% | [-4.62%,+2.51%] | 18/36 | -1.52% | REJECT |
| H1 deliv decay 0.50 | +2.52% | [+0.74%,+4.02%] | 29/36 | +0.26% | PASSES aggregate |
| H2 OI unwind 5d/3 | -1.35% | [-8.47%,+3.60%] | 21/36 | -5.59% | REJECT |
| H2 OI unwind 10d/5 | -2.01% | [-8.53%,+1.96%] | 21/36 | -2.75% | REJECT |

3/4 reject cleanly, consistent with the low prior. H1_deliv_decay_0.50
passed the aggregate bar — but per the mandatory per-trigger check this
pre-reg required before treating any pass as real:

**Only 3 trigger events fired across the ENTIRE 2015-2026, ~200-name
universe** (BSE.NS 2021-12-15; CHENNPETRO.NS 2022-06-15 and 2022-06-29 —
two of the three are the same stock two weeks apart, likely one episode,
not two independent ones). All landed in a narrow late-2021/mid-2022
window, which — given 36 OVERLAPPING 3-year windows — means these 2-3
events appear in a large, overlapping subset of the 36 "windows," fully
explaining the 29/36 apparent win rate without any real repeatable signal
behind it. This is the "one cell doing all the work" trap in a more
extreme form than usual (n=3, not a skewed-but-populated bucket like H5 in
the S/R batch) — REJECTED despite passing the aggregate statistical bar,
because the bar's own assumptions (independent-ish observations) don't
hold when 3 raw events are the entire basis for 29 "window wins."

**Root cause, checked**: a 50%+ decay in the 10-day rolling delivery%
average vs its own entry-time level is a genuinely rare event — measured
0.0% of days for RELIANCE/TCS/INFY, 1.2-1.6% for more volatile names
(BSE, CHENNPETRO), over 11 years. Combined with requiring it on an
ALREADY-HELD, currently-top-N position, only 3 coincidences occurred in
the whole history. This is not a "threshold needs tuning" problem — a
threshold this strict essentially never fires, and per the pre-reg's own
rule, this closes the mechanism family rather than prompting a search for
a better threshold (a looser threshold was tested at 0.70 and rejected
outright, on the actual aggregate bar, not a sample-size technicality).

**Verdict**: exit-side delivery%/OI flow signals are REJECTED as a
mechanism, joining entry-side rank-blend
([[delivery-pct-inconclusive]], [[oi-pcr-inconclusive]]) and discrete
event-veto ([[exit-announcements-rejected]]) — the FOURTH independent
confirmation that this strategy's edge doesn't like auxiliary overrides,
on any mechanism or any of these three data sources, tested so far.

## Why this is in scope now, and why the prior is low

Study 5 of the "state of the art" program (memory
state-of-the-art-program-2026-08). The monthly-close mandate was relaxed
2026-08-01 ("selling any time is permitted, only the month-end rebalance
is compulsory" — [[trading-mandate-constraints]]), which reopens non-price
EXIT triggers on existing holdings. This is explicitly a DIFFERENT
mechanism from what's already been rejected: delivery% and OI/PCR were
only ever tested as ENTRY-side rank-blends
([[delivery-pct-inconclusive]], [[oi-pcr-inconclusive]]) — using the
signal to help SELECT which names to buy. This tests using a CHANGE in the
same signal, on an ALREADY-HELD position, as an early-warning exit — the
signal answers a different question ("is this move losing its real
backing") than the entry version asked ("is this stock currently
elevated").

**Honest prior going in**: three independent auxiliary-override mechanisms
have now failed on this strategy — entry rank-blend (delivery%, OI/PCR)
and a discrete event-veto (corporate announcements,
[[exit-announcements-rejected]]). That memory's own conclusion: *"this
strategy's edge doesn't like auxiliary overrides, on ANY mechanism tried
so far... momentum's own tailwind resolves most 'bad news' events better
than a rules-based exit does."* The mechanism here is structurally similar
to the rejected announcement veto (an early exit trigger competing against
staying the course), which is the closest prior test to this one. This
should be treated as LIKELY to fail, tested once cleanly, and NOT
iterated through variants if it does — consistent with how conviction
sizing was framed as genuinely different (uses information already
computed) while this is framed as the SAME family as three rejections
(introduces a new external signal to override momentum's own read).

## The signal and mechanism

**H1 — delivery% decay as an exit warning.** Mechanism: sustained delivery
volume (shares actually taken for holding, not intraday churn) is a proxy
for real accumulation behind a move. A held momentum name whose delivery%
has meaningfully DROPPED since entry — the move continuing on lower-
conviction/higher-churn volume — may be running on momentum-chasing
rather than fresh accumulation, an early warning before price itself rolls
over.
Signal: `deliv_now / deliv_at_entry_ma` where both are a trailing N-day
average (not single-day, which is noisy) of DelivPer. Trigger: ratio drops
below a threshold (tested at 0.7 and 0.5 — 30%/50% decay from entry-level
average).

**H2 — futures OI unwind as an exit warning.** Mechanism: the entry study
found HIGH OI build-up predicts WORSE forward returns (chasing) — the
mirror case is whether OI actively UNWINDING on a held position (positions
being closed, not just failing to grow) is a warning the move's
conviction is fading. Signal: `FutOIChg` trailing N-day sum turns and
stays negative (open interest genuinely contracting, not just noisy
day-to-day chop). Trigger: N-day OI change sum below zero for M
consecutive sessions (tested at N=5/M=3 and N=10/M=5).

Both signals checked DAILY on already-held positions only (mirrors how
`trail_stop` is already checked in `run_backtest_laggards_only` — see
implementation plan). Neither can PREVENT a position from being sold at
month-end re-qualification or bypass the -18% catastrophic stop, which
stay unconditional; this only adds an EARLIER exit opportunity.

## Decision rule (same structure as every other study this program)

4 configs total (H1 x 2 thresholds, H2 x 2 (N,M) pairs) — bounded family,
not a sweep, same multiple-comparisons discipline as prior studies. Adopt
only if, on the 36-window walk-forward:
1. Paired block-bootstrap 95% CI on mean CAGR delta excludes zero
   (positive side), AND
2. Wins in >=12/N windows, AND
3. Max drawdown does not worsen by more than 2pp mean.

**Additional condition specific to this study, given the announcements
precedent**: the win must not be explained by a FALSE-POSITIVE / TRUE-
POSITIVE split showing false positives dominate (the exact failure mode
that killed the announcements veto — 20 false-positive exits cost -6.2%
avg while only 15 true positives saved +0.9% avg). This will be checked
per-trigger, not just in aggregate, exactly like the announcements study
did — a positive AGGREGATE result with a negative-dominated per-trigger
breakdown is still a rejection.

## What would NOT be adopted even if statistically significant

- A result driven by a small number of large single-name events (same
  concentration check as every other study — H5 in the S/R batch, the
  early-window investigation in conviction sizing).
- A result where the false-positive-exit cost exceeds the true-positive-
  save benefit, even if net aggregate CAGR looks better (could happen by
  accident if a handful of true positives are very large — the per-trigger
  check above exists specifically to catch this).

## Implementation plan (not yet built)

Add an `exit_signal_fn` hook to `run_backtest_laggards_only`, mirroring
`trail_stop`'s existing daily-check shape inside the hold-window loop:
`exit_signal_fn(symbol, date, entry_date) -> bool` (True = exit today,
checked alongside the existing catastrophic-stop/trail-stop checks, same
priority — an exit signal never overrides the -18% stop, it can only fire
earlier). Needs delivery_data/fo_data loaded and pre-joined to the price
matrix's date index once, not per-symbol-per-date (performance — this
loop already runs daily per held name, adding a per-check file read would
be prohibitively slow across 36 windows).

Given the 3/3 prior on this mechanism family, this will be run as ONE
clean pass — if 0/4 configs clear the bar, this closes the mechanism
family definitively (matching how [[sr-improvement-batch-exhausted-2026-08]]
closed price-derived S/R refinement) rather than prompting a search for a
better threshold.
