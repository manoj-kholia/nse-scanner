"""Tests for the earnings screen (C, A) and base-stage counting.

Both are built from fixtures rather than live data, because the whole point of
these two features is what they do when the data is awkward: missing rows,
loss-making base periods, uneven quarter spacing, stocks that have already run.
"""
import sys
import numpy as np
import pandas as pd

sys.path.insert(0, "/home/claude/site")
import fundamentals as fu
import find_patterns as fp

PASS = FAIL = 0


def check(label, got, want):
    global PASS, FAIL
    ok = got == want
    PASS, FAIL = PASS + ok, FAIL + (not ok)
    print(f"{'PASS' if ok else 'FAIL'}  {label:<54} {got!r}")
    assert ok, f"{label}: got {got!r}, wanted {want!r}"


def quarters(n=8, end="2026-06-30"):
    """Statement columns, newest first, the way Yahoo hands them over."""
    return pd.DatetimeIndex([pd.Timestamp(end) - pd.DateOffset(months=3 * i)
                             for i in range(n)])


def years(n=4, end="2026-03-31"):
    return pd.DatetimeIndex([pd.Timestamp(end) - pd.DateOffset(years=i)
                             for i in range(n)])


def q_stmt(eps, sales, idx=None):
    idx = quarters(len(eps)) if idx is None else idx
    return pd.DataFrame({"Diluted EPS": eps, "Total Revenue": sales}, index=idx).T


def a_stmt(eps, profit=None, idx=None):
    idx = years(len(eps)) if idx is None else idx
    d = {"Diluted EPS": eps}
    if profit is not None:
        d["Net Income"] = profit
    return pd.DataFrame(d, index=idx).T


def bal(equity, idx=None):
    idx = years(len(equity)) if idx is None else idx
    return pd.DataFrame({"Stockholders Equity": equity}, index=idx).T


# ---------------------------------------------------------------- C and A ---
print("--- C: the current quarter against the same quarter a year ago ---")

# newest first: 14.0 now vs 10.0 four quarters back = +40%
m = fu.metrics(q_stmt([14, 12, 11, 11, 10, 9, 9, 8], [280, 250, 240, 235, 220, 210, 205, 200]),
               a_stmt([40, 30, 24, 20], profit=[4000, 3000, 2400, 2000]),
               bal([16000, 14000, 12000, 10000]))
check("EPS +40% on the quarter", m["EPS_Q_Growth"], 40.0)
check("sales +27% on the quarter", m["Sales_Q_Growth"], 27.3)
check("annual EPS growth, annualised over 3 years", m["EPS_A_Growth"], 26.0)
check("ROE 25%", m["ROE"], 25.0)
check("grade", m["Earnings_Grade"], "PASS")

print("\n--- a company that looks fine on the chart and is shrinking ---")
m = fu.metrics(q_stmt([8, 9, 10, 10, 10, 10, 9, 9], [190, 200, 205, 205, 200, 198, 195, 190]),
               a_stmt([18, 20, 21, 20], profit=[1800, 2000, 2100, 2000]),
               bal([20000, 19000, 18000, 17000]))
check("EPS -20% on the quarter", m["EPS_Q_Growth"], -20.0)
check("grade", m["Earnings_Grade"], "FAIL")
check("says which test failed", "quarterly EPS" in m["Earnings_Note"], True)

print("\n--- the awkward cases the free data actually throws at you ---")
m = fu.metrics(q_stmt([5, 3, 1, -1, -4, -5, -6, -6], [150, 140, 130, 120, 110, 105, 100, 95]),
               None, None)
check("loss a year ago, profit now -> no fake percentage", m["EPS_Q_Growth"], None)
check("  ...but it is reported as a turnaround",
      "loss to profit" in m["Earnings_Note"], True)

m = fu.metrics(q_stmt([-2, -3, -3, -4, -5, -5, -6, -6], [90, 92, 95, 98, 100, 101, 103, 105]))
check("losing money in both periods -> no EPS growth", m["EPS_Q_Growth"], None)
check("  ...and sales are still measured", m["Sales_Q_Growth"], -10.0)

m = fu.metrics(None, None, None)
check("nothing at all", m["Earnings_Grade"], "NO DATA")
check("  ...never silently treated as a pass", m["EPS_Q_Growth"], None)

