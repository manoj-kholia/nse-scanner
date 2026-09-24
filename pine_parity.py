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

TWO TESTS, not one.

  The LIVE verdict is a single answer per series: what the scanner would list
  today, and at what buy point, stop and target.

  The HISTORY walk is a LIST per series - every base the rules would have armed
  before today, and what became of each. A single bar out of place anywhere in
  that list shows up, which makes it the sharper of the two. It is run under
  the same limits the chart works under (newest 10 setups, 500-bar window, the
  live base excluded, 250 bars to resolve a trade), because otherwise the two
  would "disagree" purely because one was looking at more of the chart.

The run prints which history branches the corpus actually reached. A branch the
data never hits is untested no matter what the disagreement count says - the
overhead-supply gate once passed this harness without being exercised at all,
which is why the coverage is printed rather than assumed.

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

                # hi52All[bar_index - ri], i.e. ta.highest(high, overheadLook)
                # as of bar ri. na until the window is full, which is why the
                # guard is on ri + 1, not on max(0, ...).
                hi52 = (max(high[ri + 1 - p["overhead_look"]:ri + 1])
                        if p["overhead"] > 0 and ri + 1 >= p["overhead_look"] else None)
                if hi52 is not None and hi52 > 0 and rp < hi52 * (1 - p["overhead"]):
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
    tgt = buy * (1 + p["profit_take"])
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


def crafted_aged(seed, tail=90, wide_at=None, gap=1.6):
    """A base that broke out, then a long tail - so it becomes HISTORY.

    crafted_cup() ends its base near the last bar, which makes it the live
    setup; the history walk deliberately skips that one, so those series
    exercised the past-setup path not at all. Adding a tail ages the base out
    of the live window and puts the trade path under test.

    `wide_at` bars after the entry, one bar's range is stretched to span both
    the stop and the target. That branch encodes a judgement rather than a
    measurement - a daily bar cannot say which came first, and both sides count
    it as the stop - and a branch that encodes a choice and is never exercised
    is not tested.
    """
    base = crafted_cup(seed, cup_len=95, depth=0.22, handle_len=9, breakout=True)
    rng = np.random.default_rng(10_000 + seed)
    c = base["Close"].to_numpy(float)
    walk = c[-1] * np.cumprod(1 + rng.normal(0.0015, 0.018, tail))
    df = to_ohlcv(np.concatenate([c, walk]), rng)
    if wide_at is None:
        return df
    rows = pine_history_side(df)
    entered = [r for r in rows if r["bo"] is not None]
    if not entered:
        return None
    j = entered[-1]["bo"] + wide_at
    if j >= len(df):
        return None
    d = df.copy()
    px = float(d["Close"].iloc[j])
    d.iloc[j, d.columns.get_loc("High")] = px * gap
    d.iloc[j, d.columns.get_loc("Low")] = px * 0.80
    return d


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

    # Branches that encode a judgement rather than a measurement have to be
    # exercised or the "0 disagreements" is hollow. This is the same check that
    # caught the overhead gate passing untested.
    for seed in range(60):
        for tail in (60, 120):
            cases.append((f"aged-{seed}-{tail}", crafted_aged(seed, tail)))
    for seed in range(40):
        for at, gap in ((2, 1.6), (5, 1.35), (9, 2.2)):
            w = crafted_aged(seed, 120, wide_at=at, gap=gap)
            if w is not None:
                cases.append((f"wide-{seed}-{at}-{gap}", w))

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

    # --- the history walk ------------------------------------------------
    #  The live verdict is one answer per series. The history walk is a LIST
    #  per series, so a single bar out of place anywhere in it shows up - which
    #  makes this the sharper of the two tests.
    import collections
    hbad, setups, trades = [], 0, 0
    branches = collections.Counter()
    for name, df in cases:
        ha, hb = pine_history_side(df), python_history_side(df)
        setups += len(ha)
        trades += sum(1 for r in ha if r["bo"] is not None)
        for r in ha:
            branches[r["kind"]] += 1
        if len(ha) != len(hb):
            hbad.append((name, f"{len(ha)} setups vs {len(hb)}", ha[:3], hb[:3]))
            continue
        for x, y in zip(ha, hb):
            if (x["rim"] != y["rim"] or x["kind"] != y["kind"]
                    or x["bo"] != y["bo"] or x["exit"] != y["exit"]
                    or abs(x["buy"] - y["buy"]) > 0.011):
                hbad.append((name, "setup differs", x, y))
                break

    print(f"{len(cases)} series  |  {listed} produced a signal  |  "
          f"{reasoned} rejected for a comparable reason  |  "
          f"{len(bad)} disagreements")
    print(f"{'':>13}  |  {setups} past setup(s), {trades} of them entered  |  "
          f"{len(hbad)} history disagreements")
    for name, why, x, y in hbad[:10]:
        print(f"\n  HISTORY {name}: {why}")
        print(f"    pine   {x}")
        print(f"    python {y}")

    # "0 disagreements" means nothing about a branch the corpus never reaches.
    # The overhead gate once passed this harness without being exercised at
    # all, so the coverage is printed rather than assumed.
    ALL = ("expired", "broke down", "wedged up", "skipped",
           "target", "stopped", "both", "still open")
    print("\n  history branches exercised: "
          + ", ".join(f"{k} {branches[k]}" for k in ALL))
    never = [k for k in ALL if not branches[k]]
    thin = [k for k in ALL if 0 < branches[k] < 5]
    if never:
        print(f"  NOT EXERCISED: {', '.join(never)} - these are untested, "
              f"whatever the disagreement count says")
    if thin:
        print(f"  thin coverage: {', '.join(thin)}")
    for name, a, b in bad[:15]:
        print(f"\n  {name}")
        print(f"    pine   {a}")
        print(f"    python {b}")
    if not bad:
        print("\nThe chart and the dashboard cannot disagree on these rules.")
    return 1 if bad else 0



