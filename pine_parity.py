#!/usr/bin/env python3
"""
pine_parity.py
--------------
Does cup_and_handle_v3.pine agree with find_patterns.py?

Two implementations of the same rules will drift apart. They already did: the
chart said one thing and the dashboard said another, and there was no way to
tell which was wrong without reading both. This is the test that keeps them
honest.

`pine_side()` below is a LITERAL transliteration of the Pine file - the same
loops, the same break/continue placement, the same absolute bar indices, the
same order of gates. It is deliberately not written the way you would write
Python. If the Pine has a bug, this has the same bug, and the comparison
against find_patterns.py catches it.

Data is generated, not downloaded, and that is the point: random series hit the
edge cases a single real symbol never will - no cup at all, a handle that aged
out by one day, a V that scores 0.299, a handle that wedges up by 0.001%/day.

    python3 pine_parity.py
    python3 pine_parity.py --n 2000
"""

import argparse
import numpy as np
import pandas as pd

import find_patterns as fp

P = fp.P


# ---------------------------------------------------------------------------
#  The Pine side, transliterated
# ---------------------------------------------------------------------------
def pine_side(df, p=P):
    """cup_and_handle_v3.pine, line for line."""
    high = df["High"].to_numpy(float)
    low = df["Low"].to_numpy(float)
    close = df["Close"].to_numpy(float)
    vol = df["Volume"].to_numpy(float)
    n = len(df)
    last_bar = n - 1

    avg_vol = pd.Series(vol).rolling(p["vol_len"]).mean().to_numpy()
    sma200 = pd.Series(close).rolling(200).mean().to_numpy()

    # the hand-rolled pivot detector, highest-or-equal
    piv_bar, piv_price = [], []
    for i in range(p["piv"], n - p["piv"]):
        ok = True
        for k in range(1, p["piv"] + 1):
            if high[i - k] > high[i] or high[i + k] > high[i]:
                ok = False
                break
        if ok:
            piv_bar.append(i)
            piv_price.append(high[i])

    cupL = cupR = lo_bar = None
    buy = cup_lo = depth = cup_len = None
    found = False

    np_ = len(piv_bar)
    if np_ >= 2:
        for a in range(np_ - 1, -1, -1):
            ri = piv_bar[a]
            if ri > last_bar:
                continue
            if last_bar - ri > p["handle_max"]:
                break
            rp = piv_price[a]
            for b in range(a - 1, -1, -1):
                li = piv_bar[b]
                length = ri - li
                if length > p["cup_max"]:
                    break
                if length < p["cup_min"]:
                    continue
                lp = piv_price[b]
                if not (rp <= lp * (1 + p["rim_up"]) and rp >= lp * (1 - p["rim_down"])):
                    continue

                rim_hi = max(lp, rp)
                lo, lb_, hi_in = 1e12, ri, 0.0
                for i in range(li + 1, ri):
                    if low[i] < lo:
                        lo, lb_ = low[i], i
                    hi_in = max(hi_in, high[i])

                if hi_in > rim_hi * (1 + p["pierce"]):
                    continue
                dep = (rim_hi - lo) / rim_hi
                if dep < p["depth_min"] or dep > p["depth_max"]:
                    continue
                pp = (lb_ - li) / length
                if pp < 0.15 or pp > 0.85:
                    continue

                span = rim_hi - lo
                if span <= 0:
                    continue
                acc = 0.0
                for i in range(li + 1, ri):
                    acc += (rim_hi - close[i]) / span
                shape = acc / (ri - li - 1)
                if shape < p["round_min"]:
                    continue

                if p["prior_pct"] > 0:
                    ps = max(0, li - p["prior_look"])
                    if ps >= li:
                        continue
                    p_lo = min(low[ps:li])
                    if p_lo <= 0 or (lp - p_lo) / p_lo < p["prior_pct"]:
                        continue

                cupL, cupR, buy, cup_lo = li, ri, rp, lo
                depth, cup_len, lo_bar = dep, length, lb_
                found = True
                break
            if found:
                break

    if not found:
        return dict(stage="NONE", why="no cup")

    h_days = last_bar - cupR
    h_low = min(low[cupR + 1:last_bar + 1]) if last_bar > cupR else float("nan")
    h_dep = (buy - h_low) / buy
    tgt = buy + (buy - cup_lo)
    px = close[last_bar]

    bo_bar = None
    for i in range(cupR + 1, last_bar + 1):
        if i - cupR < p["handle_min"]:
            continue
        c = close[i]
        if c <= buy or c > buy * (1 + p["max_ext"]):
            continue
        if not np.isnan(avg_vol[i]) and vol[i] < p["vol_mult"] * avg_vol[i]:
            continue
        if not np.isnan(sma200[i]) and c <= sma200[i]:
            continue
        bo_bar = i
        break

    h_end = last_bar + 1 if bo_bar is None else bo_bar
    slope = dry = tstat = None
    m = h_end - (cupR + 1)
    if m >= 3:
        sx = sy = sxy = sxx = syy = sv = 0.0
        for k in range(m):
            yv = close[cupR + 1 + k]
            sx += k
            sy += yv
            sxy += k * yv
            sxx += k * k
            syy += yv * yv
            sv += vol[cupR + 1 + k]
        den = m * sxx - sx * sx
        if den != 0:
            raw = (m * sxy - sx * sy) / den
            # handle_quality() returns round(slope, 3) and round(dry, 2), and
            # evaluate() compares those ROUNDED values against the thresholds.
            # So a handle drifting up 0.0004%/day rounds to 0.0 and passes. It
            # is an accident of the Python - rounding meant for display leaking
            # into a gate - but the dashboard has been publishing that
            # behaviour, so the chart copies it rather than quietly disagreeing
            # about a stock on the boundary.
            slope = round(raw / buy * 100, 3)
            # ...and the rise has to beat its own scatter before it counts as a
            # wedge. Sxx/Sxy/Syy here are the centred sums the t-stat needs.
            cxx = sxx - sx * sx / m
            cxy = sxy - sx * sy / m
            cyy = syy - sy * sy / m
            sse = max(cyy - (cxy / cxx) * cxy, 0.0) if cxx else 0.0
            if m > 2 and cxx > 0 and sse > 0:
                se = (sse / ((m - 2) * cxx)) ** 0.5
                tstat = round((cxy / cxx) / se, 2) if se else None
            elif m > 2 and sse == 0:
                tstat = 0.0
        ref = avg_vol[h_end - 1]
        if not np.isnan(ref) and ref > 0:
            dry = round((sv / m) / ref, 2)

    out = dict(buy=round(buy, 2), target=round(tgt, 2))

    if cup_len + h_days < p["base_min"]:
        return dict(stage="NONE", why="base under 7 weeks")
    if (slope is not None and slope > p["handle_slope"]
            and (tstat is None or tstat > p["handle_slope_t"])):
        return dict(stage="NONE", why="handle wedges upward")
    if dry is not None and dry > p["handle_vol"]:
        return dict(stage="NONE", why="no volume dry-up in handle")

    if bo_bar is not None:
        since = last_bar - bo_bar
        if since > 5:
            return dict(stage="NONE", why="breakout too old")
        if px < buy:
            return dict(stage="NONE", why="breakout failed - back below buy point")
        if px > buy * (1 + p["max_ext"]):
            return dict(stage="NONE", why="extended past the buy point")
        out.update(stage="BREAKOUT",
                   stop=round(max(h_low, close[bo_bar] * (1 - p["max_loss"])), 2))
        return out

    mid = cup_lo + (buy - cup_lo) / 2
    if h_days > p["handle_max"] or h_low < mid or h_dep > p["handle_depth"]:
        return dict(stage="NONE", why="handle broke down")
    if px > buy * (1 + p["max_ext"]):
        return dict(stage="NONE", why="extended past the buy point")
    out.update(stage="HANDLE FORMING",
               stop=round(max(h_low, buy * (1 - p["max_loss"])), 2))
    return out


