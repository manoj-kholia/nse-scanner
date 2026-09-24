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
    # MEAN NORMALISED DEPTH of the closes across the cup, not a count.
    #   shape = mean( (rim_high - close) / (rim_high - cup_low) )
    # A U spends most of its bars near the bottom, so the mean depth is high;
    # a V passes through the bottom once, so it is lower.
    #
    # This replaced a count of closes sitting in the bottom third. A count is
    # discontinuous: one bar moving a rupee across the line moved the old score
    # by 1/length. SPLPETRO scored 0.32 in this scanner and 0.27 on the
    # TradingView script against a 0.30 cutoff - same cup, same rims, same low
    # to the paisa - purely because two closes near the line landed on opposite
    # sides in two data feeds. Nothing is counted now, so nothing can tip over.
    #
    # Measured on reference shapes (calibrate_shape.py):
    #   sharp spike V 0.05, late V 0.33, wide straight V 0.49,
    #   rounded U 0.51, flat saucer 0.76
    # and under per-bar jitter the score moves at most 0.002, against 0.033 for
    # the old count. 0.41 sits in the band (0.332, 0.492) that reproduces every
    # accept/reject the old 0.30 cutoff made.
    #
    # It still does NOT separate a wide gentle V (0.49) from a U (0.51). Those
    # two are 0.016 apart and no threshold splits them honestly. Look at the
    # chart when the score lands near 0.50.
    round_min=0.41,
    prior_pct=0.25,      # advance required before the base
    prior_look=120,
    # OVERHEAD SUPPLY. O'Neil: "they will never make the fatal mistake of
    # buying a stock that has a large recent amount of overhead supply" - the
    # people who bought at a higher price and are waiting to get out even. The
    # cup's own rim test cannot catch this, because it only compares the two
    # rims with each other: a cup can be perfectly formed 30% below a peak the
    # stock made eight months ago.
    #
    # 15% IS A CONVENTION, NOT A QUOTE. The book gives no figure for the
    # distance from the 52-week high; 15% is the standard CANSLIM screen. His
    # own "5% to 10% below a stock's former high point" is about the pivot
    # versus the HIGH OF THIS BASE, which rim_down already enforces.
    #
    # It was 10% for one morning, which was wrong twice over:
    #   * rim_down lets the right rim sit 8% under the left rim, and the left
    #     rim is itself part of the 52-week window. So a textbook cup already
    #     spends 8 of a 10-point budget before any older peak is considered -
    #     the two rules were fighting each other. MCX measured "2% under the
    #     52w high" where that high WAS its own left rim.
    #   * it rejected NPST, a live signal, at 20% under - which is a genuine
    #     call - but it was one bad day away from rejecting ordinary bases.
    #
    # KNOWN EXCEPTION, not handled: O'Neil explicitly allows a first base off a
    # major bear-market low, where "some patterns that have decreased 50% to
    # 60% or more can succeed". This gate rejects those. Set overhead=0 to scan
    # for them after a bear market.
    overhead=0.15,       # buy point may sit at most this far under the 52w high
    overhead_look=252,   # ...measured over the 252 bars ending at the right rim
    handle_min=5,        # 1 week minimum
    handle_max=35,
    handle_depth=0.12,   # O'Neil's normal range tops out around 12%
    handle_slope=0.0,    # max % per day drift; a handle must NOT wedge upward
    handle_slope_t=1.0,  # ...and the rise must beat its own standard error
    handle_vol=1.0,      # handle volume must dry up: at or below the 50d average
    vol_mult=1.4,
    vol_len=50,
    max_ext=0.05,        # never chase more than this far past the buy point
    max_loss=0.08,       # O'Neil's hard stop: 7-8% below what you paid
    # THE EXIT. O'Neil's profit rule is a percentage off the pivot, not a
    # measured move: "sell each stock when it was up 20% from the breakout
    # point" (ch.10), with one exception - "if the stock was so strong that it
    # vaulted 20% in less than eight weeks, the stock had to be held at least
    # eight weeks."
    #
    # We used to publish buy + (buy - cup_low), a measured move. That is a
    # chartists' convention, not his rule, and it drifts with cup depth: a 12%
    # cup produced a +13.6% target (selling below his threshold) and a 35% cup
    # a +54% one (holding a winner back down waiting for a number he never
    # endorsed). The depth of the base does not predict the size of the move.
    profit_take=0.20,
    profit_take_max=0.25,
    hold_sessions=40,    # 8 weeks, for the "20% in under 8 weeks" exception
)


