#!/usr/bin/env python3
"""
backtest_orb.py
---------------
Does the opening-range breakout actually make money on NSE?

This exists because the answer turned out to be no, and the reason was a
parameter copied from a US paper without checking it here. The stop in the
Zarattini/Barbon/Aziz work is 10% of the 14-day ATR. On an NSE stock with a 3%
ATR that is a stop roughly 0.3% wide - barely more than the 0.183% it costs to
do the round trip. You end up paying most of your risk budget in fees before
the trade has a chance.

Measured on 23 Sep 2026, 50 liquid names, 60 sessions, 248 qualifying days
of which 72% triggered (179 trades), exiting at the close:

    stop   fees only (0.082%)              fees + slippage (0.182%)
    0.10A  -0.544  t=-1.93  [-1.10,+0.01]  -0.905  t=-3.21  [-1.46,-0.35]
    0.25A  +0.174  t= 0.91  [-0.20,+0.55]  +0.029  t= 0.15  [-0.35,+0.40]
    0.50A  +0.204  t= 1.68  [-0.03,+0.44]  +0.132  t= 1.09  [-0.10,+0.37]
    0.75A  +0.125  t= 1.49  [-0.04,+0.29]  +0.076  t= 0.91  [-0.09,+0.24]
    1.00A  +0.086  t= 1.34  [-0.04,+0.21]  +0.050  t= 0.78  [-0.07,+0.17]

READ THAT TWICE. Whether the tight stop is "significantly loss-making" or
merely "bad" depends on one assumption: how much you lose to slippage. At
0.05% a side it is significant (t = -3.21); at zero it is not (the interval
touches +0.01). Nothing in the data settles it, so this script prints BOTH
columns every run rather than picking one and hiding the choice. Zero
slippage is not achievable - you cross a real spread on a stock that just
gapped - so the truth sits nearer the right-hand column.

What both columns agree on: the 0.10 ATR stop from the US paper loses money
on NSE, and widening it to 0.50 removes the bleed without creating an edge -
every positive interval still contains zero.

How far these trades actually travel, measured at the 0.50 ATR stop:
0.5 ATR reached on 38.2%, 1 ATR on 18.0%, 1.5 ATR on 7.9%, 2.5 ATR on 3.4%.
That is what a target has to live inside.

HONEST LIMITS
  * ~60 sessions of 5-minute data is all Yahoo gives. One market window.
  * 5-minute bars, so the path inside a bar is approximated. When a bar could
    have hit both target and stop, the STOP is assumed first.
  * Slippage is an assumption, not a measurement - see above.
  * The universe is today's liquid names applied to the past - mild
    survivorship bias.

Run:  python3 backtest_orb.py                  (default 50 names)
      python3 backtest_orb.py --top 100 --stops 0.1,0.25,0.5
"""

import argparse
import statistics
import time
from datetime import datetime

import numpy as np
import pandas as pd

import intraday
import trading_costs

# the filters the live screen uses, so the test measures the live screen
MIN_RVOL, MIN_GAP, MIN_ATR, LOOKBACK = 2.0, 0.5, 1.5, 14

# Clock times to test walking away at, and targets to test taking.
EXIT_TIMES = ("13:00", "14:00", "14:30", "15:00", "15:10", "15:15", "15:25")
TARGETS = (1, 2, 3, 5, 10)


def sessions_of(df):
    """[(date, frame)] in IST, oldest first."""
    return intraday._sessions(df)