# ---------------------------------------------------------------------------
#  The Python side, as the dashboard runs it
# ---------------------------------------------------------------------------
def python_side(df, p=P):
    cup = fp.find_cup(df, p)
    if not cup:
        return dict(stage="NONE", why="no cup")
    res, reason = fp.evaluate(df, cup, p)
    if not res:
        return dict(stage="NONE", why=reason)
    return dict(stage=res["Stage"], buy=res["Buy_Point"],
                stop=res["Stop"], target=res["Target"])


# ---------------------------------------------------------------------------
#  Series to test against
# ---------------------------------------------------------------------------
def to_ohlcv(close, rng, vol=None):
    close = np.maximum(close, 1.0)
    spread = close * rng.uniform(0.004, 0.02, len(close))
    high = close + spread * rng.uniform(0.2, 1.0, len(close))
    low = close - spread * rng.uniform(0.2, 1.0, len(close))
    if vol is None:
        vol = rng.lognormal(12, 0.4, len(close))
    return pd.DataFrame(dict(High=high, Low=low, Close=close, Volume=vol))


def random_series(seed, n=520):
    rng = np.random.default_rng(seed)
    steps = rng.normal(0.0006, 0.02, n)
    return to_ohlcv(100 * np.exp(np.cumsum(steps)), rng)


