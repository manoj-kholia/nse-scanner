# Handover — NSE cup-and-handle scanner

Written 24 Sep 2026 so a fresh session can pick this up without the
conversation that produced it. The immediate next job is **building a website
on top of the published data**, so the data contract comes first.

Read [ONEIL_AUDIT.md](ONEIL_AUDIT.md) alongside this. That file says which of
O'Neil's rules the code actually enforces and which it does not; this one says
how the machine is wired.

---

## 1. Who this is for

A delivery (positional) trader on NSE via Zerodha. No F&O. Personal use, not a
product. The method is O'Neil's CANSLIM as set out in *How To Make Money In
Stocks* (2nd ed., 1995) — the book has been read and the code audited against
it rule by rule.

Two independent implementations of the same rules exist and are kept in step by
a differential test:

* `find_patterns.py` — the scanner, runs nightly, publishes the dashboard
* `cup_and_handle_v3.pine` — the TradingView chart script, same rules
* `pine_parity.py` — proves they agree (0 disagreements over 4,887 generated
  series, covering both the live verdict and the historical walk)

If you change a rule in one, change it in the other and re-run the harness.
That is the single most important convention in this repository.

---

## 2. The data contract

Everything a website needs is in two public JSON files. They are committed to
the repo and served from GitHub Pages.

```
https://manoj-kholia.github.io/nse-scanner/data.json        daily, 18:30 IST
https://manoj-kholia.github.io/nse-scanner/intraday.json    09:20 IST + on demand
```

The same files are in the repo at `docs/data.json` and `docs/intraday.json`.

Field names are short because the payload is fetched on every page load. The
mapping from the long internal names lives in `build_site.py` (`SCREEN_MAP`,
`SIG_MAP`) and `build_intraday.py` (`MAP`) — those dicts are the authority, not
this document.

### 2.1 `data.json`

```
built        "24 Sep 2026, 23:04 IST"   when this file was written
dataStamp    when the underlying price pull ran
universe     int — how many symbols had usable prices
market       object, see below
screened     array — every stock passing the leadership screen (~280)
signals      array — actual cup-and-handle setups (usually 0-5)
```

**`market`** — the O'Neil "M" gate. A website should show this above
everything else, because it is the one field that says whether any of the rest
is worth acting on.

```
verdict            "CONFIRMED UPTREND" | "UNDER PRESSURE" | "CORRECTION"
buy_breakouts      bool
advice             a full sentence, meant to be displayed verbatim
last               Nifty 50 level
day_change_pct
above_50dma        bool
above_200dma       bool
rising_50dma       bool
sma50, sma200
distribution_days  int, counted over distribution_window (25) sessions
distribution_detail  array of {date, pct} — every one, not a sample
pct_off_52w_high
checked            timestamp
```

**`screened[]`** — the ranked watchlist.

```
s    symbol            n    company        p    last price
c    day change %      rs   RS Rating 1-99
fh   % from 20-week high (negative = below)
al   % above 20-week low
h,l  20-week high / low
v    last volume       av   20-day average volume
vx   volume vs that average
d50, d200   50 and 200 day moving averages
sc   score — a sorting convenience, NOT a signal
```

**`signals[]`** — the actual setups. This is the part worth designing around.

```
s, n            symbol, company
stage           "HANDLE FORMING" | "BREAKOUT"
rs              RS Rating 1-99 (screen requires >= 80)
grp             industry name, or null if not mapped yet
gr              industry group rank 1-99, or null. 70+ = top 30%.
                null means NOT MAPPED, not weak — render it as a dash.
bs              base stage. 3 or 4 is late and failure-prone.
eg              "PASS" | "THIN" | "FAIL" | "NO DATA"
en              one line explaining eg, display verbatim
epsq, salesq, epsa, roe    the CANSLIM C and A numbers, %
p               last price      buy    the pivot / buy point
toBuy           % from last price to the buy point
stop            where to cut — the tighter of the handle low and -8%
risk            % loss if stopped
tgt             sell into strength, +20% off the pivot
tgtMax          +25% off the pivot
mm              measured move — shown for reference, NOT a rule from the book
hold            sessions left of the 8-week hold window
hd              handle age in days       hlow   handle low
hdep, cdep      handle and cup depth %   cw     cup length in weeks
vx              breakout volume vs the 50-day average
since           sessions since the breakout, null while the handle forms
```

