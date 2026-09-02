# PRE-REGISTRATION — options range-selling (short strangle/condor), Gate A

**Written 2026-09-02, BEFORE running Gate A. Frozen. Amendments appended with
a reason and a date, never silently edited — same convention as every other
PREREG in this repo.**

## RESULT (2026-09-02) — GATE A DOES NOT PASS ITS FROZEN BAR

`research_vrp_gate.py`, WATCHLIST panel, 2021-09 to 2026-08, 2433 observations
across 60 symbols and 58 expiry dates.

| test | result | verdict |
|---|---|---|
| CI excludes zero (positive side) | median 1.53pp, 95% CI [1.06pp, 1.98pp] | PASS |
| median exceeds 2pp floor | 1.53pp < 2.00pp | **FAIL** |

Per the frozen decision rule, Gate A does not pass. A gross VRP mechanism
IS measurably present (part 1 clears easily) — this is a real, statistically
significant finding, not a null result — but its SIZE is below the
pre-registered floor, and per the frozen rule that floor is not something to
retune after seeing the number.

**This 1.53pp is itself an upper bound, not a tradeable estimate.** It is
priced off `Close`/`Settle` (a theoretical settlement mark for the many
strikes that never actually traded — see "Known ways this could mislead" for
why `Settle` was used at all) — no real bid-ask was paid or received. Gate
B's real spread data (collection running via `log_options_depth.py`) would
only shrink this further; it cannot make an already-sub-floor gross number
clear the bar net of costs.

**Concentration/robustness checks, run before trusting the number**:
- 75.9% of expiry dates had positive median VRP — not a handful of cycles
  carrying the result.
- UDiFF-only subsample (n=1296, direct `Underlying` column, no futures-basis
  proxy): median 1.16pp, CI [0.27pp, 1.84pp] — agrees with the full sample
  within the full sample's CI width. The legacy-era proxy is not distorting
  the headline number.
- **A real bug was caught and fixed mid-study**: the first run showed
  implausible tail cycles (ANGELONE −81.5pp, RELIANCE −51.2pp, SHRIRAMFIN
  −74.3pp, ...) traced to the NSE F&O archive's underlying price NOT being
  retroactively split/bonus-adjusted — RELIANCE's real 2024 1:1 bonus made
  the raw archive read a fake ~55.5% "crash" where `price_data/`'s adjusted
  close shows the true move was ~−11%. Fixed by computing `realised_move`
  from `price_data/`'s adjusted close (see `adjusted_close_on_or_before`);
  `implied_move` is unaffected (a same-date ratio, no split can occur inside
  it). 16/2433 cycles were flagged and corrected. The fix moved the CI from
  [1.03,1.99] to [1.06,1.98] — negligible on the median (16 rows out of
  2433), material on the tail (removed several fake >50pp-loss cycles). The
  worst genuine cycle after the fix is ADANIENT 2023-01-25→02-23 (−50.2pp) —
  the real Hindenburg-report crash window, correctly LEFT IN the sample as a
  genuine tail event, not excluded. See PREREG amendment note in
  `research_vrp_gate.py`'s `adjusted_close_on_or_before` docstring for the
  full mechanism.

**What this means for the plan**: per the plan's own Phase 0 framing ("any
[gate] can kill the project"), Gate A failing its frozen bar is grounds to
stop before building Phase 1's forecasting/backtest infrastructure — building
that machinery on a mechanism that didn't clear its own pre-registered bar is
exactly the failure mode pre-registration exists to prevent. This is reported
to the user as a genuine, not-favorable finding rather than acted on
unilaterally (lowering the floor, or proceeding anyway, are calls the user
gets to make, not calls embedded in the measurement).

## Gate C (informational — run in parallel, capacity is not the blocker)

`research_margin_gate.py`, same WATCHLIST panel, live `basket_order_margins`.
Naked strangle median Rs132,468/lot; iron condor median Rs68,369/lot (ratio
1.89x — wings roughly halve required margin for a capped tail). At Rs
10L/20L: naked supports ~7/15 concurrent names, condor ~14/29. **Gate C does
not block this strategy** — capacity was never the constraint, Gate A's edge
size is. One data quirk noted, not a script bug: Zerodha's `final` basket
margin nets in the premium credit already collected, which for one thin name
(SONACOMS) produced a negative "additional margin required" figure — a
real economic reading (the position was fully premium-funded), not an error;
it is the panel's minimum, so it does not affect the reported medians.

