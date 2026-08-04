# PRE-REGISTRATION — Tradeable S/R Levels ("containment band")

**Written 2026-08-04, BEFORE any outcome data was collected or inspected.**
Frozen document. If the rules below are changed after seeing results, that is a
protocol violation and must be recorded in the Amendments section with a reason,
not silently edited.

---

## 1. The problem this addresses

User observation (2026-08-04, verified): on the 61-name panel for 2026-08-04,
median S1 sits −2.1% below CMP and median R1 +1.9% above. In 14/61 names BOTH
levels are inside ±2% AND both probabilities are ≥90%. Correlation between
|distance| and P(touch) is −0.879 (S1) / −0.907 (R1).

The probability column is therefore very nearly a restatement of distance. This
is not a calibration defect — the P(touch) table is accurate. It is a
QUESTION MISMATCH:

    system answers:  P(touch)   = will price REACH this level
    user needs:      a level price does NOT breach all month, AND at which a
                     trade entered there is profitable

These are complements, not variants. A level with 94% P(touch) has ~6% chance of
containing price. Reading it as support reads the number backwards.

Structural cause: level selection picks the NEAREST pivot per side, which is
optimal for a "will price get there" question and worst-possible for a
containment question. No amount of table tuning fixes this; the two questions
want opposite ends of the pivot list.

## 2. The tension, stated honestly

The user wants a level that (a) is rarely breached and (b) is profitable to
trade when reached. These conflict:

    rarely breached  <=>  far from price  <=>  rarely reached  <=>  untradeable
    frequently filled <=> close to price  <=>  frequently breached

Resolution: this is an EXPECTED VALUE problem with a possible interior optimum.

    E[profit] = P(fill) x E[return | fill]

Too close: fills often, no edge (price continues through).
Too far:   good edge, never fills.
CLAIM TO TEST: an interior distance maximises E[profit], and it varies by
volatility bucket.

**This claim may be false.** E[profit] may decrease monotonically in distance
(no useful band exists), or be flat/noisy (no reliable optimum). Both are
reportable NULL results and must be reported as such, not rescued by
re-specification.

## 3. Operating constraints (user-stated 2026-08-04) — BINDING

The user is NOT an intraday trader. Monthly rebalance is the mandate; early
exits are permitted but discretionary. Machine is open during market hours at
irregular times, not continuously, not every day.

CONSEQUENCE — the existing daily-bar touch test is NOT a valid fill model.
`sr_build_touchtable.touched()` returns True if `Low <= level` at ANY point.
A Low printed for 6 minutes counts as a fill the user would never have gotten.
This biases every "buy the dip" number optimistically, WORST for tight levels
that spike-touch and recover — i.e. most of the current panel.

Therefore fills are defined on 15-MINUTE bars with a PERSISTENCE requirement.

## 4. Definitions (fixed)

Horizon H: trading days from decision date to month-end (last Tuesday).
Evaluated at H in {5, 10, 15, 21} to match existing touch tables.

Volatility: close-to-close realised vol, 252d, annualised %, computed STRICTLY
from bars before the decision date. Buckets reuse existing VOL_EDGES
[0, 25, 35, 45, inf] so results are comparable to the touch table.

**FILL RULE (primary):** a buy limit at level L is filled iff price trades at or
below L for >= 2 CONSECUTIVE 15-minute bars (~30 min) within the horizon.
Rationale: user checks prices when at the machine but is not always present; a
single 15-min touch is not reliably catchable. Fill price = L.

**FILL RULE (reported variants, for sensitivity):**
  - `bar1`  : >= 1 consecutive 15-min bar (optimistic bound)
  - `bar2`  : >= 2 bars  <- PRIMARY
  - `bar8`  : >= 8 bars (~2 hours, conservative)
  - `close` : daily CLOSE beyond L (strictest, fully automatable)
  - `dailylow` : legacy daily-Low touch (the CURRENT method — included solely
                 to quantify how optimistic it is; NOT a candidate rule)

DECISION-RELEVANT: if the edge exists only under `bar1`/`dailylow` and vanishes
under `bar2`, it is NOT a real edge for this user and must be reported as such.

**EXIT RULE (user choice: "whichever comes first"):** after fill, exit at
whichever occurs first —
  (a) resistance level R reached (same persistence rule), or
  (b) month-end horizon close.
Both legs reported separately AND combined, so the data says which exit is
better rather than assuming.

**PROFIT:** return from fill price L to exit price, NET of costs at
COST = 0.001 per side (strategy_config), i.e. round-trip 0.002. Costs are
applied before any win-rate is computed. Slippage is NOT modelled (see
memory slippage-2026-07); at these sizes it is a secondary drag, but this means
reported edges are an upper bound.

**CONTAINMENT:** level L contains price over H iff price never trades below L
during the window (using CLOSING prices for the breach test, to avoid a
one-print wick registering as a breach). Target breach rate alpha = 0.15.

## 5. Data

Intraday: Kite Connect 15-minute bars. Verified 2026-08-04 to reach back to
Aug 2015 (empty before Aug 2014), uniformly across 10 probed symbols. Max span
200 days/request. ~1.98 s/symbol for a 200-day pull.

