"""
Gate C of the options-range-selling feasibility check — does Rs 10-20L
support a DIVERSIFIED book of short-premium positions, and does that decide
naked vs iron condor?

Plan reference: ~/.claude/plans/my-desire-i-plan-lazy-tiger.md, Phase 0 Gate C.
Short options require SPAN+exposure margin, and single-stock F&O lot sizes are
set by SEBI's contract-value floor, not by the trader — so this gate can fail
independently of Gate A's edge and Gate B's spreads: an edge that only
supports 2-3 concurrent names is a concentration risk regardless of its size.

METHOD
------
For each symbol (the same WATCHLIST panel Gate A used — reused rather than
inventing a new one), at a representative 5%/10% strike ladder around the
live front-month ATM (matching log_options_depth.py's ladder, so this reuses
the same mental model an operator would already have from that collector):

  NAKED STRANGLE = short (spot*1.05-nearest CE) + short (spot*0.95-nearest PE)
  IRON CONDOR    = strangle + long (spot*1.10-nearest CE) + long (spot*0.90-nearest PE)

Margin comes from Kite's OWN basket_order_margins — the actual SPAN+exposure
figure a real order would require, not an approximation. This is a LIVE-ONLY
measurement (no historical margin API), same limitation as Gate B.

WHAT THIS DECIDES
------------------
1. Rs-per-lot margin for both structures, per symbol — the raw capacity unit.
2. At Rs 10L / 20L, how many DIFFERENT NAMES could be held at once (a rough
   diversification count — real usage would also respect the strategy's own
   position-count/sector caps, not modelled here).
3. The margin RATIO (naked / condor) — the direct cost of the tail-risk cap,
   answering the plan's deferred naked-vs-condor question.

Usage:
    python research_margin_gate.py                # full WATCHLIST panel
    python research_margin_gate.py --symbols 10    # fast smoke test
"""
import argparse
import time

import pandas as pd

import kite_auth
from sr_daily_logger import WATCHLIST

STRIKE_CALL_OFF = 0.05     # strangle short call: +5% OTM
STRIKE_PUT_OFF = 0.05      # strangle short put: -5% OTM
WING_CALL_OFF = 0.10       # condor long call wing: +10% OTM
WING_PUT_OFF = 0.10        # condor long put wing: -10% OTM
REQUEST_DELAY = 0.34       # matches update_prices_kite.py's Kite pacing
CAPITAL_LEVELS = [1_000_000, 2_000_000]   # Rs 10L, Rs 20L — the planned carve-out


def front_month_chain(inst_df, name):
    g = inst_df[(inst_df["name"] == name) & (inst_df["instrument_type"].isin(["CE", "PE"]))]
    if g.empty:
        return None
    today = pd.Timestamp.now().date()
    fut = g[g["expiry"] > today]
    if fut.empty:
        return None
    expiry = fut["expiry"].min()
    return fut[fut["expiry"] == expiry]


def nearest(chain, target, otype):
    rows = chain[chain["instrument_type"] == otype]
    if rows.empty:
        return None
    strikes = rows["strike"].unique()
    k = min(strikes, key=lambda s: abs(s - target))
    return rows[rows["strike"] == k].iloc[0]


def build_legs(chain, spot, lot):
    """Returns (strangle_legs, condor_legs) as basket_order_margins order
    dicts, or (None, None) if any required strike is missing from the chain."""
    call = nearest(chain, spot * (1 + STRIKE_CALL_OFF), "CE")
    put = nearest(chain, spot * (1 - STRIKE_PUT_OFF), "PE")
    if call is None or put is None:
        return None, None
    call_wing = nearest(chain, spot * (1 + WING_CALL_OFF), "CE")
    put_wing = nearest(chain, spot * (1 - WING_PUT_OFF), "PE")

    def leg(row, side):
        return dict(exchange="NFO", tradingsymbol=row["tradingsymbol"],
                   transaction_type=side, variety="regular", product="NRML",
                   order_type="MARKET", quantity=lot)

    strangle = [leg(call, "SELL"), leg(put, "SELL")]
    if call_wing is None or put_wing is None or call_wing["strike"] == call["strike"] \
       or put_wing["strike"] == put["strike"]:
        condor = None   # chain too thin to place a distinct wing — don't fake it
    else:
        condor = strangle + [leg(call_wing, "BUY"), leg(put_wing, "BUY")]
    return strangle, condor


