# PRE-REGISTRATION — slippage model calibration via live market depth

**Written 2026-08-10. Frozen for the INFRASTRUCTURE phase (data collection);
the actual calibration analysis has its own decision rule, to be written
once enough data exists to design it against real data characteristics
rather than guessing at a schema in advance.**

## Why this is Study 4 of the "state of the art" program

See memory state-of-the-art-program-2026-08. Ranked #1 by expected value:
`research_slippage.py`'s square-root market-impact model has an explicitly
uncalibrated constant K — CLAUDE.md's own words: *"K isn't NSE-calibrated,
would launder an assumption into a validated number."* Slippage is flagged
as a first-order CAPACITY constraint before ~₹1Cr, which matters more for
real-capital deployment than another marginal CAGR study. Kite's live
5-level market depth (already flowing through `MODE_FULL` websocket ticks
in live_ticker.py, just not stored) is the one new data source that can
close this gap.

## Why this session only builds the LOGGER, not the calibration

Verified 2026-08-04 (memory kite-intraday-capability-2026-08): Kite market
depth is LIVE-ONLY, no historical endpoint. Unlike `data/intraday_data/`
(3 years of price bars pulled in one afternoon because Kite backfills
price history), depth cannot be backfilled at all — every session this
logger doesn't run is data that can never be recovered. So the correct
first move is infrastructure, not analysis: start the clock now, decide
the calibration's exact design once there's a real panel to design against.

## What's logged (`log_market_depth.py`)

Per (date, time, symbol), every 15 minutes during market hours (piggybacks
on the existing `stockai-intraday` systemd timer — one more `ExecStart`
step, no new always-on process): last traded price, best bid/ask
price+quantity, total buy/sell quantity, and the full 5-level depth arrays
(JSON-encoded) for a richer book-shape analysis later even though only
best-bid/ask is needed for the specific K-calibration question today.
Universe: `core.liquid_universe()` (~200 F&O-liquid names) — the same pool
the strategy actually trades from, not a narrow panel.

REST-polling (`kite.quote()`, the same batched call `live_quotes.py`
already uses), not a persistent websocket — the question this is meant to
answer is "what does the book typically look like for this name," not
tick-by-tick microstructure evolution, so a periodic snapshot is sufficient
and avoids connection-lifecycle complexity.

Stored in `data/market_depth/depth_YYYY-MM-DD.csv`, one file per day,
gitignored (covered by the existing blanket `data/` rule) — RESEARCH DATA
ONLY, never read by anything in the trading pipeline.

## What the eventual calibration will need to establish (not decided yet)

- Minimum sample size / collection window before the panel is usable —
  depends on how much cross-sectional and day-to-day variation shows up
  once there's real data to look at, not guessable in advance.
- Whether spread/depth is stable enough per-name to calibrate a single K,
  or whether K should vary by liquidity tier / volatility bucket.
- How to map observed spread+depth back into `research_slippage.py`'s
  existing square-root impact functional form without just replacing one
  assumption with another (e.g. is the FUNCTIONAL FORM itself right, or
  only the constant).

These will be pre-registered properly, with a decision rule, once the
collected panel is large enough to make that design non-arbitrary — this
document will be amended (not silently edited) when that happens.

## Status: infrastructure only

`log_market_depth.py` built, tested (market-open/closed guards, missing-
token guard, CSV write/append behavior, all verified against a mocked Kite
client since no live token was cached at build time). Wired into the
existing `stockai-intraday.timer` (15-min market-hours cadence) via a third
`ExecStart` line in `stockai-intraday.service`. Collection starts from
whenever this is merged forward — no retroactive data possible.

---

# AMENDMENT 1 — 2026-09-01: feasibility gate run before further collection

**Why amended now, and not after six months.** The original document deferred
the calibration's design until "enough sessions accumulate to design it
against real data characteristics". It listed three open questions but not
the most basic one: *is a 5-level book deep enough to span the orders this
strategy actually places?* That question is answerable from a SINGLE session,
because it is cross-sectional (book shape) rather than time-series
(execution). Running it first is cheap; discovering the answer in 2027 after
committing months of market-hours uptime is not. `research_depth_feasibility.py`.

Panel: 1,230 book observations, 221 symbols, 4 sessions, 7 snapshots.

## Finding 1 — the instrument PASSES, but only at carve-out sizes

Share of orders that fit inside the visible 5-level book:

| order | ~capital | fits |
|---|---|---|
| Rs 1,00,000 | Rs 10L | 98.5% |
| Rs 2,00,000 | Rs 20L | 93.8% |
| Rs 6,80,000 | Rs 68L | 66.5% |
| Rs 20,00,000 | Rs 2Cr | 32.3% |

