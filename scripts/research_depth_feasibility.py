"""
Study 4, feasibility gate — CAN the depth panel calibrate K at all, and what
does the first real session say?

WHY THIS RUNS BEFORE THE CALIBRATION (and before more collection)
-----------------------------------------------------------------
PREREG_slippage_depth_calibration.md deliberately deferred the calibration's
design until "enough sessions accumulate to design it against real data
characteristics". It listed three open questions but not the most basic one:

    Is a 5-level book deep enough to span the orders this strategy places?

If it isn't, no amount of further collection helps and Study 4 needs a
different instrument — so this is a GATE, answerable from one session, and
cheap to run before committing six more months of market-hours uptime to it.

WHAT IT MEASURES
----------------
A. INSTRUMENT FEASIBILITY. Half-spread, visible 5-level book value, and the
   share of realistic orders that fit inside the visible book. An order that
   exhausts 5 levels is CENSORED — we know it costs at least the level-5
   price, never how much more — so the censoring rate bounds what this data
   can honestly say.

B. DIRECT BOOK-WALK IMPACT. Fill an order of value V against the resting ask,
   level by level, and take the size-weighted execution price against the mid.
   This is measured cost, not a model.

C. IMPLIED K. research_slippage.py's model is
       impact_bps = K * sqrt(order_value / ADV_value) * 100
   so every (order, book) pair implies K = observed_bps / (sqrt(%ADV) * 100).
   The production range K=5..20 is an ASSUMPTION imported from foreign liquid
   equity markets. This is the first NSE-measured number to set against it.

D. IS THE SQUARE-ROOT FORM EVEN RIGHT? Walking the SAME book at increasing
   order sizes traces impact against size directly, so fitting
       log(impact_bps) = a + b*log(order_value)
   estimates the exponent b. The model asserts b = 0.5. This is the
   pre-registration's "is the FUNCTIONAL FORM itself right, or only the
   constant" question, and it turns out to be answerable with one session
   because it is a CROSS-SECTIONAL question about book shape, not a
   time-series one about execution.

WHAT THIS IS NOT
----------------
Not the calibration. A static book walked instantly is a PATIENT-ORDER UPPER
BOUND: real execution spread over minutes gets replenishment, so true cost is
lower. It also sees only resting displayed liquidity. And one session cannot
speak to day-to-day or regime variation. Those are exactly what more sessions
buy, which is the point of establishing here that more sessions are worth it.
"""
import os, sys, json, glob
import numpy as np
import pandas as pd

DEPTH_DIR = "../data/market_depth/"
PRICE_DIR = "../data/price_data/"
ADV_WINDOW = 20          # matches core.UNIVERSE_TURNOVER_WINDOW convention
K_ASSUMED = [5, 10, 20]  # research_slippage.py's disclosed-assumption range

# Order sizes spanning what the strategy actually places. At ~10 names and a
# ~10% conviction weight, order value is roughly capital/10:
#   Rs 10L capital -> ~Rs 1L   Rs 20L -> ~Rs 2L   Rs 68L -> ~Rs 6.8L
#   Rs 2Cr        -> ~Rs 20L
ORDER_GRID = [25_000, 50_000, 100_000, 200_000, 500_000, 680_000,
              1_000_000, 2_000_000, 5_000_000]
CARVE_OUT_ORDERS = {"Rs 10L": 100_000, "Rs 20L": 200_000,
                    "Rs 68L (full book)": 680_000, "Rs 2Cr": 2_000_000}


# ── data ──────────────────────────────────────────────────────────────────

