#!/usr/bin/env python3
"""
find_patterns.py
----------------
Scans your watchlist and reports ONLY the stocks that are in a cup-and-handle
right now. Same O'Neil rules as the TradingView script, run in batch.

Two things it reports:
    HANDLE FORMING - cup is complete, handle is building, buy point is known
    BREAKOUT       - price cleared the buy point on volume in the last few days

Outputs, next to this script:
    cup_handle_signals.xlsx      - Handle Forming / Breakout tabs
    handle_watchlist.txt         - TradingView import of just these names

Run:  python3 find_patterns.py
      python3 find_patterns.py --all        (scan all 2,300 instead of the shortlist)
      python3 find_patterns.py --days 10    (breakouts within the last 10 sessions)
"""

import os
import json
import time
import argparse
from datetime import datetime

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
LIVE = os.path.join(HERE, "EQUITY_L_live.csv")
OUT_XLSX = os.path.join(HERE, "cup_handle_signals.xlsx")
OUT_TV = os.path.join(HERE, "handle_watchlist.txt")

# --- pattern rules (mirror the Pine script defaults) -------------------------
P = dict(
    piv=5,               # pivot strength, bars each side
    cup_min=30,          # 6 weeks of cup...
    base_min=35,         # ...but 7 weeks minimum for cup + handle together
    cup_max=250,         # 50 weeks
    depth_min=0.12,
    depth_max=0.35,      # O'Neil's documented range is 12-35%
    rim_down=0.08,       # right rim may sit this far BELOW the left rim
    rim_up=0.05,         # ...or this far above
    pierce=0.02,         # bars inside the cup may poke above the rim by this
    # Share of cup bars sitting in the bottom third of the cup's range.
    # Measured scores: sharp spike V-bottom 0.04, straight-line wide V 0.33,
    # rounded U 0.38.  So 0.30 decisively rejects the sharp V-bottoms O'Neil
    # warns about. It does NOT separate a wide gentle V from a U - those two
    # score too close together for any threshold to split them honestly.
    round_min=0.30,
    prior_pct=0.25,      # advance required before the base
    prior_look=120,
    handle_min=5,        # 1 week minimum
    handle_max=35,
    handle_depth=0.12,   # O'Neil's normal range tops out around 12%
    handle_slope=0.0,    # max % per day drift; a handle must NOT wedge upward
    handle_vol=1.0,      # handle volume must dry up: at or below the 50d average
    vol_mult=1.4,
    vol_len=50,
    max_ext=0.05,        # never chase more than this far past the buy point
)


def pivot_highs(high, piv):
    """Index of every bar that is the highest of the `piv` bars each side."""
    out = []
    n = len(high)
    for i in range(piv, n - piv):
        if high[i] == high[i - piv:i + piv + 1].max():
            out.append(i)
    return out


def find_cup(df, p=P):
    """Newest valid cup whose right rim is recent enough to still have a live handle."""
    high = df["High"].to_numpy(float)
    low = df["Low"].to_numpy(float)
    close = df["Close"].to_numpy(float)
    n = len(df)
    if n < p["cup_min"] + p["piv"] * 2 + 10:
        return None

    pivots = pivot_highs(high, p["piv"])
    if len(pivots) < 2:
        return None

    last = n - 1
    for ri in reversed(pivots):
        age = last - ri                               # bars since the right rim
        if age > p["handle_max"]:                     # handle would have expired
            break
        for li in reversed([q for q in pivots if q < ri]):
            length = ri - li
            if length > p["cup_max"]:
                break
            if length < p["cup_min"]:
                continue

            lp, rp = high[li], high[ri]
            if not (lp * (1 - p["rim_down"]) <= rp <= lp * (1 + p["rim_up"])):
                continue

            inside = slice(li + 1, ri)
            if inside.stop <= inside.start:
                continue
            rim_hi = max(lp, rp)
            cup_low = low[inside].min()
            lo_bar = li + 1 + int(np.argmin(low[inside]))
            if high[inside].max() > rim_hi * (1 + p["pierce"]):
                continue

            depth = (rim_hi - cup_low) / rim_hi
            if not (p["depth_min"] <= depth <= p["depth_max"]):
                continue

            pos = (lo_bar - li) / length               # where the low sits in the cup
            if not (0.15 <= pos <= 0.85):
                continue

            third = cup_low + (rim_hi - cup_low) / 3
            if (close[inside] <= third).sum() / length < p["round_min"]:
                continue                               # V-shaped, not a cup

            if p["prior_pct"] > 0:
                start = max(0, li - p["prior_look"])
                if start >= li:
                    continue
                prior_low = low[start:li].min()
                if prior_low <= 0 or (lp - prior_low) / prior_low < p["prior_pct"]:
                    continue                           # no advance before the base

            return dict(left=li, right=ri, buy=float(rp), cup_low=float(cup_low),
                        depth=float(depth), length=int(length))
    return None


