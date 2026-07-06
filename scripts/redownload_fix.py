import yfinance as yf
import pandas as pd
import os

OUTPUT_DIR = "../data/price_data/"

stocks = [
    "AEGISVOPAK.NS", "AFCONS.NS", "AFFLE.NS", "AGARWALEYE.NS", "AIAENG.NS",
    "AKUMS.NS", "ANANTRAJ.NS", "ANGELONE.NS", "APARINDS.NS", "APLAPOLLO.NS",
    "APLLTD.NS", "GODREJPROP.NS", "GPIL.NS", "GRANULES.NS", "GRAPHITE.NS",
    "GRASIM.NS", "IDEA.NS", "IDFCFIRSTB.NS", "IEX.NS", "IFCI.NS", "IGIL.NS",
    "INDIACEM.NS", "INDIGO.NS", "MANKIND.NS", "PAYTM.NS", "PCBL.NS",
    "PERSISTENT.NS", "PETRONET.NS", "PFC.NS", "POWERGRID.NS", "SHREECEM.NS",
    "SHRIRAMFIN.NS", "TIMKEN.NS", "TITAGARH.NS", "TORNTPHARM.NS", "TORNTPOWER.NS",
    "TRENT.NS", "TRIDENT.NS", "TRITURBINE.NS", "WAAREEENER.NS", "WELCORP.NS",
    "WELSPUNLIV.NS", "WHIRLPOOL.NS", "WIPRO.NS", "ZFCVINDIA.NS", "ZYDUSLIFE.NS"
]

for sym in stocks:
    print(f"Downloading {sym}...")
    try:
        df = yf.download(sym, period="3y", interval="1d",
                         auto_adjust=True, progress=False)

        if df.empty:
            print(f"  ❌ No data")
            continue

        # Fix multi-level columns from newer yfinance
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)

        df = df.reset_index()
        df = df[["Date", "Close", "High", "Low", "Open", "Volume"]]
        df.to_csv(f"{OUTPUT_DIR}{sym}.csv", index=False)
        print(f"  ✅ {len(df)} rows saved")

    except Exception as e:
        print(f"  ❌ Error: {e}")

print("\nDone.")