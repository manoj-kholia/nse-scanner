#!/usr/bin/env python3
"""
explain_pattern.py
------------------
Why is this stock NOT on the cup-and-handle list today?

The scan prints one-line reject reasons for stocks it scanned, and says
nothing at all about stocks where no cup was found - which is the case that
makes a name vanishing from the list look arbitrary. This walks the whole
pipeline for ONE symbol and shows every gate with its number and its
threshold, so the answer is always a specific figure rather than a shrug.

    python3 explain_pattern.py MCX
    python3 explain_pattern.py MCX SPLPETRO ICIL

It reports, in order:
  1. the liquidity/leadership screen that decides whether the stock is even
     a candidate (RS Rating, 50 and 200 DMA, distance from the 20-week high)
  2. whether a cup was found, and if not, what the near misses were
  3. every handle rule, with the measured value against the limit

Run it after the close. Run mid-session and it will tell you it is looking at
yesterday, for the same reason the scan does.
"""

import argparse
import os
import sys
from datetime import datetime

import numpy as np
import pandas as pd

import find_patterns as fp
import screen_stocks

HERE = os.path.dirname(os.path.abspath(__file__))
LIVE = os.path.join(HERE, "EQUITY_L_live.csv")


def line(ok, label, got, need):
    mark = "PASS" if ok else "FAIL"
    print(f"    {mark}  {label:<42} {got:>14}   needs {need}")


def screen_check(live, sym, min_rs=80):
    """Is it even a candidate? Everything here happens BEFORE any pattern work."""
    row = live[live["Symbol"] == sym]
    if row.empty:
        print(f"    {sym} is not in the daily data file at all.")
        return False
    r = row.iloc[0]

    def num(c):
        return pd.to_numeric(pd.Series([r.get(c)]), errors="coerce").iloc[0]

    px, d50, d200 = num("Last Price"), num("50 DMA"), num("200 DMA")
    frm, vol, bars = num("% From 20W High"), num("Avg Volume 20d"), num("Bars")
    rs = num("RS Rating")

    checks = [
        (bars >= 120, "enough history", f"{bars:.0f} bars", ">= 120"),
        (pd.notna(d200) and px > d200, "above the 200 DMA",
         f"{px:.2f} vs {d200:.2f}" if pd.notna(d200) else "no 200 DMA", "price above"),
        (pd.notna(d50) and px > d50, "above the 50 DMA",
         f"{px:.2f} vs {d50:.2f}" if pd.notna(d50) else "no 50 DMA", "price above"),
        (frm >= -15.0, "within 15% of the 20-week high", f"{frm:.2f}%", ">= -15%"),
        (vol >= 50000, "liquid enough", f"{vol:,.0f}", ">= 50,000"),
        (px >= 20.0, "not a penny stock", f"{px:.2f}", ">= 20"),
        (pd.isna(rs) or rs >= min_rs, "RS Rating (O'Neil's leadership line)",
         "no rating" if pd.isna(rs) else f"{rs:.0f}", f">= {min_rs}"),
    ]
    for ok, label, got, need in checks:
        line(bool(ok), label, got, need)
    return all(bool(c[0]) for c in checks)


def as_of(df, date):
    """The data as it stood at the close of `date` - nothing after it.

    "Why is this on the list today and not three days ago?" is answerable
    only by re-running the CURRENT rules against the data as it was then.
    Otherwise you cannot tell a rule change from a market change.
    """
    if not date:
        return df
    cut = pd.Timestamp(date)
    idx = df.index
    if getattr(idx, "tz", None) is not None:
        cut = cut.tz_localize(idx.tz)
    return df[idx <= cut + pd.Timedelta(hours=23, minutes=59)]


