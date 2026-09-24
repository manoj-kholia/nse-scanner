#!/usr/bin/env python3
"""
update_nse_data.py
------------------
Reads EQUITY_L.csv (NSE equity list) and builds a live data sheet with:

    Last Price, Day Change %, Volume, Avg Volume (20d), Volume x Avg,
    20-Week High / Low, % from 20W High, % above 20W Low,
    52-Week High / Low, 50 DMA, 200 DMA, Above 200 DMA?

Writes  EQUITY_L_live.xlsx  and  EQUITY_L_live.csv  next to the source file.
The original EQUITY_L.csv is never modified.

Run it from Terminal on your Mac:
    python3 update_nse_data.py

Data source: Yahoo Finance via the yfinance package (NSE symbols use a .NS suffix).
This is free, unofficial data - good for screening, not for execution.
"""

import os
import sys
import time
import argparse
from datetime import datetime

import pandas as pd

# 20 weeks of trading ~ 100 sessions. 52 weeks ~ 250 sessions.
WEEK_BARS = 5
LOOKBACK_20W = 100
LOOKBACK_52W = 250

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "EQUITY_L.csv")
OUT_XLSX = os.path.join(HERE, "EQUITY_L_live.xlsx")
OUT_CSV = os.path.join(HERE, "EQUITY_L_live.csv")
LOG = os.path.join(HERE, "update_log.txt")


def log(msg):
    line = f"{datetime.now():%Y-%m-%d %H:%M:%S}  {msg}"
    print(line, flush=True)
    try:
        with open(LOG, "a") as fh:
            fh.write(line + "\n")
    except OSError:
        pass


NSE_LIST_URL = "https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv"


def refresh_listing(path=SRC, url=NSE_LIST_URL, timeout=30, session=None):
    """Pull the current NSE equity list over the committed snapshot.

    EQUITY_L.csv was a file committed once and never touched again, and stale
    here means INVISIBLE: on 24 Sep 2026 both screens missed SSRETAIL (+11% on
    the day, gapped +3.1%) and HEROMOTORS (+15%, gapped +4.7%) for no reason
    other than that both listed after that file was made. A scanner cannot
    report a stock it has never heard of, so the failure is silent - which is
    the worst kind this repository keeps finding.

    It REFUSES to overwrite with anything that does not look like the real
    list. NSE serves captchas, truncated bodies and HTML error pages to
    non-browser clients, and writing one of those would quietly shrink the
    universe to nothing: the same silent failure wearing a new hat. On any
    doubt the old file stays and the run says so.

    Returns (changed, message).
    """
    import io

    import requests

    try:
        s = session or requests.Session()
        s.headers.update({
            # NSE refuses non-browser clients outright.
            "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                           "AppleWebKit/537.36 (KHTML, like Gecko) "
                           "Chrome/124.0 Safari/537.36"),
            "Accept": "text/csv,application/csv,*/*",
            "Accept-Language": "en-US,en;q=0.9",
        })
        # the archive host will not serve the file without a session cookie
        # from the main site first
        try:
            s.get("https://www.nseindia.com/", timeout=timeout)
        except Exception:
            pass
        r = s.get(url, timeout=timeout)
        if r.status_code != 200:
            return False, f"NSE returned HTTP {r.status_code}; keeping the existing list"
        body = r.content
        if len(body) < 50_000:
            return False, (f"NSE returned only {len(body)} bytes, which is not the "
                           f"equity list; keeping the existing one")
        fresh = pd.read_csv(io.BytesIO(body))
        fresh.columns = [c.strip().upper() for c in fresh.columns]
        if "SYMBOL" not in fresh.columns:
            return False, "downloaded file has no SYMBOL column; keeping the existing list"
        if len(fresh) < 1500:
            return False, (f"downloaded list has only {len(fresh)} rows, far short of "
                           f"the ~2,300 NSE lists; keeping the existing one")

        old_syms = set()
        if os.path.exists(path):
            try:
                old = pd.read_csv(path)
                old.columns = [c.strip().upper() for c in old.columns]
                old_syms = set(old["SYMBOL"].astype(str).str.strip())
            except Exception:
                pass
        new_syms = set(fresh["SYMBOL"].astype(str).str.strip())

        # A real listing file never loses a big chunk of its names overnight.
        if old_syms and len(new_syms) < len(old_syms) * 0.9:
            return False, (f"downloaded list has {len(new_syms)} symbols against "
                           f"{len(old_syms)} in the current one - too big a drop to "
                           f"trust; keeping the existing list")

        with open(path, "wb") as fh:
            fh.write(body)
        added = sorted(new_syms - old_syms)
        gone = sorted(old_syms - new_syms)
        bits = [f"{len(new_syms)} symbols"]
        if added:
            bits.append(f"+{len(added)} new ({', '.join(added[:6])}"
                        + ("..." if len(added) > 6 else "") + ")")
        if gone:
            bits.append(f"-{len(gone)} delisted")
        return True, "listing refreshed: " + ", ".join(bits)
    except Exception as exc:
        return False, (f"could not refresh the listing ({type(exc).__name__}: {exc}); "
                       f"keeping the existing one")


