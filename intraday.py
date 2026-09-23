#!/usr/bin/env python3
"""
intraday.py
-----------
"Stocks in play" - the opening-range selection screen.

WHY THIS IS THE SELECTION AND NOT THE ENTRY
Zarattini, Barbon and Aziz tested a 5-minute opening range breakout across
7,000+ US stocks from 2016-2023. Run on everything, it returned 3.2% a year -
nothing. Run on only the 20 stocks each day with the most abnormal opening
volume, it returned 41.6% a year. The breakout rule was almost worthless; the
SELECTION carried the result. So this module spends its effort on picking the
right handful of stocks, and treats the entry as the simple part.

That is the same lesson the cup-and-handle work produced: the pattern mattered
far less than what the stock was and how it was trading.

WHAT IT MEASURES
    relative volume   today's first 5 minutes against the MEDIAN first five
                      minutes of the last 14 sessions. Median, not mean, so one
                      past news day cannot flatten today's signal.
    gap               today's open against yesterday's close. A stock needs
                      some dislocation to be worth a day's attention.
    ATR%              the daily true range as a % of price - how far this stock
                      actually travels, which decides both the stop and whether
                      the move can pay for the trade at all.
    break-even        what the round trip costs, from trading_costs.py, so the
                      cost is on the same row as the opportunity.

WHAT THIS IS NOT
It is not backtested. Free intraday data reaches back about 60 days, which is
an anecdote rather than a sample. Treat the output as a watchlist to study and
paper-trade, and read the break-even column before you believe any of it.
"""

import os
import json
import time
import argparse
from datetime import datetime

import numpy as np
import pandas as pd

import trading_costs

HERE = os.path.dirname(os.path.abspath(__file__))
LIVE = os.path.join(HERE, "EQUITY_L_live.csv")
OUT_CSV = os.path.join(HERE, "stocks_in_play.csv")
OUT_TV = os.path.join(HERE, "intraday_watchlist.txt")
OUT_STATUS = os.path.join(HERE, "intraday_status.json")

IST = "Asia/Kolkata"
SESSION_OPEN = "09:15"

# THE 09:15 BAR IS A TRAP. Verified against Yahoo's own feed on 23 Sep 2026
# across RELIANCE, HDFCBANK, TATASTEEL, YESBANK, ANDHRSUGAR and MCX: the
# 09:15-09:20 bar carries real volume DURING the session and is rewritten to
# zero once the day is history. Prices in that bar stay correct throughout.
#
# So the two measurements have to come from different windows:
#   price  - the 09:15 bar is the opening range, and is reliable
#   volume - must start at 09:20, or today's live number gets compared against
#            a history of zeros and every stock is rejected
# Windows are selected by CLOCK TIME, not bar position, so a missing bar
# shifts nothing.
OR_START, OR_END = "09:15", "09:20"        # the opening range, for price
VOL_START, VOL_END = "09:20", "09:25"      # the volume window, same times every day

P = dict(
    universe_top=200,      # how many liquid names to pull intraday data for
    min_turnover_cr=5.0,   # average daily turnover, Rs crore - slippage floor
    min_price=50.0,        # below this the tick size eats the move
    max_price=20000.0,
    lookback=14,           # sessions of history for the relative-volume base
    min_rvol=2.0,          # "abnormal" opening volume starts here
    min_gap=0.5,           # % - needs some dislocation to be in play
    min_atr=1.5,           # % - must travel far enough to cover costs
    # 0.50, not the 0.10 the US research used. Measured on 179 trades from
    # 248 qualifying days across 50 liquid NSE names over 60 sessions
    # (backtest_orb.py), costs including 0.05% a side of slippage:
    #   0.10 ATR -> stopped out 93% of the time, costs alone ate 0.66R per
    #               trade, and the whole thing lost 0.905R per trade
    #               (t = -3.21, 95% CI [-1.46, -0.35]) - significantly negative
    #   0.50 ATR -> stopped 33%, costs 0.13R, +0.132R per trade
    #               (t = 1.09, CI [-0.10, +0.37]) - no edge, but no bleed
    # A 0.10-ATR stop on a 3%-ATR stock is ~0.3% wide, barely more than the
    # 0.183% round trip. You were paying most of your risk budget in fees.
    stop_atr_frac=0.50,
    position=100000.0,     # Rs, for the break-even calculation
    top=20,                # the research used the top 20 by opening volume
)


