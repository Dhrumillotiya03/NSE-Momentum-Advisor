# PRE-REGISTRATION — the MAX_WEIGHT cap is infeasible at low n

**Written 2026-09-01, BEFORE running any test below. Frozen. Amendments
appended with reason, never silently edited.**

## RESULT (2026-09-01) — 0/3 CLEARED. NO ADOPTION. Documentation fix only.

36 overlapping 3y windows. Baseline (production: cap 0.20,
clip-then-renormalize, tilt 0.50) mean annual return **+31.76%**, mean Sharpe
1.20, mean maxDD 21.89%, worst-window maxDD 36.02%.

| config | mean delta | 95% CI | wins | meanDD | worstDD | verdict |
|---|---|---|---|---|---|---|
| cap_0.35 | +0.59% | [+0.09%,+1.39%] | 22/36 | -0.18% | -0.71% | **REJECT** (wins 22 < 22.74) |
| cap_0.30 | +0.17% | [-0.05%,+0.57%] | 21/36 | -0.00% | -0.25% | REJECT (CI spans 0) |
| absolute_0.20 | -3.56% | [-8.25%,+0.17%] | 10/36 | -5.19% | -5.09% | REJECT (loses return) |

`cap_0.35` is genuinely marginal — its CI excludes zero, and it fails only the
win-count condition, by 0.74 of a window. **Per this document's own rule that
is a REJECT, and this line of work closes.** It is not a threshold to retune,
and I am not adding a fourth config.

### THE FIRST RUN OF THIS STUDY WAS WRONG AND SAID ADOPT

The first pass reported cap_0.35 at **+3.45%, CI [+2.44%,+4.68%], 34/36** —
2/3 configs clearing the bar including the stricter worst-DD gate. That result
was an artifact of a bug in the harness, not in this hypothesis:
`walk_forward.run_window` defaulted to `run_backtest`, the LEGACY hard-close
engine, so the BASELINE ran a different engine from the candidates. Caught by
adversarial check [A], which ran one configuration down two code paths
(`sizing_fn=None` vs an explicit tilt-0.50 `sizing_fn`) and got +28.90% vs
+31.76% for what must be identical runs. `engine` is now a required argument;
see memory walk-forward-baseline-engine-2026-09 for the three other studies
that shared the defect.

**Worth stating plainly: without the adversarial checklist this study would
have shipped a production change off a bug.**

### WHAT THE DECOMPOSITION ACTUALLY SHOWS (check [A], corrected)

All four cells on the production engine:

| | tilt 0.00 | tilt 0.50 |
|---|---|---|
| **cap 0.20 (production)** | 29.91% | **31.76%** |
| **cap 0.35** | 28.72% | 32.35% |

- Raising the cap **alone**, with no tilt to unblock: **-1.19%**, CI
  [-2.42%,-0.28%], 9/36. Concentration by itself is a **drag**, not a benefit.
- The tilt at today's cap: +1.85%, CI [+0.54%,+3.17%], 26/36 — this is the
  corrected conviction-sizing result, and it is positive **despite** being
  blocked in 73% of rebalances.
- The tilt with headroom: +3.63%, CI [+1.64%,+5.59%], 30/36 — roughly double.

So the cap change buys ~1.8pp of extra tilt benefit and pays ~1.2pp of
concentration cost, netting +0.59pp. **The tilt is the mechanism; raising the
cap is the wrong lever to get more of it**, because it necessarily buys
concentration at the same time. A future attempt should look for a way to let
the tilt act *without* widening the maximum weight — e.g. tilting the number
of names or the deployed fraction rather than the per-name cap. That is a
different hypothesis and needs its own pre-registration.

### The other checks

- **[C]** "cap 0.35" is not a 35% cap: 1/n is already 33.3% (SIDEWAYS) and
  25.0% (BEAR), so in those regimes it is effectively **no cap**. Worst
  single-name weight anywhere rises **33.3% -> 45.0%**. That is a great deal
  of concentration to buy +0.59%.
