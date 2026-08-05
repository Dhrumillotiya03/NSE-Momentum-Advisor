# PRE-REGISTRATION — trend-quality as a second scoring factor

**Written 2026-08-05, BEFORE running any test below. Frozen. Amendments
appended with reason, never silently edited.**

## RESULT (2026-08-05, research_trend_quality_factor.py) — REJECTED

36 overlapping 3y windows (walk_forward.py's default step produced more
windows than the ~19 referenced below — that number was stale from older
memory files; the price archive has grown since). Baseline: mean annual
return +28.78%, mean Sharpe 1.15.

| config | mean delta | 95% CI | P(better) | wins | DD delta | verdict |
|---|---|---|---|---|---|---|
| H1_w0.25 | +0.67% | [-0.70%,+1.87%] | 82.0% | 22/36 | -0.58% | REJECT |
| H1_w0.50 | +0.30% | [-2.03%,+2.11%] | 57.1% | 20/36 | -0.21% | REJECT |
| H1_w1.00 | -0.19% | [-1.61%,+1.35%] | 42.8% | 20/36 | -1.07% | REJECT |
| H2_tie5pct | +0.76% | [-0.26%,+1.73%] | 92.8% | 23/36 | -0.91% | REJECT |
| H2_tie10pct | +1.14% | [-0.23%,+2.31%] | 94.4% | 25/36 | -1.01% | REJECT |

**0/5 configs cleared the bar.** All CIs include zero; H2_tie10pct came
closest (CI lower bound -0.23%, just short) but per the decision rule fixed
above, a near-miss is still a rejection, not grounds to try another
threshold.

**Pattern worth noting** (informative, not a reason to reopen): tiebreak-only
reranking (H2) consistently beat full blending (H1) at comparable magnitude,
and within H1 itself, lighter weighting (0.25) beat heavier (0.50, 1.00) —
monotonically. Consistent with the pre-reg's own correlation check
(trend_quality correlates 0.77 with the existing score): most of what
trend-quality would add is already implicit in momentum_score, so touching
only near-ties preserves more of the baseline's validated ranking than a full
reweight does. This is a mechanism explanation for WHY it failed, not
evidence it would succeed with more tuning — the ceiling-analysis lesson from
the S/R work applies here too: a pattern in the rejection doesn't imply an
adoptable variant exists nearby.

**Consequence for the wider "state of the art" program (2026-08-05)**: the
originally planned Study 3 (regime-conditional FACTOR weighting — give
trend-quality more weight in choppier regimes) has nothing to condition on,
since there is no validated second factor. Folded into this rejection rather
than run separately. Regime-conditional adjustments to the EXISTING single
factor (timing/hysteresis) were already tested and rejected separately — see
memory regime-detection-rejected-2026-08.

**How to apply**: price-only "second factor" scoring is now closed alongside
price-only S/R refinement (sr-improvement-batch-exhausted-2026-08) — both hit
the same wall for the same underlying reason (the primary signal already
captures most of what a derived price-only feature could add). Value/quality
factors remain the one plausible way to add real cross-sectional variation
this system doesn't already have — but that needs a paid fundamentals data
source with real point-in-time history, not something buildable today.

## Context and why this is scoped the way it is

User asked to make the model "state of the art," explicitly opening alpha,
parameters, and portfolio construction — the only fixed constraints are
monthly rebalance cadence and signals-only execution (mandate confirmed
2026-08-05, see CLAUDE.md). Momentum is currently the only scoring factor in
production; institutional multi-factor systems typically blend it with
value, quality, or low-volatility.