def read_symbols(path, series_filter="EQ"):
    """Read the NSE list. Column names are padded with spaces in NSE's file."""
    df = pd.read_csv(path)
    df.columns = [c.strip().upper() for c in df.columns]
    if "SYMBOL" not in df.columns:
        raise SystemExit(f"No SYMBOL column in {path}. Found: {list(df.columns)}")

    name_col = next((c for c in df.columns if "NAME" in c), None)
    df["SYMBOL"] = df["SYMBOL"].astype(str).str.strip()

    if series_filter and "SERIES" in df.columns:
        df = df[df["SERIES"].astype(str).str.strip().str.upper() == series_filter]

    df = df[df["SYMBOL"] != ""].drop_duplicates(subset="SYMBOL")
    out = pd.DataFrame({
        "Symbol": df["SYMBOL"].values,
        "Company": df[name_col].astype(str).str.strip().values if name_col else "",
    })
    return out.reset_index(drop=True)


def frame_for(raw, ticker):
    """Pull one ticker's OHLCV frame out of a yfinance download result."""
    if raw is None or len(raw) == 0:
        return None
    try:
        if isinstance(raw.columns, pd.MultiIndex):
            if ticker not in raw.columns.get_level_values(0):
                return None
            df = raw[ticker]
        else:
            df = raw
        df = df.dropna(subset=["Close"])
        return df if len(df) else None
    except (KeyError, TypeError):
        return None


def metrics(df):
    """Compute the row of numbers for one stock from its daily OHLCV frame."""
    close = df["Close"]
    high = df["High"]
    low = df["Low"]
    vol = df["Volume"]

    last = float(close.iloc[-1])
    prev = float(close.iloc[-2]) if len(close) > 1 else last
    chg = (last / prev - 1) * 100 if prev else 0.0

    w20 = df.tail(LOOKBACK_20W)
    w52 = df.tail(LOOKBACK_52W)

    hi20 = float(w20["High"].max())
    lo20 = float(w20["Low"].min())
    hi52 = float(w52["High"].max())
    lo52 = float(w52["Low"].min())

    last_vol = float(vol.iloc[-1]) if len(vol) else 0.0
    avg_vol = float(vol.tail(20).mean()) if len(vol) else 0.0

    def dma(n):
        return round(float(close.tail(n).mean()), 2) if len(close) >= n else None

    dma50 = dma(50)
    dma200 = dma(200)

    # --- Relative strength, O'Neil style -------------------------------------
    # Weighted price performance: the most recent quarter counts double the
    # others. This is the public approximation of IBD's proprietary RS Rating.
    # The raw number is meaningless on its own - it only matters once every
    # stock in the universe is ranked against it (done in build()).
    def perf(n):
        if len(close) > n:
            past = float(close.iloc[-n - 1])
            return (last / past - 1) * 100 if past > 0 else None
        return None

    windows = [(63, 0.4), (126, 0.2), (189, 0.2), (252, 0.2)]   # 3, 6, 9, 12 months
    parts = [(perf(n), w) for n, w in windows]
    have = [(p, w) for p, w in parts if p is not None]
    # need at least 6 months of history for the number to mean anything
    rs_raw = None
    if len(have) >= 2:
        tot_w = sum(w for _, w in have)
        rs_raw = round(sum(p * w for p, w in have) / tot_w, 2)

    return {
        "Last Price": round(last, 2),
        "Day Change %": round(chg, 2),
        "Volume": int(last_vol),
        "Avg Volume 20d": int(avg_vol),
        "Volume x Avg": round(last_vol / avg_vol, 2) if avg_vol else None,
        "20W High": round(hi20, 2),
        "20W Low": round(lo20, 2),
        "% From 20W High": round((last / hi20 - 1) * 100, 2) if hi20 else None,
        "% Above 20W Low": round((last / lo20 - 1) * 100, 2) if lo20 else None,
        "52W High": round(hi52, 2),
        "52W Low": round(lo52, 2),
        "50 DMA": dma50,
        "200 DMA": dma200,
        "Above 200 DMA": ("Yes" if last > dma200 else "No") if dma200 else None,
        "RS Raw": rs_raw,
        "Bars": len(df),
    }