def collect(symbols):
    kite = kite_auth.get_kite_client()
    if kite is None:
        print("No cached Kite access token — Gate C needs a live session "
              "(margin has no historical endpoint, same limitation as Gate B).")
        return pd.DataFrame()

    inst_df = pd.DataFrame(kite.instruments("NFO"))
    inst_df["expiry"] = pd.to_datetime(inst_df["expiry"], errors="coerce").dt.date

    rows, skipped = [], {"no_chain": 0, "no_quote": 0, "no_strangle": 0,
                         "no_condor": 0, "margin_call_failed": 0}
    for sym in symbols:
        base = sym[:-3] if sym.upper().endswith(".NS") else sym.upper()
        chain = front_month_chain(inst_df, base)
        if chain is None:
            skipped["no_chain"] += 1
            continue
        lot = int(chain["lot_size"].iloc[0])
        try:
            q = kite.quote([f"NSE:{base}"])
            spot = q.get(f"NSE:{base}", {}).get("last_price")
        except Exception:
            spot = None
        if not spot:
            skipped["no_quote"] += 1
            continue

        strangle, condor = build_legs(chain, spot, lot)
        if strangle is None:
            skipped["no_strangle"] += 1
            continue

        try:
            m_s = kite.basket_order_margins(strangle)
            time.sleep(REQUEST_DELAY)
        except Exception as e:
            skipped["margin_call_failed"] += 1
            print(f"  {sym}: strangle margin call failed ({e})")
            continue
        strangle_margin = m_s["final"]["total"]

        condor_margin = None
        if condor is not None:
            try:
                m_c = kite.basket_order_margins(condor)
                time.sleep(REQUEST_DELAY)
                condor_margin = m_c["final"]["total"]
            except Exception as e:
                skipped["margin_call_failed"] += 1
                print(f"  {sym}: condor margin call failed ({e})")
        else:
            skipped["no_condor"] += 1

        rows.append(dict(symbol=base, spot=spot, lot=lot,
                         notional=spot * lot,
                         strangle_margin=strangle_margin,
                         condor_margin=condor_margin))
        print(f"  {sym}: strangle Rs{strangle_margin:,.0f}"
              + (f"  condor Rs{condor_margin:,.0f}" if condor_margin else "  condor n/a"))

    print(f"\nSkipped: {skipped}")
    return pd.DataFrame(rows)


def report(df):
    if df.empty:
        print("No margin data collected — cannot evaluate Gate C.")
        return

    print(f"\n{'='*74}\nGATE C RESULT — margin capacity, naked vs iron condor\n{'='*74}")
    print(f"symbols priced: {len(df)}")

    print(f"\nSTRANGLE margin per lot (Rs): median {df.strangle_margin.median():,.0f}  "
          f"p25 {df.strangle_margin.quantile(.25):,.0f}  "
          f"p75 {df.strangle_margin.quantile(.75):,.0f}  "
          f"max {df.strangle_margin.max():,.0f} ({df.loc[df.strangle_margin.idxmax(),'symbol']})")

    have_condor = df.dropna(subset=["condor_margin"])
    if len(have_condor):
        print(f"CONDOR   margin per lot (Rs): median {have_condor.condor_margin.median():,.0f}  "
              f"p25 {have_condor.condor_margin.quantile(.25):,.0f}  "
              f"p75 {have_condor.condor_margin.quantile(.75):,.0f}")
        ratio = (have_condor.strangle_margin / have_condor.condor_margin)
        print(f"margin ratio (naked/condor): median {ratio.median():.2f}x "
              f"— i.e. the wings roughly {'HALVE' if ratio.median() > 1.7 else 'reduce'} "
              f"required margin (n={len(have_condor)}/{len(df)} symbols had a "
              f"resolvable wing strike)")

    print(f"\n{'-'*74}\nDIVERSIFICATION CAPACITY at the planned carve-out\n{'-'*74}")
    for cap in CAPITAL_LEVELS:
        n_naked = int(cap / df.strangle_margin.median())
        n_condor = int(cap / have_condor.condor_margin.median()) if len(have_condor) else None
        print(f"  Rs {cap/100000:.0f}L capital, one lot per name at median margin:")
        print(f"    naked strangle : {n_naked} concurrent names")
        if n_condor is not None:
            print(f"    iron condor    : {n_condor} concurrent names")

    print(f"\n{'-'*74}\nworst-case (highest-margin) names — where capacity is tightest\n{'-'*74}")
    print(df.nlargest(5, "strangle_margin")[["symbol", "spot", "lot", "notional",
                                             "strangle_margin", "condor_margin"]].to_string(index=False))

    print(f"\n{'='*74}")
    print("This is a per-lot, single-position-per-name measurement — real")
    print("sizing would also need the strategy's own diversification rules")
    print("(sector caps etc., not modelled here) and a margin BUFFER, not")
    print("100% utilisation. Read as an upper bound on concurrent names, not")
    print("a sizing recommendation.")
    print(f"{'='*74}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", type=int, default=None,
                    help="limit to first N watchlist symbols (fast smoke test)")
    args = ap.parse_args()
    symbols = WATCHLIST[:args.symbols] if args.symbols else WATCHLIST
    df = collect(symbols)
    report(df)


if __name__ == "__main__":
    main()
