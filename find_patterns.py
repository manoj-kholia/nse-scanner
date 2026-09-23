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
    max_loss=0.08,       # O'Neil's hard stop: 7-8% below what you paid
)


def pivot_highs(high, piv):
    """Index of every bar that is the highest of the `piv` bars each side."""
    out = []
    n = len(high)
    for i in range(piv, n - piv):
        if high[i] == high[i - piv:i + piv + 1].max():
            out.append(i)
    return out


def find_cup(df, p=P, why=None):
    """Newest valid cup whose right rim is recent enough to still have a live handle.

    Pass a dict as `why` to find out what the near-misses were. "No cup" is
    otherwise the one outcome this scanner cannot explain, which makes a stock
    vanishing from the list look arbitrary when it is not.
    """
    def no(reason):
        if why is not None:
            why[reason] = why.get(reason, 0) + 1
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
            no(f"handle aged out ({age} days since the rim, max {p['handle_max']})")
            break
        for li in reversed([q for q in pivots if q < ri]):
            length = ri - li
            if length > p["cup_max"]:
                no(f"cup too long ({length} bars)")
                break
            if length < p["cup_min"]:
                no(f"cup too short ({length} bars, min {p['cup_min']})")
                continue

            lp, rp = high[li], high[ri]
            if not (lp * (1 - p["rim_down"]) <= rp <= lp * (1 + p["rim_up"])):
                no(f"rims uneven (left {lp:.2f} vs right {rp:.2f})")
                continue

            inside = slice(li + 1, ri)
            if inside.stop <= inside.start:
                continue
            rim_hi = max(lp, rp)
            cup_low = low[inside].min()
            lo_bar = li + 1 + int(np.argmin(low[inside]))
            if high[inside].max() > rim_hi * (1 + p["pierce"]):
                no("price pierced above the rim mid-cup")
                continue

            depth = (rim_hi - cup_low) / rim_hi
            if not (p["depth_min"] <= depth <= p["depth_max"]):
                no(f"cup depth {depth*100:.1f}% outside {p['depth_min']*100:.0f}-{p['depth_max']*100:.0f}%")
                continue

            pos = (lo_bar - li) / length               # where the low sits in the cup
            if not (0.15 <= pos <= 0.85):
                no(f"cup low sits at {pos:.2f} of the way across (needs 0.15-0.85)")
                continue

            third = cup_low + (rim_hi - cup_low) / 3
            if (close[inside] <= third).sum() / length < p["round_min"]:
                no("V-shaped, not rounded enough")
                continue

            if p["prior_pct"] > 0:
                start = max(0, li - p["prior_look"])
                if start >= li:
                    continue
                prior_low = low[start:li].min()
                if prior_low <= 0 or (lp - prior_low) / prior_low < p["prior_pct"]:
                    no("no 25% advance before the base")
                    continue

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