def explain(sym, df, live, min_rs=80):
    print("=" * 78)
    print(f"{sym}")
    print("=" * 78)

    print("\n  STEP 1 - is it a candidate at all?")
    if not screen_check(live, sym, min_rs):
        print("\n  -> Filtered out BEFORE the pattern scan. Nothing above is about")
        print("     the cup; the stock simply is not a leader by these rules today.")
        return

    df, phantoms = fp.drop_phantom_sessions(df)
    if phantoms:
        print(f"\n  ({phantoms} zero-volume holiday bar(s) dropped - Yahoo emits a flat")
        print("   bar for days the exchange was shut, and counting one as a session")
        print("   shifts every pivot window and handle age by a day)")

    df, trimmed = fp.drop_unfinished_session(df)
    if trimmed:
        print("\n  (market still open - today's part-day bar dropped, so this is")
        print("   yesterday's completed picture)")

    print(f"\n  STEP 2 - is there a cup?   [{len(df)} daily bars]")
    why = {}
    cup = fp.find_cup(df, why=why)
    if not cup:
        print("    No valid cup. The near misses, most common first:")
        for reason, n in sorted(why.items(), key=lambda kv: -kv[1])[:6]:
            print(f"      {n:>4}x  {reason}")
        if not why:
            print("      (no pivot pairs were even close - there is no base here)")
        return
    print(f"    cup found: {cup['length']} bars "
          f"({cup['length']/5:.1f} weeks), depth {cup['depth']*100:.1f}%, "
          f"buy point {cup['buy']:.2f}, cup low {cup['cup_low']:.2f}")

    # What SHAPE is it? A depth and a length do not tell you whether this is a
    # rounded base or a sharp V that happens to span the same bars, and that is
    # usually the thing a chart makes obvious and a number hides.
    hi_a = df["High"].to_numpy(float)
    lo_a = df["Low"].to_numpy(float)
    cl_a = df["Close"].to_numpy(float)
    li, rj = cup["left"], cup["right"]
    inside = slice(li + 1, rj)
    rim_hi = max(hi_a[li], hi_a[rj])
    cup_low = cup["cup_low"]
    third = cup_low + (rim_hi - cup_low) / 3
    roundness = float((cl_a[inside] <= third).sum()) / cup["length"]
    lo_bar = li + 1 + int(np.argmin(lo_a[inside]))
    pos = (lo_bar - li) / cup["length"]
    start = max(0, li - fp.P["prior_look"])
    prior_low = float(lo_a[start:li].min()) if start < li else float("nan")
    advance = (hi_a[li] - prior_low) / prior_low * 100 if prior_low > 0 else float("nan")

    print("\n    what SHAPE is it?")
    line(roundness >= fp.P["round_min"], "roundness (closes in the bottom third)",
         f"{roundness:.2f}", f">= {fp.P['round_min']:.2f}")
    print(f"          reference: sharp V-bottom 0.04, wide straight-line V 0.33,")
    print(f"                     properly rounded U 0.38")
    if roundness < 0.36:
        print(f"          NOTE: {roundness:.2f} is in the band where this test cannot")
        print(f"                separate a wide V from a real U. Look at the chart.")
    line(0.15 <= pos <= 0.85, "low sits mid-cup, not at one end",
         f"{pos:.2f} across", "0.15-0.85")
    line(advance >= fp.P["prior_pct"] * 100, "advance before the base",
         f"{advance:.0f}%", f">= {fp.P['prior_pct']*100:.0f}%")
    line(True, "left rim vs right rim",
         f"{hi_a[li]:.2f} / {hi_a[rj]:.2f}", "within -8% / +5%")

    print("\n  STEP 3 - the handle")
    close = df["Close"].to_numpy(float)
    low = df["Low"].to_numpy(float)
    vol = df["Volume"].to_numpy(float)
    n, last = len(df), len(df) - 1
    ri, buy = cup["right"], cup["buy"]
    days = last - ri
    avg_vol = pd.Series(vol).rolling(fp.P["vol_len"]).mean().to_numpy()
    # The handle ENDS at the breakout bar, if there is one. Measuring the slope
    # through that bar describes the breakout, not the handle, and reports a
    # spurious "wedges upward" on every stock that has already broken out.
    bo = fp.breakout_index(df, cup, fp.P)
    h_end = bo if bo is not None else n
    slope, dry = fp.handle_quality(close, vol, avg_vol, ri + 1, h_end, buy, fp.P)
    if bo is not None:
        print(f"    (broke out {last - bo} session(s) ago at bar {bo}; the handle")
        print(f"     is measured up to that bar, not through it)")
    h_low = float(low[ri + 1:].min()) if n > ri + 1 else float("nan")
    h_depth = (buy - h_low) / buy if buy else float("nan")
    px = float(close[last])

    line(cup["length"] + days >= fp.P["base_min"], "cup + handle length",
         f"{cup['length'] + days} bars", f">= {fp.P['base_min']}")
    line(days >= fp.P["handle_min"], "handle age", f"{days} days",
         f">= {fp.P['handle_min']}")
    line(days <= fp.P["handle_max"], "handle not expired", f"{days} days",
         f"<= {fp.P['handle_max']}")
    line(h_depth <= fp.P["handle_depth"], "handle depth",
         f"{h_depth*100:.1f}%", f"<= {fp.P['handle_depth']*100:.0f}%")
    if slope is not None:
        line(slope <= fp.P["handle_slope"], "handle drifts DOWN, not up",
             f"{slope:+.3f}%/day", f"<= {fp.P['handle_slope']:.1f}")
    if dry is not None:
        line(dry <= fp.P["handle_vol"], "volume dried up in the handle",
             f"{dry:.2f}x avg", f"<= {fp.P['handle_vol']:.1f}x")
    line(px <= buy * (1 + fp.P["max_ext"]), "not extended past the buy point",
         f"{px:.2f} vs {buy:.2f}", f"<= {buy*(1+fp.P['max_ext']):.2f}")

    print("\n  STEP 4 - what the scanner concludes")
    res, reason = fp.evaluate(df, cup)
    if res:
        print(f"    LISTED as {res.get('Stage', 'a signal')}: "
              f"buy {res['Buy_Point']}, last {res['Last_Price']}, "
              f"stage {res.get('Base_Stage')}, target {res.get('Target')}")
    else:
        print(f"    NOT LISTED - {reason}")
        print(f"    (last {px:.2f}, buy point {buy:.2f}, "
              f"{(buy/px - 1)*100:+.2f}% away)")


