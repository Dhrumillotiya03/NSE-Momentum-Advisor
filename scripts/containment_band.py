"""
containment_band.py
-------------------
MONTHLY CONTAINMENT BAND — the level price is unlikely to breach before
month-end, and its honest tradeability statistics.

WHY THIS EXISTS
---------------
The production S/R output answers P(TOUCH): "will price reach this level".
Verified on the 2026-08-04 panel (61 names): median S1 was −2.1% below CMP,
median R1 +1.9% above, and corr(|distance|, prob) was −0.879 / −0.907. The
probability column had become a restatement of distance, and a "94% support"
is a level with a ~6% chance of HOLDING. Read as support, that number is
backwards.

This module answers the complementary question the user actually asks:

    "Give me a level the stock will not dip below this month."

That is a QUANTILE OF THE FORWARD MINIMUM, not a pivot. It is computed by
inverting the empirical relationship between distance, volatility and breach
frequency — the same (distance x vol) structure the touch table uses, read in
the other direction.

WHAT THE DATA SAYS — READ THIS BEFORE TRADING OFF IT
-----------------------------------------------------
Measured on 15-minute Kite bars (2023-2026, 21d horizon,
research_tradeable_levels.py, protocol in PREREG_tradeable_levels.md):

1. CONTAINMENT WORKS. An 85%-hold band lands roughly 12-20% below price
   depending on volatility bucket, and calibrates within tolerance on holdout.

2. TRADING THAT BAND DOES NOT. Buying the band when it fills FAILED the
   pre-registered adoption test on holdout in BOTH trend states. Unconditional
   dip-buying is negative-expectancy here, and the mechanism is ADVERSE
   SELECTION: at a 5% band, observations that FILLED were contained only 9.5%
   of the time versus 100% for those that did not. The order executes
   precisely when the trend has turned against you.

So this module reports the band as a RISK / EXPECTATION tool — "how far can
this reasonably go against me before month-end" — and NOT as a buy signal.
It deliberately prints the negative tradeability finding alongside the level
so the two cannot be separated in use.

3. FILL REALISM. Win rate under a daily-Low fill test (the method the existing
   touch table uses) reads 54.2%, but falls to 51.0% at a realistic 30-minute
   persistence rule and 44.4% on a close-only rule. Daily-bar touch tests are
   measurably optimistic for a non-intraday trader.

STATUS: DESCRIPTIVE / ADVISORY ONLY.
Must NOT be wired into exit_engine / paper_trader / agent_sim / the scorer
without separate walk-forward validation, per the standing rule for every
auxiliary overlay in this project.

Usage:
    python containment_band.py RELIANCE
    python containment_band.py RELIANCE --horizon 10
    python containment_band.py --panel          # today's S/R watchlist
"""
import os
import sys
import json

import numpy as np
import pandas as pd

TABLE_PATH = "../data/containment_table.json"

VOL_EDGES = [0.0, 25.0, 35.0, 45.0, 1e9]
VOL_LABELS = ["<25%", "25-35%", "35-45%", "45%+"]

# Fallback band widths (floor, ceiling) as fractions, if no fitted table exists.
# From the TRAIN calibration at 21d / 85% containment. ASYMMETRIC by
# measurement, not assumption: forward downside excursion runs 1.07-1.36x the
# upside, widest for LOW-vol names — so a symmetric band understates downside
# most on exactly the stocks that feel safest to hold.
FALLBACK_21D = {
    "<25%":   (0.106, 0.078),
    "25-35%": (0.119, 0.098),
    "35-45%": (0.139, 0.123),
    "45%+":   (0.182, 0.170),
}


def vol_bucket(v):
    for i in range(len(VOL_LABELS)):
        if VOL_EDGES[i] <= v < VOL_EDGES[i + 1]:
            return VOL_LABELS[i]
    return VOL_LABELS[-1]


def realized_vol(close):
    r = close.pct_change().dropna().tail(252)
    if len(r) < 30:
        return None
    return float(r.std() * np.sqrt(252) * 100)


def _load_table():
    if os.path.exists(TABLE_PATH):
        try:
            with open(TABLE_PATH) as f:
                return json.load(f)
        except Exception:
            pass
    return None


def scale_to_horizon(width_21d, horizon):
    """Scale a 21d band width to another horizon by sqrt-of-time.

    A range grows ~sqrt(t) under a diffusion. This is an APPROXIMATION and is
    labelled as such wherever it is surfaced — the S/R subsystem learned this
    lesson once already: rescaling a 21d touch table to 10d was off by a mean
    4.3pp versus a natively-built table. Prefer a natively fitted table when
    one exists for the horizon.
    """
    if horizon == 21:
        return width_21d
    return float(width_21d * np.sqrt(horizon / 21.0))