def base_stage(df, reset_dd=0.30, min_pullback=0.12, min_base=25):
    """How many bases has this stock built since its last deep correction?

    O'Neil counts bases from the point a stock emerges after a severe decline.
    The first base off that low is the one that works; by the third and fourth
    the move is late, everybody can see it, and the failure rate climbs sharply.

    The count here:
      * resets at the LAST bar that closed 30% or more below its running peak -
        that decline wipes the slate clean
      * from there, a base starts when price closes 12%+ off a running high and
        ends when it closes back above that high
      * a base only counts if it lasted at least five weeks; shorter dips are
        noise, not bases

    Returns (stage, in_base_now). Stage 1 means the current base is the first
    since the correction.

    Caveat worth knowing: with two years of history this is a FLOOR. A stock
    that has been advancing for three years may really be later-stage than this
    says, because the earlier bases are off the edge of the data.
    """
    high = df["High"].to_numpy(float)
    close = df["Close"].to_numpy(float)
    n = len(df)
    if n < min_base + 10:
        return None, False

    # Where the count starts. Two steps, and both matter:
    #   1. find the last bar that closed 30%+ under its running peak, resetting
    #      the peak each time so a stock that has not yet regained an old high
    #      does not trip this again on every ordinary pullback
    #   2. move forward to the actual BOTTOM after that - the low itself is
    #      where the new advance begins, and counting from anywhere on the way
    #      down would score the recovery leg as a base of its own
    trigger, peak = None, high[0]
    for i in range(n):
        peak = max(peak, high[i])
        if peak > 0 and close[i] <= peak * (1 - reset_dd):
            trigger, peak = i, high[i]
    if trigger is None:
        start = 0                       # no deep decline in view - count from the edge
    else:
        start = trigger + int(np.argmin(close[trigger:]))

    stage = 1
    in_base = False
    run_high = high[start]
    base_start, base_high = start, high[start]
    for i in range(start, n):
        if not in_base:
            run_high = max(run_high, high[i])
            if run_high > 0 and close[i] <= run_high * (1 - min_pullback):
                in_base, base_start, base_high = True, i, run_high
        elif close[i] > base_high:                  # cleared the top of that base
            if i - base_start >= min_base:
                stage += 1
            in_base = False
            run_high = high[i]
    return stage, in_base


def oneil_stop(entry, handle_low, max_loss=P["max_loss"]):
    """Where to cut, for a purchase made at `entry`.

    O'Neil's one unbreakable rule is to sell at 7-8% below what you paid, no
    matter what the chart says. The handle low is the structural level, and it
    is the right stop only when it is TIGHTER than that. When the handle is
    loose - as most of ours are - the 8% rule wins, otherwise a "stop" at the
    handle low quietly commits you to a 10-12% loss.
    """
    return max(float(handle_low), float(entry) * (1 - max_loss))


