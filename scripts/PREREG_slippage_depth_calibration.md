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
