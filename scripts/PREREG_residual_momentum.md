# PRE-REGISTRATION — residual (idiosyncratic) momentum as the ranking score

**Written 2026-09-02, BEFORE running the test. Frozen. Amendments appended
with reason, never silently edited.**

## RESULT (2026-09-02) — 0/12 REJECTED. Line CLOSED.

3 configs x 4 rebalance phases (0/5/10/15), 36 windows each. **Not one
config passed on any phase.**

| config | phase 0 | phase 5 | phase 10 | phase 15 |
|---|---|---|---|---|
| resid_replace | -1.95% | -0.78% | -0.21% | -0.16% |
| resid_blend_50 | -0.92% | -1.30% | **+0.21%** | -0.81% |
| resid_tiebreak_10 | **-2.00%** * | -0.09% | -0.04% | **-1.41%** * |

`*` = 95% CI **excludes zero in the UNFAVOURABLE direction** (phase 0
[-3.31%,-0.67%], phase 15 [-2.81%,-0.33%]). Every other CI spans zero.

**The feasibility gate called it correctly.** Corr 0.864 was higher than
trend-quality's 0.77, and trend-quality was rejected for exactly that reason.
The counter-argument — that top-3/top-4 overlap is only 1/3 and 2/4, so the
books differ where the strategy trades — turned out to be true but
irrelevant: the books differ, and the difference is not an improvement.

**THE PHASE GATE EARNED ITS KEEP ON ITS FIRST USE.** `resid_blend_50` ranges
from -1.30% to +0.21% across phases of the SAME config. A single-phase test
landing on phase 10 would have reported a mild positive and invited a
follow-up study; three other phases say otherwise. This is precisely the
noise band `research_timing_luck.py` predicted, and it is why the gate is now
mandatory for anything measuring an effect under ~3pp.

**One honest observation, recorded NOT adopted:** `resid_replace` improved
mean drawdown on all four phases (-1.27pp to -1.61pp), consistently. The
pre-registered rule is on RETURN, return is negative, and a drawdown-only win
is not an adoption — the same call made for `absolute_0.20` in
PREREG_max_weight_cap.md. Recorded so a future risk-focused study can find it.

Per this document, the line is CLOSED. This is the **9th** consecutive
rejection of a change to what the strategy ranks on, and the pattern is now
very well established: the only thing that has ever worked here used
information the strategy already computed, applied to SIZING rather than
selection.

## Hypothesis and why it is not a re-run of a closed line

Production ranks on `momentum_score` = 126-day TOTAL return / 63-day vol.
A total return mixes the stock's own move with whatever the market did to it
through its beta. Blitz, Huij & Martens (2011, *Journal of Empirical Finance*,
SSRN 2319861) rank instead on the RESIDUAL of a factor regression and report
risk-adjusted profits roughly **twice** those of total-return momentum; the
mechanism they give is that conventional momentum carries large, time-varying
factor exposures that residualising removes.

**This is deliberately NOT another auxiliary overlay.** Eight prior studies in
this repo failed with the same shape — take the existing score and add/veto
with an external series (delivery%, OI/PCR, announcements x2, trailing stops
x3, exit flow-decay, Fib+StochRSI). This changes HOW THE EXISTING SCORE IS
COMPUTED from data already on disk (prices + `nifty50.csv`), which is the one
pattern that has worked here: conviction sizing, the single adoption in the
last eight studies, also used only information the strategy already computed.

**Feasibility gate already run (2026-09-02), reported here before the test so
it cannot be quoted selectively afterwards.** Market-model residual momentum
vs production score on the current 200-name universe, 60 names scored by both:

- Pearson corr **0.864**, Spearman **0.835**.
- Top-10 overlap **9/10** — nearly identical at wide n.
- Top-3 overlap **1/3**, top-4 overlap **2/4** — materially different books at
  the production n (SIDEWAYS=3, BEAR=4).

**This cuts against the hypothesis and is the main reason to expect failure.**
Trend-quality correlated 0.77 with the score and was REJECTED as too collinear
to add anything; this is MORE collinear. The counter-argument — the only one —
is that at n=3 a small rank change swaps most of the book, so collinearity
across the whole cross-section does not imply the same selection where the
strategy actually trades. That is an argument for testing it, not for
believing it.

## Construction (fixed before running)

For each symbol at each rebalance date, using only data up to that bar:
1. Estimate `r_s = alpha + beta * r_m + e` by OLS on **756 trading days
   (~3y)** of daily log returns against Nifty 50. 3y matches Blitz et al.'s
   estimation window. Require >= 500 valid observations or the name is
   ineligible (same spirit as `momentum_score` returning None).
2. Residual momentum = `sum(e) over LOOKBACK` / `(sd(e) * sqrt(LOOKBACK))` —
   the standardised residual return, i.e. the residual analogue of the
   production ret/vol construction.
3. Keep production's eligibility gate unchanged (50DMA, positive momentum) so
   this test changes the RANKING only, not what is admissible. Changing both
   at once would make a result unattributable.

Only the market factor is used. Size/value/profitability factors are NOT
available: yfinance has no point-in-time fundamentals, which is exactly why
value/quality was ruled out in `PREREG_trend_quality_factor.md`. So this is a
CAPM-residual test, a weaker form of Blitz et al.'s FF3 version, and a null
result does not refute their finding — only the version buildable here.

## Configs (fixed; no additions after seeing results)

1. `resid_replace` — residual momentum REPLACES the ranking score.
2. `resid_blend_50` — rank-average of production score and residual momentum.
3. `resid_tiebreak_10` — production ranking, with residual momentum reordering
   names whose scores are within 10% of each other. (Prior studies found
   tiebreak-only consistently beat full blending; included for that reason.)

## Decision rule (fixed in advance)

Standard harness: `walk_forward.make_windows(3y, 3mo)`, paired block bootstrap,
`BLOCK_LEN=6`, `N_BOOT=2000`, `SEED=42`, baseline
`run_backtest_laggards_only` with an EXPLICIT engine argument.

**ADOPT only if ALL of:** bootstrap 95% CI on mean annual-return delta excludes
zero; wins >= 12/N windows; mean max drawdown does not worsen by >2pp.

**PHASE ROBUSTNESS IS AN ADDITIONAL GATE, new to this study.** Rebalance timing
luck was measured 2026-09-02 at ~9pp of full-panel CAGR spread — larger than
every effect this repo has adopted or rejected. A 1-2pp result on ONE phase is
therefore uninterpretable. Any config passing the bar above must ALSO be
re-tested on >= 3 additional rebalance phases and keep a positive mean delta on
each. A config that passes on one phase and fails on others is REJECTED, and
recorded as evidence that single-phase results at this effect size are noise.

**If 0/3 clear, the line CLOSES.** Not a prompt to try FF3-style extra factors
(unavailable), a different estimation window, or a different standardisation.

## How this could mislead me

- **Collinearity says expect failure.** 0.864 is high. If it passes, ask
  immediately whether the gain is real or a phase artifact — hence the phase gate.
- **The estimation window costs history.** Requiring 756 prior days shrinks the
  eligible universe early in the panel and for recent listings. If the
  candidate wins, check it is not winning by silently trading a different
  (smaller, more seasoned) universe than the baseline — compare eligible-name
  counts per rebalance before believing any delta.
- **Beta estimated on daily returns is noisy** for illiquid names; the F&O
  liquidity gate mitigates but does not remove this.
