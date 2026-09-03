# MONTH-END REVIEW — cycle ending 2026-08-25, compiled 2026-09-01

First complete simulation month under the current code. The month produced four
numbers that looked like verdicts on the strategy. All four were verdicts on
the measuring equipment.

**Bottom line:** no strategy parameter changed; `run_backtest_laggards_only`
verified byte-identical after every edit. Five instruments fixed, two of which
were silently invalidating research. One previously-adopted result had its
effect size corrected down (it survives its bar). Zero new adoptions — the one
study that came back positive turned out to be a harness bug, caught by its own
adversarial checklist.

---

## 01 — The walk-forward harness ran the wrong engine, but only for the baseline
**Severity: invalidated four studies. FIXED.**

`walk_forward.run_window` defaulted to `run_backtest`, the LEGACY hard-close
engine kept only for historical comparison. Four research scripts built their
baseline with a bare `run_window(matrix, index, turnover, s, e)` while PRINTING
"Running BASELINE (production run_backtest_laggards_only)" — and compared it
against candidates that DID pass the production engine explicitly.

Those studies measured **hard-close vs laggards-only + the change under test**,
not the change under test.

| configuration | mean CAGR (36 windows) | role in the studies |
|---|---|---|
| hard-close (legacy) | 28.90% | silently used as "baseline" |
| laggards-only + inverse-vol | 29.91% | the correct baseline |
| laggards-only + conviction 0.50 | **31.76%** | production today |

Every candidate got a free ~1pp CAGR head start from the engine alone.

**Impact.** Conviction sizing — this repo's only adoption in its last eight
studies — was recorded at **+2.89%, CI [+1.33%,+4.33%], 33/36 windows**.
Corrected: **+1.85%, CI [+0.54%,+3.17%], 26/36**. Still clears its
pre-registered bar, so **the adoption stands**, at two-thirds the effect and a
materially weaker win rate. The trend-quality and exit-flow REJECTIONS are
unaffected in direction — the confound favoured the candidates they rejected,
so those rejections hold *a fortiori*.

**What caught it matters more than the bug.** Every check these studies ran —
bootstrap CIs, win counts, per-window breakdowns, era splits, worst-case
drawdown — describes the DELTA, and the delta was consistently wrong, so none
of them could see it. It surfaced only by running one configuration down two
independent code paths and requiring the answers to match: `sizing_fn=None`
returned +28.90% where an explicit tilt-0.50 `sizing_fn` returned +31.76%, for
runs that are identical by definition.

**Fix:** `engine` is now a required argument raising `TypeError` if omitted,
rather than defaulting to anything. Changing the default to the production
engine was the smaller edit and the wrong one — a silent wrong default IS the
failure mode, so omission is now impossible. All four baselines are explicit.

---

## 02 — The sim reported a clean interface while the month's one consequential message failed
**Severity: 19x sizing error, undetected. FIXED.**

At the 2026-08-25 rebalance `ai_assistant.position_sizes()` correctly returned
`{"weight": "25.2%", "quantity": 394}` per name. The ADVISOR LLM rendered that
as prose reading *"Weight: 25% … Quantity to Buy: Calculated based on your
current portfolio value"* — dropping the quantity. The TRADER LLM read the
percentage as a share count and ordered **25 shares** of everything.

| name | intended | executed | of target |
|---|---|---|---|
| HFCL *(its own top pick)* | ₹97,462 · 409 sh | ₹5,950 · 25 sh | **6%** |
| WELCORP | ₹97,462 · 41 sh | ₹58,638 · 25 sh | 60% |
| RADICO | ₹97,462 · 21 sh | ₹115,750 · 25 sh | **119%** |
| LAURUSLABS | ₹97,462 · 52 sh | ₹46,655 · 25 sh | 48% |

Nothing blinked because "25" is a plausible share count for three of the four.

**Second, independent failure the same session:** orders were applied ADD-TO
rather than REBALANCE-TO, stacking new lots on existing ones and leaving the
book **54.7% deployed against a 37.5% BEAR mandate** (paper book, same window:
37.8%).

The critic reported `critic problems: 0`. It only asked whether a BUY produced
a position and whether journal rows matched. `record_fill` did its job
perfectly — the number the HUMAN acted on was wrong, which for a signals-only
system is precisely the failure the sim exists to rehearse. Note
`position_sizes`' docstring claims "'how much quantity' can never be improvised
by the LLM" — true of the TOOL, false of the PIPELINE, since the tool's output
still has to survive being re-rendered into prose.