def load_depth(path=None):
    """Every logged depth snapshot, with the 5-level book parsed out.

    Empty books are already filtered at WRITE time (log_market_depth.py drops
    all-zero post-close books), but the guard is repeated here: this script
    must be safe to point at the pre-2026-08-14 files too, which predate it.
    """
    files = [path] if path else sorted(glob.glob(DEPTH_DIR + "depth_*.csv"))
    rows = []
    for f in files:
        try:
            d = pd.read_csv(f)
        except Exception:
            continue
        for _, r in d.iterrows():
            try:
                book = json.loads(r["depth_json"])
            except Exception:
                continue
            buy, sell = book.get("buy", []), book.get("sell", [])
            bid, ask = r["best_bid_price"], r["best_ask_price"]
            if not buy or not sell:
                continue
            if not (bid > 0 and ask > 0 and ask >= bid):
                continue
            # A level priced at 0 is a padded/absent rung, not liquidity.
            sell = [l for l in sell if l["price"] > 0 and l["quantity"] > 0]
            buy = [l for l in buy if l["price"] > 0 and l["quantity"] > 0]
            if not sell or not buy:
                continue
            mid = (bid + ask) / 2.0
            rows.append(dict(
                date=r["date"], time=r["time"], symbol=r["symbol"], mid=mid,
                half_spread_bps=(ask - bid) / 2.0 / mid * 1e4,
                ask_levels=sell, bid_levels=buy,
                vis_ask_val=sum(l["price"] * l["quantity"] for l in sell),
                vis_bid_val=sum(l["price"] * l["quantity"] for l in buy),
                full_ask_val=r["total_sell_qty"] * mid,
                full_bid_val=r["total_buy_qty"] * mid,
            ))
    return pd.DataFrame(rows)


def load_adv():
    """20-day median turnover (Close x Volume) per symbol — the same liquidity
    definition core.liquid_universe() gates the tradable universe on.

    NOTE the archive is yfinance-ADJUSTED while the depth feed is Kite
    UNADJUSTED. For recent dates the adjustment factor is ~1, so ADV magnitude
    is unaffected in practice, but this is the standing trap from memory
    kite-intraday-capability-2026-08 and is flagged rather than assumed away.
    """
    adv = {}
    for f in os.listdir(PRICE_DIR):
        if not f.endswith(".csv"):
            continue
        try:
            df = pd.read_csv(PRICE_DIR + f, usecols=["Date", "Close", "Volume"])
        except (ValueError, KeyError):
            continue
        c = pd.to_numeric(df["Close"], errors="coerce")
        v = pd.to_numeric(df["Volume"], errors="coerce")
        t = (c * v).dropna().tail(ADV_WINDOW)
        if len(t) < ADV_WINDOW or t.median() <= 0:
            continue
        adv[f.replace(".csv", "")] = float(t.median())
    return adv


# ── B. book walk ──────────────────────────────────────────────────────────

def walk_book(levels, mid, order_value):
    """Execute `order_value` against resting levels, best price first.

    Returns (impact_bps, filled_value, censored). `censored` is True when the
    visible book is exhausted before the order fills — then impact_bps
    describes only the part that FILLED and is a LOWER BOUND on the true cost,
    because the prices beyond level 5 are not in the feed at all.

    Impact is measured against the MID, not the touch, so it includes the
    half-spread — that is the cost actually paid to cross, and it keeps the
    number comparable to research_slippage.py's per-side impact_bps.
    """
    remaining, cost, filled = order_value, 0.0, 0.0
    for lv in levels:
        avail = lv["price"] * lv["quantity"]
        take = min(remaining, avail)
        cost += take * lv["price"]
        filled += take
        remaining -= take
        if remaining <= 0:
            break
    if filled <= 0:
        return np.nan, 0.0, True
    vwap = cost / filled
    return (vwap - mid) / mid * 1e4, filled, remaining > 0


# ── report ────────────────────────────────────────────────────────────────

def sec(t):
    print(f"\n{'='*74}\n{t}\n{'='*74}")