**Value and quality (P/E, P/B, ROE, debt/equity) are OUT OF SCOPE for this
study.** Checked yfinance (already a dependency) for point-in-time
fundamentals: `Ticker.info` returns only a LIVE snapshot (today's P/E, not
history), and `quarterly_financials` covers just 5 quarters back
(2025-03 to 2026-06) — nowhere near this repo's ~10-year backtest window.
Building value/quality now would mean either (a) a new paid fundamentals
vendor, or (b) applying TODAY's P/E to 2015-2026 prices, which is textbook
look-ahead bias (it would show a fake edge — "cheap stocks that are cheap
today" is a different, backward-looking question, not "was this stock cheap
in 2018"). Flagged for the user as a blocked-on-data-source item, not
tested here.

**What IS buildable from data already on disk, point-in-time safe, zero new
dependency: a price-only "trend quality" factor.**

## The candidate factor and why it's not just re-deriving vol_63

Initial candidate was naive low-volatility (`-vol_63`). Checked its
relationship to the existing score first:

```
corr(momentum_score, vol_63)        = +0.199
corr(ret_6m raw, vol_63)            = +0.545
```

`ret_6m` (before dividing by vol) correlates 0.55 with volatility in this
universe — the biggest movers tend to be the choppiest. But `score =
ret_6m / vol_63` already pulls most of that out (correlation drops to
0.20). A naive low-vol factor bolted on top would substantially
double-count what the score's own denominator already does. Rejected as
the primary candidate for that reason — it's not clearly a NEW factor.

**Trend quality (signed R² of a linear fit to log-price over the
momentum lookback window)** is the alternative: it measures whether a
name's return was earned via a smooth, sustained grind versus a violent,
lumpy path (e.g., one earnings gap-up followed by chop) — two names can
have IDENTICAL total return and IDENTICAL realized volatility while one
trended steadily and the other zigzagged to the same endpoint. This is a
recognized factor in the momentum literature ("momentum quality" /
"frog-in-the-pan" effect — smooth trends persist better than jumpy ones of
the same magnitude).

Empirical check against the existing score (n=95 eligible names,
2026-08-05 snapshot):
```
corr(trend_r2, momentum_score) = 0.772
corr(trend_r2, ret_6m)         = 0.672
corr(trend_r2, vol_63)         = 0.222
```
Correlation with the existing score is high (expected: the eligibility
gate already requires positive 6m+3m momentum, so within that pre-filtered
pool strong scorers and smooth trends co-occur) but leaves real independent
variation (~40% unexplained by a linear relationship). This is the
plausible candidate — not because it's uncorrelated with momentum, but
because two names with the SAME score can have very different trend
quality, and that's the variation being tested.

**Definition** (frozen):
```python
def trend_quality(close_window):
    """Signed R^2 of log(price) vs trading-day-index over the window.
    +1 = perfect smooth uptrend, -1 = perfect smooth downtrend, ~0 = noisy/flat.
    Same window as momentum_score's ret_6m leg (LOOKBACK=126, ending
    yesterday per the existing evaluation-bar convention) so it answers
    "how was this return earned", not a different lookback."""
    y = log(close_window)  # length LOOKBACK, ending at i-1 (yesterday)
    slope, intercept = polyfit(arange(len(y)), y, degree=1)
    r2 = 1 - ss_res/ss_tot
    return r2 * sign(slope)
```
Only computed for names that already pass `momentum_score`'s eligibility
gate (positive 6m+3m momentum, above 50DMA) — this is a RE-RANKING factor
within the eligible pool, not a new entry filter. No change to WHICH names
are eligible, only to WHICH of them the strategy prefers when it can't buy
them all.

## Hypotheses (2, to bound multiple-comparisons risk)

**H1 — Blended score beats momentum-only, matched exposure.**
Mechanism: among equally-momentum'd names, the ones that got there smoothly
are less likely to be near-term exhausted/reversal-prone than ones that
spiked and are already decelerating.
Blend: `combined = zscore(momentum_score) + w * zscore(trend_quality)`,
tested at w in {0.25, 0.5, 1.0} — three weights, not a continuous sweep,
to keep this a bounded family. Selection (`select_top_n_capped`) and sizing
(inverse-vol) otherwise UNCHANGED — isolates the factor's effect from
sizing/regime logic per the "one variable at a time" standing rule.

**H2 — Trend quality as a TIEBREAK only, not a full blend.**
Softer version: keep momentum-only ranking, but when `select_top_n_capped`
would otherwise pick between names scoring within X% of each other (X in
{5%, 10%}), prefer the higher trend-quality one. Tests whether the value is
in fine reranking near the cutoff, not in reweighting the whole book.

## Decision rule (fixed before seeing results)

Adopt only if, on the walk-forward distribution (19 overlapping 3-year
windows, `walk_forward.py`, same windows as the production baseline):
1. Mean CAGR improves by the SIGN AND the paired block-bootstrap 95% CI
   (`research_statistical_hygiene.py`'s Part B method — same resampled
   blocks applied to both configs) EXCLUDES ZERO, AND
2. Wins (higher CAGR) in >=12/19 windows (matches this repo's existing
   >=14/19 "beats grid noise" convention used in param-robustness testing,
   relaxed to 12 since this is a genuinely new factor, not a retune — but
   still a clear majority), AND
3. Max drawdown does not worsen by more than 2pp mean across windows (a
   factor that trades CAGR for materially worse tail risk is not an
   improvement for this user's profile — see memory profit-oriented-preference,
   which favors CAGR/total return but still guards against blowup).

Any candidate (H1 x 3 weights, H2 x 2 thresholds = 5 configs total) that
fails ANY of the three conditions is rejected. Multiple-comparisons note:
5 configs at nominal a=0.05 gives ~23% chance of at least one false
positive by chance alone (1-0.95^5) — condition 1's bootstrap CI is the
primary defense against that, same logic as PREREG_sr_improvement_batch.md.

## What would NOT be adopted even if statistically significant

- A config that only wins in BULL-regime windows and loses in BEAR/SIDEWAYS
  — momentum's edge is regime-dependent already; stacking a second factor
  that only works when the first one is already working is not
  diversification, it's the same bet twice.
- A config whose improvement is concentrated in <3 of the 19 windows (same
  "one unbalanced cell doing all the work" trap that caught H5 in the S/R
  improvement batch — see memory sr-improvement-batch-exhausted-2026-08).

## Implementation plan (not yet built)

Add `core.trend_quality(close)` alongside `core.momentum_score` (same file,
same eligibility-gate convention). Add a `score_fn` hook to
`backtest_portfolio.run_backtest_laggards_only` mirroring the existing
`sizing_fn`/`regime_fn` pattern (default `None` = production behavior,
byte-identical) — NOT a hardcoded blend, so H1's three weights and H2's two
thresholds are all expressible through one hook without duplicating the
loop. Wire through `walk_forward.py` via `functools.partial(engine=...)`,
no changes needed to `walk_forward.py` itself.
