# PRE-REGISTRATION — conviction-weighted position sizing

**Written 2026-08-05, BEFORE running any test below. Frozen. Amendments
appended with reason, never silently edited.**

## RESULT (2026-08-05, research_conviction_sizing.py) — ADOPT tilt=0.50

36 overlapping 3y windows (see PREREG_trend_quality_factor.md's result note
on why 36, not the ~19 in older memory files). Baseline: mean annual return
+28.78%, mean Sharpe 1.15.

| config | mean delta | 95% CI | P(better) | wins | DD delta | verdict |
|---|---|---|---|---|---|---|
| tilt=0.25 | +1.89% | [+0.82%,+2.63%] | 100.0% | 31/36 | -0.30% | ADOPT |
| tilt=0.50 | +2.89% | [+1.33%,+4.33%] | 100.0% | 33/36 | +0.13% | ADOPT |
| tilt=0.75 | +4.78% | [+2.80%,+6.94%] | 100.0% | 33/36 | +0.44% | ADOPT |

**3/3 configs cleared the bar — the first study in this "state of the art"
program (and one of very few in this repo's whole history) to find a real
effect, not a rejection.** Monotonic in tilt, all three CIs fully exclude
zero, effect present in 31-33 of 36 windows (broad, not concentrated).

**Adversarial checks run before trusting this** (per the standing
quant-researcher mandate — a result this clean gets MORE scrutiny, not
less):
1. **Worst-case drawdown, not just mean DD**: baseline worst-window DD
   38.9%; every tilt level's worst-window DD was BETTER (36.99% / 36.02% /
   34.84% at tilt 0.25/0.50/0.75) — zero windows exceeded the baseline's
   worst case at any tilt. Not a hidden tail-risk trade.
2. **Per-window breakdown (tilt=0.50)**: 33 positive, 3 negative (all small,
   -1.66% to -1.77%), median 2.50% close to mean 2.89% (no single outlier
   window driving the average) — rules out the "one cell doing all the
   work" trap that caught H5 in the S/R improvement batch.
3. **Weight-math sanity check**: conviction_weights on a synthetic 5-name
   example produces smooth, monotonic reallocation toward higher-score
   names as tilt increases, correctly capped by MAX_WEIGHT=0.20 (raw
   uncapped weights at tilt=0.75 reached ~0.28 for the strongest name,
   confirming the cap is actively engaging in the real backtest, not dead
   code). No NaN, no blowup, no degenerate concentration.
4. **Not a turnover/cost artifact**: sizing_fn only reallocates capital
   among the SAME set of held names (`top`, selected before sizing runs) —
   it cannot change trade count or trigger extra round-trips, so the
   improvement cannot be a transaction-cost-side effect.
5. **Early/late window split — investigated, not fully explained**: windows
   0-7 (starting 2015-2016) averaged +6.96pp delta; windows 8-35 (2017
   onward) averaged +1.73pp — a real, sizeable difference. Checked and
   RULED OUT: universe size (flat at 200 throughout), a "just amplifies an
   already-strong bull run" story (early windows actually had LOWER
   baseline mean return and LOWER volatility than later windows — the
   opposite of what that story predicts). Score-dispersion-across-era
   hypothesis tested inconclusively (no clean monotonic pattern in
   coefficient-of-variation across 4 sample dates) — not resolved, flagged
   honestly rather than forcing an explanation. **Re-ran the bootstrap
   restricted to ONLY the 28 later (2017+) windows as a conservative check:
   still clears the bar cleanly** — mean +1.73%, 95% CI [+0.94%, +2.32%],
   P(better)=100%. The adoption decision does not depend on the early era.

**Adopting tilt=0.50** (not 0.25 or 0.75): 0.25 is the most conservative
significant result but leaves return on the table; 0.75 has the largest
effect but the least MAX_WEIGHT headroom before the cap dominates the tilt
entirely (diminishing, not proportional, sensitivity — see weight-math check
above) and no walk-forward evidence yet on whether tilt=0.75's specific
concentration profile holds up in a regime not represented in this history.
0.50 is the middle of a monotonic, all-significant range — same logic this
repo used adopting REGIME_EXPOSURE percentages and MAX_PER_SECTOR=2 (a
robust interior point, not the most extreme value that happened to backtest
best).

**Not yet done**: this result has NOT been wired into production
(`strategy_config.py` / `backtest_portfolio.run_backtest_laggards_only`'s
default sizing) or the live path (`core.py`, `full_advisor.py`,
`exit_engine.py` all currently assume plain inverse-vol sizing wherever they
size a position). Per this repo's standing pattern (test → document → get
explicit adoption confirmation → wire into production, not silently), this
pre-reg documents the walk-forward finding; production wiring is a separate,
explicit step.

## Why "covariance-aware sizing" (the originally planned Study 2) is skipped

