#!/usr/bin/env python3
"""
industry_groups.py
------------------
The part of O'Neil's method that is neither the chart nor the balance sheet:
which industry the stock is in, and whether that industry is leading.

His claim is not a small one. From chapter 18:

    "According to computer analysis, 37% of a stock's price movement is due to
     subgroup influence and 12% to major group influence."

Half the move. And the winners were not scattered across the market - "the
best-performing listed individual stocks belonged to industry groups with a
median strength rank of 61 out of 200", i.e. the top third, measured just
before their advance began. David Ryan's contest-winning picks had a median
group strength in the top 30%.

We had none of this. A stock could pass every price and earnings rule while
sitting in the deadest corner of the market, and nothing on the dashboard
would say so.

HOW THE RANK IS BUILT
    1. Each symbol is mapped to an industry. NSE's own EQUITY_L.csv carries no
       sector column, so the map is built from Yahoo one symbol at a time and
       CACHED in sector_map.csv, which is committed to the repo. Each run tops
       up a few hundred names; after a couple of weeks the map is complete and
       the daily cost is nil.
    2. A group's strength is the MEDIAN of its members' RS Raw - the same
       weighted price performance the RS Rating is ranked from.
    3. Groups are percentile-ranked against each other onto 1-99, exactly the
       way individual stocks are. 70+ means the top 30% of groups.

HONEST LIMITS - read these before trusting a rank:
  * Yahoo's `industry` for NSE names is patchy and occasionally wrong. A
    symbol with no industry is reported as NO DATA, never quietly passed.
  * Yahoo gives roughly 150 industries, not O'Neil's 200 subgroups, and they
    are US-shaped categories applied to Indian companies. A rank of 61/200 in
    his book is not the same object as a rank of 61 here.
  * A group with only a handful of listed members produces a median off two or
    three stocks. Below MIN_MEMBERS we name the group and refuse to rank it.
  * The median is of a 3/6/9/12-month composite, while he ranks groups on six
    months. Close, not identical.
"""

import os
import time

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(HERE, "sector_map.csv")

MIN_MEMBERS = 4        # fewer than this and a median means nothing
LEADING = 70           # O'Neil's winners sat in the top 30% of groups
DEFAULT_BUDGET = 250   # symbols to look up per run


def load_cache(path=CACHE):
    """Symbol -> industry, from the committed cache. Missing file is not an error."""
    if not os.path.exists(path):
        return pd.DataFrame(columns=["Symbol", "Group", "Sector", "Checked"])
    try:
        df = pd.read_csv(path)
    except Exception:
        return pd.DataFrame(columns=["Symbol", "Group", "Sector", "Checked"])
    for c in ["Symbol", "Group", "Sector", "Checked"]:
        if c not in df.columns:
            df[c] = None
    return df


def save_cache(df, path=CACHE):
    df = df.drop_duplicates(subset=["Symbol"], keep="last").sort_values("Symbol")
    df.to_csv(path, index=False)
    return df


def _lookup(symbol, downloader=None):
    """(group, sector) for one symbol, or (None, None). Never raises."""
    if downloader is None:
        import yfinance as yf

        def downloader(sym):
            return yf.Ticker(sym + ".NS").info

    try:
        info = downloader(symbol) or {}
    except Exception:
        return None, None
    g = info.get("industry") or None
    s = info.get("sector") or None
    return (str(g).strip() if g else None, str(s).strip() if s else None)


def refresh(symbols, cache=None, budget=DEFAULT_BUDGET, pause=0.4,
            downloader=None, log=print, max_seconds=420):
    """Top up the cache with symbols it has never seen. Returns the new cache.

    Only symbols with NO row at all are fetched. A symbol that came back blank
    keeps its blank row rather than being retried every single day - Yahoo
    simply does not carry an industry for some NSE names, and hammering it for
    those would spend the whole budget on the same failures forever.

    `max_seconds` is a wall-clock cap, not a nicety. Ticker.info is one HTTP
    round trip per symbol and its latency varies by an order of magnitude when
    Yahoo is rate-limiting; a slow day could otherwise eat the whole GitHub
    Actions job and lose the scan itself, which is the part that matters. On a
    cap we keep what we have, write it, and pick up the rest tomorrow - the map
    is meant to fill in over a fortnight anyway.
    """
    cache = load_cache() if cache is None else cache
    known = set(cache["Symbol"].astype(str))
    todo = [s for s in symbols if s not in known][:budget]
    if not todo:
        return cache

    rows = []
    started = time.monotonic()
    stopped_early = False
    for i, sym in enumerate(todo):
        if time.monotonic() - started > max_seconds:
            stopped_early = True
            break
        g, s = _lookup(sym, downloader)
        rows.append({"Symbol": sym, "Group": g, "Sector": s,
                     "Checked": pd.Timestamp.now().strftime("%Y-%m-%d")})
        if pause and i < len(todo) - 1:
            time.sleep(pause)

    if not rows:
        return cache
    add = pd.DataFrame(rows)
    got = int(add["Group"].notna().sum())
    if log:
        note = f" (stopped at the {max_seconds}s cap)" if stopped_early else ""
        log(f"  industry map: looked up {len(rows)}, got {got}{note} "
            f"({len(known) + len(rows)} of {len(symbols)} symbols now cached)")
    return save_cache(pd.concat([cache, add], ignore_index=True))


