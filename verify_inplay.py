#!/usr/bin/env python3
"""
verify_inplay.py
----------------
Checks the published In Play table against the market, independently.

This does NOT ask intraday.py whether intraday.py was right. It re-downloads
today's 5-minute bars and recomputes the gap, the opening range, the relative
volume and the ATR from scratch, then diffs those against what was published
in stocks_in_play.csv. A disagreement means one of the two is wrong and you
get told which field.

It then walks the rest of the session and reports what actually happened to
each published level: whether the trigger fired and when, how far the trade
ran before it came back (the MFE), whether the stop was hit, and where it
closed. That is the difference between "the screen computed a number" and
"the number was any use".

    python3 verify_inplay.py                        # today's published rows
    python3 verify_inplay.py --explain YESBANK      # why is X not listed?
    python3 verify_inplay.py --touched MANINDS:898.20
                                                    # did that price trade?

The --touched form exists because "the price never reached there" is a
checkable claim and should be settled by the tape, not by argument.
"""

import argparse
import statistics
from datetime import datetime

import pandas as pd

import intraday

TOL = {                 # how far a recomputed value may drift before it is a bug
    "Gap_pct": 0.05,
    "OR_High": 0.05,
    "OR_Low": 0.05,
    "RVol": 0.15,
    "ATR_pct": 0.10,
    "Prev_Close": 0.05,
    "Open": 0.05,
}


def recompute(df):
    """Everything the published row claims, derived again from the bars."""
    sess = intraday._sessions(df)
    if len(sess) < 10:
        return None, "not enough sessions"
    date, cur = sess[-1]
    prior = [s for _, s in sess[:-1]][-intraday.P["lookback"]:]

    o_bar = intraday._window(cur, intraday.OR_START, intraday.OR_END)
    v_bar = intraday._window(cur, intraday.VOL_START, intraday.VOL_END)
    if o_bar is None or o_bar.empty or v_bar is None or v_bar.empty:
        return None, "opening window missing"

    base = [float(intraday._window(p, intraday.VOL_START,
                                   intraday.VOL_END)["Volume"].sum())
            for p in prior]
    base = [b for b in base if b > 0]
    if len(base) < 8:
        return None, "no usable volume history"

    prev_close = float(prior[-1]["Close"].iloc[-1])
    day_open = float(o_bar["Open"].iloc[0])
    atr = intraday._daily_atr([(None, p) for p in prior])

    return dict(
        Session=date,
        Prev_Close=round(prev_close, 2),
        Open=round(day_open, 2),
        Gap_pct=round((day_open / prev_close - 1) * 100, 2),
        OR_High=round(float(o_bar["High"].max()), 2),
        OR_Low=round(float(o_bar["Low"].min()), 2),
        RVol=round(float(v_bar["Volume"].sum()) / statistics.median(base), 2),
        ATR_pct=round(atr / day_open * 100, 2) if atr else None,
        Day_High=round(float(cur["High"].max()), 2),
        Day_Low=round(float(cur["Low"].min()), 2),
        Close=round(float(cur["Close"].iloc[-1]), 2),
        bars=len(cur),
    ), None


def outcome(df, row):
    """What the published long plan actually did, bar by bar, after 09:25."""
    sess = intraday._sessions(df)
    cur = sess[-1][1]
    after = cur[cur.index.strftime("%H:%M") >= intraday.VOL_START]
    if after.empty:
        return None

    trig, stop = float(row["Long_Trigger"]), float(row["Long_Stop"])
    sd = trig - stop
    if sd <= 0:
        return dict(note="stop is not below the trigger - bad row")

    hi = after["High"].to_numpy(float)
    lo = after["Low"].to_numpy(float)
    op = after["Open"].to_numpy(float)

    for k in range(len(after)):
        if hi[k] >= trig:
            entry = max(trig, op[k])
            mfe = 0.0
            for j in range(k, len(after)):
                if (entry - lo[j]) / sd >= 1:
                    # A stop-out inside the SAME bar that triggered the entry
                    # cannot be resolved from 5-minute bars: that bar's low may
                    # have printed before the trigger was ever touched. Say so
                    # rather than reporting a loss that may not have happened.
                    return dict(fired=after.index[k].strftime("%H:%M"),
                                entry=round(entry, 2), stopped=True,
                                same_bar=(j == k),
                                mfe_R=round(mfe, 2),
                                stopped_at=after.index[j].strftime("%H:%M"))
                mfe = max(mfe, (hi[j] - entry) / sd)
            return dict(fired=after.index[k].strftime("%H:%M"),
                        entry=round(entry, 2), stopped=False, same_bar=False,
                        mfe_R=round(mfe, 2),
                        close_R=round((float(after["Close"].iloc[-1]) - entry) / sd, 2))
    return dict(fired=None)


def fetch(symbols, batch=10, pause=1.0):
    dl = intraday.default_downloader(period="60d", interval="5m")
    out = {}
    for i in range(0, len(symbols), batch):
        chunk = symbols[i:i + batch]
        try:
            raw = dl([s + ".NS" for s in chunk])
        except Exception as exc:
            print(f"  batch failed: {type(exc).__name__}")
            continue
        for s in chunk:
            f = intraday._pick(raw, s + ".NS")
            if intraday._usable(f):
                out[s] = f
    return out


