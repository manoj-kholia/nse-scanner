#!/usr/bin/env python3
"""
history_scan.py
---------------
Every cup-and-handle this stock has formed, and what happened next.

The live scanner answers "what can I buy today". It finds the newest cup whose
right rim is still inside the handle window and stops there - `find_cup` walks
pivots newest-first and BREAKS at the first rim older than handle_max. So a
textbook base that formed in March, broke out and ran 30% leaves no trace on
today's chart at all.

That is the thing O'Neil spends two chapters on. Chapter 14 is models of past
winners; selling rule 26 is "learn from your past selling mistakes - do your
own post-analysis by plotting on charts your past buy-and-sell points."

This walks the whole history and records each setup as it would have been seen
AT THE TIME, then what became of it. Everything here is causal: a setup is
armed only on the bar its right rim is confirmed (rim + piv bars, because a
pivot cannot be known until piv bars have passed), and every gate reads only
bars at or before the bar being judged. No rule looks forward.

    python3 history_scan.py MCX
    python3 history_scan.py MCX SOMANYCERA REDTAPE --period 5y

WHAT THIS IS NOT
    Not a backtest. There are no costs, no slippage, no position sizing, and
    the sample for one stock is a handful of trades - far too few to conclude
    anything about the rules. It is a record of what the rules said and what
    the price did, which is a different and more modest claim.

    Where a single day's bar touches both the stop and the target, daily data
    cannot say which came first. Those are recorded as AMBIGUOUS and counted as
    losses, because assuming the good outcome is how backtests flatter
    themselves.
"""

import argparse
import os
from datetime import datetime

import numpy as np
import pandas as pd

import find_patterns as fp

HERE = os.path.dirname(os.path.abspath(__file__))


def _first_cup_at(high, low, close, pivots, ri, p):
    """The cup ending at right rim `ri`, searched newest left rim first.

    Same order as find_cup, so a setup recorded here is one the live scanner
    would have returned on that day.
    """
    for li in reversed([q for q in pivots if q < ri]):
        length = ri - li
        if length > p["cup_max"]:
            break
        if length < p["cup_min"]:
            continue
        cup, _ = fp.cup_between(high, low, close, li, ri, p)
        if cup is not None:
            return cup
    return None