Checked memory before building anything: `risk-parity-sizing-rejected-2026-08`
already tested exactly this — a proper Ledoit-Wolf-shrunk-correlation,
equal-risk-contribution sizing scheme (`backtest_portfolio.risk_parity_weights`,
still in the codebase as a research handle), swept across 3 shrink levels x 2
correlation windows, full 19-window walk-forward. Result: a wash (within
+-0.3pp CAGR, +-0.01 Sharpe of inverse-vol, 8-12/19 windows — well under the
significance bar). Root cause verified: `MAX_PER_SECTOR=2` already does the
decorrelation work a covariance-aware scheme would try to do, so there's no
residual correlation structure left to exploit. Re-running this would
re-litigate a closed, well-diagnosed decision — the memory's own methodology
note says as much. Redirecting Study 2 instead.

## The actual gap: sizing ignores score MAGNITUDE

Production sizing is `1/vol_63`, `MAX_WEIGHT`-capped, renormalized —
completely blind to how strong the momentum signal is. A name scoring 55
(strong, fresh breakout) and a name scoring 21 (barely cleared the
eligibility gate) with similar `vol_63` get statistically the same weight
logic. This is a genuinely different question from the two already-rejected
sizing studies:
- risk-parity asked "does correlation BETWEEN held names matter for sizing"
  — rejected, no residual correlation structure.
- vol-targeting asked "should TOTAL exposure scale with trailing vol" —
  rejected, mistimes both sides of drawdowns.
- This asks "within the already-selected top-N, should the STRONGEST
  scorer get more capital than the weakest" — never tested.

## Hypothesis and mechanism

**H1 — conviction-tilted inverse-vol sizing improves risk-adjusted return.**
Mechanism: momentum_score's magnitude is not just a selection threshold, it's
a continuous measure of trend strength (ret_6m/vol_63). If the score has real
information content beyond "top-N or not" (which it must, since it's what
ranks the top-N in the first place), tilting capital toward the highest-
conviction names within the book should improve returns — same logic as why
the score ranks names for SELECTION, applied to SIZING too. This is not a new
signal — it's using an existing, already-validated signal's magnitude, not
just its rank, which is arguably a smaller extrapolation than either
already-rejected sizing study (both introduced NEW information — correlation
matrices, vol-target levels — this uses information the strategy already
computes and currently discards after the top-N cut).

**Caution going in (documented before testing, not after)**: the vol-target
rejection's root cause was "de-risking off a lagging signal mistimes both
sides." Conviction sizing is a different mechanism (no time-lag scaling, pure
cross-sectional tilt at the SAME rebalance point selection already happens),
but the general risk that "a plausible mechanism doesn't survive contact with
walk-forward data" applies equally here — 2 out of 2 prior sizing/exposure
ideas in this family failed. Prior probability of success should be treated
as LOW going in, not "this one is different so it'll work."

## Design (frozen)

`conviction_weights(scores, vols, names, tilt)`:
```python
def conviction_weights(scores, vols, names, tilt):
    """tilt in [0, 1]: 0 = pure inverse-vol (production), 1 = pure
    score-proportional (ignores vol entirely). Blends the two:
        raw_weight[s] = (1/vol[s])^(1-tilt) * score[s]^tilt
    then MAX_WEIGHT-capped and renormalized exactly as production does.
    score[s] must be > 0 for all s (guaranteed: momentum_score's eligibility
    gate already requires positive ret_6m, and score = ret_6m/vol so it's
    positive whenever ret_6m is)."""
```
Swept at tilt in {0.25, 0.5, 0.75} — 3 configs, not a continuous sweep, same
bounded-family discipline as the trend-quality study. tilt=1.0 (pure
score-proportional, dropping vol entirely) deliberately excluded: it would
abandon the inverse-vol risk control entirely, which is a much bigger
structural change than "tilt toward conviction" and not what this hypothesis
is actually testing.

Implemented via the EXISTING `sizing_fn` hook on
`run_backtest_laggards_only` (already used by the risk-parity study — no new
hook needed, `sizing_fn: callable(matrix, i, top, vols) -> weights` already
has access to `top`'s scores via closure over the score dict computed earlier
in that rebalance).

## Decision rule (identical structure to the trend-quality study, for consistency)

Adopt only if, on the same 19-window walk-forward:
1. Mean CAGR delta's paired block-bootstrap 95% CI excludes zero (positive
   side), AND
2. Wins in >=12/19 windows, AND
3. Max drawdown does not worsen by more than 2pp mean across windows.

3 configs at nominal a=0.05: ~14% chance of a false positive by chance alone
— the bootstrap CI is the primary defense, consistent with every other study
in this repo.

## What would NOT be adopted even if statistically significant

- Improvement concentrated in <3 of 19 windows (same "one cell doing all the
  work" trap flagged in the S/R improvement batch).
- Improvement that only shows up in BULL windows — a conviction tilt that
  only helps when momentum is already working isn't diversifying anything,
  it's leverage on the same bet (same objection as documented for a
  regime-stacked second factor in PREREG_trend_quality_factor.md).

## Implementation plan (not yet built)

Add `conviction_weights()` to `backtest_portfolio.py` near
`risk_parity_weights()`. Wire as a `sizing_fn` in the research script,
mirroring `research_statistical_hygiene.py`'s / the risk-parity study's
paired-bootstrap pattern exactly, for direct comparability.