def setups(df, stop_frac, cost_pct, force_long=False):
    """Every qualifying day for one symbol, walked bar by bar.

    Returns a list of dicts: whether it triggered, whether the stop was hit,
    the maximum favourable excursion in R, and the R at the close - all after
    subtracting what the round trip costs, expressed in R.
    """
    sess = sessions_of(df)
    out = []
    for i in range(LOOKBACK, len(sess)):
        cur = sess[i][1]
        prior = [s for _, s in sess[i - LOOKBACK:i]]

        o_bar = intraday._window(cur, intraday.OR_START, intraday.OR_END)
        v_bar = intraday._window(cur, intraday.VOL_START, intraday.VOL_END)
        if o_bar is None or v_bar is None or o_bar.empty or v_bar.empty:
            continue

        base = [float(intraday._window(p, intraday.VOL_START,
                                       intraday.VOL_END)["Volume"].sum())
                for p in prior]
        base = [b for b in base if b > 0]
        if len(base) < 8:
            continue

        rvol = float(v_bar["Volume"].sum()) / statistics.median(base)
        prev_close = float(prior[-1]["Close"].iloc[-1])
        day_open = float(o_bar["Open"].iloc[0])
        if prev_close <= 0 or day_open <= 0:
            continue
        gap = (day_open / prev_close - 1) * 100

        atr = intraday._daily_atr([(None, p) for p in prior])
        if not atr:
            continue
        atr_pct = atr / day_open * 100
        if not (rvol >= MIN_RVOL and abs(gap) >= MIN_GAP and atr_pct >= MIN_ATR):
            continue

        long = True if force_long else (gap > 0)
        trig = float(o_bar["High"].max()) if long else float(o_bar["Low"].min())
        or_hi, or_lo = float(o_bar["High"].max()), float(o_bar["Low"].min())
        or_range_pct = (or_hi - or_lo) / day_open * 100
        sd = stop_frac * atr
        after = cur[cur.index.strftime("%H:%M") >= intraday.VOL_START]
        if after.empty:
            continue

        hi = after["High"].to_numpy(float)
        lo = after["Low"].to_numpy(float)
        op = after["Open"].to_numpy(float)

        ei = -1
        for k in range(len(after)):
            if (hi[k] >= trig) if long else (lo[k] <= trig):
                ei = k
                entry = max(trig, op[k]) if long else min(trig, op[k])
                break
        if ei < 0:
            out.append(dict(triggered=False, rvol=rvol, gap=abs(gap)))
            continue

        cl = after["Close"].to_numpy(float)
        clock = after.index.strftime("%H:%M").to_numpy()

        # Walk once and keep the whole path in R. Every exit rule below is then
        # evaluated on the SAME trade rather than re-simulated, so the rules can
        # be compared without any of them getting a different set of days.
        mfe, stopped, stop_i = 0.0, False, None
        path = []               # (clock, high R, close R) up to the stop
        for k in range(ei, len(after)):
            adverse = (entry - lo[k]) / sd if long else (hi[k] - entry) / sd
            favour = (hi[k] - entry) / sd if long else (entry - lo[k]) / sd
            if adverse >= 1:            # conservative: the stop goes first
                stopped, stop_i = True, k
                break
            mfe = max(mfe, favour)
            path.append((clock[k], favour,
                         ((cl[k] - entry) if long else (entry - cl[k])) / sd))

        eod = float(after["Close"].iloc[-1])
        eod_r = -1.0 if stopped else ((eod - entry) if long else (entry - eod)) / sd

        # R if you simply walked away at a given clock time
        r_at = {}
        for t in EXIT_TIMES:
            done = [p for p in path if p[0] <= t]
            if stopped and (stop_i is not None) and clock[stop_i] <= t:
                r_at[t] = -1.0
            elif done:
                r_at[t] = done[-1][2]
            else:
                r_at[t] = 0.0       # flat: never got that far into the day

        # Did the target print before the stop did, and what happened after
        tgt, part = {}, {}
        for k in TARGETS:
            j = next((i for i, p in enumerate(path) if p[1] >= k), None)
            tgt[k] = j is not None
            if j is None:
                part[k] = -1.0 if stopped else eod_r
            else:
                # half off at the target, half held to the close on the same stop
                part[k] = 0.5 * k + 0.5 * (-1.0 if stopped else eod_r)

        out.append(dict(triggered=True, stopped=stopped, mfe=mfe, eod_r=eod_r,
                        r_at=r_at, tgt=tgt, part=part, long=long,
                        rvol=rvol, gap=abs(gap), atr_pct=atr_pct,
                        or_pct=or_range_pct, fire_bar=ei,
                        cost_r=cost_pct / (stop_frac * atr_pct)))
    return out