### 2.2 `intraday.json`

A separate, much weaker thing — an opening-range **watchlist**, not a tested
system. See §6.

```
built     status{ok, headline, detail, scanned, usable, kept, reasons, checked}
inPlay[]  s, n, d(session), rv(relative volume), gap(%), gsrc, atr(%),
          pc(prev close), o(open), orh/orl(opening range), orr,
          lt/ls (long trigger/stop), st/ss (short), sd, irisk,
          exit (a RULE, a string, not a price), be, cva, to, last
```

`status` exists so a website can tell "nothing was interesting today" apart
from "the feed broke" — those look identical otherwise. Show `headline` and
`detail` when `ok` is false.

`gsrc` is `"daily bar"` or `"5-minute fallback"`. Anything not on the daily
bar has an approximate gap; say so rather than printing it plain.

### 2.3 Things a website must not do with this

* **Do not print a signal without the market verdict next to it.** O'Neil's
  claim is that three in four stocks follow the market. The current dashboard
  puts the verdict at the top for that reason.
* **Do not render a missing industry rank as weakness.** `gr: null` means the
  symbol is not in the map yet.
* **`sc` is a sort key, not a rating.** It has no meaning as a number.
* **`mm` is not a target.** It is the old measured move, kept only for
  comparison. The rule is `tgt`.
* Everything is delayed end-of-day data. Nothing here is live.

---

## 3. How it runs

GitHub Actions, all on schedules, all committing their own output.

| workflow | when | what |
|---|---|---|
| `daily.yml` | 18:30 IST, Mon-Fri | refresh the NSE list → prices → screen → market → patterns → earnings → industry → build `data.json` |
| `intraday.yml` | 09:20 IST, Mon-Fri | opening-range watchlist → `intraday.json` |
| `explain.yml` | manual | why is stock X not on the list; optional history walk |
| `verify.yml` | manual | check the In Play table against the tape |
| `backtest.yml` | manual | re-measure the opening-range strategy |

The dashboard is `docs/index.html` — one self-contained file, no build step, no
dependencies. It fetches `data.json` and `intraday.json` and renders tabs. The
trade journal inside it is `localStorage` only and never leaves the browser.

Local checkout lives at `/Users/macbookpro/Trading/nse-scanner`. Pushes go
through GitHub Desktop (the credentials live there; `git push` from a shell
fails on auth).

---

## 4. The rules, and where the numbers come from

Every threshold is in the `P` dict at the top of `find_patterns.py`, with a
comment saying whether it is from the book, a convention, or a judgement call.
The short version:

**Screen (`screen_stocks.py`)** — above the 200 DMA, above the 50 DMA, within
15% of the 20-week high, RS Rating ≥ 80, ≥ 50,000 average volume, price ≥ ₹20.

**Cup** — 30–250 bars, depth 12–35%, rims within −8%/+5% of each other, buy
point within 15% of the 52-week high, low sitting 15–85% across, shape score
≥ 0.41 (mean normalised depth of the closes — a U scores ~0.51, a V ~0.49),
prior advance ≥ 25%.

**Handle** — 5–35 days, depth ≤ 12%, must sit in the upper half of the cup,
must not wedge upward (slope > 0 *and* t-stat > 1), volume ≤ the 50-day average.

**Breakout** — close above the pivot, ≤ 5% past it, volume ≥ 1.4× the 50-day
average, close above the 200 DMA.

**Exit** — stop at the tighter of the handle low and 8% below entry. Sell into
strength at +20% off the pivot, +25% at the outside; if +20% arrives within 40
sessions, hold the full 8 weeks and reassess.

**Market** — Nifty above its 50 and 200 DMA with a rising 50 DMA; distribution
days counted over 25 sessions, 4 = pressure, 6 = correction.

---

## 5. What changed on 24 Sep 2026

A long session. In order:

1. **Read the book and audited the code against it** → `ONEIL_AUDIT.md`.
2. **Replaced the exit rule.** Was `buy + cup depth`, a measured move that
   appears nowhere in O'Neil. Now +20%/+25% off the pivot with the 8-week
   exception.