def fetch_all(symbols, batch_size=60, pause=1.5, period="1y", retries=2, downloader=None,
              on_progress=None):
    """Download in batches so Yahoo does not rate-limit us. Returns {symbol: metrics}."""
    if downloader is None:
        import yfinance as yf
        downloader = lambda tickers: yf.download(
            tickers,
            period=period,
            interval="1d",
            group_by="ticker",
            auto_adjust=False,
            actions=False,
            progress=False,
            threads=True,
        )

    results, failed = {}, []
    total = len(symbols)

    for start in range(0, total, batch_size):
        batch = symbols[start:start + batch_size]
        tickers = [s + ".NS" for s in batch]
        raw = None

        for attempt in range(retries + 1):
            try:
                raw = downloader(tickers)
                break
            except Exception as exc:                      # network / parse / rate limit
                if attempt == retries:
                    log(f"  batch failed after {retries + 1} tries: {exc}")
                else:
                    time.sleep(3 * (attempt + 1))

        for sym, tk in zip(batch, tickers):
            df = frame_for(raw, tk)
            if df is None or len(df) < 2:
                failed.append(sym)
                continue
            try:
                results[sym] = metrics(df)
            except Exception as exc:
                log(f"  {sym}: {exc}")
                failed.append(sym)

        done = min(start + batch_size, total)
        log(f"  {done}/{total} symbols  ({len(results)} ok, {len(failed)} no data)")
        if on_progress:
            try:
                on_progress(done, total, results)
            except Exception as exc:
                log(f"  checkpoint skipped: {exc}")
        if done < total:
            time.sleep(pause)

    return results, failed


def rs_rating(raw):
    """Percentile-rank raw relative strength across the universe onto 1-99.

    O'Neil's point is that leadership is RELATIVE: an RS Rating of 80 means the
    stock outperformed 80% of the market. So the number only exists once every
    stock is ranked together - it cannot be computed one stock at a time.
    """
    s = pd.to_numeric(raw, errors="coerce")
    if s.notna().sum() < 2:
        return pd.Series([None] * len(s), index=s.index)
    pct = s.rank(pct=True, method="average") * 100
    return pct.round().clip(1, 99).astype("Int64")


def build(listing, results):
    cols = ["Last Price", "Day Change %", "Volume", "Avg Volume 20d", "Volume x Avg",
            "20W High", "20W Low", "% From 20W High", "% Above 20W Low",
            "52W High", "52W Low", "50 DMA", "200 DMA", "Above 200 DMA",
            "RS Raw", "Bars"]
    rows = []
    for sym in listing["Symbol"]:
        m = results.get(sym)
        rows.append(m if m else {c: None for c in cols})
    data = pd.DataFrame(rows, columns=cols)
    out = pd.concat([listing.reset_index(drop=True), data], axis=1)
    out["RS Rating"] = rs_rating(out["RS Raw"])
    out.insert(0, "Updated", datetime.now().strftime("%Y-%m-%d %H:%M"))
    return out


