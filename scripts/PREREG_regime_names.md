# Pre-registration — REGIME_NAMES (portfolio breadth) re-test

Written 2026-09-04 BEFORE running `research_regime_names.py`. Frozen.

## Why re-open a decision already made

`REGIME_NAMES` SIDEWAYS=3 and BEAR=4 are the most consequential unexamined
numbers in the strategy. Three findings make the original decisions unsafe:

1. **They were chosen on ONE rebalance phase.** The SIDEWAYS 6->3 study
   ("3 names beats/matches 6 in 16/19 windows") and the BEAR 2->4 study both
   predate `research_timing_luck.py`, which measured phase at **11.19pp of
   CAGR** — several times any plausible breadth effect. A 16/19 win count on a
   single phase is exactly the kind of result that study showed can be noise.
2. **Concentration was independently measured as a DRAG.**
   `PREREG_max_weight_cap.md` found raising the weight cap with no tilt costs
   **-1.19pp CAGR, CI [-2.42,-0.28], 9/36 windows** — concentration by itself
   destroys return here. n=3 is the most concentrated expression of the same
   thing, and it has never been re-tested against that finding.
3. **That study named this exact follow-up.** Its conclusion: "a future
   attempt needs a lever that does not buy concentration at the same time
   (tilting the NUMBER of names or the deployed fraction), pre-registered
   separately." This is that pre-registration.

Also relevant: tranching (PREREG_tranching.md, run today) was measured and
does **NOT** reduce single-name concentration — median/p90/p99/worst max
weight are unchanged at every N, because persistent momentum makes all sleeves
converge on the same leader. So breadth is the ONLY remaining lever on the
largest single-name exposure, currently **25% of total capital**.

## Hypothesis

The production breadth (SIDEWAYS 3, BEAR 4) is too narrow. Widening it
improves risk-adjusted outcome, or at minimum materially reduces drawdown and
single-name exposure at no return cost.

## Configurations (frozen — no others will be tested)

| id | BULL | SIDEWAYS | BEAR |
|----|------|----------|------|
| baseline | 10 | 3 | 4 |
| A | 10 | 5 | 6 |
| B | 10 | 6 | 8 |
| C | 10 | 8 | 10 |
| D | 15 | 5 | 6 |
| E | 10 | 5 | 5 |  (minimum breadth at which MAX_WEIGHT=0.20 can bind at all)

## Two-stage design (declared in advance to avoid peeking)

- **Stage 1 — screen.** Full-panel CAGR/Sharpe/daily-maxDD averaged over
  phases 0/5/10/15. **No adoption is possible from Stage 1.** Its only job is
  to decide which configs are worth the walk-forward's compute.
- **Stage 2 — decision.** Walk-forward, 36 windows (3y, 3mo step), paired
  block bootstrap (BLOCK_LEN=6, N_BOOT=2000, SEED=42), run on phases
  0/5/10/15 per the timing-luck phase gate.

## Decision rule (frozen)

Adopt a config only if, on **at least 3 of the 4 phases**, EITHER:

- **Return arm** — mean CAGR delta > 0 with 95% bootstrap CI excluding zero,
  AND wins >= 2/3 of windows, AND mean max-drawdown does not worsen by >2pp;
  OR
- **Risk arm** — mean CAGR delta CI includes zero with point estimate
  >= -0.50pp, AND worst-case max-drawdown improves by >= 3pp, AND median
  maximum single-name weight falls by >= 5pp.

The risk arm is stated up front because this is a risk-motivated hypothesis
and this repo's precedent would otherwise be ambiguous: residual momentum and
the absolute weight cap were both refused adoption on drawdown-only wins, but
in both cases **return was negative**. A genuinely neutral return with a large
drawdown improvement is a different case and is adoptable here. It is not a
licence to accept a return loss.

## Drawdown must be measured DAILY

Every drawdown figure previously published by this repo was computed on equity
sampled only at 21-day rebalance points and is therefore **understated by a
mean 5.79pp** (measured 2026-09-04 across phases 0/5/10/15: 28.80%->36.93%,
29.01%->34.59%, 25.26%->32.98%, 24.32%->26.04%). All drawdowns in this study
use the daily curve. Baseline comparisons must be recomputed the same way —
never against a historical rebalance-sampled number.