m = fu.metrics(q_stmt([14, 12, 11, 11, 10, 9, 9, 8], [280, 250, 240, 235, 220, 210, 205, 200]))
check("quarter only, no annuals -> THIN, not PASS", m["Earnings_Grade"], "THIN")
check("  ...and it says so", "check the rest yourself" in m["Earnings_Note"], True)

# a gap in the quarterly columns: 4 back is really 2 years, not 1
odd = pd.DatetimeIndex(["2026-06-30", "2026-03-31", "2025-12-31",
                        "2025-09-30", "2024-06-30", "2024-03-31"])
m = fu.metrics(q_stmt([14, 12, 11, 11, 10, 9], [280, 250, 240, 235, 220, 210], idx=odd))
check("quarters unevenly spaced -> refuses to compare", m["EPS_Q_Growth"], None)

# Yahoo sometimes gives Basic EPS and Operating Revenue instead
alt = pd.DataFrame({"Basic EPS": [14, 12, 11, 11, 10],
                    "Operating Revenue": [280, 250, 240, 235, 220]},
                   index=quarters(5)).T
m = fu.metrics(alt)
check("falls back to Basic EPS / Operating Revenue", m["EPS_Q_Growth"], 40.0)

print("\n--- fetch() survives a broken lookup ---")
def broken(sym):
    raise ConnectionError("yahoo said no")
df = fu.fetch(["AAA", "BBB"], downloader=broken, pause=0, log=None)
check("two rows back, no exception", len(df), 2)
check("grade", df["Earnings_Grade"].tolist(), ["NO DATA", "NO DATA"])
check("reason recorded", "lookup failed" in df["Earnings_Note"].iloc[0], True)


# ------------------------------------------------------------ base stage ---
print("\n--- base stage: how late in the move is this? ---")


def frame(closes):
    c = np.asarray(closes, float)
    return pd.DataFrame({"Open": c, "Close": c, "High": c * 1.005, "Low": c * 0.995,
                         "Volume": np.full(len(c), 1e5)},
                        index=pd.date_range("2022-01-03", periods=len(c), freq="B"))


def leg(a, b, n):
    return list(np.linspace(a, b, n))


def base(top, depth=0.18, n=40):
    """A pullback and recovery that does NOT exceed the old high."""
    lo = top * (1 - depth)
    return leg(top, lo, n // 2) + leg(lo, top * 0.995, n - n // 2)


# a stock that fell 40%, bottomed, then built exactly one base
crash = leg(200, 118, 120)                      # -41%, resets the count
first = leg(118, 150, 60) + base(150) + leg(150, 175, 30)
check("one base since the correction", fp.base_stage(frame(crash + first))[0], 2)

# the same stock, caught while that first base is still forming
check("first base, still forming",
      fp.base_stage(frame(crash + leg(118, 150, 60) + base(150, n=30)[:20]))[0], 1)

# three completed bases stacked on top of each other = a late-stage move
late = (leg(118, 150, 60) + base(150) + leg(150, 175, 25) + base(175)
        + leg(175, 205, 25) + base(205) + leg(205, 240, 25) + base(240, n=30)[:20])
st, forming = fp.base_stage(frame(crash + late))
check("three bases done, building the fourth", st, 4)
check("  ...and it knows it is in one now", forming, True)

# a shallow 6% dip is not a base
check("a 6% dip does not count as a base",
      fp.base_stage(frame(crash + leg(118, 150, 60) + base(150, depth=0.06)
                          + leg(150, 175, 30)))[0], 1)

# a two-week dip is not a base either
check("a 10-day pullback is too short to be a base",
      fp.base_stage(frame(crash + leg(118, 150, 60) + base(150, n=10)
                          + leg(150, 175, 30)))[0], 1)

# a fresh 35% correction wipes the count
after = crash + late + leg(240, 150, 70) + leg(150, 185, 45) + base(185, n=30)[:20]
check("a new 37% decline resets the count", fp.base_stage(frame(after))[0], 1)

check("too little history to judge", fp.base_stage(frame(leg(100, 120, 20)))[0], None)

print(f"\n{PASS} passed, {FAIL} failed.")
assert FAIL == 0