The visible 5 levels are a median **0.53%** of the full book — the feed shows
the top of a book far deeper than any order here, so censoring is a property
of the 5-level WINDOW, not of the stock's liquidity.

## Finding 2 — the feed has a hard ceiling, and it binds above ~Rs 10L/order

The visible ask window spans a median of only **5.15 bps** (p25 4.02, p75
7.15). That is the maximum impact this feed can ever report. Measured impact
as a share of that ceiling: 35% at Rs 1L, 41% at Rs 2L, 54% at Rs 10L, **64%
at Rs 20L, 77% at Rs 50L** — i.e. large orders report the instrument, not the
market. Combined with Finding 1, the usable range is roughly **orders below
Rs 10L**, which covers the Rs 10-20L carve-out with room to spare and does
not reach the Rs 2Cr end of the capacity curve at all.

**This retires one of the original open questions.** The large-capital end of
`research_slippage_capacity.py` cannot be calibrated by this instrument, ever.
More sessions of 5-level depth will not fix it — that needs full-depth data
or realised fills, not more of the same.

## Finding 3 — measured impact is far below the assumed K, where measurable

Median per-side impact (size-weighted fill vs mid, walking the resting ask),
against what the production model predicts at the same order size:

| capital | order | K=5 | K=10 | K=20 | MEASURED |
|---|---|---|---|---|---|
| Rs 10L | 1,00,000 | 3.74 | 7.47 | 14.95 | **1.82** |
| Rs 20L | 2,00,000 | 5.26 | 10.51 | 21.03 | **2.10** |

Implied K = observed_bps / (sqrt(%ADV) * 100) is **2.44** at Rs 1L and
**2.02** at Rs 2L — below the bottom of the assumed K=5..20 range. Half the
measured cost is simply the half-spread (median 1.40 bps); a carve-out-sized
order barely walks past the touch.

## Finding 4 — the exponent looks shallow, and that CANNOT convict the model

Fitting log(impact) = a + b*log(order_value) within each book gives median
b = 0.183 (balanced panel, books uncensored at every size: 0.148), against
the square-root model's 0.5, with 97-99% of books below 0.5. Censoring
composition is ruled out — the balanced panel agrees.

**But this is not evidence the square-root law is wrong.** Saturation
(Finding 2) produces the identical signature, and the two are not separable
inside a 5-level window. The defensible claim is a BOUND: within the top of
book, impact grows much more slowly with size than sqrt. Behaviour past level
5 is unobservable here. Recorded this way deliberately — "we refuted
Almgren-Chriss on 4 sessions of top-of-book data" is exactly the kind of
overclaim this repo's pre-registration habit exists to prevent.

## What this changes

1. **Collection stays worthwhile, with a narrowed goal.** Continue logging.
   The remaining open question it can answer is the one this session cannot:
   day-to-day and regime variation in spread/depth at carve-out sizes. One
   quiet session is not a calibration.
2. **Do not fold a K into production COST yet.** Same reasoning as before —
   4 sessions, one volatility regime. The direction (K is too high at these
   sizes) is now measured rather than assumed, which is progress, but a
   single-regime point estimate is not a constant.
3. **The carve-out decision moves in the safe direction.** CLAUDE.md's
   0.95-2.67pp CAGR drag for Rs 10-20L was computed at K=5..10; measured
   impact is roughly HALF what K=5 predicts, and a static-book walk is itself
   a patient-order upper bound (real execution gets replenishment). The
   carve-out was already judged fine; it is now fine with margin.
4. **The Rs 2Cr row of the capacity curve stays uncalibrated and should keep
   quoting the K=5..20 band.** Nothing here touches it.

## Decision rule for the eventual calibration (pre-registered now)

Adopt a measured K into production COST only if ALL hold:
- at least **20 distinct sessions** spanning at least one month, so day-to-day
  variation is estimable rather than assumed;
- sessions span **more than one volatility regime** (index realised vol in the
  month must not sit in the same tercile throughout) — August 2026 was the
  0.3rd-percentile quietest month since 2010 and calibrating a cost constant
  on tape like that repeats the S/R subsystem's own August lesson;
- the estimate is restricted to order sizes below the **60%-of-ceiling
  saturation threshold**, and reported per liquidity tier if the cross-
  sectional spread in implied K exceeds 2x between the top and bottom
  turnover terciles;
- the adopted value is the **upper end** of the session-to-session range, not
  its mean — an under-modelled cost is the expensive error here.

If those are met, the change to production is a single constant in
`research_slippage.py`; it does not touch strategy logic.