def handle_quality(close, vol, avg_vol, start, end, buy, p=P):
    """Two O'Neil handle rules we used to ignore.

    slope   - a proper handle drifts DOWN along a falling trendline. One that
              wedges upward means no shakeout happened, and those fail more.
    dry_up  - volume must contract through the handle. Heavy volume in a
              handle is distribution, not a pause.

    `end` excludes the breakout bar itself, whose price and volume both spike.
    Returns (slope_pct_per_day, volume_vs_average) or (None, None) if the
    handle is too short to judge.
    """
    if end - start < 3:
        return None, None
    y = close[start:end]
    x = np.arange(len(y), dtype=float)
    slope = float(np.polyfit(x, y, 1)[0]) / buy * 100 if buy else 0.0
    ref = avg_vol[end - 1]
    dry = float(vol[start:end].mean()) / ref if ref and not np.isnan(ref) else None
    return round(slope, 3), (round(dry, 2) if dry is not None else None)


def evaluate(df, cup, p=P, breakout_window=5):
    """Given a cup, work out what the handle has done since the right rim.

    Returns None when there is nothing actionable - including when a breakout
    already failed or has run too far to chase. The second element of the
    tuple form (see `scan`) carries the reason so we can report what was
    filtered rather than silently dropping it.
    """
    high = df["High"].to_numpy(float)
    low = df["Low"].to_numpy(float)
    close = df["Close"].to_numpy(float)
    vol = df["Volume"].to_numpy(float)
    n = len(df)
    last = n - 1

    ri, buy, cup_low = cup["right"], cup["buy"], cup["cup_low"]
    mid = cup_low + (buy - cup_low) / 2

    avg_vol = pd.Series(vol).rolling(p["vol_len"]).mean().to_numpy()
    sma200 = pd.Series(close).rolling(200).mean().to_numpy()
    sma50 = pd.Series(close).rolling(50).mean().to_numpy()

    h_slice = slice(ri + 1, n)
    if h_slice.stop <= h_slice.start:
        return None, "no handle yet"
    h_low = float(low[h_slice].min())
    h_depth = (buy - h_low) / buy
    days = last - ri

    # first qualifying breakout after the rim
    bo = None
    for i in range(ri + 1, n):
        if i - ri < p["handle_min"]:
            continue
        if close[i] <= buy or close[i] > buy * (1 + p["max_ext"]):
            continue
        if not np.isnan(avg_vol[i]) and vol[i] < p["vol_mult"] * avg_vol[i]:
            continue
        if not np.isnan(sma200[i]) and close[i] <= sma200[i]:
            continue
        bo = i
        break

    px = float(close[last])

    # 7 weeks minimum for the whole base, cup plus handle (O'Neil)
    if cup["length"] + days < p["base_min"]:
        return None, "base under 7 weeks"

    h_end = bo if bo is not None else n        # exclude the breakout bar
    slope, dry = handle_quality(close, vol, avg_vol, ri + 1, h_end, buy, p)
    if slope is not None and slope > p["handle_slope"]:
        return None, "handle wedges upward"
    if dry is not None and dry > p["handle_vol"]:
        return None, "no volume dry-up in handle"

    common = dict(
        Buy_Point=round(buy, 2),
        Last_Price=round(px, 2),
        Cup_Depth_pct=round(cup["depth"] * 100, 1),
        Cup_Weeks=round(cup["length"] / 5, 1),
        Handle_Depth_pct=round(h_depth * 100, 1),
        Handle_Days=int(days),
        Handle_Slope=slope,
        Handle_Vol=dry,
        Cup_Low=round(cup_low, 2),
        Target=round(buy + (buy - cup_low), 2),
        Vol_x_Avg=round(vol[last] / avg_vol[last], 2) if avg_vol[last] else None,
        Above_50DMA="Yes" if not np.isnan(sma50[last]) and px > sma50[last] else "No",
    )

    if bo is not None:
        since = last - bo
        if since > breakout_window:
            return None, "breakout too old"
        # A breakout that has closed back under the pivot has FAILED. This is
        # the check that was missing: we used to report the breakout bar
        # without ever asking where the price sits today.
        if px < buy:
            return None, "breakout failed - back below buy point"
        # And never chase. The 5% limit has to be measured against TODAY's
        # price, not the breakout bar, or it stops meaning anything after a
        # couple of days of follow-through.
        if px > buy * (1 + p["max_ext"]):
            return None, "extended past the buy point"
        stop = max(h_low, float(close[bo]) * 0.92)
        return dict(Stage="BREAKOUT", Days_Since_Breakout=int(since),
                    Breakout_Price=round(float(close[bo]), 2),
                    Stop=round(stop, 2),
                    Pct_To_Buy=round((buy / px - 1) * 100, 2), **common), None

    # no breakout yet - is the handle still alive?
    if days > p["handle_max"] or h_low < mid or h_depth > p["handle_depth"]:
        return None, "handle broke down"
    if px > buy * (1 + p["max_ext"]):
        return None, "extended past the buy point"

    return dict(Stage="HANDLE FORMING", Days_Since_Breakout=None,
                Breakout_Price=None, Stop=round(max(h_low, px * 0.92), 2),
                Pct_To_Buy=round((buy / px - 1) * 100, 2), **common), None