def pivot_highs(high, piv):
    """Index of every bar that is the highest of the `piv` bars each side."""
    out = []
    n = len(high)
    for i in range(piv, n - piv):
        if high[i] == high[i - piv:i + piv + 1].max():
            out.append(i)
    return out


def cup_between(high, low, close, li, ri, p=P):
    """Is the stretch from pivot `li` to pivot `ri` a valid cup?

    Returns (cup, None) or (None, reason). Every gate from the rims onward
    lives here so that the live scan and the historical scan cannot drift
    apart - the whole point of marking past setups on a chart is that they were
    judged by exactly the rules running today.

    The two LENGTH tests stay in the caller because they are loop control, not
    gates: "too long" breaks out of the search, "too short" skips to the next
    pair, and that difference is load-bearing for the Pine transliteration.
    """
    lp, rp = high[li], high[ri]
    if not (lp * (1 - p["rim_down"]) <= rp <= lp * (1 + p["rim_up"])):
        return None, f"rims uneven (left {lp:.2f} vs right {rp:.2f})"

    # Overhead supply: the buy point has to be near the stock's own
    # 52-week high, not merely near the other rim.
    # The window must be FULL before this can judge anything, because
    # Pine's ta.highest returns na until it has its full length and a
    # truncated window here would reject cups the chart accepts.
    if p["overhead"] > 0 and ri + 1 >= p["overhead_look"]:
        hi52 = float(high[ri + 1 - p["overhead_look"]:ri + 1].max())
        if hi52 > 0 and rp < hi52 * (1 - p["overhead"]):
            return None, (f"overhead supply: buy point {rp:.2f} is "
                          f"{(1 - rp / hi52) * 100:.0f}% under the 52w high "
                          f"{hi52:.2f} (max {p['overhead'] * 100:.0f}%)")

    inside = slice(li + 1, ri)
    if inside.stop <= inside.start:
        return None, None
    rim_hi = max(lp, rp)
    cup_low = low[inside].min()
    lo_bar = li + 1 + int(np.argmin(low[inside]))
    if high[inside].max() > rim_hi * (1 + p["pierce"]):
        return None, "price pierced above the rim mid-cup"

    depth = (rim_hi - cup_low) / rim_hi
    if not (p["depth_min"] <= depth <= p["depth_max"]):
        return None, (f"cup depth {depth*100:.1f}% outside "
                      f"{p['depth_min']*100:.0f}-{p['depth_max']*100:.0f}%")

    length = ri - li
    pos = (lo_bar - li) / length                   # where the low sits in the cup
    if not (0.15 <= pos <= 0.85):
        return None, f"cup low sits at {pos:.2f} of the way across (needs 0.15-0.85)"

    span = rim_hi - cup_low
    if span <= 0:
        return None, None
    shape = float(np.mean((rim_hi - close[inside]) / span))
    if shape < p["round_min"]:
        return None, f"V-shaped: shape {shape:.3f} below {p['round_min']:.2f}"

    if p["prior_pct"] > 0:
        start = max(0, li - p["prior_look"])
        if start >= li:
            return None, None
        prior_low = low[start:li].min()
        if prior_low <= 0 or (lp - prior_low) / prior_low < p["prior_pct"]:
            return None, "no 25% advance before the base"

    return dict(left=li, right=ri, buy=float(rp), cup_low=float(cup_low),
                depth=float(depth), length=int(length)), None


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

            cup, reason = cup_between(high, low, close, li, ri, p)
            if cup is None:
                if reason:
                    no(reason)
                continue
            return cup
    return None