**Fix:** the critic now checks ECONOMICS as well as plumbing — executed RUPEES
against `position_sizes()`'s own plan (rupees, not share counts, so it is
independent of wording), deployed share against `REGIME_EXPOSURE`, and whether
the advice was truncated before the trader read it. Replaying the real 08-25
orders raises 4 problems where the old critic raised 0; a control executing the
tool's own quantities raises 0. Affordability downsizes are now logged as
deviations. Separately, `model_accuracy` no longer scores MANDATED month-end
rotations as directional sell forecasts.

---

## 03 — "Target-before-stop 21%, avg R −0.30" was six stocks and zero finished races
**Severity: a normal month read as a losing system. FIXED.**

Two aggregation defects, both already in this repo's catalogue:

1. **The statistical unit was the CALL, not the SYMBOL.** The advisor logs its
   top ~8 names EVERY session, so 145 "calls" were 21 symbols re-observed in
   one shared tape. The 24 decided calls came from **six** symbols — and 15 of
   the 19 stops were two names (ADANIENSOL x9, LLOYDSME x6) re-logged daily as
   a single deteriorating position drifted down, each time with a fresh lower
   stop.
2. **The race was scored while censored.** Horizon is 42 sessions; no call had
   more than 20 sessions of forward data. ZERO races could complete, so every
   "resolved" row had resolved EARLY — a self-selected subset, and the mirror
   image of the hit/miss asymmetry behind July's fake 100% S/R hit rate.

Scored properly — per symbol, held from the call-day close, benchmarked against
Nifty over the identical span — the same month gives mean return **+1.32%**,
alpha **+2.49%, 95% CI [−0.91%, +6.01%]**, 12/21 symbols positive. Not
significant. Nowhere near an indictment. The naive row-level interval is 1.8x
too narrow, and the stops that fired mostly fired on names that genuinely kept
falling (ADANIENSOL −12.9%, LLOYDSME −12.1%) — the stop worked, the aggregation
did not.

**Caught while building the fix:** the ledger already carries an `alpha` column
(momentum alpha at call time). Writing a derived `alpha` only on the branch
where forward bars exist left zero-bar calls silently carrying the LEDGER's
value into a return average — reading as +715%.

**Fix:** every headline is per-SYMBOL with a symbol-clustered bootstrap
(`--by-call` for the old figures, labelled do-not-quote); the race is NOT
REPORTED until calls have their full horizon; a new "THE PICK, SEPARATED FROM
THE LEVEL" section reports Nifty-relative alpha, because the ATR
entry/stop/target geometry has never been walk-forward validated and only the
pick is backed by evidence. Derived keys renamed to avoid the collision.

---

## 04 — The deployment gate cannot tell a healthy strategy from a badly broken one
**Severity: the gate is being read as validation. NOW STATED IN THE OUTPUT.**

Reference per-period sd is **7.35%**. At 3–6 observations one period's noise
swamps any plausible degradation. Simulating the gate's OWN rule (one period
< p5, or two < p10) against the reference resampled with a constant handicap:

| periods | healthy | −1%/pd | −2%/pd | −3%/pd | −5%/pd |
|---|---|---|---|---|---|
| 1 *(today)* | 5% | 8% | 9% | 10% | 16% |
| **3** *(gate minimum)* | **16%** | 22% | **27%** | 30% | 44% |
| 6 | 31% | 41% | 48% | 54% | 72% |
| 12 | 54% | 68% | 76% | 83% | 95% |

"Healthy" is the FALSE-ALARM rate on a strategy behaving exactly as backtested.
A live path bleeding **−2%/period (≈ −22%/yr) passes three clean periods 73% of
the time**; at −5%/period (−46%/yr) it still passes 56%. At n=3 detection is
barely above the false-alarm rate.

This is a SAMPLE-SIZE fact, not a defect in paper_trader or gate_report — even
a full year only reaches 76% detection at −2%/pd while false alarms climb to
54%. So the gate is a **plumbing and gross-breakage check**, and "1 SCORED
period, 66th pctile, consistent" carries almost no information (80% of random
draws land p10–p90 by construction). The high-power instrument for "does the
live path implement the validated strategy" already exists:
`divergence_check.py` is deterministic, runs daily, and currently reports pool,
top-N and conviction weights all identical.

**Context on the one scored period** (2026-08-25, +4.10%): WELCORP alone
contributed +2.71pp and cash yield +0.75pp, so equity picking net of the single
best name was +0.65pp. The book also entered four sessions after the period
opened — the engine's own 07-28 book held to 08-25 returns **+5.64%** vs the
+4.10% realised — and the 07-28 and 08-03 top-four share only two of four
names. Entry timing is a real live-vs-backtest cost the reference does not
model.

**Fix:** the power table prints on every `gate_report.py` run.

---