def scan_history(df, p=fp.P, one_at_a_time=True):
    """Return a list of setups, oldest first, each with its outcome.

    one_at_a_time models what a delivery trader actually does: while a position
    is open, a second base in the same stock is noted but not taken. It also
    stops one cup being counted twice when two adjacent pivots both qualify.
    """
    high = df["High"].to_numpy(float)
    low = df["Low"].to_numpy(float)
    close = df["Close"].to_numpy(float)
    vol = df["Volume"].to_numpy(float)
    n = len(df)
    if n < p["cup_min"] + p["piv"] * 2 + 10:
        return []

    idx = df.index
    pivots = fp.pivot_highs(high, p["piv"])
    avg_vol = pd.Series(vol).rolling(p["vol_len"]).mean().to_numpy()
    sma200 = pd.Series(close).rolling(200).mean().to_numpy()

    out = []
    open_until = -1                       # bar index a live trade closes on

    for ri in pivots:
        confirmed = ri + p["piv"]         # the rim cannot be KNOWN before this
        if confirmed >= n:
            break
        cup = _first_cup_at(high, low, close, pivots, ri, p)
        if cup is None:
            continue

        buy = cup["buy"]
        mid = cup["cup_low"] + (buy - cup["cup_low"]) / 2
        rec = dict(
            rim_bar=ri, rim_date=str(idx[ri])[:10], left_bar=cup["left"],
            buy=round(buy, 2), cup_low=round(cup["cup_low"], 2),
            cup_weeks=round(cup["length"] / 5, 1),
            depth_pct=round(cup["depth"] * 100, 1),
            outcome="", entry=None, entry_date=None, stop=None, target=None,
            bars_held=None, exit_price=None, pct=None, mfe_pct=None,
            note="",
        )

        # --- walk the handle forward -------------------------------------
        bo = None
        for j in range(max(ri + 1, confirmed), n):
            days = j - ri
            if days > p["handle_max"]:
                rec["outcome"] = "EXPIRED"
                rec["note"] = f"handle ran past {p['handle_max']} days without a breakout"
                break
            h_low = float(low[ri + 1:j + 1].min())
            if h_low < mid or (buy - h_low) / buy > p["handle_depth"]:
                rec["outcome"] = "BROKE DOWN"
                rec["note"] = (f"handle low {h_low:.2f} fell through "
                               f"{'the middle of the cup' if h_low < mid else 'the depth limit'}")
                break
            if days < p["handle_min"]:
                continue
            if cup["length"] + days < p["base_min"]:
                continue
            c = close[j]
            if c <= buy or c > buy * (1 + p["max_ext"]):
                continue
            if not np.isnan(avg_vol[j]) and vol[j] < p["vol_mult"] * avg_vol[j]:
                continue
            if not np.isnan(sma200[j]) and c <= sma200[j]:
                continue
            # the same handle-quality gates evaluate() applies, measured up to
            # but not through the breakout bar
            slope, dry, tstat = fp.handle_quality(close, vol, avg_vol,
                                                  ri + 1, j, buy, p)
            if (slope is not None and slope > p["handle_slope"]
                    and (tstat is None or tstat > p["handle_slope_t"])):
                rec["outcome"] = "BROKE DOWN"
                rec["note"] = f"handle wedged upward ({slope:+.3f}%/day, t {tstat})"
                break
            if dry is not None and dry > p["handle_vol"]:
                continue          # volume may still dry up on a later bar
            bo = j
            break

        if bo is None:
            if not rec["outcome"]:
                rec["outcome"] = "STILL FORMING"
                rec["note"] = "handle still open at the end of the data"
            out.append(rec)
            continue

        if one_at_a_time and bo <= open_until:
            rec["outcome"] = "SKIPPED"
            rec["note"] = "a position from an earlier base was still open"
            out.append(rec)
            continue

        # --- the trade ----------------------------------------------------
        entry = float(close[bo])
        h_low = float(low[ri + 1:bo].min()) if bo > ri + 1 else float(low[ri])
        stop = fp.oneil_stop(entry, h_low)
        target = buy * (1 + p["profit_take"])
        rec.update(entry=round(entry, 2), entry_date=str(idx[bo])[:10],
                   stop=round(stop, 2), target=round(target, 2))

        peak = entry
        for k in range(bo + 1, n):
            peak = max(peak, float(high[k]))
            hit_stop = low[k] <= stop
            hit_tgt = high[k] >= target
            if hit_stop and hit_tgt:
                # One daily bar, both levels. The tape knows the order; a daily
                # bar does not. Counting it as the target is how a backtest
                # lies to itself, so it is counted as the stop and flagged.
                rec.update(outcome="AMBIGUOUS", exit_price=round(stop, 2),
                           bars_held=k - bo,
                           note="stop and target both touched on the same day; "
                                "counted as the stop")
                break
            if hit_stop:
                rec.update(outcome="STOPPED", exit_price=round(stop, 2),
                           bars_held=k - bo)
                break
            if hit_tgt:
                fast = (k - bo) < p["hold_sessions"]
                rec.update(outcome="TARGET", exit_price=round(target, 2),
                           bars_held=k - bo,
                           note=(f"+{p['profit_take']*100:.0f}% in {k - bo} sessions - "
                                 f"O'Neil's exception says hold the full "
                                 f"{p['hold_sessions']} and reassess" if fast else ""))
                break
        else:
            rec.update(outcome="OPEN", exit_price=round(float(close[n - 1]), 2),
                       bars_held=n - 1 - bo, note="still open at the end of the data")

        rec["pct"] = round((rec["exit_price"] / entry - 1) * 100, 1)
        rec["mfe_pct"] = round((peak / entry - 1) * 100, 1)
        open_until = bo + (rec["bars_held"] or 0)
        out.append(rec)

    return out