# ----------------------------------------------------------------- universe --
def liquid_universe(live, p=P):
    """Names liquid enough that intraday slippage will not eat the edge.

    Deliberately NOT the cup-and-handle screen: intraday wants turnover and
    range, not a base near the highs.
    """
    d = live[live["Last Price"].notna()].copy()
    for c in ["Last Price", "Avg Volume 20d", "Bars"]:
        d[c] = pd.to_numeric(d.get(c), errors="coerce")
    d = d.dropna(subset=["Last Price", "Avg Volume 20d"])

    d["Turnover_Cr"] = (d["Last Price"] * d["Avg Volume 20d"] / 1e7).round(2)
    d = d[
        (d["Last Price"] >= p["min_price"])
        & (d["Last Price"] <= p["max_price"])
        & (d["Turnover_Cr"] >= p["min_turnover_cr"])
        & (d["Bars"].fillna(0) >= 60)
    ]
    return (d.sort_values("Turnover_Cr", ascending=False)
             .head(p["universe_top"]).reset_index(drop=True))


# --------------------------------------------------------------- session math --
def _sessions(df):
    """Split a tz-aware intraday frame into one frame per trading date."""
    if df is None or df.empty:
        return []
    d = df.dropna(subset=["Open", "High", "Low", "Close"]).copy()
    if not isinstance(d.index, pd.DatetimeIndex) or d.empty:
        return []
    try:
        d.index = (d.index.tz_convert(IST) if d.index.tz is not None
                   else d.index.tz_localize("UTC").tz_convert(IST))
    except (TypeError, ValueError):
        return []
    return [(day, g) for day, g in d.groupby(d.index.date) if len(g)]


def _window(g, start, end):
    """Bars whose IST clock time falls in [start, end).

    By clock rather than by position, so a session with a missing or extra
    early bar still lines up with every other session.
    """
    if g is None or not len(g):
        return g
    t = g.index.strftime("%H:%M")
    return g[(t >= start) & (t < end)]


def _daily_atr(sessions, n=14):
    """ATR from intraday bars rolled up to daily - no second download needed."""
    if len(sessions) < 3:
        return None
    rows = [dict(High=g["High"].max(), Low=g["Low"].min(), Close=g["Close"].iloc[-1])
            for _, g in sessions]
    d = pd.DataFrame(rows)
    prev = d["Close"].shift(1)
    tr = pd.concat([d["High"] - d["Low"],
                    (d["High"] - prev).abs(),
                    (d["Low"] - prev).abs()], axis=1).max(axis=1)
    atr = tr.tail(min(n, len(tr))).mean()
    return float(atr) if np.isfinite(atr) else None


def opening_stats(df, p=P, why=None):
    """Today's opening behaviour against its own recent history.

    Returns None when there is not enough history to make the comparison
    meaningful - a blank is better than a number built on three sessions.
    Pass a dict as `why` to find out WHICH check rejected it; lumping every
    failure under one reason is how a silent empty download looks exactly like
    a genuinely quiet market.
    """
    def no(reason):
        if why is not None:
            why["reason"] = reason
        return None

    if df is None or not len(df):
        return no("no bars returned")

    sess = _sessions(df)
    if not sess:
        return no("bars returned but all empty")
    if len(sess) < 4:
        return no(f"only {len(sess)} session(s) of intraday history")

    today_date, today = sess[-1]
    prior = sess[:-1][-p["lookback"]:]
    if len(prior) < 3:
        return no("fewer than 3 prior sessions")

    or_today = _window(today, OR_START, OR_END)
    if or_today.empty:
        return no(f"today's data starts at {today.index[0]:%H:%M}, past the open")

    vol_today_bars = _window(today, VOL_START, VOL_END)
    if vol_today_bars.empty:
        return no(f"too early - the {VOL_START} bar has not printed yet")

    # Like for like: the same clock window on every past session.
    base_vols = [float(_window(g, VOL_START, VOL_END)["Volume"].sum())
                 for _, g in prior]
    base_vols = [v for v in base_vols if v > 0]
    if len(base_vols) < 3:
        return no(f"too few past sessions have a {VOL_START} bar with volume")
    median_open_vol = float(np.median(base_vols))

    open_vol = float(vol_today_bars["Volume"].sum())
    if open_vol <= 0:
        return no(f"today's {VOL_START} bar has no volume yet")

    prev_close = float(prior[-1][1]["Close"].iloc[-1])
    day_open = float(or_today["Open"].iloc[0])
    if prev_close <= 0 or day_open <= 0:
        return no("bad price data")

    atr = _daily_atr(prior)
    or_hi, or_lo = float(or_today["High"].max()), float(or_today["Low"].min())

    return dict(
        Session=str(today_date),
        Prev_Close=round(prev_close, 2),
        Open=round(day_open, 2),
        Gap_pct=round((day_open / prev_close - 1) * 100, 2),
        Open_Vol=int(open_vol),
        RVol=round(open_vol / median_open_vol, 2),
        ATR=round(atr, 2) if atr else None,
        ATR_pct=round(atr / day_open * 100, 2) if atr else None,
        OR_High=round(or_hi, 2),
        OR_Low=round(or_lo, 2),
        OR_Range_pct=round((or_hi - or_lo) / day_open * 100, 2),
        Last=round(float(today["Close"].iloc[-1]), 2),
        Bars_Today=int(len(today)),
    )


