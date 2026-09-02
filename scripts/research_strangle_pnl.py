"""
Gate A2 — direct P&L of the ACTUAL structure (5% OTM naked strangle), rather
than Gate A's ATM straddle proxy.

Pre-registered as AMENDMENT 1 in PREREG_options_range_selling.md, BEFORE this
script produced any output. Decision rule, exclusions and the "if this fails
the program CLOSES, no third cut" commitment are frozen there.

WHY THIS IS A DIFFERENT QUESTION, NOT A RETRY
----------------------------------------------
Gate A measured ATM straddle implied-move vs realised-move — a deliberately
assumption-light proxy for speed. The traded product is an OTM strangle, and
equity options carry a persistent volatility SKEW: OTM options (puts
especially) trade rich relative to realised risk because of structural
crash-insurance demand. That mechanism operates specifically away from the
money, so an ambiguous ATM result does not settle the OTM case. Gate A's 2pp
floor stands as failed for the ATM structure and is NOT relitigated here.

METHOD
------
Reuses the cached Gate A panel verbatim — the same (symbol, D, E) tuples, so
cycle selection cannot be re-cut. Per cycle, sell 1 lot of the same naked
strangle research_margin_gate.py priced:

  short CE nearest spot_D*1.05, short PE nearest spot_D*0.95
  premium = CE_D + PE_D                    (Close, else Settle — Gate A's rule)
  payout  = max(0, U_E - K_call) + max(0, K_put - U_E)
  pnl     = premium - payout,  reported as % of premium collected

`payout` reads the RAW underlying and RAW strike from the SAME date-E
bhavcopy — consistent on a single date by construction, so no split
adjustment is needed (unlike Gate A's D->E ratio, which is what the
corporate-action bug broke). corp_action_in_window cycles are excluded
anyway, belt-and-braces, and the count is reported.

GROSS of costs — no bid-ask, no brokerage. An UPPER BOUND, like Gate A.

Usage:
    python research_strangle_pnl.py
"""
import time

import numpy as np
import pandas as pd

import download_fo_bhavcopy as fo
from research_vrp_gate import (front_month_options, underlying_price,
                               date_clustered_bootstrap, REQUEST_DELAY)

PANEL = "../data/_research/vrp_gate_panel.csv"
OUT = "../data/_research/strangle_pnl_panel.csv"
CALL_OFF = 0.05
PUT_OFF = 0.05
WIN_RATE_FLOOR = 0.60      # frozen in the prereg amendment
COST_HAIRCUT = 0.05        # 5% of premium, crude round-trip spread proxy


def leg_price(chain, strike, otype):
    row = chain[(chain["Strike"] == strike) & (chain["OptionType"] == otype)]
    if row.empty:
        return None
    c, s = row["Close"].iloc[0], row["Settle"].iloc[0]
    if pd.notna(c) and c > 0:
        return float(c)
    if pd.notna(s) and s > 0:
        return float(s)
    return None


def build(panel):
    fo.init_nse_session()
    cache = {}

    def get_raw(d):
        k = d.strftime("%Y-%m-%d")
        if k not in cache:
            try:
                cache[k] = fo.download_fo_date(d)
            except Exception:
                cache[k] = None
            time.sleep(REQUEST_DELAY)
        return cache[k]

    rows, skipped = [], {"no_raw_D": 0, "no_chain": 0, "no_strikes": 0,
                         "no_premium": 0, "no_raw_E": 0, "no_underlying_E": 0}
    for i, r in panel.iterrows():
        sym, D, E = r["symbol"], r["decision_date"], r["expiry_date"]
        raw_D = get_raw(D)
        if raw_D is None:
            skipped["no_raw_D"] += 1
            continue
        chain, _ = front_month_options(raw_D, sym, D)
        if chain is None:
            skipped["no_chain"] += 1
            continue
        u_D = r["underlying_D"]
        strikes = chain["Strike"].dropna().unique()
        if len(strikes) == 0:
            skipped["no_strikes"] += 1
            continue
        k_call = min(strikes, key=lambda k: abs(k - u_D * (1 + CALL_OFF)))
        k_put = min(strikes, key=lambda k: abs(k - u_D * (1 - PUT_OFF)))
        p_call = leg_price(chain, k_call, "CE")
        p_put = leg_price(chain, k_put, "PE")
        if p_call is None or p_put is None:
            skipped["no_premium"] += 1
            continue
        premium = p_call + p_put
        if premium <= 0:
            skipped["no_premium"] += 1
            continue

        E_bd = E
        while E_bd.weekday() >= 5:
            E_bd += pd.Timedelta(days=1)
        raw_E = get_raw(E_bd)
        if raw_E is None:
            raw_E = get_raw(E_bd + pd.Timedelta(days=1))
        if raw_E is None:
            skipped["no_raw_E"] += 1
            continue
        u_E = underlying_price(raw_E, sym)
        if u_E is None:
            skipped["no_underlying_E"] += 1
            continue

        payout = max(0.0, u_E - k_call) + max(0.0, k_put - u_E)
        pnl = premium - payout
        rows.append(dict(symbol=sym, decision_date=D, expiry_date=E,
                         underlying_D=u_D, underlying_E=u_E,
                         k_call=k_call, k_put=k_put, premium=premium,
                         payout=payout, pnl=pnl,
                         pnl_pct_premium=pnl / premium,
                         breached=payout > 0))
        if len(rows) % 200 == 0:
            print(f"  ...{len(rows)} cycles priced")

    print(f"\nPriced {len(rows)} cycles. Skipped: {skipped}")
    return pd.DataFrame(rows)


