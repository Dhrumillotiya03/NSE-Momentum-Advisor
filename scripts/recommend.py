"""
Evidence-backed buy recommender.

Answers "which stock should I buy, and when" using:
  - the validated momentum scorer (ret_126/vol_63, config B — see strategy_config.py)
  - the current breadth-gated market regime (determines how many names / how much
    exposure the strategy calls for right now)
  - an EMPIRICAL confidence table (../data/confidence_table.json, built by
    confidence_table.py from ~411k historical setups): win rate and median 21-day
    forward return for stocks scoring in the same decile, AND conditioned on the
    current regime. This is a descriptive base rate, not a promise — no single
    trade is likely to be much better than a coin flip tilted slightly in your
    favor (historical top-decile win rate ~56%), and this tool says so plainly
    rather than manufacturing false confidence.

Usage:
    python recommend.py              # today's top candidates, ranked, with evidence
    python recommend.py RELIANCE.NS  # verdict + evidence for one specific stock
"""
import sys
import numpy as np

import strategy_config as sc
from confidence_table import load as load_confidence, confidence_for_score
from core import (
    load_stock, current_regime, compute_score, scan_universe,
    load_sector_map, position_size, LOOKBACK,
)


# ---------- Presentation ----------

def sizing_note(n_names):
    """Describe the sizing the engine ACTUALLY applies at this book size.

    This line used to read "live sizing is inverse-vol with a 20% cap" and was
    wrong twice over: sizing became conviction-weighted on 2026-08-05, and the
    20% cap is arithmetically infeasible whenever 1/n >= MAX_WEIGHT (n=3 in
    SIDEWAYS, n=4 in BEAR — 73% of rebalances). There the clip-then-renormalize
    step hands every name exactly 1/n, i.e. straight back above the cap it just
    applied, and CONVICTION_TILT does nothing at all. Saying "20% cap" to a user
    whose book is about to hold three names at 33% each is the kind of quiet
    misstatement this repo keeps finding in its own reporting.
    See PREREG_max_weight_cap.md.
    """
    if n_names and 1.0 / n_names >= sc.MAX_WEIGHT:
        return (f"equal-weight placeholder; live sizing is conviction-weighted "
                f"(tilt {sc.CONVICTION_TILT:g}) but at {n_names} names the "
                f"{sc.MAX_WEIGHT:.0%} cap binds on every name and renormalizes "
                f"back to {1.0/n_names:.0%} each — i.e. equal weight in practice")
    return (f"equal-weight placeholder; live sizing is conviction-weighted "
            f"(tilt {sc.CONVICTION_TILT:g}) capped at {sc.MAX_WEIGHT:.0%}")


def confidence_line(table, score, regime):
    d = confidence_for_score(table, score)
    reg = table["regime_stats"].get(regime, {})
    return d, reg


def print_recommendation(rank, sym, r, decile_stats, regime_stats, regime, sector_map, capital):
    sector = sector_map.get(sym, "unmapped sector")
    print(f"\n{rank}. {sym}  [{sector}]")
    print(f"   Price: Rs.{r['price']:.2f}   Score: {r['score']:.2f}   RSI: {r['rsi']:.0f}"
          f"   6m: {r['ret_6m']:+.1%}   3m: {r['ret_3m']:+.1%}")

    stop = r["price"] * sc.CATASTROPHIC_STOP
    print(f"   Catastrophic stop: Rs.{stop:.2f}  ({(sc.CATASTROPHIC_STOP-1)*100:.0f}% from entry)")

    print(f"   Evidence (this score decile, n={decile_stats['n']:,} historical setups, 21d fwd):"
          f"  win {decile_stats['win_rate']:.0%}  median {decile_stats['median_fwd']:+.2%}"
          f"  [P25 {decile_stats['p25_fwd']:+.1%} / P75 {decile_stats['p75_fwd']:+.1%}]")
    print(f"   Evidence (current regime={regime}, n={regime_stats.get('n',0):,}):"
          f"  win {regime_stats.get('win_rate',0):.0%}  median {regime_stats.get('median_fwd',0):+.2%}")

    if r["rsi"] > sc.RSI_OVERBOUGHT:
        print(f"   ⚠ RSI {r['rsi']:.0f} > {sc.RSI_OVERBOUGHT} — extended, wait for a pullback or size down")