3. **Added the overhead-supply gate** — the buy point must sit within 15% of
   the 52-week high. Set at 10% first, which was wrong: `rim_down` already
   allows the right rim 8% under the left, and the left rim is itself inside
   the 52-week window, so the two rules were fighting over 2 points of
   headroom.
4. **Added industry group strength** (`industry_groups.py`). O'Neil puts 37% of
   a stock's move on its subgroup and 12% on its major group.
5. **Marked past setups on the chart.** The script only ever described one
   base; `history_scan.py` plus a new Pine block now show every base the rules
   would have armed, and what became of it.
6. **Extended the parity harness to the history walk**, and made it print which
   branches the test data actually reached — a green tick on an unexercised
   branch is worth nothing.
7. **Fixed the In Play gap.** It was built from 5-minute bars: yesterday's
   15:25 close instead of the closing auction, and the first 5-minute bar's
   open instead of the opening auction. Both errors were the size of the 0.5%
   gate they fed. MANINDS published at −2.12% on a day it gapped **+0.41%**.
   Now taken from the daily bar; opens verified exact against a live broker
   feed on 18 of 18 names.
8. **Made the stock list refresh from NSE.** `EQUITY_L.csv` was committed once
   and never updated, so anything listed since was **invisible** — the screens
   missed SSRETAIL (+11% that day) and HEROMOTORS (+15%) for that reason alone.
9. **Fixed the trade journal** — it could not record an open position, and it
   priced every trade as intraday (delivery STT is 0.1% both sides against
   0.025% sell-only, so a delivery round trip cost ~3× what it reported).

---

## 6. Known gaps — read before trusting anything

**Ranked, from ONEIL_AUDIT.md:**

1. No follow-through day. The system can say "stop buying" and has no rule for
   starting again.
2. The annual-earnings test is a 3-year endpoint CAGR, not O'Neil's five
   consecutive up years. A company that fell for four years and had one
   recovery year passes.
3. Only one quarter is checked for "C", with no acceleration test.
4. No tightness / wide-and-loose measure, which he calls the characteristic
   that makes patterns "almost always fail".
5. No institutional sponsorship ("I") at all; no share count or debt ("S").

**Data quirks that have each cost a wrong diagnosis:**

* Yahoo emits a flat zero-volume bar for exchange holidays. Dropped by
  `drop_phantom_sessions`, but anything new reading raw Yahoo data must do the
  same or every bar-counted rule shifts by a day.
* Yahoo serves the in-progress day as a finished daily bar. `drop_unfinished_session`
  removes it; without that, volume tests are unreachable mid-session.
* Yahoo's daily **close** for NSE disagrees with broker feeds on maybe a third
  of names by 0.1–0.8%. This is the whole of the residual gap error in In Play
  and is unresolved.
* The intraday side has no reliable volume source. Free Yahoo intraday volume
  for NSE is often zero.
* `raw.githubusercontent.com` is reachable from a sandbox; `github.io` and
  Yahoo are **not**. Fetch published files at an explicit commit SHA — fetching
  by branch has twice served a stale copy from the CDN that looked fresh.

**A standing hazard:** TradingView Basic allows 2 chart connections and a small
number of indicators. Opening extra chart tabs has twice broken things — once
silently stopping the script from drawing at all.

---

## 7. Open position

Long 10 MCX at ₹3,400, delivery, opened 24 Sep 2026. Pivot ₹3,398 (bought
+0.06% past it). Stop ₹3,128. Target ₹4,077.60. Logged in the dashboard
journal. Entered against two of the system's own flags: the market filter read
CORRECTION with 9 distribution days, and it is a stage 3 base. The fundamentals
are strong — EPS +103% quarterly, sales +141%, ROE 47%, RS 85.

---

## 8. How to check any claim in here

```bash
python3 pine_parity.py            # the chart and the scanner still agree
python3 calibrate_shape.py        # where the shape cutoff came from
python3 explain_pattern.py MCX    # every gate for one stock, with numbers
python3 history_scan.py MCX --period 5y   # every past setup and its outcome
```

The repository's habit, worth keeping: when something looks wrong, get the two
numbers side by side before deciding which is broken. Every disagreement so far
has been either a rule bug or a feed difference, and guessing which was always
wrong.
