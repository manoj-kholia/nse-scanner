#!/usr/bin/env python3
"""
build_intraday.py
-----------------
Packs the morning's stocks-in-play run into docs/intraday.json for the
dashboard's Intraday tab. Runs after intraday.py.
"""

import os
import json
from datetime import datetime, timedelta, timezone

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "stocks_in_play.csv")
STATUS = os.path.join(HERE, "intraday_status.json")
DOCS = os.path.join(HERE, "docs")
OUT = os.path.join(DOCS, "intraday.json")

IST = timezone(timedelta(hours=5, minutes=30))

MAP = {
    "Symbol": "s", "Company": "n", "Session": "d", "RVol": "rv",
    "Gap_pct": "gap", "ATR_pct": "atr", "Prev_Close": "pc", "Open": "o",
    "OR_High": "orh", "OR_Low": "orl", "OR_Range_pct": "orr",
    "Long_Trigger": "lt", "Long_Stop": "ls",
    "Short_Trigger": "st", "Short_Stop": "ss",
    "Stop_Dist": "sd", "Risk_pct": "irisk", "Breakeven_pct": "be",
    "Cost_vs_ATR": "cva", "Turnover_Cr": "to", "Last": "last",
}


def clean(v):
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    if isinstance(v, str):
        return v
    try:
        return round(float(v), 3)
    except (TypeError, ValueError):
        return None


def main():
    os.makedirs(DOCS, exist_ok=True)
    rows = []
    if os.path.exists(SRC):
        try:
            df = pd.read_csv(SRC)
            cols = {k: v for k, v in MAP.items() if k in df.columns}
            rows = [{short: clean(r[long]) for long, short in cols.items()}
                    for _, r in df.iterrows()]
        except Exception:
            rows = []

    # Whether the DATA worked is a different question from whether anything
    # was in play, and the dashboard must not conflate the two.
    status = None
    if os.path.exists(STATUS):
        try:
            with open(STATUS) as fh:
                status = json.load(fh)
        except Exception:
            status = None

    payload = {
        "built": datetime.now(IST).strftime("%d %b %Y, %H:%M IST"),
        "status": status,
        "inPlay": rows,
        # shown on the tab so the caveats travel with the numbers
        "note": ("Selection only, not a backtested system. Free intraday data "
                 "reaches back about 60 days, which is not enough to validate "
                 "anything. Paper-trade it first."),
    }
    with open(OUT, "w") as fh:
        json.dump(payload, fh, separators=(",", ":"))
    print(f"wrote {OUT}  ({len(rows)} in play"
          + (f", data {'ok' if status.get('ok') else 'FAILED'}" if status else "") + ")")


if __name__ == "__main__":
    main()