def scan(symbols, downloader=None, batch_size=60, pause=1.5, period="2y",
         breakout_window=5, log=print):
    if downloader is None:
        import yfinance as yf
        downloader = lambda t: yf.download(t, period=period, interval="1d",
                                           group_by="ticker", auto_adjust=False,
                                           actions=False, progress=False, threads=True)
    hits, scanned, rejected = [], 0, {}
    for start in range(0, len(symbols), batch_size):
        batch = symbols[start:start + batch_size]
        tickers = [s + ".NS" for s in batch]
        try:
            raw = downloader(tickers)
        except Exception as exc:
            log(f"  batch failed: {exc}")
            continue
        for sym, tk in zip(batch, tickers):
            try:
                if isinstance(raw.columns, pd.MultiIndex):
                    if tk not in raw.columns.get_level_values(0):
                        continue
                    df = raw[tk]
                else:
                    df = raw
                df = df.dropna(subset=["Close", "High", "Low"])
                if len(df) < 60:
                    continue
                scanned += 1
                cup = find_cup(df)
                if not cup:
                    continue
                res, why = evaluate(df, cup, breakout_window=breakout_window)
                if res:
                    hits.append(dict(Symbol=sym, **res))
                elif why:
                    rejected.setdefault(why, []).append(sym)
            except Exception:
                continue
        done = min(start + batch_size, len(symbols))
        log(f"  {done}/{len(symbols)} scanned - {len(hits)} patterns so far")
        if done < len(symbols):
            time.sleep(pause)
    return hits, scanned, rejected


COLS = ["Symbol", "Company", "Stage", "RS_Rating", "Last_Price", "Buy_Point",
        "Pct_To_Buy", "Stop", "Target", "Handle_Days", "Handle_Depth_pct",
        "Handle_Slope", "Handle_Vol", "Cup_Depth_pct", "Cup_Weeks", "Cup_Low",
        "Vol_x_Avg", "Above_50DMA", "Days_Since_Breakout", "Breakout_Price"]


