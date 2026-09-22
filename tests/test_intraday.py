"""Tests for the intraday selection screen and the cost model.

Synthetic sessions again, because the container cannot reach Yahoo and because
what matters is the awkward cases: a late open, a half day, one past news spike
that would poison a mean, a stock that moves too little to pay for itself.
"""
import sys
import numpy as np
import pandas as pd

sys.path.insert(0, "/home/claude/site")
import intraday as it
import trading_costs as tc

PASS = FAIL = 0


def check(label, got, want, tol=None):
    global PASS, FAIL
    ok = (abs(got - want) <= tol) if (tol is not None and got is not None) else (got == want)
    PASS, FAIL = PASS + ok, FAIL + (not ok)
    print(f"{'PASS' if ok else 'FAIL'}  {label:<56} {got!r}")
    assert ok, f"{label}: got {got!r}, wanted {want!r}"


def session(date, open_px, bars=75, open_vol=10000, bar_vol=5000,
            drift=0.0, first_bar_range=0.004, start="09:15"):
    """One NSE trading day of 5-minute bars."""
    idx = pd.date_range(f"{date} {start}", periods=bars, freq="5min", tz=it.IST)
    close = open_px * (1 + np.linspace(0, drift, bars))
    high = close * (1 + first_bar_range / 2)
    low = close * (1 - first_bar_range / 2)
    vol = np.full(bars, float(bar_vol))
    vol[0] = float(open_vol)
    o = close.copy(); o[0] = open_px
    return pd.DataFrame({"Open": o, "High": high, "Low": low, "Close": close,
                         "Volume": vol}, index=idx)


def book(days, **kw):
    """A run of sessions; `days` is a list of (date, open, overrides) tuples."""
    frames = []
    for date, px, over in days:
        frames.append(session(date, px, **{**kw, **over}))
    return pd.concat(frames)


DATES = [f"2026-09-{d:02d}" for d in (1, 2, 3, 4, 7, 8, 9, 10, 11, 15, 16, 17, 18, 21, 22)]

print("--- relative volume: today's open against its own history ---")
# fifteen quiet sessions, then today opens on 5x the usual volume and gaps 2%
quiet = [(d, 100.0, {}) for d in DATES[:-1]]
today = [(DATES[-1], 102.0, dict(open_vol=50000))]
st = it.opening_stats(book(quiet + today))
check("relative volume", st["RVol"], 5.0)
check("gap %", st["Gap_pct"], 2.0, tol=0.05)
check("session picked up", st["Session"], "2026-09-22")

print("\n--- the median is what makes it robust ---")
# one past session had a 20x news spike; a MEAN base would hide today's signal
spiky = [(d, 100.0, {}) for d in DATES[:-2]]
spiky += [(DATES[-2], 100.0, dict(open_vol=200000))]        # the news day
st = it.opening_stats(book(spiky + today))
check("one past spike does not flatten today", st["RVol"], 5.0)

print("\n--- gates that keep junk out ---")
st_late = it.opening_stats(book(quiet + [(DATES[-1], 102.0, dict(start="11:30"))]))
check("a session whose data starts at 11:30 is refused", st_late, None)

short_hist = it.opening_stats(book([(d, 100.0, {}) for d in DATES[:3]]))
check("three sessions is not enough history", short_hist, None)

check("no data at all", it.opening_stats(pd.DataFrame()), None)

print("\n--- ATR and the trade plan ---")
# a stock that travels: each session drifts 3%
mover = [(d, 100.0, dict(drift=0.03)) for d in DATES[:-1]]
st = it.opening_stats(book(mover + [(DATES[-1], 102.0, dict(open_vol=50000, drift=0.03))]))
plan = it.add_trade_plan(st)
check("ATR% is measured", st["ATR_pct"] > 1.0, True)
check("stop is 10% of ATR", round(plan["Stop_Dist"] / st["ATR"], 3), 0.1)
check("long trigger is the opening-range high", plan["Long_Trigger"], st["OR_High"])
check("break-even is attached to the row", plan["Breakeven_pct"] > 0, True)

print("\n--- the filters ---")
rows = [
  dict(Symbol="GOOD",  RVol=6.0, Gap_pct=2.5,  ATR_pct=3.0, Score=6.0),
  dict(Symbol="QUIET", RVol=1.2, Gap_pct=2.5,  ATR_pct=3.0, Score=1.2),   # normal volume
  dict(Symbol="FLAT",  RVol=6.0, Gap_pct=0.1,  ATR_pct=3.0, Score=6.0),   # no dislocation
  dict(Symbol="DULL",  RVol=6.0, Gap_pct=2.5,  ATR_pct=0.6, Score=6.0),   # cannot pay costs
  dict(Symbol="DOWN",  RVol=9.0, Gap_pct=-3.1, ATR_pct=4.0, Score=9.0),   # gaps DOWN
]
kept = it.in_play(rows)
check("only the genuinely-in-play names survive",
      sorted(kept["Symbol"]), ["DOWN", "GOOD"])
check("ranked by relative volume", kept["Symbol"].iloc[0], "DOWN")
check("nothing in play returns an empty frame", it.in_play([]).empty, True)

print("\n--- liquid universe ---")
live = pd.DataFrame([
  dict(Symbol="BIG",   Company="Big Ltd",   **{"Last Price":500.0, "Avg Volume 20d":2_000_000, "Bars":400}),
  dict(Symbol="THIN",  Company="Thin Ltd",  **{"Last Price":500.0, "Avg Volume 20d":2_000,     "Bars":400}),
  dict(Symbol="PENNY", Company="Penny Ltd", **{"Last Price":12.0,  "Avg Volume 20d":9_000_000, "Bars":400}),
  dict(Symbol="NEW",   Company="New Ltd",   **{"Last Price":500.0, "Avg Volume 20d":2_000_000, "Bars":20}),
])
uni = it.liquid_universe(live)
check("illiquid, penny and too-new names are dropped", uni["Symbol"].tolist(), ["BIG"])
check("turnover in crore", uni["Turnover_Cr"].iloc[0], 100.0)

print("\n--- costs: the number SEBI's study is really about ---")
c = tc.round_trip(500, 200, intraday=True)
check("intraday round trip on Rs 1L is under 0.25%", c.pct_of_position < 0.25, True)
check("and over 0.10% - it is never free", c.pct_of_position > 0.10, True)

d = tc.round_trip(500, 200, intraday=False)
check("delivery costs more per trade than intraday", d.total > c.total, True)

check("STT hits the sell side only on intraday",
      round(c.stt, 2), round(500 * 200 * tc.STT_INTRADAY_SELL, 2))
check("a small position pays proportionally more",
      tc.breakeven_pct(100, 20000) > tc.breakeven_pct(100, 200000), True)
check("zero and nonsense inputs do not explode", tc.breakeven_pct(0, 100000), None)

# the thing that actually decides whether intraday is viable
be = tc.breakeven_pct(500, 100000)
print(f"\n   A Rs 1,00,000 intraday position must move {be}% to break even.")
print(f"   Three trades a day is {be * 3:.2f}% of pure cost drag, every day.")
edge = tc.edge_after_costs(0.15, 500, 100000)
check("a 0.15% scalp is negative after costs", edge < 0, True)
print(f"   A 0.15% scalp nets {edge}% - the trade is a fee, not a trade.")

print(f"\n{PASS} passed, {FAIL} failed.")
assert FAIL == 0