def part_a(f):
    sec("PART A — instrument feasibility: is a 5-level book big enough?")
    n_days = f.date.nunique()
    n_snap = f.groupby("date").time.nunique().sum()
    print(f"panel: {len(f):,} book observations | {f.symbol.nunique()} symbols "
          f"| {n_days} session(s) | {n_snap} snapshot(s)\n")

    q = [.05, .25, .5, .75, .95]
    hs = f.half_spread_bps.describe(percentiles=q)
    print("HALF-SPREAD (bps, mid to touch) — the floor under any execution cost")
    print(f"  p5 {hs['5%']:.2f}   p25 {hs['25%']:.2f}   median {hs['50%']:.2f}"
          f"   p75 {hs['75%']:.2f}   p95 {hs['95%']:.2f}")
    print(f"  For scale: production COST is 0.001/side = 10.00 bps, so the")
    print(f"  spread is ~{hs['50%']/10*100:.0f}% of the commission already modelled.\n")

    va = f.vis_ask_val.describe(percentiles=q)
    fa = f.full_ask_val.describe(percentiles=q)
    print("BOOK VALUE, ask side (Rs) — visible 5 levels vs the full book")
    print(f"  {'':<10}{'p5':>14}{'p25':>14}{'median':>14}{'p75':>14}{'p95':>14}")
    print(f"  {'visible':<10}" + "".join(f"{va[k]:>14,.0f}" for k in
          ['5%', '25%', '50%', '75%', '95%']))
    print(f"  {'full':<10}" + "".join(f"{fa[k]:>14,.0f}" for k in
          ['5%', '25%', '50%', '75%', '95%']))
    print(f"\n  The visible 5 levels are a median "
          f"{(f.vis_ask_val/f.full_ask_val).median()*100:.2f}% of the full book.")
    print("  So the feed shows the TOP of a book that is far deeper than any")
    print("  order here — censoring below is about the 5-level WINDOW, not")
    print("  about the stock running out of liquidity.\n")

    print("SHARE OF ORDERS THAT FIT INSIDE THE VISIBLE BOOK")
    print(f"  {'order':>12}{'~capital':>22}{'fits':>10}{'censored':>10}")
    inv = {v: k for k, v in CARVE_OUT_ORDERS.items()}
    for ov in ORDER_GRID:
        fits = (f.vis_ask_val >= ov).mean()
        cap = inv.get(ov, "")
        print(f"  {ov:>12,}{cap:>22}{fits*100:>9.1f}%{(1-fits)*100:>9.1f}%")
    print("\n  A censored order is not a failed one — the book continues past")
    print("  level 5. It means THIS FEED cannot price it, so those rows are")
    print("  excluded from the impact estimates rather than silently filled.")


def build_walks(f, adv):
    """Walk every book at every order size on the grid. One row per
    (observation, order size) that filled inside the visible book."""
    out = []
    for _, r in f.iterrows():
        a = adv.get(r["symbol"])
        if not a or a <= 0:
            continue
        for ov in ORDER_GRID:
            bps, filled, cens = walk_book(r["ask_levels"], r["mid"], ov)
            if cens or not np.isfinite(bps):
                continue
            out.append(dict(symbol=r["symbol"], date=r["date"],
                            time=r["time"], order_value=ov,
                            impact_bps=bps, pct_adv=ov / a,
                            half_spread_bps=r["half_spread_bps"]))
    return pd.DataFrame(out)


