# NSE Base Scanner

A personal, automated screener for NSE delivery trading. Every weekday evening it
fetches prices for ~2,300 stocks, keeps the ones in an uptrend near their 20-week
high, looks for cup-and-handle bases, and publishes the result as a web page you
can open from any device.

Runs entirely on GitHub's free tier. Nothing to install, no server, no cost.

---

## What it does each evening (18:30 IST)

| Step | Script | Output |
|---|---|---|
| 1. Fetch prices, 20W/52W high-low, volume, 50 & 200 DMA | `update_nse_data.py` | `EQUITY_L_live.csv` |
| 2. Keep RS 80+ leaders above both MAs, near their highs | `screen_stocks.py` | shortlist |
| 3. Judge market health from the Nifty 50 | `market_filter.py` | `market.json` |
| 4. Find cup-and-handle bases, count base stage, check earnings | `find_patterns.py` + `fundamentals.py` | `cup_handle_signals.csv` |
| 5. Pack it for the dashboard | `build_site.py` | `docs/data.json` |

The dashboard is `docs/index.html`. Click any symbol to open its daily chart in
TradingView.

---

## One-time setup

### 1. Create the repository

On [github.com](https://github.com) click **New repository**, name it
`nse-scanner`, and make it **Public**. (Public is required for free GitHub Pages.
Only market data and your filter settings are stored here — no personal
information and no broker access.)

### 2. Push this folder

In Terminal:

```bash
cd ~/Trading/nse-scanner
git init -b main
git add .
git commit -m "NSE base scanner"
git remote add origin https://github.com/YOUR-USERNAME/nse-scanner.git
git push -u origin main
```

Replace `YOUR-USERNAME`. If git asks for a password, use a
[personal access token](https://github.com/settings/tokens) instead — GitHub no
longer accepts account passwords.

### 3. Turn on Pages

Repository → **Settings** → **Pages** → under *Build and deployment*, set
**Source: Deploy from a branch**, **Branch: `main`**, **Folder: `/docs`** → Save.

Your dashboard appears at:

```
https://YOUR-USERNAME.github.io/nse-scanner/
```

Bookmark it on your phone's home screen.

### 4. Allow the workflow to commit

Repository → **Settings** → **Actions** → **General** → *Workflow permissions* →
select **Read and write permissions** → Save.

### 5. Run it once by hand

Repository → **Actions** tab → **Daily NSE scan** → **Run workflow**. It takes
5–10 minutes. When it finishes, reload your Pages URL and the data is there.

After that it runs itself every weekday at 18:30 IST.

---

## What of CANSLIM is here

| | Rule | Status |
|---|---|---|
| **C** | Current quarterly EPS +25%, sales +20% | `fundamentals.py`, on the signals — **flagged, not filtered** |
| **A** | Annual EPS +25%/yr, ROE 17%+ | same |
| **N** | New high out of a sound base | the cup-and-handle detector |
| **S** | Supply and demand — volume | breakout volume 1.4×, volume dry-up in the handle |
| **L** | Leader, not laggard | RS Rating 80+ |
| **I** | Institutional sponsorship | **not possible** on free NSE data |
| **M** | Market direction | `market_filter.py` |

Base stage counting sits alongside these: the first base after a deep
correction is the one that works, and a 3rd or 4th stage base is flagged as
higher risk.

## The O'Neil rules this enforces

**RS Rating (the "L" in CANSLIM).** Each stock's weighted 3/6/9/12-month price
performance is percentile-ranked 1-99 against the whole 2,300-stock universe.
O'Neil treats 80+ as leadership and under 70 as a laggard, so the screen keeps
only 80+. Change it with `--min-rs`, or pass `--min-rs 0` to switch it off.

**Market direction (the "M").** Most stocks follow the market, so breakouts
bought during a correction tend to fail. `market_filter.py` reads the Nifty 50
and returns one of three verdicts, shown as a banner on the dashboard:

| Verdict | Meaning |
|---|---|
| CONFIRMED UPTREND | above the 50 and 200 DMA, 50 DMA rising, under 4 distribution days |
| UNDER PRESSURE | trend intact but institutions selling - trade smaller |
| CORRECTION | below the 50 DMA or 6+ distribution days - do not buy breakouts |

A *distribution day* is a session where the index fell 0.2% or more on higher
volume than the day before. Signals are still listed during a correction - the
tool tells you the market is hostile rather than hiding them.

**The stop, and the Risk column.** O'Neil's one unbreakable rule is to sell at
7-8% below what you paid. The `Stop` column used to show the handle low, which
sounds right but is not: on a loose handle that low can sit 10-12% under the
buy point, so "the stop" quietly committed you to a loss half again bigger than
the rule allows. The stop is now the tighter of the handle low and 8% below the
price you would actually pay - the buy point while a handle is forming, the
breakout close once it has broken out. `Risk` shows what that stop costs you,
and turns red when the handle low is below it, which is the tool telling you
the base is loose and the 8% rule is carrying the stop rather than the chart.

**Base stage (the Base column).** O'Neil counts bases from the point a stock
emerges after a severe decline. The first base off that low is the one that
works; by the third and fourth everybody can see the move and the failure rate
climbs. The count resets at the last bar that closed 30%+ under its running
peak, then steps forward to the actual bottom; from there a base starts when
price closes 12% off a running high and ends when it closes back above that
high, and only counts if it lasted five weeks or more. Stage 3 is amber,
stage 4+ is red. With two years of history **the number is a floor** — a stock
that has been advancing for three years may really be later-stage than this
says, because the earlier bases are off the edge of the data. `--max-stage 3`
drops the late ones instead of flagging them.

**Earnings, the C and the A (the Earnings column).** This is the half of
O'Neil's method the chart cannot tell you: a cup-and-handle on a company whose
earnings are shrinking is a trap. `fundamentals.py` pulls quarterly and annual
statements for the signals only — a handful of lookups, not 2,300 — and grades
them:

| Grade | Means |
|---|---|
| PASS | three or more of the four tests are visible and all of them clear |
| FAIL | something visible falls short; the note says what and by how much |
| THIN | what's visible passes, but there's too little of it to trust |
| NO DATA | nothing usable came back — **look it up yourself** |

Hover the grade for the numbers behind it. Be aware of the coverage gaps:
Yahoo's free fundamentals are reasonable for large NSE names and patchy for
small caps, sometimes a quarter stale, and its quarter alignment for Indian
companies is not always right — so a quarter is only compared with a column
roughly four quarters earlier, and the comparison is refused when the spacing
looks wrong. A loss that turned into a profit is reported as "loss to profit"
rather than an invented percentage. Nothing is dropped for missing data;
`--require-earnings` turns FAIL into a filter if you want it to be one.

## The intraday side (separate, and much weaker evidence)

`intraday.py` runs at **09:27 IST** each weekday and produces a *stocks in play*
shortlist: the liquid names that opened on abnormal volume with a real gap.
`build_intraday.py` publishes it to the **In Play** tab.

**Why 09:27 and not 09:20.** Yahoo rewrites the 09:15-09:20 bar's volume to
**zero** once a session becomes history, while today's live 09:15 bar carries
its real number. Reading that bar compares today against a history of zeros and
rejects everything — which is exactly what happened on the first live runs. So
price comes from the 09:15 bar (reliable) and volume from the **09:20-09:25**
window, selected by clock time so a missing bar cannot shift it. That bar has
to have closed before the run, hence 09:27.

### It was tested here, and it has no demonstrated edge

`backtest_orb.py` runs the whole thing on your own universe. Run it yourself
from the **Actions → Backtest the opening-range breakout → Run workflow**; the
table lands in the run summary. Measured 23 Sep 2026 — 50 liquid NSE names,
60 sessions, 248 qualifying days of which 72% triggered (**179 trades**),
exiting at the close:

| Stop | Stopped out | Fees only: Avg R | 95% CI | + slippage: Avg R | 95% CI |
|---|---|---|---|---|---|
| **0.10 ATR** *(the US paper's)* | 93% | **−0.544** | [−1.10, +0.01] | **−0.905** | [−1.46, −0.35] |
| 0.25 ATR | 64% | +0.174 | [−0.20, +0.55] | +0.029 | [−0.35, +0.40] |
| **0.50 ATR** *(now the default)* | 33% | +0.204 | [−0.03, +0.44] | +0.132 | [−0.10, +0.37] |
| 0.75 ATR | 17% | +0.125 | [−0.04, +0.29] | +0.076 | [−0.09, +0.24] |
| 1.00 ATR | 8% | +0.086 | [−0.04, +0.21] | +0.050 | [−0.07, +0.17] |

**Two columns, on purpose.** The left one counts brokerage and taxes only
(0.082% round trip); the right adds 0.05% a side of assumed slippage (0.182%).
Slippage is a guess, not a fee, and the guess decides whether the tight stop is
merely bad (t = −1.93, interval touching zero) or significantly loss-making
(t = −3.21). Hiding that inside a default would be the whole problem, so the
script prints both every run. You cannot actually trade at zero slippage —
you cross a real spread on a stock that just gapped — so the truth sits nearer
the right-hand column.

On a 3%-ATR NSE stock the 0.10 stop is only ~0.3% wide, so fees ate most of the
risk budget before the trade had a chance. Widening it removes the bleed, but
**every positive interval still crosses zero — no edge was demonstrated**.

How far these trades travel, at the 0.50 stop: 0.5 ATR reached on **38%**,
1 ATR on **18%**, 1.5 ATR on 7.9%, 2.5 ATR on 3.4%. That is the ceiling any
target has to live under. Re-run the workflow as sessions accumulate and see
whether it changes; treat the tab as a watchlist until it does.

**Read this before using it.** Zarattini, Barbon and Aziz tested a 5-minute
opening range breakout on 7,000+ US stocks, 2016-2023. Unfiltered it returned
**3.2% a year** — nothing. Restricted to the 20 stocks each day with the most
abnormal opening volume it returned **41.6%**. The breakout rule was almost
worthless; the *selection* carried the result. That is why this module spends
its effort on the shortlist and treats the entry as the simple part.

It is **not backtested**. Free intraday data reaches back about 60 days, which
is an anecdote, not a sample. A real test needs years of minute bars — a paid
broker API (Kite Connect, Dhan, Fyers) or a data vendor.

SEBI's July 2024 study of the equity cash segment found **71% of individual
intraday traders lost money** in FY23, rising to **80%** for those trading 500+
times a year. The finding that shaped this code: loss-makers paid transaction
costs worth an **extra 57% of their losses**, while profit-makers spent 19% of
their profits. So `trading_costs.py` puts a break-even number on every row —
what the round trip costs, and what share of a normal day's range that eats.

| Column | Means |
|---|---|
| Rel Vol | today's first five minutes against the **median** of the last 14 sessions |
| Gap | open against yesterday's close |
| ATR % | how far the stock travels on a normal day |
| OR High / Low | the opening range — the 09:15–09:20 bar |
| Stop % | a stop at 50% of the 14-day ATR — see the test above for why not 10% |
| Breakeven | round-trip cost as a % of the position |
| Cost / ATR | what share of a normal day's range the costs eat — amber past 8%, red past 15% |

Settings live in the `P` dict at the top of `intraday.py`; broker rates live at
the top of `trading_costs.py` and **should be checked against your own contract
note**, because if your rates are higher every number here is optimistic.

## The trade journal

The **Journal** tab logs what you actually did, and works out what it actually
cost. Add a trade and it computes gross, the real charges (brokerage, STT,
exchange, stamp duty, GST), net, and **R** — net divided by the money truly at
risk, which is the stop distance *plus* costs. Sizing off the stop alone
understates every loss; on a typical signal the costs are around 60% of the
intended risk again.

One deliberate difference from the screen: **no slippage is added here.** The
In Play tab assumes 0.05% a side because it is guessing at a fill that has not
happened. The journal uses the price you actually got, which already contains
the slippage — adding it again would count it twice.

The tile to watch is **Cost share**: costs as a percentage of gross profit.
SEBI found profit-makers spent 19% of their gross profit on costs and
loss-makers paid an extra 57% on top of their losses. If yours is drifting
toward the second number, the trades are too small, too frequent, or both.

On the In Play tab each row has a **log** button that jumps to the journal with
the symbol, side, trigger and stop already filled from the opening range.

Trades live in that browser's local storage — nothing is uploaded, and nothing
syncs between your phone and your Mac. **Export CSV** is the backup and the way
to move them across; **Import CSV** reads it back.

## Changing the rules

| What | Where |
|---|---|
| Screening filters (distance from high, min volume, min price) | `screen_stocks.py`, the `screen()` defaults |
| Cup-and-handle rules (depth, length, handle, volume) | `find_patterns.py`, the `P` dictionary at the top |
| RS floor | `--min-rs` on `screen_stocks.py` and `find_patterns.py` |
| Earnings thresholds | `fundamentals.py`, the constants at the top |
| Base stage limit | `--max-stage` on `find_patterns.py` (0 = flag only) |
| Market thresholds (distribution days) | `market_filter.py`, the constants at the top |
| Run time | `.github/workflows/daily.yml` — cron is in **UTC**, so 13:00 UTC = 18:30 IST |

Push any change and the next run uses it.

## Running it locally instead

```bash
pip3 install -r requirements.txt
python3 update_nse_data.py && python3 screen_stocks.py && python3 market_filter.py && python3 find_patterns.py && python3 build_site.py
open docs/index.html
```

---

## Things worth knowing

- **Data source is Yahoo Finance**, free and unofficial. It covers ~99.9% of NSE
  EQ symbols (2,316 of 2,317 on the first run) but is not exchange-grade. Fine
  for screening; don't use it for execution prices.
- **Rate limiting.** The workflow paces requests at 2 seconds between batches of
  60. If a run comes back with many blanks, raise `--pause` in the workflow.
- **The V-shape filter is imperfect.** A sharp V-bottom is rejected cleanly, but a
  wide gentle V and a true rounded cup score too close together to separate
  reliably. Always confirm the shape on the chart before acting.
- **This is a screening tool, not advice.** The ranking score is a sorting
  convenience, not a signal.
