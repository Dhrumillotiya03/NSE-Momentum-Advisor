# PRE-REGISTRATION — S/R improvement batch (6 hypotheses)

**Written 2026-08-04, BEFORE running any of the tests below.**
Frozen. Amendments appended with reason, never silently edited.

User asked for "everything that could further improve accuracy or confidence in
the S/R levels". That is an open-ended request, and open-ended requests are how
false positives get manufactured. So the entire batch is specified here first,
with ONE shared decision rule and an explicit multiple-comparisons correction.

## Why this document exists

With 6 hypotheses tested at a nominal p≈0.05, the probability that AT LEAST ONE
looks significant by chance alone is 1-(0.95^6) ≈ **26%**. Running six tests and
reporting the winner is not research, it is selection. This repo has already
rejected >10 auxiliary signals ([[oi-pcr-inconclusive]],
[[sr-vol-estimator-rejected-2026-08]], [[sr-model-sweep-exhausted-2026-08]], and
7 others), so the PRIOR that any given new idea works here is low. The bar is
set accordingly and will not move after seeing results.

## The 6 hypotheses

Each is falsifiable, each has a stated mechanism, and each is testable on data
already local (200 symbols x 15min bars 2023-2026, daily archive 2015-2026).

**H1 — EXIT side: does selling into resistance beat holding to month-end?**
Mechanism: the entry study failed on ADVERSE SELECTION — a fill is itself bad
news. Exits have no such problem: the position is ALREADY held, nothing needs
to fill for you to own it. Prior evidence FROM THIS REPO's own null result:
resistance exits averaged +6.6% vs -2.1% for horizon exits. That is suggestive
and it is why this is H1, but it was measured on the losing entry population,
so it is NOT evidence for the exit rule standing alone.
Test: for a name held from month start, compare (a) exit at first resistance
touch vs (b) hold to month-end close. Same names, same dates, paired.

**H2 — Volume at the level: does arrival-on-fading-volume predict holding?**
Mechanism: a level reached on collapsing volume suggests exhaustion; reached on
heavy volume suggests conviction behind the move. Deliberately held back from
the earlier study to keep one variable at a time.
Test: add volume-ratio-at-touch (vol in the touching bars / 20d average) as a
conditioning axis on P(touch-then-hold).

**H3 — Intraday realised vol as the touch table's vol axis.**
Mechanism: 25 returns/day instead of 1 gives a far more precise vol estimate.
NOTE this is NOT the already-rejected Parkinson test
([[sr-vol-estimator-rejected-2026-08]]) — that summarised ONE bar per day and
lost because it discarded overnight gaps. This uses genuinely finer sampling
AND keeps the overnight gap as its own term.
Test: rebuild the touch table keyed on intraday RV instead of close-to-close;
compare OOS correlation.

**H4 — Level CONFLUENCE: are levels confirmed by multiple methods stronger?**
Mechanism: support_resistance.py already computes swing pivots, volume-profile
nodes and 52w levels. A price where several independently agree is plausibly
more meaningful than one method's artifact. This is a free test — the
components already exist, nothing new is computed.
Test: count how many distinct methods place a level within 1% of each other;
condition hold-rate on that count.

**H5 — Level AGE / TOUCH COUNT: does a level tested more often hold better?**
Mechanism: textbook TA says repeatedly-respected levels are stronger; an equally
standard counter-claim says each test consumes the resting liquidity, so many
touches means the level is ABOUT to break. Genuinely two-sided — good test.
Test: count prior touches in the lookback; condition hold-rate on that count.

**H6 — Regime conditioning: do S/R levels work differently by market regime?**
Mechanism: core.py already classifies Bull/Sideways/Bear. In a trending market,
levels should break; in a range, they should hold. If true, the SAME probability
number means different things in different regimes, and the band should widen or
narrow accordingly.
Test: condition containment and hold-rates on the regime at the decision date.

## Shared decision rule (fixed now, applies to ALL six)

Primary metric: out-of-sample correlation with the realised outcome, or hold-rate
lift, depending on the hypothesis (stated per test in the script).

    ADOPT only if ALL of:
      (a) OOS improvement >= +0.02 corr (or >= +5pp hold-rate lift), AND
      (b) wins a majority of the 4 horizons (5/10/15/21d), AND
      (c) survives the multiple-comparisons correction below, AND
      (d) has a stated mechanism that predicted the result BEFOREHAND
          (not a story constructed afterwards to fit it)

**Multiple-comparisons correction (Holm-Bonferroni, 6 tests, family alpha 0.05):**
sort p-values ascending; test i must satisfy p_i <= 0.05/(6-i+1). So the best
result needs p <= 0.0083, the second p <= 0.010, and so on. A test that clears
raw p<0.05 but fails Holm is reported as NOT SIGNIFICANT.