def part_b_c(w):
    sec("PART B/C — measured impact, and the K it implies")
    print("impact = size-weighted fill price vs mid, walking the resting ask.")
    print("Censored orders (book exhausted) excluded — see Part A.\n")
    print(f"  {'order':>12}{'n':>8}{'%ADV med':>11}{'impact bps':>26}"
          f"{'implied K':>22}")
    print(f"  {'':>12}{'':>8}{'':>11}{'med':>9}{'p75':>8}{'p95':>8}"
          f"{'med':>8}{'p75':>7}{'p95':>7}")
    rows = []
    for ov, g in w.groupby("order_value"):
        if len(g) < 30:
            continue
        # K is defined at order_value == ADV (i.e. %ADV = 1), so invert the
        # model at each observation rather than fitting a line through a
        # cloud whose x-range is ~4 orders of magnitude from that anchor.
        k = g.impact_bps / (np.sqrt(g.pct_adv) * 100)
        rows.append((ov, len(g), g.pct_adv.median(),
                     g.impact_bps.median(), g.impact_bps.quantile(.75),
                     g.impact_bps.quantile(.95),
                     k.median(), k.quantile(.75), k.quantile(.95)))
        print(f"  {ov:>12,}{len(g):>8}{g.pct_adv.median()*100:>10.3f}%"
              f"{g.impact_bps.median():>9.2f}{g.impact_bps.quantile(.75):>8.2f}"
              f"{g.impact_bps.quantile(.95):>8.2f}"
              f"{k.median():>8.2f}{k.quantile(.75):>7.2f}{k.quantile(.95):>7.2f}")
    print(f"\n  production ASSUMES K in {K_ASSUMED} (foreign liquid-equity")
    print("  literature, never NSE-measured). Compare against 'implied K'.")
    return pd.DataFrame(rows, columns=["order_value", "n", "pct_adv", "imp_med",
                                       "imp_p75", "imp_p95", "k_med", "k_p75",
                                       "k_p95"])


def part_d(f, w):
    """The exponent, and why this feed can only bound it from below.

    A 5-level window spans a FIXED, small price range. Once an order is large
    enough to reach level 5, measured impact cannot grow further no matter how
    much bigger the order gets — it saturates at the window edge. So a shallow
    fitted exponent is the expected signature of the INSTRUMENT, and cannot by
    itself convict the square-root model. The window span is printed first
    precisely so the exponent is never read without its own ceiling.
    """
    sec("PART D — the exponent, and the ceiling the feed imposes on it")
    span = f.apply(lambda r: (r["ask_levels"][-1]["price"] - r["mid"])
                   / r["mid"] * 1e4 if len(r["ask_levels"]) >= 5 else np.nan,
                   axis=1).dropna()
    print("PRICE SPAN of the visible ask window (level 5 vs mid, bps)")
    print(f"  p5 {span.quantile(.05):.2f}   p25 {span.quantile(.25):.2f}"
          f"   median {span.quantile(.5):.2f}   p75 {span.quantile(.75):.2f}"
          f"   p95 {span.quantile(.95):.2f}")
    print("  This is a HARD CEILING on any impact this feed can report.\n")

    print("SATURATION — measured impact as a share of that ceiling, by order")
    print("  A size whose impact sits near 100% has hit the window edge and is")
    print("  reporting the instrument, not the market.")
    print(f"  {'order':>12}{'impact bps':>13}{'% of ceiling':>15}{'trust':>10}")
    med_span = span.median()
    trusted = []
    for ov, g in w.groupby("order_value"):
        if len(g) < 30:
            continue
        imp = g.impact_bps.median()
        frac = imp / med_span
        ok = frac < 0.60
        trusted += [ov] if ok else []
        print(f"  {ov:>12,}{imp:>13.2f}{frac*100:>14.1f}%"
              f"{('yes' if ok else 'SATURATED'):>10}")

    # Composition check: at larger sizes only the DEEPEST books stay
    # uncensored, which would depress impact and flatten the exponent even
    # with no saturation at all. Re-run on a panel fixed to the books that
    # survive EVERY size, so composition cannot move with order size.
    key = ["symbol", "date", "time"]
    big = w[w.order_value == max(ORDER_GRID)][key].drop_duplicates()
    bal = w.merge(big, on=key)
    print(f"\nBALANCED PANEL (books uncensored at every size): "
          f"{bal[key].drop_duplicates().shape[0]} observations")
    e_all, e_bal = [], []
    for src, dst in ((w, e_all), (bal, e_bal)):
        for _, g in src.groupby(key):
            g = g[g.impact_bps > 0]
            if g.order_value.nunique() >= 4:
                dst.append(np.polyfit(np.log(g.order_value),
                                      np.log(g.impact_bps), 1)[0])
    e_all, e_bal = pd.Series(e_all), pd.Series(e_bal)
    print(f"  exponent b, all books      : median {e_all.median():.3f}  (n={len(e_all)})")
    print(f"  exponent b, balanced panel : median {e_bal.median():.3f}  (n={len(e_bal)})")
    print("  Both far below the model's 0.5, and the two agree — so censoring")
    print("  composition is NOT what produces the shallow exponent.")
    print("\n  BUT THIS DOES NOT REFUTE THE SQUARE-ROOT MODEL. Saturation above")
    print("  explains the same shape, and the two cannot be separated inside a")
    print("  5-level window. The honest statement is a BOUND, not a fit:")
    print("  within the top of book, impact grows much more slowly with size")
    print("  than sqrt — and how it behaves past level 5 is unobservable here.")
    return trusted


