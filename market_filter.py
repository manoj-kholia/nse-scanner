#!/usr/bin/env python3
"""
market_filter.py
----------------
Judges the health of the Indian market from the Nifty 50, O'Neil style.

His argument: roughly three out of four stocks follow the general market, so a
perfect cup-and-handle breakout bought during a market correction still fails.
This module answers one question - is it safe to buy breakouts today?

Two ingredients:
  1. Trend      - index above its 50 and 200 day averages, 50 DMA rising.
  2. Distribution days - sessions where the index falls on HIGHER volume than
     the day before. That is institutions selling into strength. A handful
     inside a few weeks is what turns an uptrend into a correction.

Writes market.json and prints the verdict. Import market_state() elsewhere.
"""

import os
import json
from datetime import datetime, timedelta, timezone

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "market.json")
IST = timezone(timedelta(hours=5, minutes=30))

INDEX = "^NSEI"          # Nifty 50
DIST_WINDOW = 25         # sessions to count distribution days over
DIST_DROP = 0.2          # a fall of this % or more counts
DIST_PRESSURE = 4        # this many = under pressure
DIST_CORRECTION = 6      # this many = correction

OK = "CONFIRMED UPTREND"
PRESSURE = "UNDER PRESSURE"
CORRECTION = "CORRECTION"


def distribution_days(df, window=DIST_WINDOW, drop=DIST_DROP):
    """Sessions in the window where the index fell meaningfully on rising volume."""
    d = df.tail(window + 1)
    if len(d) < 3:
        return 0, []
    close = d["Close"].to_numpy(float)
    vol = d["Volume"].to_numpy(float)
    idx = d.index
    days = []
    for i in range(1, len(d)):
        pct = (close[i] / close[i - 1] - 1) * 100
        if pct <= -drop and vol[i] > vol[i - 1]:
            days.append({"date": str(idx[i])[:10], "pct": round(pct, 2)})
    return len(days), days


def market_state(df):
    """Return a dict describing market health. df = Nifty daily OHLCV."""
    close = df["Close"]
    last = float(close.iloc[-1])
    prev = float(close.iloc[-2]) if len(close) > 1 else last

    sma50 = float(close.tail(50).mean()) if len(close) >= 50 else None
    sma200 = float(close.tail(200).mean()) if len(close) >= 200 else None
    # is the 50 DMA itself rising? compare with where it sat a month ago
    sma50_prev = float(close.iloc[-71:-21].mean()) if len(close) >= 71 else None

    above50 = sma50 is not None and last > sma50
    above200 = sma200 is not None and last > sma200
    rising50 = sma50 is not None and sma50_prev is not None and sma50 > sma50_prev

    dist, dist_list = distribution_days(df)

    hi52 = float(df["High"].tail(250).max())
    off_high = (last / hi52 - 1) * 100 if hi52 else 0.0

    if not above50 or dist >= DIST_CORRECTION:
        verdict, buy = CORRECTION, False
    elif dist >= DIST_PRESSURE or not above200 or not rising50:
        verdict, buy = PRESSURE, True
    else:
        verdict, buy = OK, True

    if verdict == CORRECTION:
        advice = ("Do not buy breakouts. O'Neil's rule: most stocks follow the "
                  "market, and breakouts bought in a correction fail. Watch and wait.")
    elif verdict == PRESSURE:
        advice = ("Trade smaller and be selective. The uptrend is intact but "
                  "institutions are selling into it.")
    else:
        advice = "Breakouts are worth taking at full position size."

    return {
        "index": "NIFTY 50",
        "verdict": verdict,
        "buy_breakouts": buy,
        "advice": advice,
        "last": round(last, 2),
        "day_change_pct": round((last / prev - 1) * 100, 2) if prev else 0.0,
        "above_50dma": above50,
        "above_200dma": above200,
        "rising_50dma": rising50,
        "sma50": round(sma50, 2) if sma50 else None,
        "sma200": round(sma200, 2) if sma200 else None,
        "distribution_days": dist,
        "distribution_window": DIST_WINDOW,
        # every one of them - truncating this list made the banner ("9 days")
        # disagree with what the dashboard actually listed underneath it
        "distribution_detail": dist_list,
        "pct_off_52w_high": round(off_high, 2),
        "checked": datetime.now(IST).strftime("%d %b %Y, %H:%M IST"),
    }


def fetch(period="2y", downloader=None):
    if downloader is None:
        import yfinance as yf
        downloader = lambda: yf.download(INDEX, period=period, interval="1d",
                                         auto_adjust=False, actions=False,
                                         progress=False)
    raw = downloader()
    if raw is None or not len(raw):
        raise SystemExit("Could not download the Nifty 50")
    if isinstance(raw.columns, pd.MultiIndex):
        raw = raw.xs(raw.columns.get_level_values(-1)[0], axis=1, level=-1)
    return raw.dropna(subset=["Close"])


def main():
    state = market_state(fetch())
    with open(OUT, "w") as fh:
        json.dump(state, fh, indent=1)
    print(f"NIFTY 50  {state['last']}  ({state['day_change_pct']:+.2f}%)")
    print(f"  verdict          : {state['verdict']}")
    print(f"  above 50 / 200   : {state['above_50dma']} / {state['above_200dma']}")
    print(f"  50 DMA rising    : {state['rising_50dma']}")
    print(f"  distribution days: {state['distribution_days']} in the last {DIST_WINDOW}")
    print(f"  off 52w high     : {state['pct_off_52w_high']}%")
    print(f"  {state['advice']}")
    print(f"  wrote {OUT}")


if __name__ == "__main__":
    main()
