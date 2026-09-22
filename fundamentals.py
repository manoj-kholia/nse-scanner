#!/usr/bin/env python3
"""
fundamentals.py
---------------
The C and the A in CANSLIM - the half of O'Neil's method that the chart cannot
tell you.

  C  Current quarterly earnings.  EPS up 25% or more against the SAME quarter a
     year ago (he wants 40%+ in the best ones), with sales up 20-25% too. Sales
     matter because EPS can be flattered by cost cuts or a lower share count;
     real demand shows up in revenue.
  A  Annual earnings.  EPS growing 25%+ a year over the last three years, and
     return on equity of 17% or better.

A cup-and-handle on a company whose earnings are shrinking is a trap, which is
why this runs on the signals rather than being an afterthought.

HONEST LIMITS - read these before trusting a grade:
  * The source is Yahoo's free fundamentals. Coverage of large NSE names is
    decent; for small caps it is patchy and sometimes stale by a quarter.
  * Indian companies report half-yearly in some cases, and Yahoo's quarter
    alignment for NSE is not always right. We only ever compare a quarter with
    the column roughly four quarters earlier, and refuse to guess when the
    spacing looks wrong.
  * Nothing here is filtered out for missing data. A blank is reported as
    NO DATA, never silently treated as a pass. If you see NO DATA, look the
    company up yourself - do not assume it qualifies.

Use:
    import fundamentals
    df = fundamentals.fetch(["MCX", "ANDHRSUGAR"])
"""

import time

import numpy as np
import pandas as pd

# O'Neil's thresholds
EPS_Q_MIN = 25.0      # current quarter EPS growth, %
SALES_Q_MIN = 20.0    # current quarter sales growth, %
EPS_A_MIN = 25.0      # annual EPS growth, %
ROE_MIN = 17.0        # return on equity, %

# Yahoo normalises statement rows, but not consistently across markets, so try
# the labels it actually uses in rough order of preference.
EPS_ROWS = ["Diluted EPS", "Basic EPS"]
SALES_ROWS = ["Total Revenue", "Operating Revenue"]
PROFIT_ROWS = ["Net Income Common Stockholders", "Net Income",
               "Net Income Including Noncontrolling Interests",
               "Net Income Continuous Operations"]
EQUITY_ROWS = ["Stockholders Equity", "Total Equity Gross Minority Interest",
               "Common Stock Equity"]

GRADES = ("PASS", "THIN", "FAIL", "NO DATA")


def _row(df, names):
    """First of `names` present in the frame's index, as a float Series."""
    if df is None or not hasattr(df, "index") or df.empty:
        return None
    lookup = {str(i).strip().lower(): i for i in df.index}
    for n in names:
        key = lookup.get(n.lower())
        if key is not None:
            s = pd.to_numeric(df.loc[key], errors="coerce")
            if isinstance(s, pd.DataFrame):      # duplicated row label
                s = s.iloc[0]
            s = s.dropna()
            if len(s):
                # statement columns are period-end dates, newest first
                return s.sort_index(ascending=False)
    return None


def growth(curr, prior):
    """Percent change, with O'Neil's sign conventions for loss-making periods.

    Returns (value, kind):
        ("pct")        an ordinary percentage change
        ("turnaround") a loss in the base period turned into a profit - a real
                       positive that no percentage can express honestly
        ("na")         the base period is zero or negative and it is still
                       losing money, so growth is meaningless
    """
    if curr is None or prior is None:
        return None, "na"
    try:
        curr, prior = float(curr), float(prior)
    except (TypeError, ValueError):
        return None, "na"
    if not np.isfinite(curr) or not np.isfinite(prior):
        return None, "na"
    if prior > 0:
        return round((curr / prior - 1) * 100, 1), "pct"
    if curr > 0:
        return None, "turnaround"
    return None, "na"


def _yoy(series, want_gap=4, tol=1):
    """Newest value against the one ~`want_gap` periods earlier.

    Statement columns can be missing or unevenly spaced, so check the calendar
    distance rather than trusting position alone.
    """
    if series is None or len(series) < want_gap + 1:
        return None, None
    curr_date, curr = series.index[0], series.iloc[0]
    base_date, base = series.index[want_gap], series.iloc[want_gap]
    try:
        months = abs((pd.Timestamp(curr_date) - pd.Timestamp(base_date)).days) / 30.44
        if abs(months - want_gap * 3) > tol * 3:      # quarters are ~3 months
            return None, None
    except (TypeError, ValueError):
        pass
    return curr, base


