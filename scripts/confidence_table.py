"""
Builds an EMPIRICAL confidence table: bucket every historical momentum setup by
its ret_126/vol_63 score, and record how those setups actually performed over the
next 21 trading days (win rate + median forward return + sample size).

This is the evidence behind the recommender's confidence numbers — a descriptive
conditional distribution from ~10 years of data, not a fitted/overfit model
(same philosophy as the empirical reach-probability table). Cached to
../data/confidence_table.json; rebuild with:  python confidence_table.py

All backward/forward stats are pooled across history purely to DESCRIBE the
score->outcome relationship; the table is not used to select stocks via any
lookahead, only to annotate live recommendations with historical base rates.

Setups are additionally gated to the F&O liquidity proxy (top UNIVERSE_TOP_N by
trailing turnover, POINT IN TIME per date — see strategy_config.py's Universe
gate comment / memory fno-universe-migration) so the win-rate/median base rates
shown to the user describe setups they could actually have traded, not the
full broad universe. Survivorship-free: the gate at each historical date only
uses trailing turnover up to that date.
"""
import os
import json
import numpy as np
import pandas as pd
import strategy_config as sc

PRICE_DIR = "../data/price_data/"
INDEX_PATH = "../data/index_data/nifty50.csv"
OUT_PATH  = "../data/confidence_table.json"

LOOKBACK = sc.LOOKBACK          # 126
HOLD     = sc.HOLD              # 21
VOL_WIN  = sc.VOL_WINDOW        # 63
UNIVERSE_TOP_N = sc.UNIVERSE_TOP_N
UNIVERSE_TURNOVER_WINDOW = sc.UNIVERSE_TURNOVER_WINDOW


def liquidity_rank_series():
    """For every (date, symbol), whether that symbol was in the top
    UNIVERSE_TOP_N by trailing turnover as of that date. Returns a
    DataFrame (dates x symbols) of booleans, aligned to the union of all
    price dates. Point-in-time / no lookahead: rank at date d uses only
    turnover up to and including d."""
    turnovers = {}
    for f in os.listdir(PRICE_DIR):
        if not f.endswith(".csv"):
            continue
        sym = f.replace(".csv", "")
        try:
            df = pd.read_csv(PRICE_DIR + f, usecols=["Date", "Close", "Volume"], parse_dates=["Date"], low_memory=False)
        except (ValueError, KeyError):
            continue
        df["Close"] = pd.to_numeric(df["Close"], errors="coerce")
        df["Volume"] = pd.to_numeric(df["Volume"], errors="coerce")
        df = df.dropna(subset=["Close", "Volume"]).sort_values("Date").set_index("Date")
        turnovers[sym] = df["Close"] * df["Volume"]

    matrix = pd.DataFrame(turnovers).sort_index().ffill(limit=5)
    trailing_median = matrix.rolling(UNIVERSE_TURNOVER_WINDOW, min_periods=UNIVERSE_TURNOVER_WINDOW).median()
    # rank(axis=1) ranks each row (date) across symbols; top-N by descending turnover
    ranks = trailing_median.rank(axis=1, ascending=False, method="first")
    gated = ranks <= UNIVERSE_TOP_N
    return gated


def regime_map():
    """date -> breadth-gated regime (BULL/SIDEWAYS/BEAR), computed from index + breadth."""
    df = pd.read_csv(INDEX_PATH, parse_dates=["Date"], low_memory=False)
    df["Close"] = pd.to_numeric(df["Close"], errors="coerce")
    idx = df.dropna(subset=["Close"]).sort_values("Date").set_index("Date")["Close"]
    ma50 = idx.rolling(50).mean()
    ma200 = idx.rolling(200).mean()

    # breadth from the price universe
    closes = {}
    for f in os.listdir(PRICE_DIR):
        if not f.endswith(".csv"):
            continue
        try:
            d = pd.read_csv(PRICE_DIR + f, parse_dates=["Date"], low_memory=False)
            d["Close"] = pd.to_numeric(d["Close"], errors="coerce")
            c = d.dropna(subset=["Close"]).sort_values("Date").set_index("Date")["Close"]
            if len(c) > 250:
                closes[f] = c
        except Exception:
            continue
    mat = pd.DataFrame(closes).sort_index().ffill(limit=5)
    breadth = (mat > mat.rolling(200).mean()).mean(axis=1).reindex(idx.index).ffill()

    reg = {}
    for dt in idx.index:
        price = idx.loc[dt]; m50 = ma50.loc[dt]; m200 = ma200.loc[dt]
        if pd.isna(m200):
            reg[dt] = "UNKNOWN"; continue
        if price > m50 > m200:
            b = breadth.loc[dt]
            reg[dt] = "SIDEWAYS" if (not pd.isna(b) and b < sc.BREADTH_BULL_MIN) else "BULL"
        elif price < m200:
            reg[dt] = "BEAR"
        else:
            reg[dt] = "SIDEWAYS"
    return pd.Series(reg)