def add_trade_plan(row, p=P):
    """The ORB entry, its stop, and what the round trip costs.

    The stop is 50% of the 14-day ATR. The research used 10%, which is tight
    enough that the losers are small and one winner pays for them - but that
    was measured on US stocks. Tested on NSE it was hit 93% of the time and
    lost money on both cost assumptions (see backtest_orb.py), because on a
    3%-ATR stock a 0.10 ATR stop is ~0.3% wide and the round trip alone costs
    0.18%. There is no TARGET here on purpose: only 18% of these trades ever
    travel 1 ATR, so any fixed target far out is a target that rarely pays.
    """
    atr, price = row.get("ATR"), row.get("Open")
    out = dict(Long_Trigger=row.get("OR_High"), Short_Trigger=row.get("OR_Low"))

    stop_dist = atr * p["stop_atr_frac"] if atr else None
    out["Stop_Dist"] = round(stop_dist, 2) if stop_dist else None
    if stop_dist and row.get("OR_High"):
        out["Long_Stop"] = round(row["OR_High"] - stop_dist, 2)
        out["Short_Stop"] = round(row["OR_Low"] + stop_dist, 2)
        out["Risk_pct"] = round(stop_dist / row["OR_High"] * 100, 2)

    # The exit. Not a price, because every fixed price target measured WORSE
    # than simply holding to the close (backtest_orb.py, 248 setups):
    #     exit at close  +0.132R   43% win
    #     target 1R      -0.053R   51% win   <- better win rate, loses money
    #     target 2R      -0.001R
    #     target 3R      +0.018R
    # Capping the winners is what kills it: only 18% of these trades ever
    # travel 1 ATR, so the few that run are the entire result, and a target
    # sells them early. "Close" means your broker's intraday square-off.
    out["Exit"] = "close"

    be = trading_costs.breakeven_pct(price, p["position"], intraday=True)
    out["Breakeven_pct"] = be
    # Is the stock's normal daily travel even big enough to pay for the trade?
    if be is not None and row.get("ATR_pct"):
        out["Cost_vs_ATR"] = round(be / row["ATR_pct"] * 100, 1)
    return out


def score(row, p=P):
    """Rank by abnormal opening volume, which is what the evidence points at.

    Gap and range are gates, not scores - a stock either dislocated enough to be
    worth the day or it did not.
    """
    rv = row.get("RVol") or 0
    return round(float(rv), 2)


# --------------------------------------------------------------------- scan --
def default_downloader(period="1mo", interval="5m"):
    """Yahoo intraday.

    Intraday is much fussier than daily: large batched requests frequently come
    back with the column structure present and every value NaN, which looks
    exactly like a quiet market unless you check. So batches stay small and a
    batch that returns nothing is retried one ticker at a time before it is
    written off.
    """
    import yfinance as yf

    def dl(tickers):
        return yf.download(tickers, period=period, interval=interval,
                           group_by="ticker", auto_adjust=False, actions=False,
                           progress=False, threads=True, prepost=False)
    return dl