def main():
    ap = argparse.ArgumentParser(description="Why is this stock not on the list?")
    ap.add_argument("symbols", nargs="+")
    ap.add_argument("--source", default=LIVE)
    ap.add_argument("--min-rs", type=int, default=80)
    ap.add_argument("--asof", default="",
                    help="pretend it is this date (YYYY-MM-DD) and ignore every "
                         "bar after it - shows WHEN a signal appeared")
    args = ap.parse_args()

    live = pd.read_csv(args.source)
    syms = [s.strip().upper() for s in args.symbols]
    print(f"{datetime.now():%Y-%m-%d %H:%M}  explaining {', '.join(syms)}\n")

    import yfinance as yf
    raw = yf.download([s + ".NS" for s in syms], period="2y", interval="1d",
                      group_by="ticker", auto_adjust=False, actions=False,
                      progress=False, threads=True)
    for s in syms:
        tk = s + ".NS"
        try:
            if isinstance(raw.columns, pd.MultiIndex):
                df = raw[tk] if tk in raw.columns.get_level_values(0) else None
            else:
                df = raw
            if df is None:
                print(f"{s}: no data came back.")
                continue
            df = df.dropna(subset=["Close", "High", "Low"])
            if args.asof:
                before = len(df)
                df = as_of(df, args.asof)
                print(f"  [as of {args.asof}: {len(df)} bars, "
                      f"{before - len(df)} later bars ignored]")
            explain(s, df, live, args.min_rs)
            print()
        except Exception as exc:
            print(f"{s}: {type(exc).__name__}: {exc}")


if __name__ == "__main__":
    main()