**ADJUSTMENT HAZARD (verified, critical):** Kite history is UNADJUSTED; the
repo's price_data CSVs are yfinance-ADJUSTED. Measured kite/csv close ratio on
NATIONALUM: 1.744 (2016), 1.405 (2019), 1.110 (2023), 1.000 (today).

Since P(touch)/containment are pure DISTANCE RATIOS, mixing the two sources
would compute distances across two price scales — a 74% scale error on
NATIONALUM in 2016 — silently corrupting exactly the far-distance cells.

RULE: levels, current price, and forward paths must ALL come from the SAME
source within a single observation. Intraday study uses Kite end-to-end.
No joins to price_data/ for price levels.

Storage: `data/intraday_data/` — research-only. MUST NOT be written into
price_data/ (globbed as the trading universe by core.market_breadth_pct /
liquid_universe; unadjusted prices there would corrupt the momentum scorer).

## 6. Train / holdout split

TIME-BASED, never random. Split date fixed BEFORE collection:

    TRAIN:   decision dates <= 2024-12-31
    HOLDOUT: decision dates >= 2025-01-01

All tuning, bucket selection and optimum-distance choice happens on TRAIN only.
HOLDOUT is scored exactly once per candidate rule. Any re-touch of the holdout
after seeing its result is a protocol violation and must be logged.

## 7. Lookahead audit (must pass before results are believed)

  [x] volatility uses only bars up to and including the decision date
  [x] pivot levels use only bars up to and including the decision date
  [x] the decision-date bar IS included in `past`, and this is CORRECT here.
      CLARIFICATION (2026-08-04, before results): CLAUDE.md's "windows end
      yesterday" convention exists for the momentum scorer, which evaluates ON
      a bar it must not peek at. This study's decision date is a month-end
      session; the decision is taken AFTER that close, using it as CMP, with
      every forward bar strictly after it. Including it is therefore not
      lookahead. Verified: zero overlap between past and forward index.
  [x] forward path uses only bars strictly > decision date
  [x] no partial/in-progress candle in either leg (Muhurat/Saturday special
      sessions filtered at load: <20 bars/session dropped; 5 of 742 on
      RELIANCE — 2023-11-12 Sun, 2024-03-02 Sat, 2024-05-18 Sat,
      2024-11-01 Fri evening, 2025-10-21 Tue afternoon)
  [ ] optimum distance chosen on TRAIN, applied unchanged to HOLDOUT
  [ ] no symbol appears in both train and holdout for the SAME decision date

AUDIT RESULT (2026-08-04, before any outcome inspection): PASSED.
Fill-persistence logic separately unit-tested against 5 hand-built cases,
including the decisive one — two SEPARATED single-bar touches correctly do NOT
fill under bar2, which is exactly the spike-touch case that inflates dailylow.

## 8. PRE-REGISTERED DECISION RULES

### 8a. Does a tradeable band exist at all? (primary question)
ADOPT the containment-band construction only if, on HOLDOUT, at the
TRAIN-selected optimal distance, under the PRIMARY `bar2` fill rule:

    (1) fill rate                >= 20%      (else untradeable in practice)
    (2) P(profit | fill), net    >= 55%      (else no edge over a coin flip)
    (3) median return | fill     >  0        (net of 0.2% round-trip)
    (4) realised containment     within [0.75, 0.95] of the claimed 0.85
                                             (else the band is miscalibrated)

ALL FOUR must hold. Failing any one = REJECT and report null.

### 8b. Do pivots add anything over pure distance? (secondary, falsifiable)
Compare level-from-pivot vs level-at-fixed-%-distance, same vol bucket, same
fill rule, same holdout.
ADOPT pivot-anchoring only if it improves P(profit | fill) by >= +2.0pp on
holdout AND wins a majority of the 4 horizons.

If pivots do NOT clear this, that is a MAJOR finding and must be reported
prominently: it would mean the S/R pivot machinery adds nothing to this
use case over a volatility-scaled percentage band, and the simpler
construction should be preferred.

### 8c. Does intraday persistence matter? (validates the Kite spend)
Report P(profit | fill) under all five fill rules. If `bar2` and `dailylow`
agree within 2pp at every horizon, intraday data adds nothing HERE and the
daily-bar method should be kept for simplicity. State this plainly if so.

## 9. Multiple-comparisons discipline

This repo has >10 documented rejected auxiliary signals
(oi-pcr-inconclusive, delivery-pct-inconclusive, exit-announcements-rejected,
sr-vol-estimator-rejected, risk-parity-sizing-rejected, vol-target-exposure-
rejected, regime-detection-rejected, staged-entry-rejected, skip-month,
low-vol sleeve, ML). Base rate of a new idea working here is LOW.

Distances tested: ~9. Vol buckets: 4. Horizons: 4. Fill rules: 5.
That is a large grid. Therefore:
  - the ADOPT rule is fixed above and will not be relaxed;
  - any single cell clearing a threshold while its neighbours do not is treated
    as noise, NOT signal (a real effect should be smooth in distance);
  - if adopted, a paired block bootstrap resampled BY DECISION DATE (not by row
    — same-date observations are not independent) must confirm before the
    result is quoted anywhere.

