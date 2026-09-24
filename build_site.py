#!/usr/bin/env python3
"""
build_site.py
-------------
Packs the latest run into docs/data.json, which the dashboard fetches.
Runs after update_nse_data.py -> screen_stocks.py -> find_patterns.py.
"""

import os
import json
from datetime import datetime, timedelta, timezone

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
LIVE = os.path.join(HERE, "EQUITY_L_live.csv")
SIGS = os.path.join(HERE, "cup_handle_signals.csv")
DOCS = os.path.join(HERE, "docs")
OUT = os.path.join(DOCS, "data.json")

IST = timezone(timedelta(hours=5, minutes=30))

SCREEN_MAP = {
    "Symbol": "s", "Company": "n", "Last Price": "p", "Day Change %": "c",
    "RS Rating": "rs",
    "% From 20W High": "fh", "% Above 20W Low": "al", "20W High": "h",
    "20W Low": "l", "Volume": "v", "Avg Volume 20d": "av",
    "Volume x Avg": "vx", "50 DMA": "d50", "200 DMA": "d200", "Score": "sc",
}
SIG_MAP = {
    "Symbol": "s", "Company": "n", "Stage": "stage", "RS_Rating": "rs",
    "Group": "grp", "Group_Rank": "gr",
    "Base_Stage": "bs", "Earnings_Grade": "eg", "Earnings_Note": "en",
    "EPS_Q_Growth": "epsq", "Sales_Q_Growth": "salesq",
    "EPS_A_Growth": "epsa", "ROE": "roe",
    "Last_Price": "p",
    "Buy_Point": "buy", "Pct_To_Buy": "toBuy", "Stop": "stop",
    "Risk_pct": "risk", "Target": "tgt", "Target_Max": "tgtMax",
    "Measured_Move": "mm", "Hold_Days_Left": "hold",
    "Handle_Days": "hd", "Handle_Low": "hlow", "Handle_Depth_pct": "hdep",
    "Cup_Depth_pct": "cdep",
    "Cup_Weeks": "cw", "Vol_x_Avg": "vx", "Days_Since_Breakout": "since",
}


def clean(v):
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    if isinstance(v, str):
        return v
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return round(f, 2)


def pack(df, mapping):
    cols = {k: v for k, v in mapping.items() if k in df.columns}
    return [{short: clean(row[long]) for long, short in cols.items()}
            for _, row in df.iterrows()]


def main():
    os.makedirs(DOCS, exist_ok=True)

    if not os.path.exists(LIVE):
        raise SystemExit("EQUITY_L_live.csv missing - run update_nse_data.py first")

    live = pd.read_csv(LIVE)
    universe = int(live["Last Price"].notna().sum())

    import screen_stocks
    picks = screen_stocks.screen(live)

    signals = pd.DataFrame()
    if os.path.exists(SIGS):
        try:
            signals = pd.read_csv(SIGS)
        except Exception:
            signals = pd.DataFrame()

    stamp = ""
    if "Updated" in live.columns and len(live):
        stamp = str(live["Updated"].iloc[0])

    market = None
    mpath = os.path.join(HERE, "market.json")
    if os.path.exists(mpath):
        try:
            with open(mpath) as fh:
                market = json.load(fh)
        except Exception:
            market = None

    payload = {
        "built": datetime.now(IST).strftime("%d %b %Y, %H:%M IST"),
        "dataStamp": stamp,
        "universe": universe,
        "market": market,
        "screened": pack(picks, SCREEN_MAP),
        "signals": pack(signals, SIG_MAP) if len(signals) else [],
    }

    with open(OUT, "w") as fh:
        json.dump(payload, fh, separators=(",", ":"))

    kb = os.path.getsize(OUT) / 1024
    print(f"wrote {OUT}  ({kb:.0f} KB)")
    print(f"  universe {universe} | screened {len(payload['screened'])} | "
          f"signals {len(payload['signals'])} | "
          f"market {market['verdict'] if market else 'unknown'}")


if __name__ == "__main__":
    main()
