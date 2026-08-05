"""
redownload_fix.py — RETIRED 2026-08-05. Superseded by repair_price_gaps.py.

The download logic has been REMOVED, not just disabled. This file is kept only
so the name does not get re-created, and to record why.

WHAT IT USED TO DO. Re-download ~46 hardcoded symbols' full history from
yfinance and overwrite each CSV. It was a one-off repair script from before the
Kite switch.

WHY IT HAD TO GO. Its hardcoded list was exactly the set of symbols that kept
coming back with NaN-OHLC bars — WIPRO, PFC, ANGELONE, PAYTM, SHRIRAMFIN, PCBL,
GRAPHITE and the rest. yfinance intermittently serves a bar with real Volume but
NaN OHLC, so a single run of this script REINTRODUCED the corruption it was
written to fix, and wiped out any repair made since.

Two things made it especially dangerous:
  1. It executed at MODULE level — no `if __name__ == "__main__"` — so merely
     importing it downloaded and overwrote 46 files.
  2. yfinance data is split/dividend-ADJUSTED while Kite's is UNADJUSTED. A
     wholesale yfinance rewrite moves every historical price, shifting every
     S/R pivot and invalidating the P(touch) tables and every backtest figure.

Traced 2026-08-05: the nightly pipeline had not called this since the Kite
switch, yet five symbols were repaired and went bad again within hours, and
exactly 46 files carried a newer timestamp than the other 447.

USE INSTEAD:
    python repair_price_gaps.py --apply     # repair NaN bars / interior gaps
    python update_prices_kite.py            # daily append
Both pull from Kite, append only, verify agreement at the splice point, and
never rewrite history.
"""
import sys

if __name__ == "__main__":
    sys.stderr.write(__doc__.strip() + "\n\n")
    sys.stderr.write("This script has been retired and its logic removed.\n")
    sys.exit(1)