def breakout_index(df, cup, p=P):
    """Index of the first qualifying breakout after the right rim, or None.

    Shared by evaluate() and by explain_pattern.py. The handle ENDS here: the
    breakout bar's price and volume both spike, so measuring the handle's slope
    or its volume through that bar describes the breakout, not the handle.
    Keeping one definition means the scanner and the tool that explains the
    scanner cannot drift apart.
    """
    close = df["Close"].to_numpy(float)
    vol = df["Volume"].to_numpy(float)
    n = len(df)
    ri, buy = cup["right"], cup["buy"]
    avg_vol = pd.Series(vol).rolling(p["vol_len"]).mean().to_numpy()
    sma200 = pd.Series(close).rolling(200).mean().to_numpy()
    for i in range(ri + 1, n):
        if i - ri < p["handle_min"]:
            continue
        if close[i] <= buy or close[i] > buy * (1 + p["max_ext"]):
            continue
        if not np.isnan(avg_vol[i]) and vol[i] < p["vol_mult"] * avg_vol[i]:
            continue
        if not np.isnan(sma200[i]) and close[i] <= sma200[i]:
            continue
        return i
    return None


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

    bo = breakout_index(df, cup, p)

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

    stage, _ = base_stage(df)

    common = dict(
        Base_Stage=stage,
        Buy_Point=round(buy, 2),
        Last_Price=round(px, 2),
        Cup_Depth_pct=round(cup["depth"] * 100, 1),
        Cup_Weeks=round(cup["length"] / 5, 1),
        Handle_Low=round(h_low, 2),
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
        entry = float(close[bo])
        stop = oneil_stop(entry, h_low)
        return dict(Stage="BREAKOUT", Days_Since_Breakout=int(since),
                    Breakout_Price=round(entry, 2),
                    Stop=round(stop, 2),
                    Risk_pct=round((1 - stop / entry) * 100, 1),
                    Pct_To_Buy=round((buy / px - 1) * 100, 2), **common), None

    # no breakout yet - is the handle still alive?
    if days > p["handle_max"] or h_low < mid or h_depth > p["handle_depth"]:
        return None, "handle broke down"
    if px > buy * (1 + p["max_ext"]):
        return None, "extended past the buy point"

    # The stop belongs to the price you will actually pay, which is the buy
    # point - not today's price, because you are not supposed to buy yet.
    stop = oneil_stop(buy, h_low)
    return dict(Stage="HANDLE FORMING", Days_Since_Breakout=None,
                Breakout_Price=None, Stop=round(stop, 2),
                Risk_pct=round((1 - stop / buy) * 100, 1),
                Pct_To_Buy=round((buy / px - 1) * 100, 2), **common), None


MARKET_CLOSE = "15:30"          # IST


def drop_unfinished_session(df, now=None):
    """Remove today's bar while the market is still open.

    Yahoo serves the in-progress day as if it were a finished daily bar, and
    two of this scanner's tests are wrecked by that:

      * VOLUME IS CUMULATIVE. At 09:42 a stock has traded 27 minutes' worth.
        The breakout test wants 1.4x the 50-day average volume, which a
        part-day total can almost never reach - so real breakouts are invisible
        mid-session and appear only after the close.
      * THE RANGE IS INCOMPLETE. "Extended past the buy point", "broke back
        below it" and the handle's depth are all judged against a high, low and
        close that have not finished happening.

    The visible symptom is a watchlist that changes every time you run it. The
    honest answer is that daily patterns can only be as fresh as the last
    FINISHED day, so mid-session this drops the part-day bar and reports
    yesterday's completed picture, which does not flicker.
    """
    if df is None or df.empty or not isinstance(df.index, pd.DatetimeIndex):
        return df, False
    now = now or pd.Timestamp.now(tz="Asia/Kolkata")
    last = df.index[-1]
    last_date = last.date() if hasattr(last, "date") else last
    if last_date != now.date():
        return df, False                       # last bar is an earlier session
    if now.strftime("%H:%M") >= MARKET_CLOSE:
        return df, False                       # today is over, the bar is real
    return df.iloc[:-1], True


def drop_phantom_sessions(df):
    """Remove bars where nothing actually traded.

    On an exchange holiday Yahoo does not skip the day - it emits a bar with
    open = high = low = close and volume 0. TradingView omits the day entirely.
    A bar that nobody traded is not a session, and carrying it corrupts every
    rule that counts bars rather than dates:

      * PIVOTS. A pivot high must be the highest of the five bars each side.
        One phantom bar shifts that window by a day, which is enough to keep a
        rim that a real session would have disqualified - or the reverse.
      * HANDLE AGE, CUP LENGTH, BASE LENGTH. All measured in bars.
      * THE 50-DAY VOLUME AVERAGE, which a zero drags down, making the 1.4x
        breakout test easier to pass than it should be.

    Found by reconciling the chart against the scanner: 14 Sep 2026 was an NSE
    holiday, Yahoo filled it with a flat zero-volume bar, and that single bar
    was the whole of the disagreement over SPLPETRO - the scanner put the right
    rim 9 bars back and called it a breakout, the chart put it 8 bars back and
    threw the rim out. The chart was right.
    """
    if "Volume" not in df.columns or df.empty:
        return df, 0
    vol = pd.to_numeric(df["Volume"], errors="coerce").fillna(0)
    flat = df["High"].to_numpy(float) == df["Low"].to_numpy(float)
    phantom = (vol <= 0).to_numpy() & flat
    return df[~phantom], int(phantom.sum())


def scan(symbols, downloader=None, batch_size=60, pause=1.5, period="2y",
         breakout_window=5, log=print):
    if downloader is None:
        import yfinance as yf
        downloader = lambda t: yf.download(t, period=period, interval="1d",
                                           group_by="ticker", auto_adjust=False,
                                           actions=False, progress=False, threads=True)
    hits, scanned, rejected = [], 0, {}
    dropped_partial = [0]
    dropped_phantom = [0]
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
                df, trimmed = drop_unfinished_session(df)
                if trimmed:
                    dropped_partial[0] += 1
                df, phantoms = drop_phantom_sessions(df)
                dropped_phantom[0] += phantoms
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
    if dropped_partial[0]:
        log(f"  NOTE: the market is still open, so today's part-day bar was "
            f"dropped for {dropped_partial[0]} symbols.")
        log("  These are yesterday's completed patterns. A mid-session scan "
            "cannot see today's breakouts,")
        log("  because a breakout needs 1.4x average volume and today's volume "
            "is not finished accumulating.")
    return hits, scanned, rejected


COLS = ["Symbol", "Company", "Stage", "RS_Rating", "Base_Stage",
        "Earnings_Grade", "EPS_Q_Growth", "Sales_Q_Growth", "EPS_A_Growth", "ROE",
        "Last_Price", "Buy_Point",
        "Pct_To_Buy", "Stop", "Risk_pct", "Target", "Handle_Days",
        "Handle_Low", "Handle_Depth_pct",
        "Handle_Slope", "Handle_Vol", "Cup_Depth_pct", "Cup_Weeks", "Cup_Low",
        "Vol_x_Avg", "Above_50DMA", "Days_Since_Breakout", "Breakout_Price",
        "Earnings_Note"]


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
    ap.add_argument("--max-stage", type=int, default=0,
                    help="drop bases later than this stage (0 = flag only, never drop)")
    ap.add_argument("--no-earnings", action="store_true",
                    help="skip the CANSLIM C/A lookup for the signals")
    ap.add_argument("--require-earnings", action="store_true",
                    help="drop signals whose earnings FAIL O'Neil's C/A thresholds")
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

    # Late-stage bases: flagged by default, never dropped, because a 3rd-stage
    # base in a genuine leader is still tradeable - just smaller and later.
    if args.max_stage and "Base_Stage" in df.columns:
        late = df["Base_Stage"].notna() & (df["Base_Stage"] > args.max_stage)
        if late.any():
            print(f"\nDropped {int(late.sum())} signal(s) past stage {args.max_stage}: "
                  + ", ".join(df.loc[late, "Symbol"]))
            df = df[~late].reset_index(drop=True)

    # O'Neil's C and A. Only the signals, so it is a few lookups, not 2,300.
    if not args.no_earnings and len(df):
        print(f"\nChecking earnings for {len(df)} signal(s)...")
        try:
            import fundamentals
            fun = fundamentals.fetch(df["Symbol"].tolist())
            df = df.merge(fun, on="Symbol", how="left")
            if args.require_earnings:
                bad = df["Earnings_Grade"].eq("FAIL")
                if bad.any():
                    print("  dropped on earnings: "
                          + ", ".join(df.loc[bad, "Symbol"]))
                    df = df[~bad].reset_index(drop=True)
        except Exception as exc:
            print(f"  earnings check unavailable ({type(exc).__name__}: {exc})")

    if not len(df):
        print("\nNothing left after the filters.")
        return

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
    show = [c for c in ["Symbol", "Stage", "RS_Rating", "Base_Stage",
                        "Earnings_Grade", "Last_Price", "Buy_Point",
                        "Pct_To_Buy", "Stop", "Risk_pct", "Target",
                        "Handle_Days", "Cup_Depth_pct"] if c in df]
    print(df[show].to_string(index=False))

    if "Base_Stage" in df.columns:
        late = df[df["Base_Stage"].notna() & (df["Base_Stage"] >= 3)]
        for _, r in late.iterrows():
            print(f"  !! {r['Symbol']}: stage-{int(r['Base_Stage'])} base - "
                  "late in the move, these fail more often")
    if "Earnings_Grade" in df.columns:
        for _, r in df.iterrows():
            if r["Earnings_Grade"] in ("FAIL", "NO DATA", "THIN"):
                print(f"  ?? {r['Symbol']}: earnings {r['Earnings_Grade']} - "
                      f"{r['Earnings_Note']}")


if __name__ == "__main__":
    main()