- **[B]** Late-era-only: +0.57%, CI [+0.03%,+1.30%], 13/18 — consistent with
  the full sample, still marginal. Not an era artifact either way.
- **[E]** Worst single-window CAGR improves (-2.27% -> -1.18%), and negative
  windows fall 3 -> 2. Real, but it does not rescue the win-count condition
  and I am not going to let a check that was not a pre-registered gate become
  one after the fact.
- **absolute_0.20** is the measured price of honouring the documented cap:
  about **-3.6pp CAGR for -5.1pp of worst-case drawdown**. Recorded as a
  risk/return trade-off for the user to decide on, explicitly NOT adopted by
  this rule — the prereg said in advance that a drawdown-only win is not an
  adoption.

### The documentation fix (the actual deliverable)

Production behaviour is unchanged. `recommend.py` no longer tells the user
"live sizing is inverse-vol with a 20% cap" — wrong twice over, since sizing
became conviction-weighted on 2026-08-05 and the cap does not bind at n=3/n=4.
It now states, at the book's actual size, that the cap binds on every name and
renormalizes back to 1/n. `strategy_config.py` and
`backtest_portfolio.py`'s sizing block say the same.

## The finding that prompted this (measured, not hypothesised)

`MAX_WEIGHT = 0.20` is applied as clip-then-renormalise, in four independent
places (`backtest_portfolio.py:652`, `paper_trader.py:250`,
`ai_assistant.py:628`, and described to the user in `recommend.py:113`):

```python
w = {s: min(v, sc.MAX_WEIGHT) for s, v in raw_w.items()}
w = {s: v / sum(w.values()) for s, v in w.items()}
```

When `1/n >= MAX_WEIGHT` **every** name clips, and renormalising n identical
values returns exactly `1/n`. The cap is then arithmetically infeasible — you
cannot have 3 names each at or below 20% summing to 100% — and the
renormalisation silently pushes the weights back *above* the cap it just
applied.

Measured over every 21-bar rebalance in the current panel (126 rebalances,
`i` stepping from 252):

| regime | n | equal wt | rebalances fully clipped | mean final wt spread | mean raw (pre-cap) spread |
|---|---|---|---|---|---|
| SIDEWAYS | 3 | 33.3% | **62/62 (100%)** | 0.0000 | 0.1068 |
| BEAR | 4 | 25.0% | **18/30 (60%)** | 0.0107 | 0.1032 |
| BULL | 10 | 10.0% | 0/34 | 0.0599 | 0.0599 |

**63.5% of all rebalances are exactly equal-weighted.** Two consequences,
both previously undocumented:

1. **`CONVICTION_TILT = 0.50` is inert in SIDEWAYS and mostly inert in
   BEAR.** The +2.89% mean CAGR in `PREREG_conviction_sizing.md` is real —
   the walk-forward measured production *including* this clip — but it is a
   **BULL-regime-only effect**. The live paper book has been BEAR since
   inception (2026-07-10) and has therefore never once used the change this
   repo adopted in August.
