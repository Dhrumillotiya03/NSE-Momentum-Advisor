"""
sr_monthend_analysis.py
------------------------
Reads ../data/sr_daily_log.csv (built by sr_daily_logger.py) and runs
month-end accuracy + calibration analysis.

Analyses performed:
  1. Basic hit-rate: did price touch S1 / R1 within the logged period?
  2. Level drift: how much did S1/R1 move day-to-day for each stock?
  3. Probability calibration: bucketed predicted-prob vs actual hit-rate
  4. Sample size (n) sensitivity: does higher n predict better accuracy?
  5. Distance vs accuracy: does closer S/R hit more often than far S/R?

Usage:
    python sr_monthend_analysis.py
    python sr_monthend_analysis.py --month 2026-08   ← one month's cohort only
    python sr_monthend_analysis.py --touch-pct 1.0   ← override touch tolerance
    python sr_monthend_analysis.py --window 10       ← FIXED forward horizon
    python sr_monthend_analysis.py --exclude-day0    ← drop already-touching levels
    python sr_monthend_analysis.py --exclude-contaminated  ← drop rows whose CMP
                                     matches no archive bar for its own date

THE DEFAULT IS NOW THE PRODUCTION HORIZON (2026-08-31). Every row is scored
against its OWN logged HorizonDays — distance to that month's last Tuesday —
because that is the question the subsystem actually answers. It used to default
to a fixed 21 days, which on the August log produced "R1 95.3%": no August row
had 21 sessions of forward data, so the figure was built almost entirely from
JULY rows, and July predates min-separation (levels sat a median 1.9-2.3% from
spot and were near-guaranteed to be touched). A headline number assembled from
the previous month's pre-fix rows is worse than no number. `--window N` still
forces a fixed horizon for comparison; without HorizonDays in the log the tool
falls back to it and says so.

WINDOW NOTE: an N-day hit rate needs N trading bars after each log date. A
shorter window is NOT comparable to the 21d backtested ~65-68% figure — fewer
bars, strictly fewer touches, so it reads lower by construction. The production
horizon shrinks through the month (16 days early, 1 day at the end), so its
pooled rate is likewise not comparable to any fixed-window number; section 1b
breaks it out by horizon length for exactly that reason.
"""

import os, sys
import numpy as np
import pandas as pd

LOG_PATH  = "../data/sr_daily_log.csv"
PRICE_DIR = "../data/price_data/"

TOUCH_PCT = 0.01   # price must come within 1% of level to count as "touched"

# Forward window in trading days. 21 is the validated/backtested horizon, but a
# short forward log cannot RESOLVE a 21d window (needs 21 bars after the log
# date), and an unresolved window is not a miss. Shortening the window to
# something the data can actually close (--window 7/10) trades horizon for
# resolvability and yields a real, if shorter-horizon, hit rate.
WINDOW_DAYS = 21

# --to-month-end: use each row's own logged HorizonDays (distance to that
# month's last Tuesday) instead of one fixed window. This measures exactly the
# production question — "will it touch before the rebalance date?" — where the
# window differs per row. Rows logged before HorizonDays existed fall back to
# WINDOW_DAYS.
# DEFAULT TRUE since 2026-08-31 — see the docstring. Set False by passing an
# explicit --window N. Falls back to WINDOW_DAYS (loudly) if the log predates
# the HorizonDays column.
USE_LOGGED_HORIZON = True

# Exclude levels already inside the touch band on the log date. Those are
# arithmetically guaranteed hits carrying zero predictive content, and they
# inflate S1/R1 by ~7pp on this sample. Off by default (keeps the historical
# definition); --exclude-day0 turns it on.
EXCLUDE_DAY0 = False

# Restrict to one month's log dates, e.g. "2026-08". None = whole log.
MONTH = None

# Drop rows whose logged CMP matches no bar in the price archive for its own
# date. Those come from pre-settlement evening runs and mid-session live-tick
# rows (see audit_sr_log.py); 27% of August's cohort carried one, concentrated
# in five whole-panel dates. LOG_PREVIOUS_SESSION closed the leak on 2026-08-19
# — zero contaminated rows since — so this only ever affects historical months.
EXCLUDE_CONTAMINATED = False
CONTAM_TOL = 0.0005   # same 0.05% float-noise tolerance as audit_sr_log.TOL

# Bootstrap draws for the date-clustered confidence intervals.
BOOTSTRAP_N = 4000


# ──────────────────────────────────────────────
# LOAD
# ──────────────────────────────────────────────

def load_log():
    if not os.path.exists(LOG_PATH):
        print(f"❌ No log found at {LOG_PATH}. Run sr_daily_logger.py first.")
        sys.exit(1)
    df = pd.read_csv(LOG_PATH)
    # Month files (sr_month_YYYY-MM.csv) carry AVG summary rows whose Date is
    # the literal "AVG". Those are derived output, not observations — drop them
    # before anything tries to parse dates, or they become NaT and (worse) get
    # scored as if they were a logged snapshot.
    if "Date" in df.columns:
        df = df[df["Date"].astype(str).str.upper() != "AVG"]
    df["Date"] = pd.to_datetime(df["Date"], errors="coerce", format="mixed")
    # Guard the malformed-row class that has bitten this repo before (a
    # truncated download leaves a row whose Date won't parse). Those survive as
    # NaT and break any date comparison downstream.
    df = df[df["Date"].notna()]
    return df.sort_values(["Symbol", "Date"])


# v2 went live 2026-07-04; rows before it came from the old analog scan whose
# probabilities are on a different scale and would corrupt calibration.
#
# This is deliberately DATE-anchored, not range-anchored. An earlier version
# keyed on v2's 57-78 output band, which broke the moment the P(touch) table
# replaced the bounce table — P(touch) legitimately spans ~5-95%, so a band
# test would have silently discarded most valid modern rows. The cutover date
# is a fixed historical fact and cannot drift with the scorer.
V2_START_DATE = pd.Timestamp("2026-07-04")


def drop_legacy_rows(df):
    """Drop pre-v2 rows (logged before V2_START_DATE)."""
    if "Date" not in df.columns:
        return df, 0
    legacy = df["Date"] < V2_START_DATE
    return df[~legacy], int(legacy.sum())