def write_xlsx(df, path):
    def tab(xw, name, data):
        if data.empty:
            return
        cols = [c for c in COLS if c in data.columns]
        data[cols].to_excel(xw, sheet_name=name, index=False)
        wb, ws = xw.book, xw.sheets[name]
        head = wb.add_format({"bold": True, "bg_color": "#1F4E78",
                              "font_color": "white", "border": 1, "text_wrap": True})
        for i, c in enumerate(cols):
            ws.write(0, i, c, head)
            ws.set_column(i, i, 32 if c == "Company" else 14)
        ws.freeze_panes(1, 2)
        ws.autofilter(0, 0, len(data), len(cols) - 1)

    with pd.ExcelWriter(path, engine="xlsxwriter") as xw:
        tab(xw, "Handle Forming", df[df["Stage"] == "HANDLE FORMING"])
        tab(xw, "Breakout", df[df["Stage"] == "BREAKOUT"])
        tab(xw, "All Signals", df)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", default=LIVE)
    ap.add_argument("--all", action="store_true", help="scan every symbol, not the shortlist")
    ap.add_argument("--days", type=int, default=5, help="report breakouts this recent")
    ap.add_argument("--min-rs", type=int, default=80,
                    help="RS Rating floor for candidates (O'Neil: 80+)")
    ap.add_argument("--batch-size", type=int, default=60)
    ap.add_argument("--pause", type=float, default=1.5)
    args = ap.parse_args()

    if not os.path.exists(args.source):
        raise SystemExit(f"Cannot find {args.source}. Run update_nse_data.py first.")

    live = pd.read_csv(args.source)
    if args.all:
        cand = live[live["Last Price"].notna()].copy()
    else:
        import screen_stocks
        cand = screen_stocks.screen(live, min_rs=args.min_rs)
    names = dict(zip(cand["Symbol"], cand["Company"]))
    rs = dict(zip(cand["Symbol"], pd.to_numeric(cand.get("RS Rating"), errors="coerce"))) \
        if "RS Rating" in cand.columns else {}
    syms = cand["Symbol"].tolist()

    # O'Neil's M: most stocks follow the market, so a breakout bought during a
    # correction usually fails. We still report the patterns - we just say so.
    market = None
    try:
        import market_filter
        market = market_filter.market_state(market_filter.fetch())
        print(f"MARKET: {market['verdict']} - Nifty {market['last']} "
              f"({market['day_change_pct']:+.2f}%), "
              f"{market['distribution_days']} distribution days")
        print(f"        {market['advice']}\n")
    except Exception as exc:
        print(f"MARKET: could not be checked ({exc}) - treat signals with caution\n")

    print(f"{datetime.now():%Y-%m-%d %H:%M}  scanning {len(syms)} stocks for cup & handle")
    hits, scanned, rejected = scan(syms, batch_size=args.batch_size,
                                   pause=args.pause, breakout_window=args.days)

    if rejected:
        print("\nCups found but filtered out:")
        for why, syms_ in sorted(rejected.items(), key=lambda kv: -len(kv[1])):
            shown = ", ".join(syms_[:6]) + ("..." if len(syms_) > 6 else "")
            print(f"  {len(syms_):3d}  {why:<38} {shown}")

    if not hits:
        print(f"\nScanned {scanned}. No tradeable cup-and-handle setups today.")
        print("That is a normal result - real bases are not common every day,")
        print("and they are scarcest when the market itself is under pressure.")
        return

    df = pd.DataFrame(hits)
    df.insert(1, "Company", df["Symbol"].map(names))
    df.insert(2, "RS_Rating", df["Symbol"].map(rs))
    df = df.sort_values(["Stage", "Pct_To_Buy"]).reset_index(drop=True)

    write_xlsx(df, OUT_XLSX)
    df.to_csv(os.path.join(HERE, "cup_handle_signals.csv"), index=False)
    if market:
        with open(os.path.join(HERE, "market.json"), "w") as fh:
            json.dump(market, fh, indent=1)
    with open(OUT_TV, "w") as fh:
        fh.write(",".join("NSE:" + s for s in df["Symbol"]))

    forming = (df["Stage"] == "HANDLE FORMING").sum()
    brk = (df["Stage"] == "BREAKOUT").sum()
    print(f"\nScanned {scanned}.  Handle forming: {forming}   Breakout: {brk}")
    if market and not market["buy_breakouts"]:
        print(f"  !! {market['verdict']}: {market['advice']}")
    print(f"  wrote {OUT_XLSX}")
    print(f"  wrote {OUT_TV}\n")
    show = [c for c in ["Symbol", "Stage", "RS_Rating", "Last_Price", "Buy_Point",
                        "Pct_To_Buy", "Stop", "Target", "Handle_Days",
                        "Cup_Depth_pct"] if c in df]
    print(df[show].to_string(index=False))


if __name__ == "__main__":
    main()