def handle_quality(close, vol, avg_vol, start, end, buy, p=P):
    """Two O'Neil handle rules we used to ignore.

    slope   - a proper handle drifts DOWN along a falling trendline. One that
              wedges upward means no shakeout happened, and those fail more.
    dry_up  - volume must contract through the handle. Heavy volume in a
              handle is distribution, not a pause.

    `end` excludes the breakout bar itself, whose price and volume both spike.
    Returns (slope_pct_per_day, volume_vs_average, slope_t) or three Nones if
    the handle is too short to judge. slope_t is the slope over its own standard
    error - how much of the drift is signal rather than scatter.
    """
    if end - start < 3:
        return None, None, None
    y = close[start:end]
    x = np.arange(len(y), dtype=float)
    n = len(y)
    sxx = float(((x - x.mean()) ** 2).sum())
    sxy = float(((x - x.mean()) * (y - y.mean())).sum())
    raw = sxy / sxx if sxx else 0.0
    slope = raw / buy * 100 if buy else 0.0

    # How sure are we that the handle rises at all?  A slope of +0.18%/day means
    # nothing on its own: on a choppy handle that is well inside the noise. The
    # scanner and the chart read two different feeds, and a couple of rupees on
    # a couple of bars was enough to flip the SIGN of this number on MCX and
    # SOMANYCERA - and with it the verdict. So the gate now asks whether the
    # rise is bigger than its own standard error, not merely whether it is
    # positive. t below 1 means "flat, within noise".
    syy = float(((y - y.mean()) ** 2).sum())
    sse = max(syy - raw * sxy, 0.0)
    tstat = None
    if n > 2 and sxx > 0 and sse > 0:
        se = (sse / ((n - 2) * sxx)) ** 0.5
        tstat = raw / se if se else None
    elif n > 2 and sse == 0:
        tstat = 0.0                      # a perfectly straight handle, no scatter

    ref = avg_vol[end - 1]
    dry = float(vol[start:end].mean()) / ref if ref and not np.isnan(ref) else None
    return (round(slope, 3),
            (round(dry, 2) if dry is not None else None),
            (round(tstat, 2) if tstat is not None else None))


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
    slope, dry, slope_t = handle_quality(close, vol, avg_vol, ri + 1, h_end, buy, p)
    # Wedging upward now needs BOTH: a rise, and a rise that stands out from the
    # handle's own scatter. Either alone is a coin toss between two data feeds.
    if (slope is not None and slope > p["handle_slope"]
            and (slope_t is None or slope_t > p["handle_slope_t"])):
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
        Handle_Slope_t=slope_t,
        Handle_Vol=dry,
        Cup_Low=round(cup_low, 2),
        # O'Neil's exit, off the pivot - not off the depth of the cup.
        Target=round(buy * (1 + p["profit_take"]), 2),
        Target_Max=round(buy * (1 + p["profit_take_max"]), 2),
        # Kept only so the old number is still visible for comparison. It is
        # NOT a rule from the book and nothing keys off it.
        Measured_Move=round(buy + (buy - cup_low), 2),
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
        # The 8-week clock. If +20% arrives before this many sessions have
        # passed since the breakout, O'Neil's exception says hold the full
        # eight weeks and reassess rather than taking the 20%.
        hold_left = max(0, p["hold_sessions"] - int(since))
        return dict(Stage="BREAKOUT", Days_Since_Breakout=int(since),
                    Breakout_Price=round(entry, 2),
                    Stop=round(stop, 2),
                    Risk_pct=round((1 - stop / entry) * 100, 1),
                    Hold_Days_Left=hold_left,
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
                Hold_Days_Left=p["hold_sessions"],
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


COLS = ["Symbol", "Company", "Stage", "RS_Rating", "Group", "Group_Rank",
        "Base_Stage",
        "Earnings_Grade", "EPS_Q_Growth", "Sales_Q_Growth", "EPS_A_Growth", "ROE",
        "Last_Price", "Buy_Point",
        "Pct_To_Buy", "Stop", "Risk_pct", "Target", "Target_Max",
        "Measured_Move", "Hold_Days_Left", "Handle_Days",
        "Handle_Low", "Handle_Depth_pct",
        "Handle_Slope", "Handle_Vol", "Cup_Depth_pct", "Cup_Weeks", "Cup_Low",
        "Vol_x_Avg", "Above_50DMA", "Days_Since_Breakout", "Breakout_Price",
        "Earnings_Note"]


def write_no_signals(reason, log=print):
    """A scan that finds nothing must still overwrite yesterday's files.

    Both "nothing found" paths used to just `return`, which left the previous
    run's cup_handle_signals.csv sitting on disk. build_site.py reads that file,
    so the dashboard reprinted the LAST SUCCESSFUL scan's signals under a fresh
    "built" timestamp. A day with no setups was indistinguishable from a day
    with yesterday's setups, and nothing on the page gave it away.

    That is how SPLPETRO survived on the list after the holiday-bar fix removed
    its base: the scan correctly found nothing, returned early, and the site
    republished the stale row - buy point, stop and target included.

    Silence has to be published as silence.
    """
    empty = pd.DataFrame(columns=COLS)
    empty.to_csv(os.path.join(HERE, "cup_handle_signals.csv"), index=False)
    try:
        write_xlsx(empty, OUT_XLSX)
    except Exception as exc:                      # never let this hide the CSV
        log(f"  (xlsx not rewritten: {type(exc).__name__}: {exc})")
    with open(OUT_TV, "w") as fh:
        fh.write("")
    log(f"  Wrote empty signal files ({reason}), so the dashboard publishes "
        "an empty list rather than reprinting the last run's.")


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
    ap.add_argument("--no-groups", action="store_true",
                    help="skip the industry-group rank entirely")
    ap.add_argument("--group-budget", type=int, default=250,
                    help="uncached symbols to look up for the industry map this run "
                         "(0 = rank from the cache as it stands, fetch nothing)")
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
        write_no_signals("no cups passed the rules")
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

    # O'Neil's group test. He puts 37% of a stock's move on its subgroup and
    # 12% on its major group, and his winners sat in the top third of groups.
    # Flagged, never dropped: the industry map is incomplete by design (it
    # fills in a few hundred symbols a run) and a rank we cannot compute must
    # not read as weakness.
    if not args.no_groups and len(df):
        try:
            import industry_groups
            cache = industry_groups.load_cache()
            if args.group_budget:
                cache = industry_groups.refresh(
                    live["Symbol"].dropna().astype(str).tolist(),
                    cache, budget=args.group_budget)
            df = industry_groups.annotate(df, live, cache)
            ranked = df["Group_Rank"].notna()
            if ranked.any():
                lag = ranked & (df["Group_Rank"] < industry_groups.LEADING)
                print(f"\nIndustry groups: {int(ranked.sum())}/{len(df)} signal(s) "
                      f"in a rankable group, {int(lag.sum())} outside the top 30%")
                for _, r in df[ranked].iterrows():
                    mark = "lagging group" if r["Group_Rank"] < industry_groups.LEADING else "leading group"
                    print(f"  {r['Symbol']:<12} {int(r['Group_Rank']):>3}  "
                          f"{mark:<14} {r['Group']}")
            else:
                print("\nIndustry groups: none of today's signals are in a group "
                      "with enough mapped members to rank yet.")
        except Exception as exc:
            print(f"  industry groups unavailable ({type(exc).__name__}: {exc})")

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
        write_no_signals("every candidate was filtered out")
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