def load_price(symbol):
    path = os.path.join(PRICE_DIR, f"{symbol}.NS.csv")
    if not os.path.exists(path):
        # ETFs (GOLDBEES) live in etf_data/, not price_data/ — see
        # support_resistance.load_stock for why they must stay separate.
        path = os.path.join("../data/etf_data/", f"{symbol}.NS.csv")
        if not os.path.exists(path):
            return None
    df = pd.read_csv(path, parse_dates=["Date"])
    df = df.sort_values("Date").set_index("Date")
    for col in ["High", "Low", "Close"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return df.dropna(subset=["High", "Low", "Close"])


# ──────────────────────────────────────────────
# STATISTICAL UNIT: THE DATE, NOT THE ROW
# ──────────────────────────────────────────────

def clustered_ci(df, col="Hit", n_boot=None):
    """95% CI for a rate, resampling DATES rather than rows.

    The panel logs ~61 symbols on the same session, and on any given day they
    share the market's move — a quiet tape suppresses touches across the whole
    panel at once. Treating those 61 rows as independent observations
    understates the interval by roughly sqrt(panel size), which is enough to
    make an ordinary month look like a significant deviation. Resampling whole
    dates keeps the within-day correlation intact.

    This is the same lesson as the 2026-08-15 touch-table calibration study,
    which had to switch its statistical unit from rows to dates before its
    result meant anything.
    """
    if "Date" not in df.columns or not len(df):
        return (float("nan"), float("nan"))
    dates = df["Date"].unique()
    if len(dates) < 2:
        return (float("nan"), float("nan"))
    groups = [df.loc[df["Date"] == d, col].to_numpy(dtype=float) for d in dates]
    rng = np.random.default_rng(0)   # fixed seed: same log => same interval
    n = len(groups)
    means = np.empty(n_boot or BOOTSTRAP_N)
    for i in range(len(means)):
        pick = rng.integers(0, n, n)
        means[i] = np.concatenate([groups[j] for j in pick]).mean()
    return float(np.percentile(means, 2.5)), float(np.percentile(means, 97.5))


def fmt_ci(lo, hi):
    if lo != lo or hi != hi:      # NaN
        return "   —          "
    return f"[{lo*100:5.1f},{hi*100:5.1f}]"


def contaminated_rows(log_df):
    """(Date, Symbol) pairs whose logged CMP matches no archive bar for that
    date. Recomputed here rather than read from audit_sr_log's CSV so the
    exclusion can never silently run against a stale audit."""
    bad = set()
    cache = {}
    for _, r in log_df.iterrows():
        sym = r["Symbol"]
        if sym not in cache:
            cache[sym] = load_price(sym)
        pdf = cache[sym]
        if pdf is None or pd.isna(r.get("CMP")):
            continue
        d = r["Date"]
        if d not in pdf.index:
            continue
        close = float(pdf.loc[d, "Close"])
        cmp_ = float(r["CMP"])
        if close > 0 and abs(cmp_ - close) / close > CONTAM_TOL:
            bad.add((d, sym))
    return bad


def annualised_vol(returns):
    return float(returns.std() * np.sqrt(252) * 100) if len(returns) >= 3 else None


# ──────────────────────────────────────────────
# 0. LOG DATA QUALITY
# ──────────────────────────────────────────────

def coverage_report(log_df):
    """Surface pipeline defects BEFORE any accuracy number is read.

    A bad hit rate caused by a broken pipeline is a completely different
    finding from a miscalibrated model, and the three failure modes below have
    all actually occurred in this repo: duplicate (date,symbol) pairs from
    wall-clock stamping, partial days from a stale-data/freshness bug, and
    repeated identical snapshots from a symbol's CSV silently not updating.
    """
    print(f"\n{'='*70}")
    print(f"  0. LOG DATA QUALITY")
    print(f"{'='*70}")

    dup = log_df.duplicated(subset=["Date", "Symbol"]).sum()
    print(f"  Duplicate (Date,Symbol) pairs: {dup}" + ("  ⚠️" if dup else "  ✅"))

    per_day = log_df.groupby("Date")["Symbol"].nunique()
    if len(per_day):
        panel = int(per_day.max())
        partial = per_day[per_day < panel * 0.8]
        print(f"  Panel size (max symbols on a day): {panel}")
        if len(partial):
            print(f"  ⚠️  {len(partial)} partial day(s) — under 80% of the panel "
                  f"(stale-data / missed-run class):")
            for d, c in partial.items():
                print(f"      {pd.Timestamp(d).date()}  {c}/{panel} symbols")
        else:
            print("  ✅ No partial days")

    # Frozen series: identical CMP on consecutive log dates means that
    # symbol's price CSV did not update — the freshness bug. Levels recomputed
    # from an unchanged file are a duplicate observation, not new evidence.
    frozen = []
    for sym, grp in log_df.sort_values("Date").groupby("Symbol"):
        if len(grp) < 3:
            continue
        cmp_ = pd.to_numeric(grp["CMP"], errors="coerce")
        streak = int((cmp_.diff() == 0).astype(int).groupby(
            (cmp_.diff() != 0).cumsum()).sum().max() or 0)
        if streak >= 2:
            frozen.append((sym, streak))
    if frozen:
        print(f"  ⚠️  {len(frozen)} symbol(s) with an unchanged CMP across "
              f"consecutive log dates (possible stale CSV):")
        for s, k in sorted(frozen, key=lambda x: -x[1])[:8]:
            print(f"      {s}: {k+1} consecutive identical closes")
    else:
        print("  ✅ No frozen price series")


# ──────────────────────────────────────────────
# 1. BASIC HIT-RATE PER LOGGED SNAPSHOT
# ──────────────────────────────────────────────

def check_touch(price_df, log_date, level, direction, window_days=None):
    """
    direction: 'down' = check Low reaches level (support)
               'up'   = check High reaches level (resistance)

    Returns True (touched inside the window), False (RESOLVED miss — the full
    window elapsed without a touch), or None (window still open; outcome
    genuinely unknown, must not be scored either way).

    The True/None distinction is the whole ballgame: scoring an open window as
    a miss deflates the rate, and dropping open windows while keeping every hit
    (the previous `resolved = FwdDays>=21 | Hit` filter) forces exactly 100%.
    """
    W = window_days or WINDOW_DAYS
    future = price_df[price_df.index > log_date]
    n_bars = len(future)

    # SYMMETRY REQUIREMENT: score a snapshot only when the full W-bar window
    # exists. Returning True the moment a touch happens (even at bar 3) while
    # misses must wait the full W biases the sample catastrophically — hits
    # resolve early, misses never resolve, and the rate pins at 100%. That is
    # exactly the bug this replaced. Both outcomes must face the same bar count.
    if n_bars < W:
        return None

    window = future.head(W)
    if direction == "down":
        return bool((window["Low"] <= level * (1 + TOUCH_PCT)).any())
    return bool((window["High"] >= level * (1 - TOUCH_PCT)).any())


def already_touching(cmp_, level, direction):
    """True if the level sits inside the touch band at log time — a guaranteed
    hit with no predictive content."""
    if cmp_ is None or not cmp_ or pd.isna(cmp_):
        return False
    if direction == "down":
        return float(cmp_) <= float(level) * (1 + TOUCH_PCT)
    return float(cmp_) >= float(level) * (1 - TOUCH_PCT)


RES_COLUMNS = ["Level", "Symbol", "Prob", "N", "Hit", "FwdDays",
               "Date", "Horizon", "DistPct", "VolAssumed", "VolRealised"]


def analyse_hit_rates(log_df):
    # Does the log actually carry per-row horizons? --to-month-end silently
    # falls back to WINDOW_DAYS without them, which would misreport what was
    # measured — the same class of silent-fallback bug that produced the fake
    # 100%. Say plainly which horizon is in force.
    have_horizon = ("HorizonDays" in log_df.columns
                    and log_df["HorizonDays"].notna().any())
    horizon_desc = f"within {WINDOW_DAYS} days of log date"
    if USE_LOGGED_HORIZON:
        if have_horizon:
            hd = pd.to_numeric(log_df["HorizonDays"], errors="coerce").dropna()
            horizon_desc = (f"by each row's own month-end "
                            f"({int(hd.min())}-{int(hd.max())} days, per row)")
        else:
            horizon_desc = (f"within {WINDOW_DAYS} days "
                            f"[--to-month-end IGNORED: log has no HorizonDays yet]")

    print(f"\n{'='*70}")
    print(f"  1. HIT-RATE — did price touch S1 / R1 {horizon_desc}?")
    if EXCLUDE_DAY0:
        print(f"     (excluding levels already inside the {TOUCH_PCT*100:.0f}% touch band at log time)")
    print(f"{'='*70}")

    results = []
    price_cache = {}
    adjusted_skips = {}
    open_windows = {}
    day0_skips = {}

    for _, row in log_df.iterrows():
        sym = row["Symbol"]
        if sym not in price_cache:
            price_cache[sym] = load_price(sym)
        pdf = price_cache[sym]
        if pdf is None:
            continue

        log_date = row["Date"]
        fwd_days = min(len(pdf[pdf.index > log_date]), 21)

        # Corporate-action guard: the logged CMP is the close of log_date. If
        # the CSV's close for that same date has since shifted materially,
        # yfinance back-adjusted the series after we logged (split/bonus) —
        # every logged level is in pre-adjustment price terms and would score
        # garbage against the adjusted forward bars. Skip those rows. (Same
        # >15% threshold as data_integrity_check's held-name detector, which
        # does NOT cover panel names nobody holds — this guard is S/R's own.)
        if pd.notna(row.get("CMP")) and log_date in pdf.index:
            ref = float(pdf.loc[log_date, "Close"])
            if ref > 0 and not 0.85 < float(row["CMP"]) / ref < 1.15:
                adjusted_skips[sym] = float(row["CMP"]) / ref
                continue

        for lvl, direction in [("S1", "down"), ("R1", "up"),
                               ("S2", "down"), ("R2", "up")]:
            if not pd.notna(row.get(lvl)):
                continue
            if EXCLUDE_DAY0 and already_touching(row.get("CMP"), row[lvl], direction):
                day0_skips[lvl] = day0_skips.get(lvl, 0) + 1
                continue
            # Per-row horizon when requested and available, else the flag/default.
            W = WINDOW_DAYS
            if USE_LOGGED_HORIZON and pd.notna(row.get("HorizonDays")):
                W = int(row["HorizonDays"])
            if W <= 0:
                continue
            hit = check_touch(pdf, log_date, row[lvl], direction, W)
            if hit is not None:   # None = window still open, not scoreable
                cmp_ = row.get("CMP")
                dist = (float(row[lvl]) / float(cmp_) - 1) * 100 \
                    if pd.notna(cmp_) and float(cmp_) else np.nan
                # Vol the table ASSUMED (trailing 252d, what reach_probability_v2
                # keys on) vs vol that actually MATERIALISED over the window —
                # the pair section 1c needs to tell a quiet month from decay.
                hist = pdf.loc[pdf.index <= log_date, "Close"].pct_change().dropna().tail(252)
                fut = pdf.loc[pdf.index > log_date, "Close"].pct_change().dropna().head(W)
                results.append((lvl, sym, row[f"{lvl}_prob"], row[f"{lvl}_n"],
                                hit, fwd_days, log_date, W, dist,
                                annualised_vol(hist) if len(hist) >= 30 else np.nan,
                                annualised_vol(fut)))
            else:
                open_windows[lvl] = open_windows.get(lvl, 0) + 1

    if adjusted_skips:
        print("  ⚠️  Skipped symbols whose price history was back-adjusted AFTER "
              "logging (split/bonus — logged levels are in pre-adjustment terms):")
        for s, r in sorted(adjusted_skips.items()):
            print(f"      {s}: logged CMP is {r:.2f}x the CSV's close on the log date")

    if not results:
        print("  Not enough time has passed since logging to evaluate any touches yet.")
        print("  (Need at least ~5 trading days after a log date to check.)")
        return pd.DataFrame(columns=RES_COLUMNS)

    if day0_skips:
        print(f"  Excluded {sum(day0_skips.values())} level(s) already inside the "
              f"touch band at log time (guaranteed hits): "
              + ", ".join(f"{k} {v}" for k, v in sorted(day0_skips.items())))

    res_df = pd.DataFrame(results, columns=RES_COLUMNS)

    # Every row here is RESOLVED by construction: check_touch returned None for
    # any window that hadn't completed, and those never entered `results`. So a
    # False is a real miss and the mean is an honest rate — no filter needed.
    # (The previous `FwdDays>=21 | Hit` filter kept all hits but dropped every
    # unresolved miss, which forced 100% whenever no window had closed.)
    print(f"  {'':4} {'hit rate':>9} {'n':>6} {'dates':>6}  {'95% CI (date-clustered)':>24}")
    for lvl in ["S1", "R1", "S2", "R2"]:
        sub = res_df[res_df["Level"] == lvl]
        if not len(sub):
            print(f"  {lvl:4} {'—':>9}   no resolved snapshots yet")
            continue
        lo, hi = clustered_ci(sub)
        line = (f"  {lvl:4} {sub['Hit'].mean()*100:8.1f}% {len(sub):6d} "
                f"{sub['Date'].nunique():6d}  {fmt_ci(lo, hi):>24}")
        pending = open_windows.get(lvl, 0)
        if pending:
            line += f"   [{pending} open]"
        print(line)

    if len(res_df):
        print(f"\n  Overall hit rate: {res_df['Hit'].mean()*100:.1f}%  "
              f"(n={len(res_df)} resolved, {sum(open_windows.values())} still open)")
    else:
        print(f"\n  No {WINDOW_DAYS}-day window has completed yet. Re-run with "
              f"--window 7 or --window 10 for a shorter but resolvable horizon.")

    if sum(open_windows.values()) > len(res_df):
        print(f"  ⚠️  More windows are open than resolved — the resolved set is "
              f"skewed toward EARLIER log dates. Treat as provisional.")

    return res_df


# ──────────────────────────────────────────────
# 1b. HORIZON STRATIFICATION
# ──────────────────────────────────────────────

def analyse_by_horizon(res_df):
    """The production horizon SHRINKS through the month, so the pooled rate
    mixes two different questions.

    Every row in a month points at the same last-Tuesday horizon end, so
    HorizonDays counts down (16 early in the month, 1 on the final day). "Will
    price touch this tomorrow" and "within sixteen sessions" are not the same
    question, and P(touch) is monotone in horizon by construction — pooling
    them produces an average of two things nobody asked. Splitting also gives
    the cleanest single check of whether horizon scaling works at all: the
    model's predictions should track the realised rate ACROSS these buckets.
    """
    print(f"\n{'='*70}")
    print("  1b. BY HORIZON LENGTH — the production window shrinks through the month")
    print(f"{'='*70}")
    d = res_df[res_df["Level"].isin(["S1", "R1"])].dropna(subset=["Prob"])
    if len(d) < 20 or d["Horizon"].nunique() < 2:
        print("  Not enough spread in horizon length to stratify.")
        return
    print(f"  {'horizon':>8} {'dates':>6} {'n':>6} {'actual':>8} {'predicted':>10} {'gap':>8}")
    rows = []
    MIN_BUCKET = 20   # below this a bucket is one or two symbols' luck
    for H, g in d.groupby("Horizon"):
        act, pred = g["Hit"].mean() * 100, g["Prob"].mean()
        thin = len(g) < MIN_BUCKET
        if not thin:
            rows.append((H, act, pred))
        print(f"  {int(H):7d}d {g['Date'].nunique():6d} {len(g):6d} {act:7.1f}% "
              f"{pred:9.1f}% {act - pred:+7.1f}"
              + ("   thin — excluded from the correlations below" if thin else ""))
    if len(rows) >= 3:
        # Correlations run on well-populated buckets only. A bucket holding two
        # observations swings between 0% and 100% on one symbol's move, and a
        # handful of those is enough to drag the correlation down and make
        # working horizon scaling look broken. --exclude-contaminated thins
        # specific horizons hard (it removes whole panel-days), so this guard
        # matters exactly when the exclusion is on.
        a = pd.DataFrame(rows, columns=["H", "act", "pred"])
        print(f"\n  (correlations over {len(a)} buckets with n>={MIN_BUCKET})")
        print(f"\n  corr(horizon, actual)    = {a['H'].corr(a['act']):+.3f}"
              "   does reality depend on horizon?")
        print(f"  corr(horizon, predicted) = {a['H'].corr(a['pred']):+.3f}"
              "   does the model track it?")
        print(f"  corr(predicted, actual)  = {a['pred'].corr(a['act']):+.3f}"
              "   <- near 1.0 means the SHAPE is right even if the LEVEL is off")


# ──────────────────────────────────────────────
# 1c. VOLATILITY REGIME
# ──────────────────────────────────────────────

def analyse_vol_regime(res_df):
    """Report the tape the month actually delivered, next to the tape the
    table assumed.

    WHY THIS SECTION EXISTS. reach_probability_v2 keys on distance x TRAILING
    252-day volatility, so it cannot see a volatility regime that changes
    inside the month. August 2026 realised 5.4% annualised on the index — the
    0.3rd percentile of every 16-session window since 2010, against a 13.3%
    trailing figure — and the model duly ran ~10pp hot. Conditioned on the
    regime that materialised, it was calibrated (+5.1pp) where vol came in at
    or above what was assumed, and hot (-11.7pp) where it came in quieter.

    Without these numbers printed beside the hit rate, the next reader sees a
    32% rate against a "65-68%" note and concludes the model has decayed. This
    project has thrown away or misread working instruments three times that
    way (the fake 100% S/R hit rate, a cash-only month scored "consistent",
    call_report silently broken for 10 days), and an unexplained rate is the
    most likely route to discarding a model that is in fact fine.

    It is DIAGNOSTIC, not a correction: realised vol is not knowable at log
    time, so this explains a month, it never adjusts a probability.
    """
    print(f"\n{'='*70}")
    print("  1c. VOLATILITY REGIME — the tape the month delivered vs the tape assumed")
    print(f"{'='*70}")
    d = res_df.dropna(subset=["VolAssumed", "VolRealised", "Prob"]).copy()
    if len(d) < 30:
        print("  Not enough scored rows carrying both volatility measures.")
        return
    d["ratio"] = d["VolRealised"] / d["VolAssumed"]
    d = d[np.isfinite(d["ratio"])]
    print(f"  median trailing-252d vol the table USED    : {d['VolAssumed'].median():5.1f}%")
    print(f"  median vol actually REALISED over horizon  : {d['VolRealised'].median():5.1f}%")
    print(f"  median ratio realised/assumed              : {d['ratio'].median():5.2f}"
          f"   ({(d['ratio'].median()-1)*100:+.0f}% vs assumed)")
    print(f"  share of rows quieter than assumed         : {(d['ratio'] < 1).mean()*100:5.0f}%")

    idx_line = _index_tape_context(d["Date"].min(), d["Date"].max())
    if idx_line:
        print(idx_line)

    print(f"\n  Calibration split on the regime that MATERIALISED:")
    print(f"  {'subset':>34} {'n':>6} {'dates':>6} {'actual':>8} {'pred':>7} {'gap':>8}")
    for label, sub in [("realised >= 90% of assumed", d[d["ratio"] >= 0.9]),
                       ("realised <  90% of assumed", d[d["ratio"] < 0.9])]:
        if len(sub) < 20:
            print(f"  {label:>34} {len(sub):6d}   (too few to read)")
            continue
        act, pred = sub["Hit"].mean() * 100, sub["Prob"].mean()
        print(f"  {label:>34} {len(sub):6d} {sub['Date'].nunique():6d} "
              f"{act:7.1f}% {pred:6.1f}% {act - pred:+7.1f}")
    print("\n  READ THIS BEFORE JUDGING THE HIT RATE. A P(touch) table fitted on ~11y of")
    print("  average tape SHOULD overpredict touches in an unusually quiet month — that is")
    print("  an unconditional model meeting an unusual month, not decay. Only a gap that")
    print("  persists in the 'realised >= assumed' row is evidence against the model.")


def _index_tape_context(start, end):
    """Where this month's index volatility sits in the archive's own history."""
    try:
        idx = pd.read_csv("../data/index_data/nifty50.csv")
        idx["Date"] = pd.to_datetime(idx["Date"], errors="coerce")
        idx = idx.dropna(subset=["Date"]).sort_values("Date").set_index("Date")
        close = pd.to_numeric(idx["Close"], errors="coerce").dropna()
    except Exception:
        return None
    win = close.loc[start:end]
    if len(win) < 5:
        return None
    r = close.pct_change().dropna()
    month_vol = annualised_vol(win.pct_change().dropna())
    if month_vol is None:
        return None
    roll = (r.rolling(max(5, len(win))).std() * np.sqrt(252) * 100).dropna()
    pct = float((roll < month_vol).mean() * 100) if len(roll) else float("nan")
    return (f"\n  index realised vol over this cohort        : {month_vol:5.1f}% ann\n"
            f"  percentile vs all {len(win)}-session windows    : {pct:5.1f}th"
            f"   (median window {roll.median():.1f}%)")


# ──────────────────────────────────────────────
# 1d. ACTED-ON OUTCOMES
# ──────────────────────────────────────────────

# Threshold for the flow-change race, in percent either side of the level.
FLOW_THRESH = 2.0


def _race(after, level, direction, thresh):
    """After a touch, did price move `thresh`% the RIGHT way before the wrong way?

    STARTS ON THE BAR AFTER THE TOUCH, deliberately. The touch bar cannot be
    used: price reaches a support by FALLING to it, so that same bar's High sits
    above the level by construction and "moved favourably" fires on bar zero for
    almost every observation — measured, it put the 1% race at 88%. Daily bars
    also carry no intraday sequencing, so a same-bar high may have printed
    BEFORE the low. Same reason CLAUDE.md records that daily-Low touch tests
    overstate win rate ~9pp against a close-only rule.
    """
    r = after.iloc[1:]
    if len(r) < 2:
        return None
    for _, b in r.iterrows():
        if direction == "down":
            fav = float(b["High"]) >= level * (1 + thresh / 100)
            adv = float(b["Low"]) <= level * (1 - thresh / 100)
        else:
            fav = float(b["Low"]) <= level * (1 - thresh / 100)
            adv = float(b["High"]) >= level * (1 + thresh / 100)
        if fav or adv:
            return bool(fav and not adv)   # same-bar tie resolved AGAINST the level
    return False


def analyse_acted_outcomes(log_df):
    """What happened to someone who ACTED on the level, not just watched it.

    Three different questions, deliberately reported together because they give
    very different numbers and the subsystem is routinely read as answering all
    three at once:

      REACHED   — P(touch), what the table actually predicts.
      HELD      — reached, and price never closed back through it.
      FLOW      — reached, then moved FLOW_THRESH% the right way before the
                  wrong way. This is the "a level where the stock turns"
                  reading, and it is the OLD sr_reach_table's touch-and-bounce
                  metric, which the P(touch) rebuild deliberately replaced.

    THE CONTROL COLUMN IS NOT OPTIONAL. Measured on 2026-08, FLOW came out at
    70% — which looks like a large edge until you place a level at a comparable
    distance that has nothing to do with support structure, which also scores
    71%. Two mechanical effects inflate the raw number: a bar whose Low touches
    the level almost always CLOSES above its own low (91% of August's support
    touches closed favourably on the touch bar itself, median +1.11%), and a
    quiet mean-reverting tape bounces off any price. Printing FLOW without the
    control would manufacture an edge out of both.

    The control permutes DISTANCES across the symbols logged on the same date:
    same distance distribution, same tape, no link to where support actually
    sits. An earlier version rebuilt the level from its own distance
    (cmp0*(1-dist)), which reconstructs the level EXACTLY and reported a 0.0
    difference by construction.
    """
    print(f"\n{'='*70}")
    print(f"  1d. ACTED-ON OUTCOMES — reached / held / changed flow (+-{FLOW_THRESH:.0f}%)")
    print(f"{'='*70}")

    rng = np.random.default_rng(0)
    cache = {}
    real, ctrl, hs = [], [], []
    pending = {}

    for _, row in log_df.iterrows():
        sym = row["Symbol"]
        if sym not in cache:
            cache[sym] = load_price(sym)
        pdf = cache[sym]
        if pdf is None:
            continue
        W = WINDOW_DAYS
        if USE_LOGGED_HORIZON and pd.notna(row.get("HorizonDays")):
            W = int(row["HorizonDays"])
        if W <= 0:
            continue
        fwd = pdf[pdf.index > row["Date"]].head(W)
        if len(fwd) < W:          # horizon not closed — not scoreable
            continue
        cmp_ = row.get("CMP")
        if pd.isna(cmp_) or float(cmp_) <= 0:
            continue
        cmp_ = float(cmp_)

        for side, direction in [("S1", "down"), ("R1", "up")]:
            lv = row.get(side)
            if pd.isna(lv) or float(lv) <= 0:
                continue
            L = float(lv)
            tou = (fwd["Low"] <= L * (1 + TOUCH_PCT)) if direction == "down" \
                else (fwd["High"] >= L * (1 - TOUCH_PCT))
            touched = bool(tou.any())
            rec = {"Date": row["Date"], "side": side, "touched": touched,
                   "held": np.nan, "flow": np.nan, "ret": np.nan}
            if touched:
                f = fwd.index[tou.argmax()]
                after = fwd.loc[f:]
                end = float(after["Close"].iloc[-1])
                rec["held"] = (not bool((after["Close"] < L).any())) if direction == "down" \
                    else (not bool((after["Close"] > L).any()))
                rec["ret"] = (end / L - 1) * 100 if direction == "down" \
                    else (L / end - 1) * 100
                rec["flow"] = _race(after, L, direction, FLOW_THRESH)
                c0 = float(after["Close"].iloc[0])
                hs.append(c0 / L - 1 if direction == "down" else 1 - c0 / L)
            real.append(rec)
            pending.setdefault((row["Date"], side), []).append(
                (cmp_, abs(L / cmp_ - 1), direction, fwd))

    if not real:
        print("  No level has a closed horizon yet.")
        return

    for (dt, side), grp in pending.items():
        if len(grp) < 3:
            continue
        for (cmp_, _, direction, fwd), dc in zip(
                grp, rng.permutation([g[1] for g in grp])):
            Lc = cmp_ * (1 - dc) if direction == "down" else cmp_ * (1 + dc)
            tc = (fwd["Low"] <= Lc * (1 + TOUCH_PCT)) if direction == "down" \
                else (fwd["High"] >= Lc * (1 - TOUCH_PCT))
            if not tc.any():
                continue
            after = fwd.loc[fwd.index[tc.argmax()]:]
            res = _race(after, Lc, direction, FLOW_THRESH)
            if res is not None:
                ctrl.append({"Date": dt, "side": side, "flow": res})

    R = pd.DataFrame(real)
    C = pd.DataFrame(ctrl)
    print(f"  {'':>5} {'levels':>7} {'reached':>9} {'+HELD':>9} {'+FLOW':>9} "
          f"{'control':>9} {'mean ret':>10}")
    for side in ["S1", "R1"]:
        g = R[R["side"] == side]
        if not len(g):
            continue
        t = g[g["touched"]]
        c = C[C["side"] == side] if len(C) else C
        cf = f"{c['flow'].mean()*100:8.0f}%" if len(c) else "       —"
        print(f"  {side:>5} {len(g):7d} {g['touched'].mean()*100:8.0f}% "
              f"{t['held'].mean()*100:8.0f}% {t['flow'].mean()*100:8.0f}% {cf} "
              f"{t['ret'].mean():+9.2f}%")
    print(f"\n  reached  = P(touch), the quantity the table predicts")
    print(f"  +HELD    = of those reached, price never closed back through the level")
    print(f"  +FLOW    = of those reached, moved {FLOW_THRESH:.0f}% the right way first")
    print(f"  control  = SAME race at a permuted distance, unrelated to support structure")

    # HELD scales with horizon and is NOT comparable across rows logged at
    # different points in the month: with one session left a level can barely
    # be broken, so late-month rows inflate it. Pooled HELD read 53% on August
    # while the month-START levels (16 sessions to run) held only 28%.
    ht = R[R["touched"]].copy()
    if "Horizon" not in ht.columns and len(ht):
        ht = ht.join(log_df.set_index(["Date"])[[]], how="left")
    print(f"\n  HELD BY HORIZON — it is nearly automatic when little time is left, so the")
    print(f"  pooled figure above is not comparable across the month:")
    hh = R[R["touched"]].merge(
        log_df[["Date", "Symbol", "HorizonDays"]].drop_duplicates("Date"),
        on="Date", how="left")
    if "HorizonDays" in hh.columns and hh["HorizonDays"].notna().any():
        hh["hb"] = pd.cut(pd.to_numeric(hh["HorizonDays"], errors="coerce"),
                          [0, 4, 8, 12, 25],
                          labels=["1-4d left", "5-8d", "9-12d", "13d+"])
        for b, g in hh.groupby("hb", observed=True):
            if len(g) < 10:
                continue
            print(f"      {str(b):>10}  n={len(g):4d}   held {g['held'].mean()*100:3.0f}%"
                  f"   flow {g['flow'].mean()*100:3.0f}%")

    if len(C) and len(R[R["touched"]]):
        a = R[R["touched"]]["flow"].dropna().to_numpy(dtype=float)
        b = C["flow"].dropna().to_numpy(dtype=float)
        if len(a) > 20 and len(b) > 20:
            diff = np.array([rng.choice(a, len(a), True).mean()
                             - rng.choice(b, len(b), True).mean() for _ in range(4000)])
            print(f"\n  FLOW minus control: {diff.mean()*100:+.1f}pp  "
                  f"95% CI [{np.percentile(diff,2.5)*100:+.1f}, "
                  f"{np.percentile(diff,97.5)*100:+.1f}]  "
                  f"P(level better) {(diff>0).mean()*100:.0f}%")
            print(f"  A control this close means the raw FLOW number is the TAPE, not the level.")
    if hs:
        h = np.array(hs)
        print(f"\n  Mechanical head start: the touch bar itself closes favourably "
              f"{(h>0).mean()*100:.0f}% of")
        print(f"  the time, median {np.median(h)*100:+.2f}% — a bar whose Low reaches a level "
              f"nearly always")
        print(f"  closes above its own low. Much of FLOW is that, banked before the race starts.")


# ──────────────────────────────────────────────
# 2. LEVEL DRIFT
# ──────────────────────────────────────────────

def analyse_drift(log_df):
    print(f"\n{'='*70}")
    print(f"  2. LEVEL DRIFT — how much did S1/R1 move over the logged period?")
    print(f"{'='*70}")
    print(f"  {'Symbol':<14} {'S1 first':>10} {'S1 last':>10} {'S1 drift%':>10}   "
          f"{'R1 first':>10} {'R1 last':>10} {'R1 drift%':>10}")
    print("  " + "─"*84)

    for sym, grp in log_df.groupby("Symbol"):
        grp = grp.sort_values("Date")
        s1_first, s1_last = grp["S1"].iloc[0], grp["S1"].iloc[-1]
        r1_first, r1_last = grp["R1"].iloc[0], grp["R1"].iloc[-1]

        s1_drift = round((s1_last - s1_first) / s1_first * 100, 1) if pd.notna(s1_first) and s1_first else None
        r1_drift = round((r1_last - r1_first) / r1_first * 100, 1) if pd.notna(r1_first) and r1_first else None

        s1_flag = " ⚠" if s1_drift is not None and abs(s1_drift) >= 5 else ""
        r1_flag = " ⚠" if r1_drift is not None and abs(r1_drift) >= 5 else ""

        print(f"  {sym:<14} {s1_first:>10.2f} {s1_last:>10.2f} {str(s1_drift)+'%':>9}{s1_flag}   "
              f"{r1_first:>10.2f} {r1_last:>10.2f} {str(r1_drift)+'%':>9}{r1_flag}")

    print(f"\n  ⚠ = level moved 5%+ over the period (less stable — treat with caution)")


# ──────────────────────────────────────────────
# 3. PROBABILITY CALIBRATION
# ──────────────────────────────────────────────

def analyse_calibration(res_df):
    print(f"\n{'='*70}")
    print(f"  3. PROBABILITY CALIBRATION — predicted prob vs actual hit-rate")
    print(f"{'='*70}")
    print(f"  ⓘ Scores the probabilities AS LOGGED. Rows predating the P(touch)")
    print(f"    table carry the OLD P(bounce|touched) metric and are dropped as")
    print(f"    legacy; 2026-08 is the first full month logged entirely under the")
    print(f"    new table AND under min-separation, so from that month on these")
    print(f"    buckets do judge the live model. Read section 1c FIRST — a quiet")
    print(f"    tape moves every bucket down at once and is not miscalibration.")

    if len(res_df) == 0:
        print("  No data yet — need touch results from section 1 first.")
        return

    res_df = res_df.dropna(subset=["Prob"])
    if len(res_df) == 0:
        print("  No probability data available.")
        return

    # Bucket edges must match the ACTIVE table's output range. The bounce table
    # was compressed into ~57-77 so it needed narrow bins there; the P(touch)
    # table legitimately spans ~5-95%, where those bins would leave most rows
    # in a single "0-57" bucket. Pick the geometry from the data itself.
    span = res_df["Prob"].max() - res_df["Prob"].min() if len(res_df) else 0
    if span > 30:      # wide-range table (P(touch))
        buckets = [(0, 20), (20, 40), (40, 60), (60, 75), (75, 90), (90, 101)]
    else:              # compressed legacy band (P(bounce|touched))
        buckets = [(0, 58), (58, 62), (62, 66), (66, 70), (70, 74), (74, 101)]
    print(f"  {'predicted bin':>14} {'mean pred':>10} {'actual':>8} {'n':>6} "
          f"{'dates':>6}  {'95% CI':>16}  gap")
    tot_w = tot_gap = 0.0
    for lo, hi in buckets:
        sub = res_df[(res_df["Prob"] >= lo) & (res_df["Prob"] < hi)]
        if len(sub) == 0:
            continue
        actual = sub["Hit"].mean() * 100
        # MEAN PREDICTED, not the bucket midpoint. The midpoint was a stand-in
        # for the real prediction and it misstates the gap whenever the bucket
        # is wide or rows cluster at one end: on the August cohort the 0-20
        # bucket averaged 5.7% predicted against a 9.5% midpoint, so the
        # midpoint version reported a 7pp error where the true one was 3pp.
        pred = sub["Prob"].mean()
        gap = actual - pred
        tot_w += len(sub); tot_gap += abs(gap) * len(sub)
        clo, chi = clustered_ci(sub)
        # Flag only when the prediction sits OUTSIDE the date-clustered
        # interval — a fixed +-10pp rule fires on noise in thin buckets and
        # stays silent on a real miss in a dense one.
        flag = ""
        if clo == clo and not (clo * 100 <= pred <= chi * 100):
            flag = "  OVERCONFIDENT" if gap < 0 else "  underconfident"
        print(f"  {f'{lo}-{hi-1}%':>14} {pred:9.1f}% {actual:7.1f}% {len(sub):6d} "
              f"{sub['Date'].nunique():6d}  {fmt_ci(clo, chi):>16} {gap:+6.1f}{flag}")
    if tot_w:
        print(f"\n  weighted mean |actual - predicted| = {tot_gap/tot_w:.1f}pp")

    print(f"\n  Well-calibrated = the predicted value sits inside the date-clustered CI.")
    print(f"  Overconfident   = model says high % but reality is lower — discount high scores.")

    _discrimination(res_df)


def _discrimination(res_df):
    """Separate RANKING quality from CALIBRATION.

    A model can be badly level-shifted and still order outcomes perfectly —
    which is exactly the August 2026 picture (10pp hot, AUC 0.839). Reporting
    only the calibration gap would condemn a model whose ranking is intact, so
    print both. AUC answers "does a higher logged probability actually mean a
    likelier touch"; Brier skill answers "is this worth more than always
    quoting the base rate".
    """
    y = res_df["Hit"].to_numpy(dtype=float)
    p = res_df["Prob"].to_numpy(dtype=float) / 100.0
    n1, n0 = y.sum(), len(y) - y.sum()
    if n1 < 5 or n0 < 5:
        return
    ranks = pd.Series(p).rank().to_numpy()
    auc = (ranks[y == 1].sum() - n1 * (n1 + 1) / 2) / (n1 * n0)
    brier = float(((p - y) ** 2).mean())
    base = y.mean()
    brier_ref = float(((base - y) ** 2).mean())
    skill = (1 - brier / brier_ref) * 100 if brier_ref else float("nan")
    print(f"\n  DISCRIMINATION (is the RANKING right, separately from the level?)")
    print(f"    AUC                     {auc:.3f}   0.5 = coin flip, 0.7 useful, 0.8+ strong")
    print(f"    Brier                   {brier:.4f}  vs {brier_ref:.4f} for always "
          f"predicting the {base*100:.1f}% base rate")
    print(f"    skill over base rate   {skill:+6.1f}%   positive = the probabilities carry "
          f"real information")
    print(f"  A large calibration gap with a high AUC is a LEVEL problem, not a broken model.")
    print(f"  Do NOT flat-shift the probabilities to close it on one month's evidence — that")
    print(f"  fits the month, and is wrong in the next one with a different volatility regime.")


# ──────────────────────────────────────────────
# 4. SAMPLE SIZE (n) SENSITIVITY
# ──────────────────────────────────────────────

def analyse_n_sensitivity(res_df):
    print(f"\n{'='*70}")
    print(f"  4. CELL SUPPORT (n) — hit rate by reach-table cell sample size")
    print(f"{'='*70}")
    print(f"  NOTE: under reach_probability_v2, n is the TRAINING-CELL size of the")
    print(f"  (distance x vol) bucket, not a per-signal trial count. Low n means the")
    print(f"  prediction came from a thin/fallback cell — trust it less. This is a")
    print(f"  data-coverage check, not the old 'more history = better' test.")

    if len(res_df) == 0:
        print("  No data yet.")
        return

    res_df = res_df.dropna(subset=["N"])
    if len(res_df) == 0:
        print("  No sample-size data available.")
        return

    buckets = [(0, 30), (30, 100), (100, 250), (250, 100000)]
    print(f"  {'n range':<14} {'Hit rate':>10} {'count':>8}")
    print("  " + "─"*36)
    for lo, hi in buckets:
        sub = res_df[(res_df["N"] >= lo) & (res_df["N"] < hi)]
        if len(sub) == 0:
            continue
        hr = sub["Hit"].mean() * 100
        label = f"{lo}-{hi if hi < 100000 else '+'}"
        print(f"  {label:<14} {hr:>9.1f}% {len(sub):>8}")

    print(f"\n  n<30 = thin/fallback cell (prob defaulted to base rate) — low confidence.")


# ──────────────────────────────────────────────
# 5. DISTANCE VS ACCURACY
# ──────────────────────────────────────────────

def analyse_distance(log_df, res_df):
    print(f"\n{'='*70}")
    print(f"  5. DISTANCE vs ACCURACY — do closer levels hit more often?")
    print(f"{'='*70}")

    if len(res_df) == 0:
        print("  No data yet.")
        return

    # merge distance info back in from log_df
    merged = []
    for _, row in res_df.iterrows():
        log_rows = log_df[(log_df["Symbol"] == row["Symbol"])]
        if len(log_rows) == 0:
            continue
        # approximate: use first log entry's CMP and level to get distance
        first = log_rows.iloc[0]
        cmp_  = first["CMP"]
        lvl_col = row["Level"]
        if lvl_col not in first or pd.isna(first[lvl_col]) or cmp_ == 0:
            continue
        level = first[lvl_col]
        if row["Level"] in ("S1", "S2"):
            dist = (cmp_ - level) / cmp_ * 100
        else:
            dist = (level - cmp_) / cmp_ * 100
        merged.append((dist, row["Hit"]))

    if not merged:
        print("  Not enough matched data.")
        return

    dist_df = pd.DataFrame(merged, columns=["Distance", "Hit"])
    buckets = [(0, 5), (5, 10), (10, 15), (15, 100)]
    print(f"  {'Distance range':<16} {'Hit rate':>10} {'count':>8}")
    print("  " + "─"*38)
    for lo, hi in buckets:
        sub = dist_df[(dist_df["Distance"] >= lo) & (dist_df["Distance"] < hi)]
        if len(sub) == 0:
            continue
        hr = sub["Hit"].mean() * 100
        label = f"{lo}-{hi if hi < 100 else '+'}%"
        print(f"  {label:<16} {hr:>9.1f}% {len(sub):>8}")

    print(f"\n  If accuracy drops sharply beyond 10-15%, tighten your proximity filter.")


# ──────────────────────────────────────────────
# MAIN
# ──────────────────────────────────────────────

def main():
    global TOUCH_PCT, LOG_PATH, WINDOW_DAYS, EXCLUDE_DAY0
    global USE_LOGGED_HORIZON, MONTH, EXCLUDE_CONTAMINATED
    if "--touch-pct" in sys.argv:
        idx = sys.argv.index("--touch-pct")
        TOUCH_PCT = float(sys.argv[idx + 1]) / 100
    # --window N: force ONE fixed forward horizon for every row, overriding the
    # per-row production horizon that is now the default. Use it to compare
    # against the 21d backtested figure, not as the headline.
    if "--window" in sys.argv:
        idx = sys.argv.index("--window")
        WINDOW_DAYS = int(sys.argv[idx + 1])
        USE_LOGGED_HORIZON = False
    # --to-month-end is now the DEFAULT and kept only so existing invocations
    # and the sr_monthly_review.sh script keep working unchanged.
    if "--to-month-end" in sys.argv:
        USE_LOGGED_HORIZON = True
    if "--exclude-day0" in sys.argv:
        EXCLUDE_DAY0 = True
    # --month YYYY-MM: restrict to one month's LOG dates. The natural cohort,
    # because every row in a month shares one horizon end (its last Tuesday).
    if "--month" in sys.argv:
        idx = sys.argv.index("--month")
        MONTH = sys.argv[idx + 1]
    if "--exclude-contaminated" in sys.argv:
        EXCLUDE_CONTAMINATED = True
    # --log <path>: analyse an alternate log, e.g. ../data/sr_dynamic_log.csv
    # (built by sr_dynamic_logger.py). Default stays the fixed-panel log.
    if "--log" in sys.argv:
        idx = sys.argv.index("--log")
        LOG_PATH = sys.argv[idx + 1]

    log_df = load_log()

    if "--keep-legacy" not in sys.argv:
        log_df, n_legacy = drop_legacy_rows(log_df)
        if n_legacy:
            print(f"\nDropped {n_legacy} row(s) logged before {V2_START_DATE.date()} "
                  f"(pre-v2 scorer, different probability scale; "
                  f"--keep-legacy to retain)")

    if MONTH:
        before = len(log_df)
        log_df = log_df[log_df["Date"].dt.strftime("%Y-%m") == MONTH]
        if not len(log_df):
            print(f"\nNo rows logged in {MONTH}.")
            sys.exit(0)
        print(f"\nCohort --month {MONTH}: kept {len(log_df)} of {before} rows")

    # Data quality is assessed on the RAW cohort, BEFORE any exclusion — a day
    # thinned by --exclude-contaminated is not a "partial day" (a missed run or
    # stale data), and reporting it as one would invent a pipeline fault that
    # does not exist.
    print(f"\nLoaded {len(log_df)} log rows across {log_df['Symbol'].nunique()} stocks")
    print(f"Date range: {log_df['Date'].min().date()} → {log_df['Date'].max().date()}")
    coverage_report(log_df)

    if EXCLUDE_CONTAMINATED:
        bad = contaminated_rows(log_df)
        if bad:
            keep = [(d, s) not in bad for d, s in zip(log_df["Date"], log_df["Symbol"])]
            dropped_dates = sorted({d.date() for d, _ in bad})
            log_df = log_df[keep]
            print(f"\nExcluded {len(bad)} row(s) whose CMP matches no archive bar for "
                  f"their own date,\n  across {len(dropped_dates)} date(s): "
                  + ", ".join(str(x) for x in dropped_dates[:8])
                  + (" ..." if len(dropped_dates) > 8 else ""))
        else:
            print("\nNo contaminated rows in this cohort — every CMP matches its "
                  "own date's archive bar.")

    res_df = analyse_hit_rates(log_df)
    analyse_by_horizon(res_df)
    analyse_vol_regime(res_df)
    analyse_acted_outcomes(log_df)
    analyse_drift(log_df)
    analyse_calibration(res_df)
    analyse_n_sensitivity(res_df)
    analyse_distance(log_df, res_df)

    print(f"\n{'='*70}\n")


if __name__ == "__main__":
    main()