def metrics(q_income=None, a_income=None, balance=None):
    """Compute C and A from three statement frames. Pure - no network."""
    out = {
        "EPS_Q_Growth": None, "Sales_Q_Growth": None,
        "EPS_A_Growth": None, "ROE": None,
        "Earnings_Grade": "NO DATA", "Earnings_Note": "no fundamentals available",
    }
    notes = []

    # --- C: the latest quarter against the same quarter a year ago ----------
    eps_q = _row(q_income, EPS_ROWS)
    curr, base = _yoy(eps_q)
    g, kind = growth(curr, base)
    if kind == "turnaround":
        out["EPS_Q_Growth"] = None
        notes.append("quarterly EPS: loss to profit")
    elif g is not None:
        out["EPS_Q_Growth"] = g

    sales_q = _row(q_income, SALES_ROWS)
    curr, base = _yoy(sales_q)
    g, _ = growth(curr, base)
    if g is not None:
        out["Sales_Q_Growth"] = g

    # --- A: annual EPS growth, and return on equity -------------------------
    eps_a = _row(a_income, EPS_ROWS)
    if eps_a is not None and len(eps_a) >= 2:
        span = min(3, len(eps_a) - 1)                 # up to 3 years back
        g, kind = growth(eps_a.iloc[0], eps_a.iloc[span])
        if g is not None:
            # annualise so a 3-year number is comparable with O'Neil's "per year"
            out["EPS_A_Growth"] = round(((1 + g / 100) ** (1 / span) - 1) * 100, 1)
        elif kind == "turnaround":
            notes.append("annual EPS: loss to profit")

    profit = _row(a_income, PROFIT_ROWS)
    equity = _row(balance, EQUITY_ROWS)
    if profit is not None and equity is not None and len(profit) and len(equity):
        e = float(equity.iloc[0])
        if e > 0:
            out["ROE"] = round(float(profit.iloc[0]) / e * 100, 1)

    # --- grade on what we can actually see ----------------------------------
    checks = [
        ("quarterly EPS", out["EPS_Q_Growth"], EPS_Q_MIN),
        ("quarterly sales", out["Sales_Q_Growth"], SALES_Q_MIN),
        ("annual EPS", out["EPS_A_Growth"], EPS_A_MIN),
        ("ROE", out["ROE"], ROE_MIN),
    ]
    known = [(n, v, lim) for n, v, lim in checks if v is not None]
    fails = [f"{n} {v:+.0f}% vs {lim:.0f}% needed" if n != "ROE"
             else f"ROE {v:.0f}% vs {lim:.0f}% needed"
             for n, v, lim in known if v < lim]

    if not known:
        out["Earnings_Grade"] = "NO DATA"
        out["Earnings_Note"] = "; ".join(notes) if notes else "no fundamentals available"
    elif fails:
        out["Earnings_Grade"] = "FAIL"
        out["Earnings_Note"] = "; ".join(fails[:2])
    elif len(known) >= 3:
        out["Earnings_Grade"] = "PASS"
        out["Earnings_Note"] = "; ".join(
            f"{n} {v:+.0f}%" if n != "ROE" else f"ROE {v:.0f}%" for n, v, _ in known)
    else:
        out["Earnings_Grade"] = "THIN"
        seen = ", ".join(n for n, _, _ in known)
        out["Earnings_Note"] = f"only {seen} available - check the rest yourself"

    if notes and out["Earnings_Grade"] != "NO DATA":
        out["Earnings_Note"] += "; " + "; ".join(notes)
    return out


def fetch_one(symbol, downloader=None):
    """Statements for one NSE symbol. Never raises."""
    if downloader is None:
        import yfinance as yf

        def downloader(sym):
            t = yf.Ticker(sym + ".NS")
            return t.quarterly_income_stmt, t.income_stmt, t.balance_sheet

    try:
        q, a, b = downloader(symbol)
    except Exception as exc:
        return dict(metrics(), Earnings_Note=f"lookup failed: {type(exc).__name__}")
    return metrics(q, a, b)


def fetch(symbols, downloader=None, pause=0.6, log=print):
    """Fundamentals for a handful of symbols, as a DataFrame keyed by Symbol.

    One request per symbol, so this is meant for the signal list (a few names),
    not the whole universe.
    """
    rows = []
    for i, s in enumerate(symbols):
        rows.append(dict(Symbol=s, **fetch_one(s, downloader)))
        if pause and i < len(symbols) - 1:
            time.sleep(pause)
    df = pd.DataFrame(rows)
    if log and len(df):
        have = (df["Earnings_Grade"] != "NO DATA").sum()
        log(f"  fundamentals: {have}/{len(df)} symbols returned usable data")
    return df


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="Check CANSLIM C and A for some symbols")
    ap.add_argument("symbols", nargs="+")
    args = ap.parse_args()
    df = fetch(args.symbols)
    cols = ["Symbol", "Earnings_Grade", "EPS_Q_Growth", "Sales_Q_Growth",
            "EPS_A_Growth", "ROE", "Earnings_Note"]
    print(df[cols].to_string(index=False))