def crafted_cup(seed, cup_len=100, depth=0.22, handle_len=9, breakout=True):
    """A textbook base, so at least some runs exercise the listing branches."""
    rng = np.random.default_rng(seed)
    pre = 120
    adv = np.linspace(60, 100, pre)                       # the prior advance
    t = np.linspace(0, np.pi, cup_len)
    cup = 100 - 100 * depth * np.sin(t) ** 1.6            # rounded U
    handle = np.linspace(100, 100 * (1 - 0.05), handle_len)
    tail = np.array([104.0, 106.0, 107.0]) if breakout else np.array([99.0, 98.5, 99.2])
    close = np.concatenate([adv, cup, handle, tail])
    close = close * (1 + rng.normal(0, 0.002, len(close)))
    v = rng.lognormal(12, 0.25, len(close))
    v[pre + cup_len: pre + cup_len + handle_len] *= 0.45  # dry-up in the handle
    if breakout:
        v[-3:] *= 3.0
    return to_ohlcv(close, rng, v)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=600)
    args = ap.parse_args()

    cases = []
    for s in range(args.n):
        cases.append((f"random-{s}", random_series(s)))

    # The crafted sweep matters more than the random one: it walks the cup
    # length, depth and handle length across and past every threshold, so the
    # listing branches - the code v2 got wrong - are the ones under test.
    # Trailing bars are trimmed to age a breakout out of the 5-session window
    # one day at a time.
    s = 0
    for cup_len in (55, 75, 95, 115, 145, 195, 245, 265):
        for depth in (0.10, 0.125, 0.18, 0.24, 0.30, 0.34, 0.36, 0.42):
            for handle_len in (3, 4, 5, 8, 14, 30, 34, 36, 40):
                for bo in (True, False):
                    df = crafted_cup(s, cup_len=cup_len, depth=depth,
                                     handle_len=handle_len, breakout=bo)
                    cases.append((f"cup-{cup_len}-{depth}-{handle_len}-{bo}", df))
                    s += 1
    # ...and the same bases seen a few days later, which is what ages a
    # breakout out of the window and what pushes price past the 5% extension.
    extra = []
    for name, df in cases[args.n:]:
        for grow in (2, 4, 6):
            rng = np.random.default_rng(len(extra))
            nxt = df["Close"].iloc[-1] * (1 + rng.normal(0.004, 0.012, grow))
            more = to_ohlcv(np.concatenate([df["Close"].to_numpy(), nxt]), rng)
            extra.append((f"{name}+{grow}", more))
    cases += extra

    # Reasons worth comparing: the ones both sides phrase the same way. A cup
    # that was never found has no shared vocabulary, so it is excluded.
    SHARED = ("base under 7 weeks", "handle wedges upward",
              "no volume dry-up in handle", "breakout too old",
              "breakout failed - back below buy point",
              "extended past the buy point", "handle broke down")

    bad, listed, reasoned = [], 0, 0
    for name, df in cases:
        a, b = pine_side(df), python_side(df)
        if a["stage"] != "NONE":
            listed += 1
        same = a["stage"] == b["stage"]
        if same and a["stage"] != "NONE":
            for k in ("buy", "stop", "target"):
                if abs(a.get(k, 0) - b.get(k, 0)) > 0.011:
                    same = False
        if same and a["stage"] == "NONE":
            ra, rb = a.get("why", ""), b.get("why", "")
            if ra in SHARED or rb in SHARED:
                reasoned += 1
                if ra != rb:
                    same = False
        if not same:
            bad.append((name, a, b))

    print(f"{len(cases)} series  |  {listed} produced a signal  |  "
          f"{reasoned} rejected for a comparable reason  |  "
          f"{len(bad)} disagreements")
    for name, a, b in bad[:15]:
        print(f"\n  {name}")
        print(f"    pine   {a}")
        print(f"    python {b}")
    if not bad:
        print("\nThe chart and the dashboard cannot disagree on these rules.")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