**Bootstrap requirement:** any hypothesis clearing (a)-(d) must additionally
survive a paired block bootstrap resampled BY DECISION DATE — never by row,
since same-date observations across symbols are not independent (this repo made
that mistake's opposite correctly in research_sr_model_bootstrap.py; keep it).

## Splits and lookahead

Time-based only, fixed before running:
  - intraday tests: train < 2025-01-01, holdout >= 2025-01-01
  - daily-archive tests: train < 2022-01-01, holdout >= 2022-01-01
All features computed from bars at or before the decision date; all outcomes
strictly after. No feature may use the forward window in any form.

## Honest expectations, recorded in advance

Given the ceiling analysis ([[sr-model-sweep-exhausted-2026-08]]) showed an
ORACLE fitted on its own answers beats production by only +0.008 corr, H3 and H4
in particular are attacking a target with very little headroom. I expect most of
this batch to fail. Recording that now so a null result cannot be reframed later
as "we always knew" OR as a surprise.

H1 is the only one where I hold a genuine prior that it works, for the stated
structural reason (no fill required => no adverse selection).

## What CANNOT be tested here
- order-book depth over time (Kite gives snapshot only, no history) — would
  need collection starting now, evaluated in months
- anything requiring fundamentals/ownership flow (not in this repo)
- pre-2015 regimes (Kite intraday starts Aug 2015)

## RESULTS — 2026-08-05

**ALL SIX REJECTED.** Nothing cleared the pre-registered bar.

### H1 — exit into resistance (research_sr_exit_side.py)
199 symbols, 16,984 obs, holdout 13,964. This was the one with a genuine prior.

    horizon   hold%   resist%   delta      band%   delta
       5d     0.77%    0.66%   -0.11pp     0.74%  -0.03pp
      10d     1.25%    1.07%   -0.18pp     1.06%  -0.19pp
      15d     2.06%    1.74%   -0.33pp     1.73%  -0.34pp
      21d     2.01%    1.39%   -0.62pp     1.51%  -0.50pp
    mean -0.31pp, wins 0/4                 mean -0.26pp, wins 0/4

Block bootstrap (21d holdout, by date): resistance delta -0.62pp,
95% CI [-1.12, -0.08]pp, P(better)=1.5% — **CI EXCLUDES ZERO IN THE WRONG
DIRECTION**. Selling into resistance is significantly WORSE than holding.

Note the train/holdout reversal: +0.63pp on train (2023-24) vs -0.62pp on
holdout (2025-26). Had this been fitted without a time split it would have
looked like a winner. This is the clearest single vindication of the split
discipline in the batch.

Mechanism (post-hoc, therefore weak — flagged as such): resistance touches rise
from 11% (5d) to 36% (21d) of cases, so the rule increasingly sells winners
early in an up-drifting market. The adverse selection that killed the ENTRY
study is genuinely absent, but it is replaced by a plain opportunity cost.

### H2..H6 (research_sr_batch.py) — 200 symbols, 26,176 holdout obs
Stratified on the existing (distance x vol) cell so a feature proxying those
axes scores ~0 by construction.

    test                    lift        p    Holm     verdict
    H5 touch count       -3.90pp   0.0020  0.0083   not sig (effect size)
    H3 intraday RV       -1.35pp   0.0599  0.0100   not sig
    H2 volume-at-level   -0.48pp   0.5230  0.0125   not sig
    H4 confluence        +0.17pp   0.7285  0.0167   not sig
    H6 regime            +3.50pp        —       —   descriptive only

**H5 deserves the explicit note** because it PASSED its Holm threshold
(p=0.0020 <= 0.0083) and failed only the +5pp effect-size gate. Investigated
rather than dismissed on the technicality. Verdict: NOT REAL. The decline
(62.0% at 0 touches -> 57.4% at 10+) appears ONLY in the 0-2% distance bucket
(67.7% vs 54.5%) and is flat or reversed in all five other buckets. A genuine
liquidity-consumption mechanism would be smooth in distance. Also badly
unbalanced: 20,888 of 26,176 rows sit in the "10+" bin. One cell doing all the
work is what the effect-size gate exists to catch.

**H3 is the informative null.** Intraday realised vol (25 samples/day + the
overnight gap) does NOT beat close-to-close — same verdict as the Parkinson
test it was designed to improve on
([[sr-vol-estimator-rejected-2026-08]]), despite fixing that test's stated
weakness. Two independent routes to a better vol axis have now failed, which is
consistent with the ceiling analysis: the vol axis is not where the remaining
headroom is.

**H6 regime** shows a 3.5pp spread (BULL 59.8 / SIDEWAYS 59.4 / BEAR 58.7).
Below the 5pp bar and reported as DESCRIPTIVE only — it is a categorical split
without a permutation test, so it must not be quoted as a validated effect.

### Family-level conclusion
6 hypotheses, 0 adoptions. With ~26% expected chance of at least one false
positive at nominal p=0.05, the fact that the one sub-threshold p-value
dissolved under inspection is the expected outcome, not a disappointment.

Combined with [[sr-model-sweep-exhausted-2026-08]] (16 variants + oracle
ceiling of +0.008) this closes price-derived S/R refinement. The remaining
untested avenues need data this repo does not have: historical order-book
depth (Kite is snapshot-only), and fundamental/ownership flow.