2. **The realised single-name cap is 33.3% (SIDEWAYS) and 25% (BEAR), not
   20%.** `concentration-risk-2026-07` diagnosed exactly this mechanism at
   BEAR=2 and moved to BEAR=4; `strategy_config.py:130` records the fix as
   complete ("don't lower back to 2 without re-addressing the cap-defeat
   finding"). It was not complete — 4 x 20% = 80% < 100%, so the cap is
   still infeasible, just less severely.

Note check #3 in `PREREG_conviction_sizing.md` asserted "confirming the cap
is actively engaging in the real backtest, not dead code". That check ran on
a **synthetic 5-name** example, where 1/n = 20% and the cap is exactly
feasible. It does not hold at the n=3 and n=4 the strategy actually uses in
73% of rebalances. This is the specific reason the study is being re-opened.

## What is NOT in question

The adopted results stand as measured. SIDEWAYS=3, BEAR=4 and
CONVICTION_TILT=0.50 were all walk-forwarded *with* this clipping behaviour
in place, so their reported deltas are honest descriptions of production. The
question here is not "were those studies wrong" but "the cap does not do what
three files claim it does — is the current behaviour the one we want, and
what does the alternative cost?"

## Hypotheses (fixed before running; no configs added afterwards)

Baseline = production exactly as shipped (MAX_WEIGHT=0.20,
clip-then-renormalise, CONVICTION_TILT=0.50).

- **H1 — `max_weight = 0.35`.** Raw conviction spread is ~0.10, so a 0.35 cap
  is non-binding for most names at n=3 and n=4: the adopted tilt is
  *unblocked* in the two regimes where it currently does nothing. Mechanism:
  the +2.89% CAGR effect already validated in BULL should extend to the other
  73% of rebalances. Directly increases single-name concentration, which is
  why the drawdown conditions below are hard gates, not soft ones.
- **H2 — `max_weight = 0.30`.** The milder version of H1, to see whether any
  effect is monotonic in cap headroom (the same shape check that made
  conviction sizing credible) rather than a single lucky point.
- **H3 — absolute cap, residual to cash.** The cap means what it says: no
  name exceeds 20% of the momentum sleeve, and the un-allocable remainder
  (40% at n=3, 20% at n=4) stays in cash earning `CASH_YIELD`. This is the
  only variant that actually enforces the documented risk control. It should
  *lower* return (less deployed) and *lower* drawdown; it is included so the
  cost of honouring the stated cap is a number rather than an assumption.

Nothing here re-tunes `REGIME_NAMES`. Raising n until the cap becomes
feasible would be tuning a validated parameter to rescue an unrelated
constraint, and both SIDEWAYS=3 and BEAR=4 have their own walk-forward
evidence.

## Decision rule (fixed in advance)

Walk-forward, standard harness (`walk_forward.make_windows` / `run_window`,
paired block bootstrap, `BLOCK_LEN=6`, `N_BOOT=2000`, `SEED=42`) — the same
harness and rule as conviction sizing, trend-quality and exit-flow signals.

**ADOPT a config only if ALL of:**
1. paired-bootstrap 95% CI on mean annual-return delta **fully excludes
   zero**, and
2. it wins in **>= 12/N** windows, and
3. **mean** max drawdown does not worsen by more than **2pp**, and
4. **worst-case window drawdown does not worsen at all.**

Condition 4 is an addition to the standard bar, and it is deliberate: H1/H2
are explicitly concentration-*increasing* changes, and mean DD can improve
while the tail gets worse. Conviction sizing passed this same check
incidentally; here it is a gate.

**If 0/3 clear the bar**, production behaviour stays exactly as it is and the
outcome is a DOCUMENTATION fix only — `recommend.py`'s "20% single-name cap,
not equal-weight" claim and the `strategy_config.py` comment are corrected to
say what the code actually does. That is a valid, complete outcome for this
study and is not a reason to search for a fourth config.

**If H3 clears on drawdown but loses on return**, it is NOT adopted by this
rule (condition 1 is on return). It would be recorded as a measured
risk/return trade-off for the user to decide on separately, not adopted
silently on my judgement.

## Known ways this could mislead me

- **Cap headroom is confounded with the tilt.** H1/H2 change concentration
  AND unblock conviction sizing at the same time. If a config wins, I cannot
  attribute the win to "less capping" vs "more conviction" from this test
  alone. Recorded now so the writeup does not claim a mechanism the design
  cannot separate.
- **Only ~34 of 126 rebalances are BULL**, so the baseline's tilt effect is
  carried by a minority of periods; the H1/H2 deltas will be dominated by
  SIDEWAYS, which is 49% of all rebalances. A win is therefore mostly a
  statement about SIDEWAYS books.
- **This repo's base rate on adoptions is low** (1 adoption in the last 7+
  studies). A clean-looking positive here gets the same adversarial checklist
  conviction sizing got: worst-case DD separately from mean, per-window
  concentration, and an era split.