## 05 — The 20% position cap has never bound in three quarters of rebalances
**Severity: documented risk control does not exist. TESTED, NOT CHANGED.**

Clip-then-renormalise: whenever `1/n >= MAX_WEIGHT` every name clips, and
renormalising n identical values returns exactly `1/n` — straight back above
the cap. You cannot have three names each ≤20% summing to 100%.

| regime | n | equal wt | fully clipped | final wt spread | raw spread |
|---|---|---|---|---|---|
| SIDEWAYS | 3 | 33.3% | **62/62 (100%)** | 0.0000 | 0.1068 |
| BEAR | 4 | 25.0% | **18/30 (60%)** | 0.0107 | 0.1032 |
| BULL | 10 | 10.0% | 0/34 (0%) | 0.0599 | 0.0599 |

**63.5% of all rebalances are exactly equal-weighted**, realised single-name
weight is 33.3%/25.0% not 20%, and `CONVICTION_TILT` does nothing at all in 73%
of them — the live paper book, BEAR since inception, has **never once used the
sizing change adopted in August**. `concentration-risk-2026-07` diagnosed this
mechanism at n=2 and records the fix as complete; it was not, since
4 × 20% = 80% < 100%.

Three alternatives pre-registered and tested (`PREREG_max_weight_cap.md`).
**0/3 cleared.** The decomposition is worth more than an adoption:

| | tilt 0.00 | tilt 0.50 |
|---|---|---|
| **cap 0.20 (production)** | 29.91% | **31.76%** |
| **cap 0.35** | 28.72% | 32.35% |

- cap alone, no tilt to unblock: **−1.19%**, CI [−2.42%,−0.28%], 9/36 —
  **concentration by itself is a DRAG.**
- tilt at today's cap: **+1.85%**, 26/36 — positive *despite* being blocked in
  73% of rebalances.
- tilt with headroom: **+3.63%**, 30/36 — roughly double.

Raising the cap buys ~1.8pp of extra tilt and pays ~1.2pp of concentration,
netting +0.59pp on 22/36 windows against a 22.74 gate. Marginal, and per the
pre-registration that CLOSES the line rather than prompting a retune. **The
tilt is the mechanism; the cap is the wrong lever**, because it necessarily
buys concentration at the same time — at cap 0.35 the worst single-name weight
reaches 45.0%. Honouring the cap absolutely (remainder to cash) costs about
**−3.6pp CAGR for −5.1pp worst-case drawdown**: a real risk/return trade-off,
recorded for the user, explicitly not adopted because the prereg said in
advance that a drawdown-only win is not an adoption.

**The first run of this study reported +3.45%, 34/36 and said ADOPT.** That was
finding 01's bug. Without the adversarial checklist this session would have
shipped a production change off a harness defect.

**Fix:** production unchanged. The deliverable was documentation —
`recommend.py` no longer claims "live sizing is inverse-vol with a 20% cap",
wrong twice over.

---

## 06 — The biggest driver of the reported numbers is the calendar
**Severity: largest effect ever measured here. QUOTING FIXED; strategy unchanged.**

Finding 04 noted the paper book's 07-28 and 08-03 top-4 shared only 2 of 4
names. That was a symptom of something nobody had measured. The IDENTICAL
strategy on all 21 rebalance phases (`research_timing_luck.py`;
`run_backtest_laggards_only` gained `phase=`, phase 0 byte-identical to every
historical number):

|  | min | max | spread | sd |
|---|---|---|---|---|
| CAGR | +21.94% | +33.13% | **11.19pp** | 3.23pp |
| Sharpe | 0.89 | 1.31 | 0.42 | 0.11 |
| max drawdown | 23.29% | 33.49% | 10.21pp | 2.85pp |

Controlled: passive Nifty over the same slicing moves **0.89pp**, so the
strategy moves 10x more — phase, not start date. Mechanism is textbook
(Newfound, "Quantifying Timing Luck"): timing-luck vol scales with turnover x
portfolio vol / sqrt(rebalance frequency); monthly + high-turnover + 3-4 names
is the worst case on all three axes.

**Phase-averaged walk-forward: 31.51%, sd 2.61pp, range 28.42-34.50%.** The
long-quoted **33.4% is phase 0** — near the TOP of that range — and its
worst-case DD (30.2%) is the BEST of seven phases (others reach 35.8%).
**Quote 31.5% +- 2.6pp.** Phase 0 is the 43rd percentile on the full panel, so
nothing was cherry-picked; a point estimate was quoted where a distribution was
needed. Note the BACKTEST steps a fixed 21-day grid while LIVE rotates on the
last Tuesday — not even the same phase, and `divergence_check.py` cannot see
it because it compares selection on a given day, not the rebalance calendar.