def _pick(raw, tk):
    """One ticker's frame out of whatever shape the download returned."""
    if raw is None or not len(raw):
        return None
    if isinstance(raw.columns, pd.MultiIndex):
        if tk not in raw.columns.get_level_values(0):
            return None
        return raw[tk]
    return raw


def _usable(df):
    return df is not None and len(df) and df["Close"].notna().any()


def scan(symbols, downloader=None, batch_size=10, pause=1.0, p=P, log=print):
    """Opening stats for a list of symbols. Returns (rows, scanned, skipped)."""
    downloader = downloader or default_downloader()
    rows, scanned, skipped = [], 0, {}

    for start in range(0, len(symbols), batch_size):
        batch = symbols[start:start + batch_size]
        tickers = [s + ".NS" for s in batch]
        try:
            raw = downloader(tickers)
        except Exception as exc:
            log(f"  batch failed: {type(exc).__name__}: {exc}")
            raw = None

        frames = {s: _pick(raw, tk) for s, tk in zip(batch, tickers)}

        # A batch that came back empty is usually the request, not the market.
        missing = [s for s, f in frames.items() if not _usable(f)]
        if missing and len(missing) == len(batch):
            log(f"    batch returned nothing - retrying {len(missing)} one at a time")
            for s in missing:
                try:
                    frames[s] = _pick(downloader([s + ".NS"]), s + ".NS")
                except Exception:
                    frames[s] = None
                time.sleep(0.3)

        for sym in batch:
            df = frames.get(sym)
            try:
                scanned += 1
                why = {}
                st = opening_stats(df, p, why) if _usable(df) else None
                if st is None:
                    skipped.setdefault(why.get("reason", "no data returned"), []).append(sym)
                    continue
                st["Symbol"] = sym
                st.update(add_trade_plan(st, p))
                st["Score"] = score(st, p)
                rows.append(st)
            except Exception as exc:
                skipped.setdefault(f"error: {type(exc).__name__}", []).append(sym)

        done = min(start + batch_size, len(symbols))
        log(f"  {done}/{len(symbols)} fetched - {len(rows)} with usable opening data")
        if done < len(symbols):
            time.sleep(pause)
    return rows, scanned, skipped


def in_play(rows, p=P):
    """Apply the gates, then rank."""
    if not rows:
        return pd.DataFrame()
    d = pd.DataFrame(rows)
    d = d[
        (d["RVol"] >= p["min_rvol"])
        & (d["Gap_pct"].abs() >= p["min_gap"])
        & (d["ATR_pct"].fillna(0) >= p["min_atr"])
    ]
    return d.sort_values("Score", ascending=False).head(p["top"]).reset_index(drop=True)


def write_status(scanned, usable, skipped, kept):
    """Say out loud whether the DATA worked, separately from whether the market
    was interesting. An empty screen and a broken feed look identical on a
    dashboard, and only one of them means "nothing to do today".
    """
    reasons = {k: len(v) for k, v in skipped.items()}
    top = max(reasons, key=reasons.get) if reasons else None

    if usable == 0 and scanned:
        ok, headline = False, "No usable intraday data"
        if top and "opening volume" in top:
            detail = ("The free Yahoo feed returned prices for these stocks but "
                      "NO intraday volume - every opening bar came back at zero. "
                      "Relative volume is the whole basis of this screen, so it "
                      "cannot run on this data source. Intraday volume for NSE "
                      "needs a broker API (Kite Connect, Dhan, Fyers) or a paid "
                      "vendor. This is not a quiet market - it is a missing feed.")
        else:
            detail = (f"All {scanned} symbols were rejected: {top}. "
                      "This is a data problem, not a market reading.")
    else:
        ok, headline = True, f"{kept} in play"
        detail = (f"{usable} of {scanned} symbols had usable opening data."
                  if usable < scanned else f"All {scanned} symbols returned data.")

    status = dict(ok=ok, headline=headline, detail=detail,
                  scanned=int(scanned), usable=int(usable), kept=int(kept),
                  reasons=reasons,
                  checked=datetime.now().strftime("%d %b %Y, %H:%M"))
    with open(OUT_STATUS, "w") as fh:
        json.dump(status, fh, indent=1)
    return status