def main():
    ap = argparse.ArgumentParser(description="Check the In Play table against the tape")
    ap.add_argument("--published", default="stocks_in_play.csv")
    ap.add_argument("--explain", default="", help="comma-separated symbols to diagnose")
    ap.add_argument("--touched", default="",
                    help="SYMBOL:PRICE[,SYMBOL:PRICE] - did that price trade today?")
    args = ap.parse_args()

    print(f"{datetime.now():%Y-%m-%d %H:%M}  verifying against freshly pulled 5m bars\n")

    pub = pd.read_csv(args.published)
    syms = pub["Symbol"].tolist()
    extra = [s.strip().upper() for s in args.explain.split(",") if s.strip()]
    touch = []
    for spec in args.touched.split(","):
        if ":" in spec:
            s, p = spec.split(":", 1)
            touch.append((s.strip().upper(), float(p)))
    want = list(dict.fromkeys(syms + extra + [s for s, _ in touch]))

    frames = fetch(want)
    print(f"got bars for {len(frames)}/{len(want)} symbols\n")

    # --- 1. does the published row match the market? -----------------------
    print("=" * 78)
    print("PUBLISHED vs RECOMPUTED")
    print("=" * 78)
    bad = 0
    for _, r in pub.iterrows():
        s = r["Symbol"]
        if s not in frames:
            print(f"{s:<12} no data returned - cannot verify")
            continue
        got, err = recompute(frames[s])
        if got is None:
            print(f"{s:<12} {err}")
            continue
        diffs = []
        for f, tol in TOL.items():
            if f not in r or got.get(f) is None:
                continue
            d = abs(float(r[f]) - got[f])
            if d > tol:
                diffs.append(f"{f}: published {r[f]} vs actual {got[f]}")
        if diffs:
            bad += 1
            print(f"{s:<12} MISMATCH")
            for d in diffs:
                print(f"             {d}")
        else:
            print(f"{s:<12} ok   day {got['Day_Low']}-{got['Day_High']} "
                  f"close {got['Close']}")
    print(f"\n{bad} row(s) disagreed with the market.\n")

    # --- 2. what did the published plan actually do? -----------------------
    print("=" * 78)
    print("WHAT THE PUBLISHED LONG PLAN DID (trigger -> stop or close)")
    print("=" * 78)
    print(f"{'symbol':<12} {'trigger':>9} {'stop':>9} {'fired':>7} "
          f"{'entry':>9} {'MFE(R)':>7} {'result':>16}")
    print("-" * 78)
    fired = stopped = ambiguous = 0
    mfes = []
    for _, r in pub.iterrows():
        s = r["Symbol"]
        if s not in frames:
            continue
        o = outcome(frames[s], r)
        if not o or o.get("note"):
            print(f"{s:<12} {o.get('note','no data') if o else 'no data'}")
            continue
        if not o.get("fired"):
            print(f"{s:<12} {r['Long_Trigger']:>9} {r['Long_Stop']:>9} "
                  f"{'no':>7} {'':>9} {'':>7} {'never triggered':>16}")
            continue
        fired += 1
        mfes.append(o["mfe_R"])
        if o["stopped"]:
            res = f"stopped {o['stopped_at']}" + (" ?" if o.get("same_bar") else "")
            ambiguous += bool(o.get("same_bar"))
        else:
            res = f"close {o['close_R']:+.2f}R"
        stopped += bool(o["stopped"])
        print(f"{s:<12} {r['Long_Trigger']:>9} {r['Long_Stop']:>9} "
              f"{o['fired']:>7} {o['entry']:>9} {o['mfe_R']:>7.2f} {res:>16}")
    if mfes:
        amb = f" ({ambiguous} marked ? - stopped inside the entry bar, unresolvable)" if ambiguous else ""
        print(f"\n{fired} triggered, {stopped} stopped out{amb}. "
              f"Best run reached {max(mfes):.2f}R; "
              f"median {statistics.median(mfes):.2f}R.")
        for k in (1, 2, 3):
            print(f"  reached {k}R: {sum(m >= k for m in mfes)}/{len(mfes)}")

    # --- 3. why is a symbol missing? ---------------------------------------
    for s in extra:
        print("\n" + "=" * 78)
        print(f"WHY IS {s} NOT LISTED?")
        print("=" * 78)
        if s not in frames:
            print("  No intraday data came back for it at all.")
            continue
        why = {}
        st = intraday.opening_stats(frames[s], why=why)
        if st is None:
            print(f"  Rejected before scoring: {why.get('reason', 'unknown')}")
            continue
        print(f"  It passed the data checks. Its numbers today:")
        for k in ("Session", "RVol", "Gap_pct", "ATR_pct", "Open", "OR_High", "OR_Low"):
            print(f"    {k:<10} {st.get(k)}")
        gates = [
            ("relative volume", st["RVol"], ">=", intraday.P["min_rvol"]),
            ("abs(gap) %", abs(st["Gap_pct"]), ">=", intraday.P["min_gap"]),
            ("ATR %", st["ATR_pct"], ">=", intraday.P["min_atr"]),
        ]
        for name, val, _, need in gates:
            ok = val >= need
            print(f"    {'PASS' if ok else 'FAIL'}  {name} = {val} "
                  f"(needs >= {need})")

    # --- 4. did a given price actually trade? ------------------------------
    for s, price in touch:
        print("\n" + "=" * 78)
        print(f"DID {s} TRADE AT {price}?")
        print("=" * 78)
        if s not in frames:
            print("  No data.")
            continue
        cur = intraday._sessions(frames[s])[-1][1]
        lo, hi = float(cur["Low"].min()), float(cur["High"].max())
        print(f"  Session range {lo} - {hi}")
        if not (lo <= price <= hi):
            print(f"  NO - {price} is outside the day's range.")
            continue
        hits = cur[(cur["Low"] <= price) & (cur["High"] >= price)]
        print(f"  YES - {len(hits)} five-minute bar(s) span that price. First at "
              f"{hits.index[0]:%H:%M}, last at {hits.index[-1]:%H:%M}.")


if __name__ == "__main__":
    main()
