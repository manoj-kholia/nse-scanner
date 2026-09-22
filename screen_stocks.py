#!/usr/bin/env python3
"""
screen_stocks.py
----------------
Turns EQUITY_L_live.csv into a short, ranked watchlist worth charting.

Filters (all must pass):
    - above the 200 DMA          -> long-term uptrend
    - above the 50 DMA           -> medium-term uptrend
    - within N% of the 20W high  -> building a base near highs, not falling
    - liquid enough to trade     -> average volume and minimum price

Then ranks by a simple score: proximity to the 20W high + distance already
travelled off the 20W low + a volume kicker.

Outputs, next to this script:
    EQUITY_L_shortlist.xlsx   - Watchlist / Near High / Volume Surge tabs
    watchlist_tradingview.txt - paste-ready import for a TradingView watchlist

Run:  python3 screen_stocks.py
"""

import os
import argparse
from datetime import datetime

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "EQUITY_L_live.csv")
OUT_XLSX = os.path.join(HERE, "EQUITY_L_shortlist.xlsx")
OUT_TV = os.path.join(HERE, "watchlist_tradingview.txt")

SHOW = ["Symbol", "Company", "Last Price", "Day Change %", "RS Rating",
        "% From 20W High", "% Above 20W Low", "20W High", "20W Low", "Volume",
        "Avg Volume 20d", "Volume x Avg", "50 DMA", "200 DMA", "Score"]


def screen(df, from_high=-15.0, min_vol=50000, min_price=20.0, min_bars=120,
           min_rs=80):
    """O'Neil's 'L' - leaders, not laggards - plus liquidity and trend.

    min_rs is the RS Rating floor. O'Neil treats 80+ as leadership and under 70
    as a laggard, so 80 is his line, not mine. Pass 0 to ignore it.
    """
    d = df[df["Last Price"].notna()].copy()
    for c in ["Last Price", "50 DMA", "200 DMA", "% From 20W High",
              "% Above 20W Low", "Avg Volume 20d", "Volume x Avg", "Bars"]:
        d[c] = pd.to_numeric(d[c], errors="coerce")
    d["RS Rating"] = pd.to_numeric(d.get("RS Rating"), errors="coerce")

    d = d[
        (d["Bars"] >= min_bars)                       # enough history to trust
        & (d["200 DMA"].notna())
        & (d["Last Price"] > d["200 DMA"])
        & (d["Last Price"] > d["50 DMA"])
        & (d["% From 20W High"] >= from_high)
        & (d["Avg Volume 20d"] >= min_vol)
        & (d["Last Price"] >= min_price)
    ].copy()

    if min_rs and d["RS Rating"].notna().any():
        d = d[d["RS Rating"] >= min_rs]

    # closer to the high = better; already well off the low = real advance behind it
    d["Score"] = (
        (100 + d["% From 20W High"]) * 0.6
        + d["% Above 20W Low"].clip(upper=100) * 0.3
        + d["Volume x Avg"].fillna(0).clip(upper=5) * 2.0
    ).round(1)

    return d.sort_values("Score", ascending=False).reset_index(drop=True)


def sheet(xw, name, data):
    if data.empty:
        return
    cols = [c for c in SHOW if c in data.columns]
    data[cols].to_excel(xw, sheet_name=name, index=False)
    wb, ws = xw.book, xw.sheets[name]
    head = wb.add_format({"bold": True, "bg_color": "#1F4E78", "font_color": "white",
                          "border": 1, "align": "center", "text_wrap": True})
    for i, c in enumerate(cols):
        ws.write(0, i, c, head)
        ws.set_column(i, i, 34 if c == "Company" else 13)
    ws.freeze_panes(1, 2)
    ws.autofilter(0, 0, len(data), len(cols) - 1)
    c = cols.index("% From 20W High")
    ws.conditional_format(1, c, len(data), c,
                          {"type": "3_color_scale", "min_color": "#F8696B",
                           "mid_color": "#FFEB84", "max_color": "#63BE7B"})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", default=SRC)
    ap.add_argument("--from-high", type=float, default=-15.0,
                    help="max %% below the 20-week high (e.g. -15)")
    ap.add_argument("--min-vol", type=float, default=50000)
    ap.add_argument("--min-price", type=float, default=20.0)
    ap.add_argument("--min-rs", type=int, default=80,
                    help="RS Rating floor (O'Neil: 80+ is a leader, under 70 a laggard)")
    ap.add_argument("--top", type=int, default=0, help="keep only the top N by score")
    args = ap.parse_args()

    if not os.path.exists(args.source):
        raise SystemExit(f"Cannot find {args.source}. Run update_nse_data.py first.")

    df = pd.read_csv(args.source)
    picks = screen(df, args.from_high, args.min_vol, args.min_price, min_rs=args.min_rs)
    if args.top:
        picks = picks.head(args.top)

    near = picks[picks["% From 20W High"] >= -5]
    surge = picks[picks["Volume x Avg"] >= 2]

    with pd.ExcelWriter(OUT_XLSX, engine="xlsxwriter") as xw:
        sheet(xw, "Watchlist", picks)
        sheet(xw, "Near High", near)
        sheet(xw, "Volume Surge", surge)

    with open(OUT_TV, "w") as fh:
        fh.write(",".join("NSE:" + s for s in picks["Symbol"]))

    print(f"{datetime.now():%Y-%m-%d %H:%M}  screened {len(df)} rows")
    print(f"  RS floor     : {args.min_rs}")
    print(f"  watchlist    : {len(picks)}")
    print(f"  near high    : {len(near)}  (within 5% of the 20W high)")
    print(f"  volume surge : {len(surge)}  (2x average volume)")
    print(f"  wrote {OUT_XLSX}")
    print(f"  wrote {OUT_TV}")
    print()
    cols = [c for c in ["Symbol", "Last Price", "RS Rating", "% From 20W High",
                        "% Above 20W Low", "Volume x Avg", "Score"] if c in picks.columns]
    print(picks.head(20)[cols].to_string(index=False))


if __name__ == "__main__":
    main()