# ---------------------------------------------------------------------------
#  The Pine PAST-SETUPS block, transliterated
# ---------------------------------------------------------------------------
#  Same rules as above, but the history walk rather than the live verdict. It
#  is written the way the Pine is written - collect rims newest-first up to
#  pastMax, then replay them oldest-first so one open position can block the
#  next - and not the way you would write Python.
#
#  The chart's limits are part of the contract here, so history_scan.py is run
#  under the same ones (max_setups, hist_bars, exclude_live, fwd_max). Without
#  that the two would "disagree" purely because one of them was looking at more
#  of the chart than the other, which tests nothing.
# ---------------------------------------------------------------------------
PAST_MAX = 10
PAST_FWD = 250
HIST_BARS = 500


def pine_history_side(df, p=P, past_max=PAST_MAX, past_fwd=PAST_FWD,
                      hist_bars=HIST_BARS):
    high = df["High"].to_numpy(float)
    low = df["Low"].to_numpy(float)
    close = df["Close"].to_numpy(float)
    vol = df["Volume"].to_numpy(float)
    n = len(df)
    avg_vol = pd.Series(vol).rolling(p["vol_len"]).mean().to_numpy()
    sma200 = pd.Series(close).rolling(200).mean().to_numpy()

    # hand-rolled pivot detector, ties accepted, exactly as the Pine
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

    last_b = n - 1
    hist_f = max(0, last_b - hist_bars + 1)

    p_rim, p_left, p_buy, p_lo = [], [], [], []
    np_ = len(piv_bar)
    if np_ >= 2:
        for a in range(np_ - 1, -1, -1):
            if len(p_rim) >= past_max:
                break
            ri2 = piv_bar[a]
            if ri2 > last_b or ri2 < hist_f:
                continue
            if last_b - ri2 <= p["handle_max"]:
                continue
            if a < 1:
                continue
            rp2 = piv_price[a]
            for b in range(a - 1, -1, -1):
                li2 = piv_bar[b]
                if li2 < hist_f:
                    break
                len2 = ri2 - li2
                if len2 > p["cup_max"]:
                    break
                if len2 < p["cup_min"]:
                    continue
                lp2 = piv_price[b]
                if not (rp2 <= lp2 * (1 + p["rim_up"]) and rp2 >= lp2 * (1 - p["rim_down"])):
                    continue
                h52 = (max(high[ri2 + 1 - p["overhead_look"]:ri2 + 1])
                       if p["overhead"] > 0 and ri2 + 1 >= p["overhead_look"] else None)
                if h52 is not None and h52 > 0 and rp2 < h52 * (1 - p["overhead"]):
                    continue
                rim_h2 = max(lp2, rp2)
                lo2, lb2, hi_in2 = 1e12, ri2, 0.0
                for i in range(li2 + 1, ri2):
                    if low[i] < lo2:
                        lo2, lb2 = low[i], i
                    hi_in2 = max(hi_in2, high[i])
                if hi_in2 > rim_h2 * (1 + p["pierce"]):
                    continue
                dep2 = (rim_h2 - lo2) / rim_h2
                if dep2 < p["depth_min"] or dep2 > p["depth_max"]:
                    continue
                pp2 = (lb2 - li2) * 1.0 / len2
                if pp2 < 0.15 or pp2 > 0.85:
                    continue
                span2 = rim_h2 - lo2
                if span2 <= 0:
                    continue
                acc2 = 0.0
                for i in range(li2 + 1, ri2):
                    acc2 += (rim_h2 - close[i]) / span2
                if acc2 / (ri2 - li2 - 1) < p["round_min"]:
                    continue
                prior_ok = True
                if p["prior_pct"] > 0:
                    ps2 = max(0, li2 - p["prior_look"])
                    if ps2 >= li2:
                        prior_ok = False
                    else:
                        p_lo2 = min(low[ps2:li2])
                        if p_lo2 <= 0 or (lp2 - p_lo2) / p_lo2 < p["prior_pct"]:
                            prior_ok = False
                if not prior_ok:
                    continue
                p_rim.append(ri2)
                p_left.append(li2)
                p_buy.append(rp2)
                p_lo.append(lo2)
                break

    out = []
    open_till = -1
    for q in range(len(p_rim) - 1, -1, -1):
        ri3, li3, buy3, clo3 = p_rim[q], p_left[q], p_buy[q], p_lo[q]
        mid3 = clo3 + (buy3 - clo3) / 2
        len3 = ri3 - li3

        bo3, why3, h_lo3 = None, "expired", 1e12
        j_end = min(last_b, ri3 + p["handle_max"])
        for j in range(ri3 + 1, j_end + 1):
            h_lo3 = min(h_lo3, low[j])
            if h_lo3 < mid3 or (buy3 - h_lo3) / buy3 > p["handle_depth"]:
                why3 = "broke down"
                break
            if j - ri3 < max(p["handle_min"], p["piv"]):
                continue
            if len3 + (j - ri3) < p["base_min"]:
                continue
            c3 = close[j]
            if c3 <= buy3 or c3 > buy3 * (1 + p["max_ext"]):
                continue
            av3 = avg_vol[j]
            if not np.isnan(av3) and vol[j] < p["vol_mult"] * av3:
                continue
            s3 = sma200[j]
            if not np.isnan(s3) and c3 <= s3:
                continue
            m3 = j - (ri3 + 1)
            ok3 = True
            if m3 >= 3:
                xs = np.arange(m3, dtype=float)
                ys = close[ri3 + 1:j]
                sx3, sy3 = xs.sum(), ys.sum()
                sxy3 = float((xs * ys).sum())
                sxx3 = float((xs * xs).sum())
                syy3 = float((ys * ys).sum())
                sv3 = float(vol[ri3 + 1:j].sum())
                den3 = m3 * sxx3 - sx3 * sx3
                if den3 != 0 and buy3 != 0:
                    raw3 = (m3 * sxy3 - sx3 * sy3) / den3
                    slp3 = round(raw3 / buy3 * 100, 3)
                    cxx3 = sxx3 - sx3 * sx3 / m3
                    cxy3 = sxy3 - sx3 * sy3 / m3
                    cyy3 = syy3 - sy3 * sy3 / m3
                    sse3 = max(cyy3 - (cxy3 / cxx3) * cxy3, 0.0) if cxx3 != 0 else 0.0
                    tst3 = None
                    if m3 > 2 and cxx3 > 0 and sse3 > 0:
                        se3 = (sse3 / ((m3 - 2) * cxx3)) ** 0.5
                        tst3 = round((cxy3 / cxx3) / se3, 2) if se3 != 0 else None
                    elif m3 > 2 and sse3 == 0:
                        tst3 = 0.0
                    if slp3 > p["handle_slope"] and (tst3 is None or tst3 > p["handle_slope_t"]):
                        why3, ok3 = "wedged up", False
                ref3 = avg_vol[j - 1]
                if ok3 and not np.isnan(ref3) and ref3 > 0:
                    if round((sv3 / m3) / ref3, 2) > p["handle_vol"]:
                        ok3 = False
            if not ok3:
                if why3 == "wedged up":
                    break
                continue
            bo3, why3 = j, "breakout"
            break

        if bo3 is None:
            out.append(dict(rim=ri3, buy=round(buy3, 2), kind=why3, bo=None, exit=None))
            continue
        if bo3 <= open_till:
            out.append(dict(rim=ri3, buy=round(buy3, 2), kind="skipped", bo=None, exit=None))
            continue

        ent3 = close[bo3]
        h_l3 = low[ri3 + 1]
        if bo3 > ri3 + 2:
            h_l3 = min(h_l3, min(low[ri3 + 2:bo3]))
        stp3 = max(h_l3, ent3 * (1 - p["max_loss"]))
        tgt3 = buy3 * (1 + p["profit_take"])
        res3, out3 = None, "still open"
        k_end = min(last_b, bo3 + past_fwd)
        for k in range(bo3 + 1, k_end + 1):
            hit_s = low[k] <= stp3
            hit_t = high[k] >= tgt3
            if hit_s and hit_t:
                res3, out3 = k, "both"
                break
            if hit_s:
                res3, out3 = k, "stopped"
                break
            if hit_t:
                res3, out3 = k, "target"
                break
        out.append(dict(rim=ri3, buy=round(buy3, 2), kind=out3, bo=bo3, exit=res3))
        open_till = last_b if res3 is None else res3

    return out


def python_history_side(df, p=P):
    """history_scan.py, under the same limits the chart works under."""
    import history_scan as hs
    rows = hs.scan_history(df, p, one_at_a_time=True, max_setups=PAST_MAX,
                           hist_bars=HIST_BARS, exclude_live=True,
                           fwd_max=PAST_FWD)
    kind = {"EXPIRED": "expired", "BROKE DOWN": "broke down",
            "WEDGED UP": "wedged up", "SKIPPED": "skipped",
            "OPEN": "still open", "TARGET": "target", "STOPPED": "stopped",
            "AMBIGUOUS": "both", "STILL FORMING": "expired"}
    return [dict(rim=r["rim_bar"], buy=r["buy"],
                 kind=kind.get(r["outcome"], r["outcome"]),
                 bo=r["entry_bar"], exit=r["exit_bar"]) for r in rows]

if __name__ == "__main__":
    raise SystemExit(main())