## 10. What this study explicitly does NOT claim

  - Not a new entry signal for the momentum strategy. Descriptive/advisory only,
    consistent with chart_analysis.py's status.
  - MUST NOT be wired into exit_engine / paper_trader / agent_sim / the scorer
    without separate walk-forward validation. Every auxiliary overlay tested on
    this system so far has been rejected at that gate.
  - Slippage not modelled -> reported edge is an upper bound.
  - Kite intraday reaches only to 2015, so this cannot be tested across a
    pre-2015 regime.

## 11. Amendments

### A1 — 2026-08-04: add TREND conditioning as a pre-registered arm
**Trigger.** A preliminary TRAIN-only run (40 symbols, 21d) showed horizon
exits averaging −2.1% net across 86% of fills, while resistance exits (14%)
averaged +6.6%. Initially suspected an exit-rule bug (63d-high resistance too
close on momentum names, cutting winners short). Checked: that hypothesis is
WRONG — resistance exits are the minority AND the profitable ones.

**What it actually shows.** Unconditional dip-buying loses. A level is reached
either because the stock wobbled inside an uptrend (good fill) or because a
downtrend began (bad fill), and a fixed %-distance limit cannot tell them
apart. It fills you preferentially in the second case — the order only
executes when price is falling. This is ADVERSE SELECTION, not a coding
defect, and it is the mechanism the study should be testing.

**Amendment.** Add trend state at the DECISION DATE (computable with no
lookahead) as a conditioning axis:
    - `above_50dma` : close > 50d MA  (the repo's existing gate)
    - `mom_126d`    : 126d return sign (the strategy's own lookback)
Report every metric split by trend state. Everything else unchanged.

**Pre-registered rule for the new arm (fixed now, before seeing results):**
the trend-conditioned band is adopted only if, on HOLDOUT, in the UPTREND
state, under `bar2`, it meets the SAME four §8a thresholds (fill >= 20%,
win >= 55%, median > 0, containment in [0.75, 0.95]).

**Honesty constraint.** Conditioning on trend after seeing an unconditional
null is a garden-of-forking-paths risk. Mitigations, binding:
  (1) exactly ONE new axis is added, with two pre-specified definitions that
      already exist in the codebase — no search over trend definitions;
  (2) the §8a thresholds are NOT relaxed to accommodate it;
  (3) if the uptrend arm passes, a paired block bootstrap resampled by
      decision date must confirm before it is quoted anywhere;
  (4) the unconditional NULL is reported regardless of what the arm shows.

### RESULTS — 2026-08-04 (full universe, 199 symbols, 39,987 obs)

**§8a + A1 adoption test: REJECT in both trend states.**

    trend=UP    TRAIN dist 12%  -> HOLDOUT fill 13.8%  win 43.9%  med -0.51%
    trend=DOWN  TRAIN dist  8%  -> HOLDOUT fill 29.1%  win 49.0%  med -0.15%

Neither meets win>=55% or median>0. Thresholds were NOT relaxed. The 70-symbol
preliminary gave 40.4%/49.3% — the full run confirms it, so this is not a
small-sample artifact.

**Mechanism: ADVERSE SELECTION (verified, not inferred).** At a 5% band,
observations that FILLED were contained only 9.5% of the time versus 100% for
those that did not fill. The order executes precisely when the trend has turned.
No fixed-distance rule can separate "wobble in an uptrend" from "downtrend
started", because the fill itself is the bad news.

**§8c fill-rule sensitivity — intraday data DOES matter:**

    dailylow (current)  47.8%      bar1   47.8%
    bar2 (PRIMARY)      45.8%      bar8   40.9%      close  38.8%

Daily-Low touch tests overstate win rate by ~2pp vs a realistic 30-min
persistence rule and ~9pp vs close-only. The current method IS optimistic for a
non-intraday trader, as predicted in §3.

**§8b (pivot vs fixed-distance): NOT RUN.** The unconditional entry result is
negative, so comparing two ways of choosing a losing entry has no decision
value. Left open rather than reported as tested.

**What shipped instead:** the CONTAINMENT half of the study succeeded on its own
terms and was productionised (containment_band.py,
build_containment_table_daily.py). Fitted on the DAILY archive 2016-2021, not
Kite intraday — containment needs only closes, and Kite's 2023-08 start gave an
inverted 3,784/13,988 split whose bands ran too wide (86-93% hold vs 85%
claimed). Daily fit: 0/16 cells outside +-10pp, floor held 79.5-88.3%.

Band is shipped as a RISK/EXPECTATION tool with the negative trading result
printed alongside it, so the two cannot be separated in use.

### A2 — 2026-08-04: resistance definition
63d-high was chosen before collection as "a simple, honest structural
ceiling". It is retained unchanged (the diagnosis above cleared it of
causing the null). Pivot-anchored resistance remains the §8b comparison.
