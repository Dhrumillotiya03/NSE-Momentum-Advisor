# PRE-REGISTRATION — Fibonacci retracement & Stochastic RSI as flow-change signals

**Written 2026-08-31, BEFORE running any test below. Frozen. Amendments
appended with reason, never silently edited.**

## Where this came from

The user asked for TradingView-style Fibonacci levels and, separately,
described a two-line oscillator (red/blue crossover) they'd seen predict a
reversal. Both were researched against TradingView's own documentation and the
academic literature before any code was written (chart_analysis.py's
`fib_retracement`/`stoch_rsi`, shipped display-only, cite the sources inline).

**What TradingView itself claims: nothing.** Its Auto Fib docs call the method
"based on the theory that markets will retrace a specific portion of a move"
and recommend using it "with other tools." Its Stochastic RSI docs warn that
"by adding the Stochastic calculation to RSI, speed is greatly increased. This
can generate many more signals and therefore more bad signals," and call
trading crossovers against trend "a dangerous proposition." TradingView sells
charting; neither tool ships with an accuracy claim.

**What the literature says**, and why the prior here is low before a single
test is run:

* Tsinaslanidis & Guijarro (2021, *Expert Systems with Applications*,
  Dow/NASDAQ/DAX): an algorithmic Fib-zone identifier built specifically to
  remove hand-drawing subjectivity found bounce probability on a Fibonacci
  zone "statistically indistinguishable from... any other non-Fibonacci
  zone." No support for Fib as a standalone rule.
* Bajgrowicz & Scaillet (2012, *Journal of Financial Economics*, Dow Jones
  1897-2011, false-discovery-rate corrected): for technical trading rules
  generally (including oscillator-style rules), "an investor would never have
  been able to select ex ante the future best-performing rules," and even
  IN-SAMPLE performance is "completely offset by the introduction of low
  transaction costs."
* This repo's own August 2026 S/R review ran the identical test — permuted-
  distance control on its OWN levels (swing pivots, volume nodes, wick
  rejection, not arithmetic fractions) — and got the same null: flow-change
  minus control +0.4pp, 95% CI [-6.9,+7.1]. See memory
  sr-levels-dont-mark-flow-changes-2026-08. Fib levels have a WEAKER
  construction (a fixed fraction of a range, no claim anything happened
  there) than the levels that already failed this exact test.
* This strategy specifically has rejected the auxiliary-overlay pattern SIX
  times across three different mechanism shapes (entry blend, continuous
  exit-overlay, discrete event-veto) and four different data sources
  (delivery%, OI/PCR, announcements, S/R levels). A crossover-confirms-a-level
  rule is a seventh instance of the same pattern before it is even tested.

None of that makes the result knowable in advance — it makes the prior low
enough that this pre-registration exists mainly to fix the decision rule
before the data can influence it, exactly as PREREG_trend_quality_factor.md
and PREREG_sr_vol_regime_interaction.md did.

## What will be tested

Reusing the exact harness built for the August 2026 S/R review
(`sr_monthend_analysis.analyse_acted_outcomes`'s `_race` function and its
permutation-control method), applied to the SAME quantity the user asked
about: reached the level, then changed flow (moved FLOW_THRESH=2% the
favourable way before the adverse way, race starting the bar AFTER the touch
to avoid the same-bar head-start bias documented in that review).

Three configs, fixed now, no others added later:

1. **FIB-ALONE** — `chart_analysis.fib_retracement`'s 5 levels
   (23.6/38.2/50/61.8/78.6%), tested as standalone reversal levels exactly
   like S1/R1 were in the August review, against a permuted-distance control
   built the SAME way (distances shuffled across symbols sharing a date —
   NOT reconstructed from the level's own distance, which was a caught bug
   in the August review that reproduces the level exactly and reports a
   fake 0.0 difference).
2. **STOCHRSI-ALONE** — does a %K/%D bullish cross predict a subsequent N-day
   (N=5, matching the Stoch RSI's own 14/3/3 periods) upward flow-change in
   price, and symmetrically for a bearish cross and downward flow, tested
   against a control of RANDOM cross dates drawn from the same symbol's own
   history (matches base rate of "any 5-day window," isolates whether the
   CROSS specifically carries information).
3. **COMBINED** — FIB level reached AND a same-direction StochRSI cross
   within +/-2 sessions of the touch. Lowest prior of the three: it is a
   two-signal AND-conjunction, which multiplies the false-positive-control
   burden, and it is the exact "auxiliary confirms a level" shape already
   rejected six times on this strategy's exit side.

Universe: the F&O-liquid ~200-name universe (core.liquid_universe), full
price archive, NOT limited to one month — unlike the August S/R review (which
had no choice, being a fresh subsystem), this test has ~11 years of history
available and must use it. A one-month result on either of these would be
exactly the single-window trap this repo's own statistical-hygiene memory
warns against.

## Decision rule (fixed before seeing results)

Adopt (meaning: promote from display-only toward validated-tool status, NOT
an automatic license to wire into exit_engine/scoring — that requires the
strategy's own walk-forward bar, a separate and higher step) a config only if
**all four** hold:

1. Flow-change-minus-control clears a 95% block-bootstrap CI (resampling
   DATES, not rows, per the August review's own lesson) that **excludes
   zero**, in the favourable direction.
2. The edge is **at least 5 percentage points** — matching the
   `PREREG_tradeable_levels.md` bar (55% vs a ~50% base, i.e. this repo's
   established threshold for "worth trading," not a new easier one invented
   for this study).
3. Holds on an **out-of-sample split** — fit/read any threshold choice on
   the first 70% of history, confirm on the held-out last 30%. A result that
   only appears in-sample is exactly what Bajgrowicz & Scaillet found evaporates.
4. Transaction costs (COST=0.001/side, matching production) do not erase it.
   This is the single factor named in BOTH cited papers as decisive against
   crossover/reversal rules, so it is checked explicitly, not folded into a
   vaguer "walk-forward Sharpe" number that could hide it.

If 0 of 3 configs clear: this line of work CLOSES, recorded as the 7th
confirmation of the auxiliary-overlay pattern, and `chart_analysis.py`'s two
functions remain permanently display-only with their existing caveats (no
further action needed — they already carry the negative-prior framing).

If COMBINED clears but neither standalone config does: treat as likely noise
from the extra degree of freedom (two thresholds, two mechanisms) rather than
a real interaction, per the "a pattern in the rejection doesn't imply an
adoptable variant exists nearby" lesson from the S/R ceiling analysis — do
not chase it with more combined-config variants.

## What must NOT be done regardless of outcome

* **No wiring into exit_engine/paper_trader/agent_sim/the scorer** even if
  all four criteria clear here. That step needs this strategy's own
  walk-forward bar (pre-registered rule, block-bootstrap Sharpe/CAGR CI,
  wins a majority of windows) — the SAME bar every one of the six prior
  auxiliary-overlay attempts was held to and failed. Clearing THIS study's
  bar only justifies building that second, harder study.
* **No retuning the Fib ratios or Stoch RSI periods off this test's own
  data.** They are fixed at TradingView's own defaults specifically so this
  measures the tool as actually used, not a curve-fit variant.