def failure_slices(trades, fields=None):
    """Does anything you can see at 09:25 tell you the breakout will fail?

    A failed breakout here means: the trigger printed, and the stop was hit.
    Each candidate is split into low/mid/high thirds and the stop-out rate and
    expectancy are reported for each. A filter is only worth having if the
    thirds actually separate - if all three are the same, that field knows
    nothing and adding it to the screen would just cost you trades.
    """
    T = [t for t in trades if t.get("triggered")]
    if len(T) < 30:
        return None
    fields = fields or [("rvol", "relative volume"), ("gap", "abs gap %"),
                        ("atr_pct", "ATR %"), ("or_pct", "opening range %"),
                        ("fire_bar", "bars until it triggered")]
    out = []
    for key, label in fields:
        vals = sorted(t[key] for t in T if t.get(key) is not None)
        if len(vals) < 30:
            continue
        lo, hi = vals[len(vals) // 3], vals[2 * len(vals) // 3]
        buckets = [("low", lambda v: v <= lo),
                   ("mid", lambda v: lo < v <= hi),
                   ("high", lambda v: v > hi)]
        rows = []
        for name, test in buckets:
            grp = [t for t in T if t.get(key) is not None and test(t[key])]
            if not grp:
                continue
            rs = [t["eod_r"] - t["cost_r"] for t in grp]
            rows.append(dict(
                bucket=name, n=len(grp),
                span=f"{min(t[key] for t in grp):.2f}-{max(t[key] for t in grp):.2f}",
                fail=round(sum(t["stopped"] for t in grp) / len(grp) * 100),
                avg_r=round(sum(rs) / len(rs), 3)))
        if len(rows) == 3:
            spread = max(r["fail"] for r in rows) - min(r["fail"] for r in rows)
            out.append((label, rows, spread))
    out.sort(key=lambda x: -x[2])
    return out


def noise_by_time(frames):
    """Average 5-minute true range, as a % of price, by clock time.

    This settles the "the last fifteen minutes are the noisiest" question with
    the tape instead of a feeling. If the closing bars really are wilder, they
    show up here as a fatter number.
    """
    buckets = {}
    for df in frames.values():
        for _, g in intraday._sessions(df):
            if len(g) < 30:
                continue
            rng = ((g["High"] - g["Low"]) / g["Close"] * 100).to_numpy(float)
            for t, v in zip(g.index.strftime("%H:%M"), rng):
                if np.isfinite(v):
                    buckets.setdefault(t, []).append(v)
    return {t: (round(float(np.mean(v)), 3), len(v))
            for t, v in sorted(buckets.items()) if len(v) > 50}


def summarise(trades, targets=(1, 2, 3, 5, 10)):
    """Expectancy per exit rule, net of costs, with a t-stat so you can tell
    a real edge from a lucky sample."""
    T = [t for t in trades if t["triggered"]]
    if not T:
        return None

    def score(rule):
        rs = [rule(t) - t["cost_r"] for t in T]
        n, m = len(rs), sum(rs) / len(rs)
        sd = statistics.stdev(rs) if n > 1 else 0.0
        se = sd / np.sqrt(n) if n > 1 and sd else None
        return dict(avg_r=round(m, 3),
                    win_pct=round(sum(r > 0 for r in rs) / n * 100),
                    t=round(m / se, 2) if se else None,
                    ci95=[round(m - 1.96 * se, 3), round(m + 1.96 * se, 3)] if se else None)

    res = dict(setups=len(trades), triggered=len(T),
               trigger_pct=round(len(T) / len(trades) * 100, 1),
               stopped_pct=round(sum(t["stopped"] for t in T) / len(T) * 100),
               avg_cost_r=round(sum(t["cost_r"] for t in T) / len(T), 2),
               reached={f"{k}R": round(sum(t["mfe"] >= k for t in T) / len(T) * 100, 1)
                        for k in targets})
    res["exit at close"] = score(lambda t: t["eod_r"])
    for k in targets:
        res[f"target {k}R"] = score(
            lambda t, k=k: k if t["mfe"] >= k else (-1.0 if t["stopped"] else t["eod_r"]))
    # half off at the target, half held to the close - the usual objection to
    # a hard target is that it sells the runners, and this is the fix people
    # actually use, so it gets measured too rather than argued about
    for k in targets:
        if "part" in T[0]:
            res[f"half at {k}R"] = score(lambda t, k=k: t["part"][k])
    # and simply walking away earlier in the day
    if "r_at" in T[0]:
        for t0 in EXIT_TIMES:
            res[f"walk at {t0}"] = score(lambda t, t0=t0: t["r_at"][t0])
    return res


def main():
    ap = argparse.ArgumentParser(description="Does the ORB pay on NSE?")
    ap.add_argument("--source", default=intraday.LIVE)
    ap.add_argument("--top", type=int, default=50, help="how many liquid names")
    ap.add_argument("--stops", default="0.10,0.25,0.50,0.75,1.00")
    ap.add_argument("--batch-size", type=int, default=10)
    ap.add_argument("--pause", type=float, default=1.0)
    args = ap.parse_args()

    live = pd.read_csv(args.source)
    uni = intraday.liquid_universe(live, dict(intraday.P, universe_top=args.top))
    syms = uni["Symbol"].tolist()

    # Two cost assumptions, always both. Slippage is the single biggest lever
    # on the answer and it is an assumption rather than a fee, so hiding the
    # choice inside a default would be the whole problem.
    scenarios = [
        ("fees only", trading_costs.breakeven_pct(
            500, intraday.P["position"], intraday=True, slippage=0)),
        (f"fees + {trading_costs.DEFAULT_SLIPPAGE * 100:.2f}% slippage a side",
         trading_costs.breakeven_pct(
            500, intraday.P["position"], intraday=True,
            slippage=trading_costs.DEFAULT_SLIPPAGE)),
    ]
    print(f"{datetime.now():%Y-%m-%d %H:%M}  {len(syms)} names, 60 sessions of 5m bars")
    for label, pct in scenarios:
        print(f"  {label:<28} round trip = {pct}% of position")
    print()

    dl = intraday.default_downloader(period="60d", interval="5m")
    frames = {}
    for start in range(0, len(syms), args.batch_size):
        batch = syms[start:start + args.batch_size]
        try:
            raw = dl([s + ".NS" for s in batch])
        except Exception as exc:
            print(f"  batch failed: {type(exc).__name__}")
            continue
        for s in batch:
            f = intraday._pick(raw, s + ".NS")
            if intraday._usable(f):
                frames[s] = f
        print(f"  {min(start + args.batch_size, len(syms))}/{len(syms)} fetched"
              f" - {len(frames)} usable")
        time.sleep(args.pause)

    if not frames:
        raise SystemExit("No intraday data came back - nothing to test.")

    for label, cost_pct in scenarios:
        print(f"\n=== {label} ({cost_pct}% round trip) ===")
        print(f"{'stop':>10} {'setups':>7} {'trig%':>6} {'stop-out%':>10} "
              f"{'costs(R)':>9} {'close':>8} {'t':>6}  {'95% CI':>18}")
        print("-" * 82)
        keep = None
        for frac in [float(x) for x in args.stops.split(",")]:
            trades = []
            for s, f in frames.items():
                try:
                    trades += setups(f, frac, cost_pct)
                except Exception:
                    continue
            r = summarise(trades)
            if not r:
                continue
            c = r["exit at close"]
            ci = f"[{c['ci95'][0]:+.2f}, {c['ci95'][1]:+.2f}]" if c["ci95"] else ""
            print(f"{frac:>9.2f}A {r['setups']:>7} {r['trigger_pct']:>5.0f}% "
                  f"{r['stopped_pct']:>9}% {r['avg_cost_r']:>9.2f} "
                  f"{c['avg_r']:>+8.3f} {c['t'] if c['t'] is not None else 0:>6.2f}  {ci:>18}")
            if abs(frac - intraday.P["stop_atr_frac"]) < 1e-9:
                print(f"{'':>10} reached: " + "  ".join(
                    f"{k} {v}%" for k, v in r["reached"].items()))
                keep = r          # the stop actually in use - exit rules below

        # Which exit rule is best at the stop the screen actually uses? This
        # is how the Target column is chosen: by measurement, not by taste.
        if keep:
            print(f"\n  exit rule at the {intraday.P['stop_atr_frac']:.2f} ATR stop:")
            print(f"  {'rule':<16} {'avg R':>8} {'win%':>6} {'t':>6}  {'95% CI':>18}")
            rules = (["exit at close"]
                     + [k for k in keep if k.startswith("target ")]
                     + [k for k in keep if k.startswith("half at ")]
                     + [k for k in keep if k.startswith("walk at ")])
            for name in rules:
                v = keep[name]
                ci = (f"[{v['ci95'][0]:+.2f}, {v['ci95'][1]:+.2f}]"
                      if v.get("ci95") else "")
                print(f"  {name:<16} {v['avg_r']:>+8.3f} {v['win_pct']:>5}% "
                      f"{v['t'] if v['t'] is not None else 0:>6.2f}  {ci:>18}")

    # Can a failed breakout be spotted at 09:25, before you commit?
    base_cost = scenarios[-1][1]
    live = []
    for s, f in frames.items():
        try:
            live += setups(f, intraday.P["stop_atr_frac"], base_cost)
        except Exception:
            continue
    sl = failure_slices(live)
    if sl:
        print("\n=== can a failed breakout be seen coming? ===")
        print("  stop-out rate and expectancy by what was visible at 09:25,")
        print("  split into thirds. A field only helps if the thirds separate.\n")
        for label, rows, spread in sl:
            print(f"  {label}  (spread {spread} points)")
            for r in rows:
                print(f"    {r['bucket']:<5}{r['span']:>16}  n={r['n']:<4}"
                      f" failed {r['fail']:>3}%   avg {r['avg_r']:>+6.3f}R")
        print("\n  A spread under about 15 points is noise at this sample size.")

    # Does trading the gap direction beat simply always going long?
    print("\n=== direction: follow the gap, or always go long? ===")
    for label, force in (("follow the gap (what was tested)", False),
                         ("always long (what the table shows)", True)):
        tr = []
        for s, f in frames.items():
            try:
                tr += setups(f, intraday.P["stop_atr_frac"], base_cost, force_long=force)
            except Exception:
                continue
        r = summarise(tr)
        if not r:
            continue
        c = r["exit at close"]
        ci = f"[{c['ci95'][0]:+.2f}, {c['ci95'][1]:+.2f}]" if c["ci95"] else ""
        print(f"  {label:<36} {c['avg_r']:>+7.3f}R  t={c['t'] or 0:>5.2f}  {ci}")

    # Is the close really the noisy part of the day?
    prof = noise_by_time(frames)
    if prof:
        print("\n=== how wild is each part of the day? ===")
        print("  average 5-minute range as a % of price, all names, all sessions")
        worst = sorted(prof.items(), key=lambda kv: -kv[1][0])[:5]
        print("  widest bars:  " + "   ".join(f"{t} {v:.3f}%" for t, (v, _) in worst))
        late = [t for t in prof if t >= "15:10"]
        mid = [t for t in prof if "11:00" <= t < "14:00"]
        if late and mid:
            lm = np.mean([prof[t][0] for t in late])
            mm = np.mean([prof[t][0] for t in mid])
            print(f"  last 20 min average {lm:.3f}%   midday average {mm:.3f}%"
                  f"   ratio {lm / mm:.2f}x" if mm else "")
        for t in ("09:15", "09:30", "10:00", "12:00", "14:00",
                  "15:00", "15:10", "15:15", "15:20", "15:25"):
            if t in prof:
                v, n = prof[t]
                print(f"    {t}  {v:6.3f}%  {'#' * int(v * 40):<28} n={n}")

    print("\nA t below about 2 means the sample cannot tell this apart from zero.")
    print("The two tables differ ONLY in the slippage assumption. If they disagree")
    print("about whether something is significant, the data has not settled it.")


if __name__ == "__main__":
    main()