## Why this exists

See `/home/dhrumil/.claude/plans/my-desire-i-plan-lazy-tiger.md` for the full
plan. The user wants to sell CE+PE around a monthly band per F&O stock,
profiting if price stays inside the band to expiry. This is a genuinely
different strategy from the momentum equity book — not a modification of the
S/R subsystem — with a different payoff (negative skew: small frequent wins,
rare large losses) and a different economic mechanism (the variance risk
premium: implied vol tends to overstate realised vol, and selling that gap is
the entire edge). Nothing in this repo has tested that mechanism before.

Phase 0 of the plan is three feasibility gates that can each independently
kill the project, run BEFORE building any forecasting/backtest infrastructure
— mirrors the depth-feasibility gate run 2026-09-01 (verify the instrument
before committing months to it). This document covers **Gate A only**: does
the variance risk premium actually exist, measurably, on NSE single stocks?

## What Gate A measures

For a sample of (symbol, expiry cycle) pairs:

- **Implied move at the decision date D** — front-month ATM straddle price as
  a fraction of underlying: `(ATM_call_close + ATM_put_close) / underlying`.
  This is the standard straddle-implied-move approximation (no Black-Scholes
  inversion, no risk-free-rate assumption, no dividend adjustment needed —
  robust and appropriate for a feasibility gate). "ATM" = the strike nearest
  the underlying price on D among that cycle's front-month contracts.
