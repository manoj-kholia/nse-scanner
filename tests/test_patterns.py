"""Build textbook and near-miss shapes; the scanner must accept one and reject the rest."""
import sys
import numpy as np
import pandas as pd

sys.path.insert(0, "/home/claude")
import find_patterns as fp


def frame(closes, vols=None):
    closes = np.asarray(closes, float)
    n = len(closes)
    if vols is None:
        vols = np.full(n, 100000.0)
    return pd.DataFrame({
        "Open": closes, "Close": closes,
        "High": closes * 1.004, "Low": closes * 0.996,
        "Volume": np.asarray(vols, float),
    }, index=pd.date_range("2023-01-01", periods=n, freq="B"))


def build(prior_gain=0.60, cup_len=90, depth=0.25, handle_len=10,
          handle_drop=0.06, breakout=False, vshape=False, tail_flat=0):
    """prior advance -> cup -> handle -> (optional) breakout."""
    base = 100.0
    prior = list(np.linspace(base, base * (1 + prior_gain), 160))
    rim = prior[-1]
    trough = rim * (1 - depth)

    half = cup_len // 2
    if vshape == "sharp":                        # spike bottom: drifts down, stabs, recovers
        k = max(2, cup_len // 30)
        shape = np.concatenate([
            np.linspace(1.0, 0.35, half - k),
            np.linspace(0.35, 0.0, k),
            np.linspace(0.0, 0.35, k),
            np.linspace(0.35, 1.0, cup_len - half - k),
        ])
        cup = list(trough + (rim - trough) * shape)
    elif vshape == "wide":                       # straight-line V over the whole cup
        down = list(np.linspace(rim, trough, half))
        up = list(np.linspace(trough, rim, cup_len - half))
        cup = down + up
    else:                                        # rounded U
        t = np.linspace(-np.pi / 2, np.pi / 2, cup_len)
        cup = list(trough + (rim - trough) * (np.sin(t) ** 2 if False else (np.sin(t) ** 2)))
        # sin^2 over -pi/2..pi/2 gives a U: 1 at the edges, 0 in the middle
        cup = list(trough + (rim - trough) * (np.cos(np.linspace(-np.pi/2, np.pi/2, cup_len))**2 * 0 +
                                              np.sin(np.linspace(-np.pi/2, np.pi/2, cup_len))**2))
    handle = list(np.linspace(rim, rim * (1 - handle_drop), handle_len))
    series = prior + cup + handle
    vols = [100000.0] * len(series)
    if breakout:
        series += [rim * 1.01]
        vols += [300000.0]
    if tail_flat:                                # realistic post-breakout drift upward
        last = series[-1]
        series += list(np.linspace(last * 1.01, last * 1.08, tail_flat))
        vols += [100000.0] * tail_flat
    return frame(series, vols)


def check(label, df, expect_stage, window=5):
    cup = fp.find_cup(df)
    # evaluate() returns (result, reason) - it has for a while, and this test
    # was still unpacking the old single-value form. It only appeared to pass
    # because it was importing a stale copy of find_patterns from elsewhere.
    res = fp.evaluate(df, cup, breakout_window=window)[0] if cup else None
    got = res["Stage"] if res else None            # no result = not reported at all
    ok = got == expect_stage
    note = "" if got else ("  (cup found, handle rejected)" if cup else "  (no cup)")
    print(f"{'PASS' if ok else 'FAIL'}  {label:<46} -> {got}{note}")
    if not ok:
        print(f"      expected {expect_stage}; cup={cup}")
    assert ok, label
    return res


print("--- shapes that SHOULD be found ---")
r = check("textbook cup + handle, 25% deep, 10-day handle", build(), "HANDLE FORMING")
print(f"      buy point {r['Buy_Point']}  cup {r['Cup_Depth_pct']}% / {r['Cup_Weeks']}w  "
      f"handle day {r['Handle_Days']} depth {r['Handle_Depth_pct']}%  target {r['Target']}")

r2 = check("same base, breakout day on 3x volume", build(breakout=True), "BREAKOUT")
print(f"      broke out {r2['Days_Since_Breakout']} day(s) ago at {r2['Breakout_Price']}, "
      f"stop {r2['Stop']}, target {r2['Target']}")

check("shallow 14% cup", build(depth=0.14), "HANDLE FORMING")
# 34% and 38% sit either side of O'Neil's documented 35% ceiling. This file
# used to expect 38% to pass, from before depth_max was tightened to 0.35 -
# it never failed because the suite was importing a stale copy of the module.
check("deep 34% cup, just inside O'Neil's limit", build(depth=0.34), "HANDLE FORMING")
check("long 200-bar cup", build(cup_len=200), "HANDLE FORMING")

print("\n--- shapes that should be REJECTED ---")
check("sharp V-bottom (spike low, no base)", build(vshape="sharp"), None)
check("no prior uptrend (flat before the cup)", build(prior_gain=0.02), None)
check("cup too shallow at 6%", build(depth=0.06), None)
check("cup too deep at 55%", build(depth=0.55), None)
check("38% cup - past O'Neil's 35% ceiling", build(depth=0.38), None)
check("cup too short (20 bars)", build(cup_len=20), None)
check("handle too deep (22% drop)", build(handle_drop=0.22), None)
check("handle too old (45 days)", build(handle_len=45), None)
check("broke out 12 days ago, now 8% extended", build(breakout=True, tail_flat=12), None)

print("\n--- known limitation, stated honestly ---")
check("wide straight-line V is NOT filtered out", build(vshape="wide"), "HANDLE FORMING")
print("      (scores 0.33 vs 0.38 for a true U - too close to separate reliably)")

print("\n--- volume gate ---")
low_vol = build(breakout=True)
low_vol.iloc[-1, low_vol.columns.get_loc("Volume")] = 90000.0   # below 1.4x average
cup = fp.find_cup(low_vol)
res, _why = fp.evaluate(low_vol, cup, breakout_window=5)
assert res is not None and res["Stage"] == "HANDLE FORMING", res
print("PASS  breakout without the volume surge is NOT called a breakout")

print("\n--- batch scan wiring ---")
def fake(tickers):
    out = {}
    for i, t in enumerate(tickers):
        out[t] = build(breakout=(i == 1)) if i < 2 else frame(np.linspace(100, 60, 300))
    return pd.concat(out, axis=1)

hits, scanned, _rejected = fp.scan(["AAA", "BBB", "CCC", "DDD"], downloader=fake,
                        batch_size=4, pause=0, log=lambda m: None)
stages = {h["Symbol"]: h["Stage"] for h in hits}
assert scanned == 4, scanned
assert stages == {"AAA": "HANDLE FORMING", "BBB": "BREAKOUT"}, stages
print(f"PASS  scanned 4, flagged only the 2 real patterns -> {stages}")

print("\nAll detection tests passed.")