def cmd_scan():
    print("\n==============================")
    print("STOCK RECOMMENDER — evidence-backed")
    print("==============================")

    regime, breadth = current_regime()
    n = sc.REGIME_NAMES[regime]
    exposure = sc.REGIME_EXPOSURE[regime]
    capital = sc.__dict__.get("CAPITAL", 1_000_000)
    try:
        import yaml
        with open("../config.yaml") as f:
            capital = yaml.safe_load(f)["capital"]
    except Exception:
        pass

    if not np.isnan(breadth):
        print(f"\nRegime: {regime}  (Nifty vs its own 200DMA drives this; "
              f"separately, {breadth:.0%} of the universe trades above ITS 200DMA)")
    else:
        print(f"\nRegime: {regime}")
    print(f"Strategy calls for {n} names at {exposure:.0%} total exposure right now.")

    table = load_confidence()
    reg_stats = table["regime_stats"].get(regime, {})
    print(f"\nHistorical base rate in this regime (n={reg_stats.get('n',0):,} setups, 21d fwd):"
          f"  win {reg_stats.get('win_rate',0):.0%}  median {reg_stats.get('median_fwd',0):+.2%}")
    if regime != "BULL":
        print("Note: momentum setups historically perform weaker outside BULL regimes"
              " — recommendations below are still the best available, but base rates are lower.")

    print("\nScanning universe...")
    results = scan_universe()
    if not results:
        print("No eligible momentum setups found right now.")
        return

    ranked = sorted(results.items(), key=lambda kv: kv[1]["score"], reverse=True)
    sector_map = load_sector_map()

    top = ranked[:n]
    print(f"\n{len(results)} stocks eligible (positive 6m & 3m momentum, above 50DMA) out of scanned universe.")
    print(f"Showing top {len(top)} (what the strategy would actually hold this rebalance):")

    weight_each = 1.0 / len(top) if top else 0
    for i, (sym, r) in enumerate(top, 1):
        d, _ = confidence_line(table, r["score"], regime)
        print_recommendation(i, sym, r, d, reg_stats, regime, sector_map, capital)
        alloc = position_size(capital, weight_each, exposure)
        print(f"   Indicative allocation ({sizing_note(len(top))}): ~Rs.{alloc:,.0f}")

    print(f"\n{'='*60}")
    print("This ranks by the SAME formula validated in the walk-forward backtest.")
    print("Position sizing at execution time comes from "
          "ai_assistant.position_sizes() /")
    print("backtest_portfolio.conviction_weights — not from this equal-weight "
          "placeholder.")


def cmd_single(symbol):
    if not symbol.upper().endswith(".NS"):
        symbol = symbol.upper() + ".NS"
    else:
        symbol = symbol.upper()

    print(f"\n==============================")
    print(f"VERDICT: {symbol}")
    print(f"==============================")

    df = load_stock(symbol)
    if df is None:
        print(f"No price data for {symbol}. Check the symbol (NSE ticker + .NS).")
        return
    if len(df) < LOOKBACK + 60:
        print(f"Not enough history for {symbol} ({len(df)} rows) to score it.")
        return

    regime, breadth = current_regime()
    table = load_confidence()
    reg_stats = table["regime_stats"].get(regime, {})
    sector_map = load_sector_map()

    r = compute_score(df)
    if r is None:
        print(f"NOT ELIGIBLE right now — fails the momentum filter (needs positive 6m")
        print(f"AND 3m returns, and price above the 50-day moving average).")
        close = df["Close"]
        ret_6m = close.iloc[-1] / close.iloc[-LOOKBACK - 1] - 1 if len(close) > LOOKBACK else np.nan
        ret_3m = close.iloc[-1] / close.iloc[-64] - 1 if len(close) > 64 else np.nan
        ma50 = close.rolling(50).mean().iloc[-1]
        print(f"  6m return: {ret_6m:+.1%}   3m return: {ret_3m:+.1%}"
              f"   price vs 50DMA: {(close.iloc[-1]/ma50-1):+.1%}" if not np.isnan(ma50) else "")
        print("\nVerdict: AVOID / WAIT. Momentum strategies lose their edge on stocks")
        print("that aren't already trending — buying here isn't backed by the evidence.")
        return

    d, _ = confidence_line(table, r["score"], regime)
    print_recommendation(1, symbol, r, d, reg_stats, regime, sector_map, 1_000_000)

    win = d["win_rate"]
    rsi_caution = sc.RSI_OVERBOUGHT - 10   # soft caution band before the hard cap
    if r["rsi"] > sc.RSI_OVERBOUGHT:
        verdict = "WAIT — eligible but RSI overextended; risk of near-term pullback"
    elif r["rsi"] > rsi_caution:
        verdict = f"BUY (cautiously) — eligible, RSI {r['rsi']:.0f} is extended; consider a smaller size or waiting for a pullback"
    elif win >= 0.56:
        verdict = "BUY — eligible, favorable historical base rate, not overextended"
    else:
        verdict = "MARGINAL — eligible but in a below-median score decile"

    print(f"\nVerdict: {verdict}")
    print(f"Confidence is the historical win rate for setups like this ({win:.0%}) —")
    print("not a guarantee. Size positions so a single loss is tolerable regardless.")


def main():
    if len(sys.argv) > 1:
        cmd_single(sys.argv[1])
    else:
        cmd_scan()


if __name__ == "__main__":
    main()