def summarise(rows):
    """Counts only. Deliberately no equity curve, no win-rate headline.

    A handful of trades in one stock cannot support a performance claim, and
    printing one invites the reader to believe it.
    """
    taken = [r for r in rows if r["entry"] is not None]
    done = [r for r in taken if r["outcome"] in ("TARGET", "STOPPED", "AMBIGUOUS")]
    return dict(
        setups=len(rows),
        broke_out=len(taken),
        expired=sum(1 for r in rows if r["outcome"] == "EXPIRED"),
        broke_down=sum(1 for r in rows if r["outcome"] == "BROKE DOWN"),
        skipped=sum(1 for r in rows if r["outcome"] == "SKIPPED"),
        resolved=len(done),
        hit_target=sum(1 for r in done if r["outcome"] == "TARGET"),
        stopped=sum(1 for r in done if r["outcome"] in ("STOPPED", "AMBIGUOUS")),
        still_open=sum(1 for r in taken if r["outcome"] == "OPEN"),
    )


def report(sym, rows):
    print("=" * 78)
    print(sym)
    print("=" * 78)
    if not rows:
        print("  No cup-and-handle has formed in this window.")
        return
    print(f"  {'rim date':<12} {'buy':>9} {'cup':>6} {'depth':>7}  "
          f"{'outcome':<13} {'entry':>9} {'exit':>9} {'%':>7} {'held':>5} {'MFE':>7}")
    print("  " + "-" * 92)
    def num(v, fmt="{:.2f}"):
        return "-" if v is None else fmt.format(v)

    for r in rows:
        entry = num(r["entry"])
        exitp = num(r["exit_price"])
        pct = num(r["pct"], "{:+.1f}")
        mfe = num(r["mfe_pct"], "{:+.1f}")
        held = "-" if r["bars_held"] is None else str(r["bars_held"])
        print(f"  {r['rim_date']:<12} {r['buy']:>9.2f} {r['cup_weeks']:>5.1f}w "
              f"{r['depth_pct']:>6.1f}%  {r['outcome']:<13} "
              f"{entry:>9} {exitp:>9} {pct:>7} {held:>5} {mfe:>7}")
        if r["note"]:
            print(f"  {'':<12} {r['note']}")
    s = summarise(rows)
    print(f"\n  {s['setups']} setup(s): {s['broke_out']} broke out, "
          f"{s['expired']} expired, {s['broke_down']} broke down, "
          f"{s['skipped']} skipped while a position was open")
    if s["resolved"]:
        print(f"  of {s['resolved']} resolved trade(s): {s['hit_target']} reached "
              f"+{fp.P['profit_take']*100:.0f}%, {s['stopped']} stopped out"
              + (f", {s['still_open']} still open" if s["still_open"] else ""))
        print("  This is far too small a sample to mean anything. It is a record,")
        print("  not a result: no costs, no slippage, no sizing.")


def main():
    ap = argparse.ArgumentParser(description="Every past cup-and-handle, and what happened")
    ap.add_argument("symbols", nargs="+")
    ap.add_argument("--period", default="5y", help="how far back to look (yfinance period)")
    ap.add_argument("--csv", default="", help="also write every setup to this CSV")
    args = ap.parse_args()

    import yfinance as yf
    syms = [s.strip().upper() for s in args.symbols]
    print(f"{datetime.now():%Y-%m-%d %H:%M}  history for {', '.join(syms)} "
          f"over {args.period}\n")
    raw = yf.download([s + ".NS" for s in syms], period=args.period, interval="1d",
                      group_by="ticker", auto_adjust=False, actions=False,
                      progress=False, threads=True)
    allrows = []
    for s in syms:
        tk = s + ".NS"
        try:
            if isinstance(raw.columns, pd.MultiIndex):
                if tk not in raw.columns.get_level_values(0):
                    print(f"{s}: no data came back.\n")
                    continue
                df = raw[tk]
            else:
                df = raw
            df = df.dropna(subset=["Close", "High", "Low"])
            df, _ = fp.drop_phantom_sessions(df)
            df, _ = fp.drop_unfinished_session(df)
            rows = scan_history(df)
            report(s, rows)
            print()
            for r in rows:
                allrows.append(dict(Symbol=s, **r))
        except Exception as exc:
            print(f"{s}: {type(exc).__name__}: {exc}\n")

    if args.csv and allrows:
        pd.DataFrame(allrows).to_csv(args.csv, index=False)
        print(f"wrote {args.csv} ({len(allrows)} setups)")


if __name__ == "__main__":
    main()