def write_excel(df, path):
    try:
        with pd.ExcelWriter(path, engine="xlsxwriter") as xw:
            df.to_excel(xw, sheet_name="Live Data", index=False, startrow=0)
            wb, ws = xw.book, xw.sheets["Live Data"]
            head = wb.add_format({"bold": True, "bg_color": "#1F4E78", "font_color": "white",
                                  "border": 1, "align": "center", "valign": "vcenter",
                                  "text_wrap": True})
            pct = wb.add_format({"num_format": "0.00"})
            num = wb.add_format({"num_format": "#,##0.00"})
            intf = wb.add_format({"num_format": "#,##0"})

            for i, name in enumerate(df.columns):
                ws.write(0, i, name, head)
            widths = {"Company": 34, "Symbol": 14, "Updated": 17}
            for i, name in enumerate(df.columns):
                fmt = intf if name in ("Volume", "Avg Volume 20d") else (
                    pct if "%" in name or name == "Volume x Avg" else num)
                ws.set_column(i, i, widths.get(name, 14), fmt if i > 2 else None)

            ws.freeze_panes(1, 2)
            ws.autofilter(0, 0, len(df), len(df.columns) - 1)

            # red when far below the 20-week high, green when close to it
            c = df.columns.get_loc("% From 20W High")
            ws.conditional_format(1, c, len(df), c,
                                  {"type": "3_color_scale",
                                   "min_color": "#F8696B", "mid_color": "#FFEB84",
                                   "max_color": "#63BE7B"})
            c = df.columns.get_loc("Volume x Avg")
            ws.conditional_format(1, c, len(df), c,
                                  {"type": "cell", "criteria": ">=", "value": 2,
                                   "format": wb.add_format({"bg_color": "#C6EFCE"})})
        return True
    except ImportError:
        log("xlsxwriter not installed - writing CSV only. Fix: pip3 install xlsxwriter")
        return False


def main():
    ap = argparse.ArgumentParser(description="Update NSE stock data from EQUITY_L.csv")
    ap.add_argument("--source", default=SRC, help="path to EQUITY_L.csv")
    ap.add_argument("--batch-size", type=int, default=60)
    ap.add_argument("--pause", type=float, default=1.5, help="seconds between batches")
    ap.add_argument("--limit", type=int, default=0, help="only the first N symbols (for testing)")
    ap.add_argument("--series", default="EQ", help="series filter, blank for all")
    ap.add_argument("--no-refresh-listing", action="store_true",
                    help="do not pull a fresh EQUITY_L.csv from NSE first")
    args = ap.parse_args()

    if not os.path.exists(args.source):
        raise SystemExit(f"Cannot find {args.source}")

    log("=" * 60)
    # Do this BEFORE reading the list: a stock that is not in the file is not
    # merely unranked, it is invisible, and nothing downstream can tell you so.
    if not args.no_refresh_listing and args.source == SRC:
        changed, msg = refresh_listing(args.source)
        log(("  " if changed else "  NOTE: ") + msg)

    listing = read_symbols(args.source, args.series or None)
    if args.limit:
        listing = listing.head(args.limit)
    log(f"Loaded {len(listing)} symbols from {os.path.basename(args.source)}")

    started = time.time()

    # Save a partial CSV every few batches so a long run is never lost entirely.
    def checkpoint(done, total, partial):
        if done % (args.batch_size * 5) == 0 and done < total:
            build(listing, partial).to_csv(OUT_CSV, index=False)
            log(f"  checkpoint saved ({len(partial)} rows so far)")

    results, failed = fetch_all(listing["Symbol"].tolist(),
                                batch_size=args.batch_size, pause=args.pause,
                                on_progress=checkpoint)

    out = build(listing, results)
    out.to_csv(OUT_CSV, index=False)
    wrote_xlsx = write_excel(out, OUT_XLSX)

    mins = (time.time() - started) / 60
    log(f"Done in {mins:.1f} min - {len(results)} updated, {len(failed)} without data")
    if failed:
        log("No data for: " + ", ".join(failed[:25]) + ("..." if len(failed) > 25 else ""))
    log(f"Wrote {OUT_CSV}" + (f" and {OUT_XLSX}" if wrote_xlsx else ""))


if __name__ == "__main__":
    main()
