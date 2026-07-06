import yfinance as yf
import pandas as pd

SECTORS = {
    "IT": "^CNXIT",
    "Banking": "^NSEBANK",
    "FMCG": "^CNXFMCG",
    "Auto": "^CNXAUTO",
    "Pharma": "^CNXPHARMA",
    "Metal": "^CNXMETAL",
    "Realty": "^CNXREALTY",
    "Energy": "^CNXENERGY"
}


def momentum(symbol):

    df = yf.download(symbol, period="6mo", progress=False)

    if df is None or df.empty:
        return None

    # ---- Handle MultiIndex columns ----
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)

    if "Close" not in df.columns:
        return None

    close = pd.to_numeric(df["Close"], errors="coerce").dropna()

    if len(close) < 2:
        return None

    return float(close.iloc[-1] / close.iloc[0] - 1)


def main():

    print("\n==============================")
    print("🔄 SECTOR ROTATION ANALYSIS")
    print("==============================")

    scores = {}

    for name, sym in SECTORS.items():

        m = momentum(sym)

        if m is not None:
            scores[name] = m

    ranked = sorted(scores.items(), key=lambda x: float(x[1]), reverse=True)

    print("\n🏆 LEADING SECTORS:")
    for s, v in ranked[:3]:
        print(f"• {s:10s} {v:.2%}")

    print("\n⚠️ LAGGING SECTORS:")
    for s, v in ranked[-3:]:
        print(f"• {s:10s} {v:.2%}")


if __name__ == "__main__":
    main()