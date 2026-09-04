# Pre-registration — Tranched (overlapping) rebalancing

Written 2026-09-04, BEFORE running `research_tranching.py`. Frozen; amendments
appended below with their own date.

## Why this is being run now

`research_timing_luck.py` (2026-09-02) measured the rebalance grid's PHASE at
**11.19pp of full-panel CAGR** (21.94%–33.13%, sd 3.23pp) on identical rules
and identical data. That is larger than every effect this repo's research
programme has ever adopted (conviction sizing, +1.85pp) or rejected
(cap_0.35 +0.59pp, trend-quality +1.14pp, sectors.json +0.72pp). It is pure
noise: nobody chose the calendar, and production's last-Tuesday rule is not
even the phase the backtest grid uses.

That study also *measured* the remedy and then declined to adopt it, on the
sole ground that it conflicted with a fixed user mandate of one rebalance
date per month. **That mandate was lifted 2026-09-04** ("only thing is follow
the trading style, i.e. of month rebalance"). Tranching does not change the
trading style: every sleeve is rebalanced monthly and every position is held
for one month. Only the arbitrary choice of *which* day is diversified away.

## Hypothesis

Splitting capital across N sleeves rebalanced on staggered phases (the
Jegadeesh–Titman overlapping-portfolio construction) reduces (a) outcome
dispersion attributable to the calendar and (b) single-name concentration in
the aggregate book, at no material cost to mean return.

## THIS IS NOT AN ALPHA CLAIM — so the usual bar is the WRONG bar

Total wealth across self-contained sleeves is their sum, so the tranched mean
return is close to the average of the individual phases **by construction**.
Demanding "mean CAGR delta > 0 with a bootstrap CI excluding zero" — this
repo's standard adoption bar — would reject a change that is correct by
arithmetic. The bar below therefore tests what is actually claimed: variance
down, risk not worse, return not damaged.

## Decision rule (frozen)

Adopt tranching at some N > 1 only if ALL of:

- **R1 — return not damaged.** Mean CAGR across all 21 phase-offsets for
  tranched(N) is no worse than untranched mean CAGR − 0.50pp. (Frictions, not
  arithmetic, are what could break this: more orders, more partial fills.)
- **R2 — dispersion materially reduced.** sd of CAGR across the 21 offsets
  falls by ≥ 40% versus N=1.
- **R3 — risk not worse.** Mean max-drawdown across offsets does not worsen by
  more than 1.0pp, and worst-case max-drawdown across offsets IMPROVES.

Reported but NOT gating (they qualify the claim, they do not decide it):

- **C1 — concentration.** Median count of distinct names in the AGGREGATE book
  and the realised maximum single-name weight. The concentration benefit is
  only claimed if median distinct names ≥ 1.5× the single-sleeve count.
  **If sleeves select the same names, this benefit is zero and only R2
  survives** — momentum is persistent, and top-3 sets 21 days apart may
  overlap heavily. This must be measured, not assumed.
- **C2 — turnover and tax.** Rupee turnover and the holding-period
  distribution must be materially unchanged (tranching splits the same
  rotation across more dates; it must not manufacture extra round-trips).
  A rise in rupee turnover >10% falsifies the "same style, same tax" claim
  and is grounds to reject regardless of R1–R3.

## Choice of N

Tested: N ∈ {1, 2, 3, 4, 7, 21}. If several clear, adopt the SMALLEST N whose
dispersion reduction is within 20% of the best — operational load scales with
N and this repo's precedent (CONVICTION_TILT, REGIME_EXPOSURE, MAX_PER_SECTOR)
is to take the robust interior point, not the extreme that measures best.
N=3 is expected to look anomalously poor (21/3 = 7 spaces the phases in a way
that correlated on this panel) and that is a known small-sample artifact of
having only 21 offsets — it is NOT evidence against tranching in general.

## Adversarial checks required before believing any result

1. Engine `daily_marks` must reproduce the returned equity array EXACTLY with
   and without the hook (done 2026-09-04: 13,665,296.343059 both ways).
2. The N=1 arm must reproduce `research_timing_luck.py`'s published full-panel
   phase spread (21.94%–33.13%). If it does not, the daily-marks curve is
   wrong and nothing else in the run may be read.
3. Drawdown must be computed on the SUMMED daily curve, never as a mean of
   per-sleeve drawdowns (which would understate it).
4. Report whether the Sharpe gain is vol reduction or return gain — the claim
   is the former; a return gain would mean the construction is not what I
   think it is.

## What a negative result means

If R2 fails, timing luck is not diversifiable by staggering and the 11.19pp
must simply be quoted as irreducible uncertainty on every number this repo
publishes. If R1 or R3 fails, the variance reduction is being paid for and
the trade-off goes to the user, not to me.

---

## VERDICT — 2026-09-04. Cleared its variance bar, NOT ADOPTED.

R1-R3 all PASS at N in {2,4,7,21} (N=3 fails R2, the known 21/3=7 phase-spacing
artifact): mean CAGR within +0.6/+0.8pp of N=1, sd of CAGR across the 21
offsets falls 52-100%, worst-case daily maxDD improves at every N (45.5% ->
33-35% at N=2-4, 28.5% at N=21).

NOT ADOPTED:
1. The mandate — rebalance on the last Tuesday, one decision date per month
   (confirmed 2026-09-04; an earlier reading that it was relaxed was wrong).
2. rebalance_idx= on the real last-Tuesday calendar removes the problem
   tranching solved: the 11.19pp was the SPREAD across 21 arbitrary phases,
   and under a firm last-Tuesday rule there is exactly ONE calendar.

Also measured (C1): tranching does NOT reduce single-name concentration —
median/p90/p99/worst max single-name weight unchanged at every N (25% of total
capital), because persistent momentum makes every sleeve converge on the same
leader. Only the variance benefit was real. daily_marks/book_log stay in the
engine (they also serve the daily-drawdown fix).