COLS = ["Symbol", "Company", "Session", "Score", "RVol", "Gap_pct", "ATR_pct",
        "Prev_Close", "Open", "OR_High", "OR_Low", "OR_Range_pct",
        "Long_Trigger", "Long_Stop", "Short_Trigger", "Short_Stop",
        "Stop_Dist", "Risk_pct", "Exit", "Breakeven_pct", "Cost_vs_ATR",
        "Turnover_Cr", "Open_Vol", "Last"]


def main():
    ap = argparse.ArgumentParser(description="Opening-range 'stocks in play' screen")
    ap.add_argument("--source", default=LIVE)
    ap.add_argument("--top", type=int, default=P["top"])
    ap.add_argument("--universe-top", type=int, default=P["universe_top"])
    ap.add_argument("--min-rvol", type=float, default=P["min_rvol"])
    ap.add_argument("--min-gap", type=float, default=P["min_gap"])
    ap.add_argument("--position", type=float, default=P["position"],
                    help="position size in rupees, for the break-even column")
    ap.add_argument("--batch-size", type=int, default=10)
    ap.add_argument("--pause", type=float, default=1.0)
    args = ap.parse_args()

    if not os.path.exists(args.source):
        raise SystemExit(f"Cannot find {args.source}. Run update_nse_data.py first.")

    p = dict(P, top=args.top, universe_top=args.universe_top,
             min_rvol=args.min_rvol, min_gap=args.min_gap, position=args.position)

    live = pd.read_csv(args.source)
    uni = liquid_universe(live, p)
    names = dict(zip(uni["Symbol"], uni["Company"]))
    turn = dict(zip(uni["Symbol"], uni["Turnover_Cr"]))
    print(f"{datetime.now():%Y-%m-%d %H:%M}  {len(uni)} liquid names "
          f"(turnover >= Rs {p['min_turnover_cr']}cr, price Rs {p['min_price']}+)")

    rows, scanned, skipped = scan(uni["Symbol"].tolist(), batch_size=args.batch_size,
                                  pause=args.pause, p=p)
    picks = in_play(rows, p)

    if skipped:
        print("\nSkipped:")
        for why, syms in sorted(skipped.items(), key=lambda kv: -len(kv[1])):
            shown = ", ".join(syms[:6]) + ("..." if len(syms) > 6 else "")
            print(f"  {len(syms):3d}  {why:<32} {shown}")

    status = write_status(scanned, len(rows), skipped, len(picks))

    if picks.empty:
        # Always leave the file behind, even empty. A missing file and an empty
        # one mean very different things, and the one that broke this on the
        # first run was a pathspec that did not exist.
        pd.DataFrame(columns=COLS).to_csv(OUT_CSV, index=False)
        open(OUT_TV, "w").close()
        print(f"\n{status['headline']}: {status['detail']}")
        if status["ok"]:
            print("  A quiet open is not a reason to trade.")
        return

    picks.insert(1, "Company", picks["Symbol"].map(names))
    picks["Turnover_Cr"] = picks["Symbol"].map(turn)
    picks[[c for c in COLS if c in picks]].to_csv(OUT_CSV, index=False)
    with open(OUT_TV, "w") as fh:
        fh.write(",".join("NSE:" + s for s in picks["Symbol"]))

    print(f"\nFetched {scanned}.  In play: {len(picks)}")
    print(f"  wrote {OUT_CSV}")
    print(f"  wrote {OUT_TV}\n")
    show = [c for c in ["Symbol", "RVol", "Gap_pct", "ATR_pct", "Open",
                        "OR_High", "OR_Low", "Risk_pct", "Breakeven_pct",
                        "Cost_vs_ATR"] if c in picks]
    print(picks[show].to_string(index=False))

    thin = picks[picks["Cost_vs_ATR"].fillna(0) > 15]
    for _, r in thin.iterrows():
        print(f"  !! {r['Symbol']}: costs eat {r['Cost_vs_ATR']:.0f}% of a normal "
              "day's range - the move has to be near-perfect to pay")


if __name__ == "__main__":
    main()