def group_strength(live, cache=None, min_members=MIN_MEMBERS):
    """Rank every industry 1-99 by the median RS Raw of its members.

    `live` is EQUITY_L_live.csv as a frame; it must carry Symbol and RS Raw.
    Returns (ranks, members) where ranks maps group -> 1-99 (only groups with
    enough members) and members maps group -> how many were used.
    """
    cache = load_cache() if cache is None else cache
    m = cache[cache["Group"].notna()][["Symbol", "Group"]]
    if m.empty or "RS Raw" not in live.columns:
        return {}, {}

    d = live[["Symbol", "RS Raw"]].copy()
    d["RS Raw"] = pd.to_numeric(d["RS Raw"], errors="coerce")
    d = d.dropna(subset=["RS Raw"]).merge(m, on="Symbol", how="inner")
    if d.empty:
        return {}, {}

    agg = d.groupby("Group")["RS Raw"].agg(["median", "count"])
    members = {g: int(r["count"]) for g, r in agg.iterrows()}

    big = agg[agg["count"] >= min_members]
    if len(big) < 2:
        return {}, members
    pct = big["median"].rank(pct=True, method="average") * 100
    ranks = {g: int(round(min(99, max(1, v)))) for g, v in pct.items()}
    return ranks, members


def annotate(df, live, cache=None, min_members=MIN_MEMBERS):
    """Add Group and Group_Rank to a signals frame. Never drops a row.

    A missing rank is left as None and shown as NO DATA. O'Neil's rule is a
    preference, not a gate - and a rank we cannot compute is not evidence of
    weakness, so it must not read like one.
    """
    if not len(df):
        return df
    cache = load_cache() if cache is None else cache
    ranks, members = group_strength(live, cache, min_members)
    gmap = dict(zip(cache["Symbol"].astype(str), cache["Group"]))

    out = df.copy()
    out["Group"] = out["Symbol"].map(gmap)
    out["Group_Rank"] = out["Group"].map(lambda g: ranks.get(g) if isinstance(g, str) else None)
    return out


def main():
    import argparse

    ap = argparse.ArgumentParser(description="Industry group strength, O'Neil style")
    ap.add_argument("--source", default=os.path.join(HERE, "EQUITY_L_live.csv"))
    ap.add_argument("--budget", type=int, default=DEFAULT_BUDGET,
                    help="how many uncached symbols to look up this run")
    ap.add_argument("--no-refresh", action="store_true",
                    help="rank using the cache as it stands, fetch nothing")
    ap.add_argument("--top", type=int, default=25)
    args = ap.parse_args()

    if not os.path.exists(args.source):
        raise SystemExit(f"Cannot find {args.source}. Run update_nse_data.py first.")
    live = pd.read_csv(args.source)

    cache = load_cache()
    if not args.no_refresh:
        cache = refresh(live["Symbol"].dropna().astype(str).tolist(),
                        cache, budget=args.budget)

    ranks, members = group_strength(live, cache)
    if not ranks:
        raise SystemExit("Not enough of the industry map is filled in yet to rank "
                         "groups. Run again tomorrow - it fills in a few hundred "
                         "symbols per run.")

    rows = sorted(ranks.items(), key=lambda kv: -kv[1])
    print(f"{len(ranks)} rankable groups "
          f"(of {len(members)} seen; the rest have under {MIN_MEMBERS} members)\n")
    print(f"{'rank':>5}  {'members':>7}  group")
    print("-" * 60)
    for g, r in rows[:args.top]:
        print(f"{r:>5}  {members.get(g, 0):>7}  {g}")
    print(f"\n{LEADING}+ is the top 30% - where O'Neil's winners came from.")


if __name__ == "__main__":
    main()