**THE CAVEAT THAT STOPS THIS BEING ALARMING — measured, not assumed.** The
11pp applies to LEVELS. Every study here uses a PAIRED comparison on the same
phase, which cancels most of the phase term. The ADOPTED conviction tilt
re-tested across phases:

| phase | baseline | tilt 0.50 | delta | wins | |
|---|---|---|---|---|---|
| 0 | +29.80% | +31.67% | +1.87% | 27/36 | PASS |
| 5 | +30.66% | +32.59% | +1.93% | 29/36 | PASS |
| 10 | +27.27% | +28.92% | +1.65% | 26/36 | PASS |
| 15 | +27.92% | +29.67% | +1.75% | 28/36 | PASS |

**4/4, mean +1.80% vs +1.85% on record — a 0.28pp spread while the baseline
LEVEL underneath moves 5.3pp.** Past adopt/reject calls stand. What IS true:
phase-sensitivity of the delta is diagnostic — a real effect barely moves, a
null one swings.

**Tranching measured, NOT adopted** (Jegadeesh-Titman overlapping portfolios;
exact to compute, since sleeves are self-contained and total wealth is their
sum): sd 3.35% -> 1.69% (2 sleeves) -> 0.74% (4) -> 0.60% (7), mean unchanged
(27.86% -> 28.24%). A variance reduction, not alpha. It needs N decision dates
per month against the mandated one, N x positions on a 3-4 name book and N x
STCG events. **The mandate is the user's to relax.**

---

## 07 — Residual momentum tested and rejected, 0/12
**Severity: none. A closed line.**

Researched from the literature rather than proposed from memory. Blitz, Huij &
Martens (2011) report residual momentum earning ~2x the risk-adjusted profit of
total-return momentum. Chosen over any "add an indicator" idea precisely
because it is NOT an auxiliary overlay — eight prior studies failed with that
shape — and instead recomputes the EXISTING score from data already on disk.

Feasibility gate, recorded in the prereg BEFORE the test: corr **0.864**
Pearson / 0.835 Spearman, HIGHER than trend-quality's 0.77 which was rejected
for that collinearity. Counter-argument was that top-3/top-4 overlap is only
1/3 and 2/4. True, and irrelevant: the books differ, and the difference is not
an improvement.

**0/12** (3 configs x 4 phases x 36 windows). Every delta negative or trivially
positive; two cells had CIs excluding zero in the UNFAVOURABLE direction.

**The phase gate earned its keep on first use:** `resid_blend_50` ranged
-1.30% to +0.21% across phases of the SAME config. A single-phase test landing
on phase 10 would have reported a mild positive and invited a follow-up study.

Recorded NOT adopted: `resid_replace` improved mean DD on all four phases
(-1.27 to -1.61pp). The rule is on return, return is negative, and a
drawdown-only win is not an adoption. 9th consecutive rejection of a change to
what the strategy RANKS on.

---

## What did not change, and what is still open

No strategy parameter was altered. `MAX_WEIGHT`, `CONVICTION_TILT`,
`REGIME_NAMES` and the engine are as they were;
`run_backtest_laggards_only` produces a byte-identical equity curve
(₹13,665,296.343058728) after every edit. All 96 modules import;
`data_integrity_check` reports 504 files clean; `divergence_check` reports live
and backtest selection identical.

One book repaired: agent-sim's equity log still carried the stranded 2026-08-19
row from August's early-rebalance bug (cash ₹256.30 against a journal with no
08-19 trade). Corrected to ₹1,004,348.30 / ₹634,489.70, validated by
reconstructing the 08-17 position from the journal and matching its logged
equity to the paisa. Backup:
`data/_quarantine/agentsim_equity_pre_0819fix_2026-09-01.csv`.

**Open, deliberately not acted on:**
- **Depth collection is the real bottleneck** — 7 snapshots across 4 sessions
  against ~25 for one full session. K gates the ₹10–20L carve-out decision and
  at this rate is years away, not months.
- **agent-sim covers 31/37 sessions** vs the paper book's 37, and cannot
  self-heal a missed run.
- **A tilt lever that does not buy concentration** — tilting the NUMBER of
  names or the deployed fraction rather than the per-name cap. Separate
  hypothesis, separate pre-registration.
- **Everything observed is BEAR.** One month, one rebalance period, one regime.

## The reusable lesson

Finding 01 was invisible to every statistical check that was actually run,
because all of them describe a delta and the delta was uniformly wrong. It
surfaced only when one configuration was expressed two different ways and the
answers were required to match. **Any harness with more than one route to the
same configuration should carry that identity check as a test.** It is cheap,
and it is the only thing in this session that caught a bug the rest of the
discipline was built to miss.