## What a negative result means

If no config clears, n=3/n=4 is vindicated on evidence it never previously
had, the 25%-of-capital single-name exposure is a deliberate accepted risk
rather than an oversight, and it must be disclosed as such in the README.

---

## AMENDMENT 1 — breadth x tilt (registered 2026-09-04, BEFORE running)

Stage 1 showed every plain widening costs 2.7-4.2pp CAGR for 4.9-7.4pp less
drawdown. **This does not contradict `PREREG_max_weight_cap.md`'s finding that
"concentration alone is a drag", and my rationale #2 above was wrong to assume
it would.** The two studies move different levers in opposite directions:
raising the weight CAP concentrates further into the SAME names, while
widening n DILUTES into WORSE-RANKED names. Both can be negative without
contradiction, and both apparently are.

That points at the combination neither study tested. At n>=5 the cap
`MAX_WEIGHT=0.20` becomes arithmetically FEASIBLE for the first time
(1/8 = 12.5% < 20%), so `CONVICTION_TILT` — provably inert in 73% of
production rebalances — can finally act. A wider book with a HARDER tilt
should keep weight in the top-ranked names (preserving the return that
concentration is buying) while carrying a tail of genuinely small positions
(cutting single-name catastrophe exposure). This is precisely the "lever that
does not buy concentration at the same time" the max-weight study asked a
future attempt to find.

**Configs (frozen, no others):** breadth C (BULL 10 / SIDEWAYS 8 / BEAR 10)
crossed with CONVICTION_TILT in {0.50 (production), 0.75, 1.00}, plus breadth
A (10/5/6) at tilt 0.75. Baseline is unchanged production
(3/4 names, tilt 0.50).

**Decision rule:** UNCHANGED from the main pre-registration above (return arm
or risk arm, >=3 of 4 phases). Adding a second free parameter raises the
multiple-comparisons burden, so a marginal pass on the return arm is NOT
adoptable here: the CI must exclude zero on >=3 phases, not merely on the
pooled result.

**Prior:** weak-to-moderate. This repo has rejected nine consecutive changes
to what the strategy RANKS on; its single success (conviction sizing) was a
SIZING change, and this is a sizing change. But the tilt's measured benefit
came from a regime where it was mostly blocked, so extrapolating it to a
regime where it is live is an extrapolation, not a confirmation.

---

## VERDICT — 2026-09-04. REJECTED. Breadth stays SIDEWAYS=3 / BEAR=4.

Stage 1 screen (full panel, phases 0/5/10/15 mean): every widening cost CAGR —
A -2.68pp, B -4.21pp, C -2.75pp, D -3.80pp, E -2.86pp — while cutting daily
maxDD 4.9-7.4pp. Confirms PREREG_max_weight_cap.md's decomposition from the
other direction: raising the weight CAP concentrates into the SAME names (a
drag), widening n DILUTES into WORSE-ranked names (also a drag). Both
negative, no contradiction.

Stage 2 (config C, 36 windows x 4 phases): dCAGR -0.66% / +0.16% / +1.11% /
+0.08%, every CI includes zero, 17-20/36 wins. 0/4 phases. Neither arm clears.

Amendment 1 (breadth C/A x CONVICTION_TILT 0.75/1.00), 36 x 4:
  C tilt 0.50: 0/4   C tilt 0.75: 1/4   A tilt 0.75: 1/4
  C tilt 1.00: 2/4 (phases 10,15 PASS; 0,5 fail) — needed >=3, so FAILS, and
  the 2/4 pattern is the timing-luck signature of a marginal/null effect.

0/4 configs cleared. The line is CLOSED — do not retune n or the n x tilt
crossing. The 25%-of-total-capital maximum single-name weight (n=3 -> 33.3%,
n=4 -> 25.0%, cap infeasible below n=5) is a DELIBERATE, PRICED risk: widening
to reduce it costs 2-4pp/yr, walk-forward-confirmed. Disclosed in the README.