- **Realised move to expiry E** — `abs(underlying_E / underlying_D - 1)`.
- **VRP for that cycle** = implied move − realised move. Positive means the
  option market overpriced the move (the seller's edge, before costs).

## Data source and the underlying-price decision (frozen)

`download_fo_bhavcopy.download_fo_date(date)` (widened 2026-09-02, see that
file's docstring) returns per-contract rows with Strike/Expiry/Close/Settle,
plus `Underlying` — populated only in the UDiFF era (2024-07-15 on, direct
NSE column). Confirmed live 2026-09-02: `aggregate_by_symbol` output is
byte-identical before/after the widening (verified against
`data/fo_data/RELIANCE.NS.csv`, 2026-06-15, max abs diff 0.0) — data/fo_data/
consumers are unaffected.

**Legacy era (2015-01 to 2024-07-14, most of the available history) has no
underlying-price column.** The decision, frozen now: use that SAME date's
near-month FUTSTK settlement price as the underlying proxy — self-consistent
(same unadjusted NSE derivatives source as the options themselves, no
cross-source join), available on every date back to 2015, and it is exactly
the standing trap this codebase has been bitten by before (NATIONALUM 1.744x
adjustment mismatch, memory kite-intraday-capability-2026-08) applied in
reverse: rather than risk joining an unadjusted option price against
`price_data/`'s yfinance-ADJUSTED close, stay entirely inside the unadjusted
NSE F&O archive for both legs of every observation. Futures-vs-spot basis is
a known, small, and roughly cost-of-carry-explained deviation — acceptable
noise for a feasibility gate; NOT acceptable to silently ignore, so it is
recorded here as a limitation and the UDiFF-era direct-underlying subsample
is analysed separately as a check (see Decision rule, part 3).

## Sampling design (frozen)

- **Panel**: `sr_daily_logger.WATCHLIST` (the repo's existing 61-name fixed
  S/R calibration panel) — reused rather than inventing a new universe,
  consistent with this repo's convention of reusing an established panel
  wherever one already exists. Names without listed options simply return no
  data at the fetch step and are dropped; this is discovered, not assumed.
- **Cycles**: one observation per (symbol, expiry) where expiry is read
  DIRECTLY from each decision date's own bhavcopy — `min(Expiry)` among that
  symbol's OPTSTK rows on date D that is `> D` — never a hardcoded "last
  Thursday" rule, because NSE has changed expiry-day conventions over the
  sample window and the plan explicitly calls for deriving this from data.
- **Decision date D** per cycle = the first trading day on/after the PRIOR
  cycle's expiry (mirrors how a real monthly seller would roll: enter the
  next cycle right after the previous one settles).
- **Sample window for Gate A specifically** (not the eventual Phase 3
  backtest): monthly cycles over the last 5 years (2021-09 to 2026-08, ~60
  cycles), not the full 11-year archive — this is a feasibility check, and
  Phase 3 is where the full-history backtest belongs if Gate A passes. Two
  bhavcopy fetches per cycle per symbol (decision date + expiry date), not a
  daily backfill.
- **Exclusions applied and counted, not silently dropped**: a cycle is
  excluded if the symbol has no OPTSTK rows on D, no ATM strike within 3% of
  underlying, or the expiry-date bhavcopy is missing (holiday shift, delisted
  name, download failure). The exclusion count and reasons are reported
  alongside the result — a result built from a silently-shrunk sample is not
  trustworthy (this is the same lesson as the S/R log's day-0/contamination
  exclusions elsewhere in this repo).

## Decision rule (fixed before seeing any result)

1. **Primary test**: median VRP across all (symbol, cycle) observations, with
   a 95% CI from a paired block bootstrap **clustered by expiry date**
   (`research_conviction_sizing.paired_window_bootstrap`'s machinery, same
   `BLOCK_LEN=6`/`SEED=42` convention, applied here as a single-sample
   block-bootstrap over expiry-date groups rather than a paired A/B — same
   principle, adapted because this is one series, not two configs being
   compared). The CI must **exclude zero on the positive side** for Gate A to
   pass at all.
2. **Effect size floor**: median VRP must exceed 2 percentage points of
   underlying price. This is a placeholder margin, NOT yet netted against
   real option bid-ask (Gate B collects that; forward-only, cannot be
   backfilled) — Gate A establishes whether a GROSS edge exists worth Gate
   B's collection effort. The actual go/no-go against realistic spread cost
   happens once Gate B has data, and is NOT decided by this document alone.
3. **UDiFF-era check**: repeat the primary test restricted to observations
   where `Underlying` was directly available (2024-07-15 on) — a smaller,
   more recent subsample, but immune to the futures-basis proxy. If the
   full-sample and UDiFF-only results disagree in sign or magnitude by more
   than the CI width, the futures-basis proxy is unreliable and the legacy
   portion of any later backtest needs a different treatment (or exclusion).
4. **Report the full distribution, not just the median.** This is a
   negative-skew payoff on the OTHER side of this trade (i.e. cycles with
   large negative VRP are exactly the cycles that would have hurt a seller
   most) — a mean can look fine while the tail is dangerous. Report the
   5th/25th/50th/75th/95th percentiles and the worst 3 cycles by name.

## What would NOT be treated as a pass even if the CI clears zero

- **A result driven by 2020 COVID-crash cycles alone.** Realised vol spiked
  in Feb–Apr 2020 in a way implied vol partially anticipated but not fully —
  if VRP is strongly positive except during that stretch, that is not the
  same claim as "VRP is generally positive"; the by-cycle breakdown must show
  it is not concentrated in a handful of dates (same "one cell doing all the
  work" check the conviction-sizing and max-weight studies used).
- **A result driven by only the largest few names** (RELIANCE-class ultra-
  liquids). Gate A's panel is F&O-representative, not mega-cap-only, on
  purpose — the per-name breakdown must show the effect is not confined to a
  handful of names, since Gate B may show many of those names have spreads
  too wide to trade anyway.
- **Passing Gate A alone is not a green light to trade.** It establishes that
  the mechanism (VRP) exists gross of costs. Gate B (spreads) and Gate C
  (margin/sizing) still gate the decision, and Phase 3's real backtest on
  actual settlement prices is the evidence that would actually be acted on.

## Known ways this could mislead

- **Straddle-implied-move is an approximation**, not a rigorous IV extraction
  — it is skew- and smile-blind. Acceptable for "does an edge exist at all"
  (Gate A); Phase 2's strike selection needs a real IV surface, not this
  shortcut.
- **Settlement price vs traded close.** Legacy `CLOSE` is 0.0 for untraded
  strikes; `SETTLE_PR`/`SttlmPric` (theoretical/settlement) is used instead,
  per `download_fo_bhavcopy.py`'s `EXTRA_COLS` note — but a settlement price
  for a genuinely untraded contract is a theoretical mark, not a price
  anyone could have actually transacted at. This is exactly what Gate B's
  real bid-ask measurement is for; Gate A's number is therefore an UPPER
  BOUND on what a real seller could capture, not a tradeable estimate.
- **Survivorship**: the WATCHLIST panel is today's names, not a point-in-time
  F&O roster from 2021. A name that lost its F&O listing mid-window would be
  silently absent from earlier cycles it should have appeared in. Flagged,
  not fixed for Gate A (fixing it means deriving a point-in-time F&O roster,
  which is Phase 3-scale work); acceptable for a feasibility gate, NOT
  acceptable for the Phase 3 backtest, which must use a point-in-time
  universe the same way `core.liquid_universe()` does for the equity engine.

## Amendments

(none yet)

---

# AMENDMENT 1 — 2026-09-02: Gate A2, direct P&L of the ACTUAL structure

**Written BEFORE running `research_strangle_pnl.py`. Frozen.**

## Why this is a new question, not a retune of Gate A's floor

Gate A measured **ATM straddle** implied-move vs realised-move. That was
frozen deliberately as a fast, assumption-light feasibility proxy ("no
Black-Scholes inversion, no risk-free rate, robust and appropriate for a
feasibility gate"). It is **not the product**. The strategy sells **OTM**
strikes — the 5% ladder `research_margin_gate.py` already priced — and equity
options carry a persistent **volatility skew**: OTM options, puts especially,
trade rich relative to realised risk because of structural crash-insurance
demand. That is a documented mechanism operating specifically away from the
money, so an ambiguous ATM result does not settle the OTM question either way.

Testing it is therefore a genuinely different hypothesis. **It is not
permission to relitigate Gate A's 2pp floor**, which stands as failed for the
ATM structure. If Gate A2 also fails, the program CLOSES — no third cut, no
further moneyness/tenor search. That is stated here, before the result, to
foreclose exactly the multiple-comparisons hunt this repo's base rate (~1
adoption in 8 studies) warns about.

## Method

Reuses the cached Gate A panel (`data/_research/vrp_gate_panel.csv`) — the
same (symbol, decision date D, expiry E) tuples, so cycle selection is
untouched and cannot be re-cut. For each cycle, sell 1 lot of the naked
strangle `research_margin_gate.py` measured margin for:

  short CE at strike nearest `spot_D * 1.05`, short PE nearest `spot_D * 0.95`

  premium_per_share = CE_price_D + PE_price_D      (Close, else Settle — the
                                                    same rule Gate A used)
  payout_per_share  = max(0, U_E - K_call) + max(0, K_put - U_E)
  pnl_per_share     = premium - payout
  pnl_pct_of_premium = pnl_per_share / premium_per_share

`payout` uses the RAW underlying and RAW strike **read from the same date E
bhavcopy** — consistent by construction on a single date, so it needs no
adjustment (unlike Gate A's D→E *ratio*, which is what the split bug broke).
The 16 `corp_action_in_window` cycles are excluded anyway as belt-and-braces,
and that exclusion is reported.

GROSS of costs, like Gate A: no bid-ask (Gate B), no brokerage. An upper
bound again — so a marginal pass here is still not a green light.

## Decision rule (fixed before seeing any result)

ADOPT — meaning Phase 1 infrastructure is worth building — only if ALL hold:

1. Date-clustered bootstrap 95% CI on **mean** `pnl_pct_of_premium` excludes
   zero on the positive side (clustered on expiry date, `SEED=42`, same
   machinery as Gate A).
2. **Win rate >= 60%** — cycles finishing inside both strikes. A 5% strangle
   should win most months by construction; below this the structure is simply
   struck too tight, regardless of average P&L.
3. Mean P&L survives a **crude cost haircut**: subtract 5% of premium as a
   round-trip spread proxy (Gate B has no data yet; 5% of premium is
   deliberately optimistic for single-stock options, so passing this is
   necessary, not sufficient).

REPORT REGARDLESS, and do not treat as a pass:
- the 5th-percentile cycle and the single worst cycle, in % of premium — for
  a negative-skew payoff the tail is the decision, not the mean;
- how much of the mean is carried by the best few expiry dates.

If 1-3 hold but the tail shows a single cycle capable of erasing many months
of premium at Gate C's implied sizing, the honest verdict is "edge exists,
structure unsafe naked" — which points to the iron condor (Gate C: ~1.89x
less margin, capped tail), NOT to trading it naked.

## RESULT — GATE A2 FAILS. PROGRAM CLOSED (2026-09-02)

`research_strangle_pnl.py`, 2414 cycles, 60 symbols, 58 expiry dates
(16 corp-action cycles excluded as pre-specified; 3 more dropped for missing
premium).

| frozen criterion | result | verdict |
|---|---|---|
| 1. CI on mean excludes zero | mean **+4.5%** of premium, 95% CI **[-11.8%, +19.3%]** | **FAIL** |
| 2. win rate >= 60% | 68.9% | PASS |
| 3. survives 5%-of-premium cost haircut | +4.5% - 5% = **-0.5%** | **FAIL** |

2 of 3 fail. Per AMENDMENT 1, frozen before the run, **the program closes
here — no third cut.**

### The three headline numbers tell one coherent story

Win rate 68.9%, median cycle **+82.8%** of premium, mean **+4.5%**. That gap
between median and mean IS the finding: the frequent small wins are almost
exactly cancelled by rare large losses. This is the canonical short-premium
signature, and it is not "nearly working" — it is the distribution behaving
exactly as the negative-skew literature says it does.

### The CI width is the most important result, and it is structural

Even at n=2414 across 58 expiry dates, the 95% CI on the mean spans
[-11.8%, +19.3%] — **the edge cannot be statistically distinguished from
zero**. That is not a sample-size problem more collection would fix; it is a
property of a payoff whose variance is dominated by rare tail events. The
operational consequence is decisive: **you could trade this profitably for a
year and still not know whether you had an edge or were merely in the
pre-tail part of the distribution.** A strategy that cannot tell you it is
working is not deployable at real capital, regardless of its mean.

### The tail, in the units that matter (margin blocked, per Gate C)

Against Gate C's median naked-strangle margin of Rs132,468/lot and a
representative Rs600k lot notional:

| percentile | P&L vs notional | vs MARGIN BLOCKED |
|---|---|---|
| p1 | -21.4% | **-97.1%** |
| p5 | -10.4% | -47.3% |
| p25 | -1.3% | -5.6% |
| p50 | +1.8% | +8.1% |

A 1-in-100 cycle destroys essentially **the entire margin blocked** on that
position. Worst observed: ITC 2025-12-30 cycle, -2627% of premium.

### Tail correlation invalidates Gate C's diversification count

Gate C reported "Rs10L supports ~7 concurrent naked names", which is only
meaningful if those positions fail independently. They do not:

- the 50 worst cycles span only **21 distinct expiry dates**;
- **8 of the 50 worst share a single expiry (2026-03-30)**; 5 more on
  2023-11-30, 5 on 2023-12-28;
- the worst 5 expiry dates account for **61%** of all negative-date loss;
- INFY and TCS both appear in the worst 5 cycles on the *same* expiry
  (2026-01-27 -> 2026-02-24) — a sector-wide shock breaching both at once.

So a bad month does not cost 1/7th of the book. It breaches many names
simultaneously, each capable of a ~97%-of-margin loss. **Holding 7 names is
not 7 independent bets**, and the capacity headroom Gate C measured is
substantially illusory for this payoff.

### Why the iron condor does NOT rescue this

The obvious objection is "cap the tail with wings." It fails on arithmetic,
not on preference: the gross mean is already ~zero (+4.5%, CI spanning zero),
and buying wings costs a material share of premium collected. Capping the
tail while paying for the cap converts "occasionally catastrophic, mean
approximately zero" into "reliably slightly negative". Condors do not rescue
a structure whose mean is indistinguishable from zero — they make the
negative expectation reliable. Not tested further, and per AMENDMENT 1 not to
be tested as a third cut.

### Consistency across both gates

Gate A (ATM straddle proxy): gross VRP +1.53pp, below its 2pp floor.
Gate A2 (actual 5% OTM structure): mean +4.5% of premium, CI spanning zero,
negative after a generous cost haircut. Two independent framings of the same
question, agreeing. The skew hypothesis that motivated A2 — that OTM strikes
might carry a materially richer premium — is **not supported**: whatever skew
premium exists is consumed by the higher breach severity at those strikes.

### Final verdict

**CLOSED.** The mechanism is real but too small and far too tail-dominated to
trade at this capital, in this universe, at monthly tenor. Gate B (live
option spread collection) is moot — there is no edge left for spreads to eat.