def containment_band(df, horizon=21, alpha=0.15, cur=None):
    """Band that price is ~(1-alpha) likely to stay inside through `horizon`.

    `df` — daily OHLC, index = dates, ending at the decision bar.
    `cur` — override reference price (e.g. a live quote); defaults to last close.

    Returns a dict, or None if there is insufficient history.
    """
    if df is None or len(df) < 60:
        return None
    close = df["Close"]
    vol = realized_vol(close)
    if vol is None:
        return None
    price = float(cur) if cur else float(close.iloc[-1])
    if not price or not np.isfinite(price):
        return None

    vb = vol_bucket(vol)
    table = _load_table()
    fw21, cw21 = FALLBACK_21D.get(vb, (0.139, 0.123))
    source = "fallback (TRAIN calibration, 21d)"
    scaled = False

    if table:
        a = int(round(alpha * 100))
        cell = table.get("table", {}).get(f"{vb}|{horizon}|{a}")
        if cell:                                    # native table at this horizon
            fw, cw = float(cell["floor_width"]), float(cell["ceiling_width"])
            source = f"fitted table @{horizon}d (n={cell.get('n','?')})"
            return _band(price, vol, vb, horizon, alpha, fw, cw, source)
        cell = table.get("table", {}).get(f"{vb}|21|{a}")
        if cell:                                    # fall back to 21d + rescale
            fw21, cw21 = float(cell["floor_width"]), float(cell["ceiling_width"])
            source = f"fitted table @21d (n={cell.get('n','?')})"

    fw = scale_to_horizon(fw21, horizon)
    cw = scale_to_horizon(cw21, horizon)
    if horizon != 21:
        source += ", sqrt-scaled to horizon (approximation)"
        scaled = True
    return _band(price, vol, vb, horizon, alpha, fw, cw, source, scaled)


def _band(price, vol, vb, horizon, alpha, fw, cw, source, scaled=False):
    return {
        "price": price,
        "vol": vol,
        "vol_bucket": vb,
        "horizon_days": horizon,
        "alpha": alpha,
        "confidence": 1 - alpha,
        "floor_width": fw,
        "ceiling_width": cw,
        "floor": price * (1 - fw),
        "ceiling": price * (1 + cw),
        "asymmetry": (fw / cw) if cw else float("nan"),
        "source": source,
        "sqrt_scaled": scaled,
    }


def _asym_note(a):
    """State the asymmetry in the direction it actually points.

    Sign matters and flips with the fitting window: the 2023-26 intraday fit had
    downside > upside (1.07-1.36x), while the 2016-21 daily fit has upside wider
    at high vol, because the longer window contains real upside tails. Reporting
    a bare ratio with a fixed caption got this backwards once — hence this.
    """
    if not np.isfinite(a):
        return "asymmetry unavailable"
    if a > 1.02:
        return f"asymmetric {a:.2f}x — downside excursions exceed upside"
    if a < 0.98:
        return f"asymmetric {1/a:.2f}x the other way — upside tail is wider"
    return "roughly symmetric"


def format_band(sym, b):
    if b is None:
        return f"{sym}: insufficient history"
    L = [
        f"{sym}  —  {b['confidence']*100:.0f}% CONTAINMENT BAND "
        f"({b['horizon_days']}d horizon)",
        f"  price {b['price']:.2f}   vol {b['vol']:.1f}% ({b['vol_bucket']})",
        "",
        f"  CEILING  {b['ceiling']:>10.2f}   (+{b['ceiling_width']*100:.1f}%)",
        f"  FLOOR    {b['floor']:>10.2f}   (-{b['floor_width']*100:.1f}%)",
        f"  ({_asym_note(b['asymmetry'])})",
        "",
        f"  Read as: ~{b['confidence']*100:.0f}% chance price stays inside this",
        f"  band through the horizon. Breach expected ~{b['alpha']*100:.0f}% of months.",
        f"  source: {b['source']}",
        "",
        "  NOT A BUY SIGNAL. Buying the floor when it fills tested",
        "  NEGATIVE-expectancy on holdout (adverse selection: fills happen",
        "  because the trend turned, not because the stock got cheap).",
        "  Use this to size risk and set expectations, not to time entries.",
    ]
    return "\n".join(L)


def main():
    argv = sys.argv[1:]
    horizon = 21
    if "--horizon" in argv:
        i = argv.index("--horizon")
        horizon = int(argv[i + 1]); argv = argv[:i] + argv[i + 2:]
    alpha = 0.15
    if "--alpha" in argv:
        i = argv.index("--alpha")
        alpha = float(argv[i + 1]); argv = argv[:i] + argv[i + 2:]

    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from support_resistance import load_stock

    syms = [a.upper() for a in argv if not a.startswith("--")]
    if not syms:
        print(__doc__.split("Usage:")[1]); return

    show_all = "--levels" in argv
    for s in syms:
        sym = s if s.endswith(".NS") else s + ".NS"
        df = load_stock(sym)
        b = containment_band(df, horizon=horizon, alpha=alpha)
        print(format_band(s, b))
        if show_all and b:
            # Same stock at several confidence levels, so the tradeability
            # tradeoff is visible: tighter bands are nearer but break more.
            print("\n  Other confidence levels:")
            print(f"    {'conf':>6} {'FLOOR':>11} {'CEILING':>11} "
                  f"{'floor%':>9} {'ceil%':>8}")
            for a in (0.25, 0.15, 0.10, 0.05):
                bb = containment_band(df, horizon=horizon, alpha=a)
                if not bb:
                    continue
                print(f"    {(1-a)*100:>5.0f}% {bb['floor']:>11.2f} "
                      f"{bb['ceiling']:>11.2f} "
                      f"{-bb['floor_width']*100:>8.1f}% "
                      f"{bb['ceiling_width']*100:>7.1f}%")
        print()


if __name__ == "__main__":
    main()