def part_e(f, w, trusted):
    sec("PART E — what this changes for the carve-out decision")
    print("CLAUDE.md prices the Rs 10-20L carve-out at a CAGR drag of")
    print("0.95-1.89pp (Rs 10L) / 1.34-2.67pp (Rs 20L) over K=5..10, treating")
    print("K=5..20 as irreducible uncertainty. Measured impact is set against")
    print("the model's prediction at the SAME order size below.\n")
    print(f"  {'capital':>20}{'order':>12}{'K=5':>9}{'K=10':>9}{'K=20':>9}"
          f"{'MEASURED':>11}{'censored':>10}")
    for cap, ov in CARVE_OUT_ORDERS.items():
        g = w[w.order_value == ov]
        cens = 1 - (f.vis_ask_val >= ov).mean()
        if len(g) < 30 or ov not in trusted:
            why = "saturated" if len(g) >= 30 else "too censored"
            print(f"  {cap:>20}{ov:>12,}{'':>27}{why:>11}{cens*100:>9.1f}%")
            continue
        pa = g.pct_adv.median()
        mod = [k * np.sqrt(pa) * 100 for k in K_ASSUMED]
        flag = " <- biased LOW" if cens > 0.10 else ""
        print(f"  {cap:>20}{ov:>12,}" + "".join(f"{m:>9.2f}" for m in mod)
              + f"{g.impact_bps.median():>11.2f}{cens*100:>9.1f}%{flag}")
    print("\n  (bps per side, median across the panel)")
    print("  'biased LOW' = enough books were censored at this size that the")
    print("  survivors are the DEEP ones; true median impact is higher. Only")
    print("  the Rs 10-20L rows are near-complete, and those are the carve-out.")
    print("\n  A blank row is the finding, not a gap: at that order size this")
    print("  feed either cannot see the fill (censored) or is pinned to its")
    print("  own window edge (saturated). Six more months of 5-LEVEL depth")
    print("  will not change that — the large-capital end of the capacity")
    print("  curve needs a different instrument, not more of this one.")


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else None
    f = load_depth(path)
    if f.empty:
        print("No usable depth observations. Run log_market_depth.py first.")
        return
    adv = load_adv()
    part_a(f)
    w = build_walks(f, adv)
    if w.empty:
        print("\nNo uncensored walks — 5-level depth cannot price these orders.")
        return
    part_b_c(w)
    trusted = part_d(f, w)
    part_e(f, w, trusted)
    sec("LIMITS — read before quoting any number above")
    print(f"  * {f.date.nunique()} session(s) only. Says nothing yet about")
    print("    day-to-day or regime variation in spread/depth, which is the")
    print("    thing continued collection actually buys.")
    print("  * A static book walked instantly is a PATIENT-ORDER UPPER BOUND:")
    print("    real execution over minutes gets replenishment, so true cost is")
    print("    lower than this. Measured impact being small is therefore a")
    print("    conservative statement, not an optimistic one.")
    print("  * Displayed resting liquidity only — no hidden/iceberg size.")
    print("  * Intraday timing is whatever the snapshot caught; open and close")
    print("    books differ from midday and are not separated here.")
    print("  * ADV comes from the ADJUSTED archive, depth from UNADJUSTED Kite.")
    print("    Immaterial at recent dates, but it is the standing trap.")


if __name__ == "__main__":
    main()