def report(df):
    if df.empty:
        print("No cycles priced — cannot evaluate Gate A2.")
        return

    print(f"\n{'='*74}\nGATE A2 RESULT — 5% naked strangle, direct P&L\n{'='*74}")
    print(f"cycles: {len(df)} | symbols: {df.symbol.nunique()} | "
          f"expiry dates: {df.expiry_date.nunique()}")

    win_rate = (df.pnl >= 0).mean()
    mean_pct = df.pnl_pct_premium.mean()
    _, lo, hi = date_clustered_bootstrap(df.pnl_pct_premium.values,
                                         df.expiry_date.values)
    # date_clustered_bootstrap returns the MEDIAN as its point estimate; the
    # prereg's rule is on the MEAN, so recompute the bootstrap on means.
    groups = [g["pnl_pct_premium"].to_numpy()
              for _, g in df.groupby("expiry_date")]
    rng = np.random.default_rng(42)
    n = len(groups)
    means = np.empty(2000)
    for i in range(2000):
        pick = rng.integers(0, n, n)
        means[i] = np.concatenate([groups[j] for j in pick]).mean()
    lo_m, hi_m = np.percentile(means, [2.5, 97.5])

    print(f"\nwin rate (kept full premium, no breach): {win_rate*100:.1f}%")
    print(f"mean P&L as % of premium: {mean_pct*100:+.1f}%  "
          f"95% CI [{lo_m*100:+.1f}%, {hi_m*100:+.1f}%]  (date-clustered)")
    print(f"median P&L as % of premium: {df.pnl_pct_premium.median()*100:+.1f}%")

    c1 = lo_m > 0
    c2 = win_rate >= WIN_RATE_FLOOR
    net = mean_pct - COST_HAIRCUT
    c3 = net > 0
    print(f"\n  1. CI excludes zero (positive):        {'PASS' if c1 else 'FAIL'}")
    print(f"  2. win rate >= {WIN_RATE_FLOOR*100:.0f}%:                  "
          f"{'PASS' if c2 else 'FAIL'}  ({win_rate*100:.1f}%)")
    print(f"  3. survives {COST_HAIRCUT*100:.0f}%-of-premium cost haircut: "
          f"{'PASS' if c3 else 'FAIL'}  ({net*100:+.1f}%)")

    print(f"\n{'-'*74}\nTAIL — the decision for a negative-skew payoff\n{'-'*74}")
    q = df.pnl_pct_premium.quantile([.01, .05, .25])
    print(f"  p1  {q[.01]*100:+.0f}% of premium")
    print(f"  p5  {q[.05]*100:+.0f}% of premium")
    print(f"  p25 {q[.25]*100:+.0f}% of premium")
    worst = df.nsmallest(5, "pnl_pct_premium")[
        ["symbol", "decision_date", "expiry_date", "premium", "payout", "pnl_pct_premium"]]
    print("\n  worst 5 cycles:")
    print(worst.to_string(index=False))
    n_wipe = (df.pnl_pct_premium < -10).sum()
    print(f"\n  cycles losing >10x the premium collected: {n_wipe} "
          f"({n_wipe/len(df)*100:.2f}%)")
    print(f"  breach rate: {df.breached.mean()*100:.1f}%")

    print(f"\n{'-'*74}\nCONCENTRATION — is the mean carried by a few expiry dates?\n{'-'*74}")
    by_date = df.groupby("expiry_date").pnl_pct_premium.mean().sort_values()
    print(f"  expiry dates with positive mean: "
          f"{(by_date > 0).mean()*100:.1f}% ({(by_date>0).sum()}/{len(by_date)})")
    print(f"  worst 3 dates: "
          + ", ".join(f"{d.date()} {v*100:+.0f}%" for d, v in by_date.head(3).items()))

    print(f"\n{'='*74}")
    if c1 and c2 and c3:
        print("GATE A2: PASSES its frozen bar (gross of real spreads).")
        print("Per the prereg, read the TAIL block above before acting — a pass")
        print("on the mean with a catastrophic tail means 'edge exists,")
        print("structure unsafe naked', pointing to the iron condor, not to")
        print("selling this naked.")
    else:
        print("GATE A2: DOES NOT PASS its frozen bar.")
        print("Per AMENDMENT 1, the program CLOSES here — no third cut, no")
        print("further moneyness/tenor search. Do not retune and re-run.")
    print(f"{'='*74}")


def main():
    panel = pd.read_csv(PANEL, parse_dates=["decision_date", "expiry_date"])
    n0 = len(panel)
    panel = panel[~panel["corp_action_in_window"]]
    print(f"Panel: {len(panel)} cycles ({n0 - len(panel)} corp-action cycles excluded)")
    df = build(panel)
    if not df.empty:
        df.to_csv(OUT, index=False)
        print(f"Saved {OUT}")
    report(df)


if __name__ == "__main__":
    main()
