# PREREG — is the P(touch) table miscalibrated in DISTANCE?

Written **before** looking at any result from the wide panel. Registered
2026-08-15.

## Background / why this is not a re-opening of a closed question

`sr-model-sweep-exhausted-2026-08` closed the search for a better S/R model
on **OOS correlation** — i.e. ranking quality. An oracle table fitted on the
holdout beat production by +0.008 corr, so bucket/threshold retuning is
chasing noise.

This study asks a **different property**: *calibration*. Does a cell that
says 14.6% actually come true 14.6% of the time? A table can rank correctly
and still be systematically wrong in absolute level; correlation is invariant
to a monotone distortion, so `oos_corr = 0.529` says nothing about this.
Both claims can hold simultaneously. This is explicitly NOT a licence to
retune bucket geometry.

## Motivating (unvalidated) observation

A single 4-date holdout suggested the table is too STEEP in distance —
near levels overstated, far levels understated, in every vol bucket
(0-2% delta −6.1/−7.0/−22.8/−14.4pp; 8-12% delta +7.5/+4.3/+9.1/+4.2pp;
11/24 cells outside a Wilson CI). That result is **not trusted**: 4 test
dates, and ~500 symbols sharing one date are not independent observations,
so those CIs are far too narrow. See memory
`sr-touch-table-distance-calibration-2026-08`.

A separate hypothesis — that the thin `<25%` vol column (large caps) is
miscalibrated — was tested and **REJECTED** in that same run (best corr of
the four columns, 0.595; zero fallback cells). It is not re-tested here.

## Design

- **Panel**: `sr_build_touchtable.collect()` verbatim, `TEST_MONTHS = 72`
  (was 12) → ~72 monthly test dates. Nothing about the touch test, bucket
  geometry, complete-window requirement or wrong-side filtering is changed.
- **Walk-forward, expanding window**: for each test date `d` after an initial
  24-date burn-in, build the table from observations with `td < d` only and
  predict on `td == d`. No look-ahead at any point.
- **Unit of observation is the TEST DATE, not the row.** For each date and
  each distance bucket, record `error = mean(actual) − mean(predicted)`.
  This is the fix for the cross-sectional correlation that made the first
  run's CIs dishonest — same family as the Lo-2002 adjustment already applied
  to Sharpe here (`statistical-hygiene-2026-07`).

## Pre-registered decision rule

A distance bucket is declared **MISCALIBRATED** only if BOTH hold:

1. the 95% bootstrap CI of the date-level mean error **excludes zero**, and
2. the error carries the **same sign in ≥70% of dates**.

Both are required. Criterion 1 alone can be driven by a few extreme dates;
criterion 2 alone can hold with a trivially small effect. This mirrors the
conviction-sizing bar (CI excludes zero AND wins a majority of windows).

## Committed in advance

- If **no** bucket clears both criteria: the first result was a regime
  artifact. Record as rejected, change nothing, do not retune.
- If buckets clear: this is evidence of a calibration bias, and the finding
  is **still not** a licence to hand-edit cell probabilities. The only
  defensible follow-up is a mechanism (why would near levels be overstated?)
  plus a separate validation. Recalibration without a mechanism is exactly
  the curve-fitting the ceiling analysis warns about.
- Adverse checks to run before believing any positive result: does the effect
  concentrate in a few dates (per-date breakdown)? Does it flip sign between
  the early and late half of the panel? Is it explained by realized-vol
  regime rather than distance?

## What would falsify the motivating observation

A distance-error profile that is flat, sign-inconsistent across dates, or
concentrated in the 2026-03..06 stretch specifically.

---

# RESULT (2026-08-15) — REJECTED, 0/6 buckets miscalibrated

Panel: 60,305 obs, 74 test dates 2020-05-31..2026-06-30, 50 evaluation dates
after burn-in. `research_sr_touch_calibration.py`.

| bucket | dates | mean err | 95% CI | same-sign | verdict |
|---|---|---|---|---|---|
| 0-2% | 50 | −0.8pp | [−2.9,+0.8] | 62% | — |
| 2-4% | 50 | −0.3pp | [−2.9,+2.3] | 50% | — |
| 4-6% | 50 | −0.7pp | [−3.5,+2.2] | 50% | — |
| 6-8% | 50 | +1.0pp | [−1.7,+3.8] | 58% | — |
| 8-12% | 50 | −1.3pp | [−4.1,+1.7] | 56% | — |
| 12%+ | 50 | +0.1pp | [−1.3,+1.7] | 62% | — |

Every CI includes zero (criterion 1 fails for all six); sign consistency
50-62% against the 70% bar (criterion 2 also fails for all six). Adverse
checks confirm rather than rescue: 3/6 buckets FLIP SIGN between panel halves
(2-4%, 4-6%, 12%+), and trimming the 3 most extreme dates leaves every effect
"GONE". Max mean error ±1.3pp on a table quoting 5-95%.

**Conclusion.** The table is well calibrated in distance. The motivating
observation was exactly what the falsification clause anticipated — a regime
artifact of the 2026-03..06 window, amplified by row-level CIs that assumed
an independence the data does not have. Per the commitments above: nothing
changes, and this line is closed.

**Carry-forward.** Use DATES (or months) as the unit for any future S/R
calibration work, never rows — the row-level version of this same test
reported a large, confident, and entirely spurious effect.