def score_series(close):
    """ret_126/vol_63 momentum score at every date, plus the eligibility mask."""
    ret126 = close / close.shift(LOOKBACK) - 1
    ret63  = close / close.shift(63) - 1
    vol63  = close.pct_change(fill_method=None).rolling(VOL_WIN).std()
    ma50   = close.rolling(50).mean()
    score  = ret126 / vol63
    eligible = (ret126 > 0) & (ret63 > 0) & (close > ma50) & (vol63 > 0)
    return score, eligible


def build():
    reg = regime_map()
    liquidity = liquidity_rank_series()
    rows_score = []
    rows_fwd = []
    rows_reg = []
    files = [f for f in os.listdir(PRICE_DIR) if f.endswith(".csv")]
    for f in files:
        try:
            sym = f.replace(".csv", "")
            df = pd.read_csv(PRICE_DIR + f, parse_dates=["Date"], low_memory=False)
            df["Close"] = pd.to_numeric(df["Close"], errors="coerce")
            c = df.dropna(subset=["Close"]).sort_values("Date").set_index("Date")["Close"]
            if len(c) < LOOKBACK + HOLD + 60:
                continue
            score, elig = score_series(c)
            fwd = c.shift(-HOLD) / c - 1
            if sym in liquidity.columns:
                liquid_mask = liquidity[sym].reindex(c.index).fillna(False)
            else:
                liquid_mask = pd.Series(False, index=c.index)
            mask = elig & score.notna() & fwd.notna() & liquid_mask
            rows_score.append(score[mask].values)
            rows_fwd.append(fwd[mask].values)
            rows_reg.append(reg.reindex(c.index[mask]).values)
        except Exception:
            continue

    s = np.concatenate(rows_score)
    fwd = np.concatenate(rows_fwd)
    regs = np.concatenate(rows_reg)
    print(f"Pooled {len(s):,} eligible momentum setups across history")

    # regime-conditioned base rates (the "when to buy" evidence)
    regime_stats = {}
    for rg in ["BULL", "SIDEWAYS", "BEAR"]:
        m = regs == rg
        if m.sum() > 0:
            fk = fwd[m]
            regime_stats[rg] = {
                "n": int(m.sum()),
                "win_rate": round(float((fk > 0).mean()), 4),
                "median_fwd": round(float(np.median(fk)), 4),
            }

    # decile buckets by score
    edges = np.quantile(s, np.linspace(0, 1, 11))
    edges[0] = -np.inf
    edges[-1] = np.inf
    table = []
    for k in range(10):
        lo, hi = edges[k], edges[k + 1]
        m = (s >= lo) & (s < hi)
        fk = fwd[m]
        if len(fk) == 0:
            continue
        table.append({
            "decile": k + 1,
            "score_lo": None if np.isinf(lo) else round(float(lo), 4),
            "score_hi": None if np.isinf(hi) else round(float(hi), 4),
            "n": int(len(fk)),
            "win_rate": round(float((fk > 0).mean()), 4),
            "median_fwd": round(float(np.median(fk)), 4),
            "mean_fwd": round(float(np.mean(fk)), 4),
            "p25_fwd": round(float(np.percentile(fk, 25)), 4),
            "p75_fwd": round(float(np.percentile(fk, 75)), 4),
        })

    out = {
        "hold_days": HOLD,
        "lookback": LOOKBACK,
        "built_from_setups": int(len(s)),
        "score_edges": [None if np.isinf(e) else round(float(e), 4) for e in edges],
        "deciles": table,
        "regime_stats": regime_stats,
        "overall_win_rate": round(float((fwd > 0).mean()), 4),
        "overall_median_fwd": round(float(np.median(fwd)), 4),
    }
    with open(OUT_PATH, "w") as fp:
        json.dump(out, fp, indent=2)
    print(f"Saved -> {OUT_PATH}")
    print(f"Overall: win rate {out['overall_win_rate']:.1%}, median 21d {out['overall_median_fwd']:+.2%}")
    for d in table:
        print(f"  decile {d['decile']:>2}: win {d['win_rate']:.0%}  median {d['median_fwd']:+.2%}  n={d['n']:,}")
    print("Regime base rates:")
    for rg, st in regime_stats.items():
        print(f"  {rg:<9}: win {st['win_rate']:.0%}  median {st['median_fwd']:+.2%}  n={st['n']:,}")
    return out


def load():
    if not os.path.exists(OUT_PATH):
        return build()
    with open(OUT_PATH) as fp:
        return json.load(fp)


def confidence_for_score(table, score):
    """Return the historical decile stats for a given live score."""
    edges = table["score_edges"]
    edges = [(-np.inf if e is None and i == 0 else (np.inf if e is None else e))
             for i, e in enumerate(edges)]
    for k in range(10):
        lo = edges[k] if edges[k] is not None else -np.inf
        hi = edges[k + 1] if edges[k + 1] is not None else np.inf
        if score >= lo and score < hi:
            for d in table["deciles"]:
                if d["decile"] == k + 1:
                    return d
    return table["deciles"][-1]


if __name__ == "__main__":
    build()
