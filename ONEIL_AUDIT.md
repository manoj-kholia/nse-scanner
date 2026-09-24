# Audit: this scanner against *How To Make Money In Stocks*

Source: William J. O'Neil, *How To Make Money In Stocks*, 2nd edition, 1995.
Every rule below is from that edition. Where a threshold in the code comes from
a later edition or from nowhere in the book, it says so — that distinction is
the whole point of the document.

Audited 24 Sep 2026. Three gaps were closed the same day and are marked FIXED;
the rest are open and ranked at the bottom by what they cost a delivery trader
on NSE.

---

## C — Current quarterly earnings

| Rule | Book | Us |
|---|---|---|
| Minimum current-quarter EPS growth vs the **same quarter a year earlier** | "not buy any stock that doesn't show earnings per share up at least 18% or 20%"; many use 25–30%; prefer 40–50%+ | `fundamentals.py` `EPS_Q_MIN = 25.0` ✅ |
| Compare like with like, never sequentially | ch.1 | `_yoy()` checks the **calendar** distance (~12 months ±3) and refuses to guess when the spacing is wrong ✅ |
| Sales growth confirming the EPS | — (he stresses it; the explicit 20–25% sales rule is a later edition) | `SALES_Q_MIN = 20.0` — honest extra, not from this edition ⚠️ |
| **Two** quarters each up materially | "You will be even safer if you insist the last two quarters each show a significant percentage increase" | ❌ not implemented — we look at one quarter |
| **Acceleration**, and deceleration as a warning | ch.1; sell rule 15 in ch.10 | ❌ not implemented |
| Omit one-time extraordinary gains | ch.1 | ❌ we take Yahoo's `Diluted EPS`/`Basic EPS` as reported |

## A — Annual earnings

| Rule | Book | Us |
|---|---|---|
| Each of the **last five years** up on the prior year | ch.2 (one down year tolerated if it recovers to new highs) | ❌ we compute an endpoint CAGR over up to **three** years (`span = min(3, len(eps_a) - 1)`). A company that fell for four years and had one recovery year passes us and fails him. |
| Compound annual growth 25–50%+ | ch.2 (winners' measured average 24%, median 21%) | `EPS_A_MIN = 25.0` ✅ |
| Earnings **stability** rank under 20–25 | ch.2 — "Growth stocks with good stability of earnings tend to show a stability figure below 20 or 25" | ❌ not computed |
| P/E is **not** a selection criterion | ch.2, and mistake #7 | ✅ absent from the code, deliberately |
| Return on equity ≥ 17% | **not in this edition** — it appears in later ones | `ROE_MIN = 17.0` ⚠️ flagged as an import, not a misreading |

## N — New products, new management, **new highs**

| Rule | Book | Us |
|---|---|---|
| Buy at or near new highs emerging from a base | ch.3 | ✅ the buy point is the right rim of the cup |
| Base-building 7–8 weeks up to 15 months | ch.3; "reliable base structures must have a minimum of six to eight weeks" (ch.15) | `base_min = 35` bars (7 wks) for cup + handle ✅; `cup_max = 250` bars (50 wks) is **tighter** than his 65 weeks ⚠️ |
| Never buy more than 5–10% past the exact buy point | ch.3, ch.5, ch.15 | `max_ext = 0.05` ✅ (stricter end of his range) |
| **Overhead supply** — never buy under a wall of trapped sellers | ch.15, "What Is Overhead Supply?" | ✅ **FIXED** — `overhead = 0.15` over `overhead_look = 252` bars in `find_patterns.find_cup()`, mirrored in `cup_and_handle_v3.pine` and `pine_parity.py`. The rim test could not catch this: it only compares the two rims with each other, so a textbook cup could sit 30% below a peak the stock made eight months ago. **15% is the conventional CANSLIM screen, not a figure from the book** — his "5% to 10% below a stock's former high point" is about the pivot versus the high of *this* base, which `rim_down` already enforces. |
| A major new product / management / industry change (>95% of winners) | ch.3 | ❌ not automatable from price data; no attempt made |

## S — Supply and demand

| Rule | Book | Us |
|---|---|---|
| Breakout volume at least **50%** above normal | ch.4, ch.15 | `vol_mult = 1.4` ⚠️ **looser than the book** |
| Volume dries up on corrections | ch.4, ch.15 | `handle_vol = 1.0` ✅ (mean handle volume ≤ the 50-day average) |
| "Extreme trading volume dry ups… near the lows in the price pullback phase of the handle" | ch.15 | ⚠️ partially — we use the handle **mean**, he describes the **lows** |
| Small capitalisation: >95% of winners under 25M shares, median 4.6M | ch.4 | ❌ shares outstanding / float not in the pipeline |
| Low debt-to-equity, falling | ch.4 | ❌ |
| Company buying back its own stock | ch.4 | ❌ |
| Avoid low-priced, illiquid stocks with no institutional following | ch.4, mistake #4 | ⚠️ `min_price = 20.0` and `min_vol = 50000` shares — a literal transplant of his \$20 floor. ₹20 ≈ \$0.24 and lets through exactly what he was excluding; 50,000 shares at ₹20 is ₹10 lakh a day. Rupee turnover is the faithful translation. |

## L — Leader or laggard

| Rule | Book | Us |
|---|---|---|
| RS Rating 80+; under 70 is a laggard; winners averaged 87 | ch.5 | `min_rs = 80` in `screen_stocks.screen()` ✅ |
| RS is **relative** — "outperformed 70% of the stocks in the comparison group" | ch.5 | ✅ `update_nse_data.rs_rating()` percentile-ranks the whole universe; it cannot be computed one stock at a time and we don't |
| The big winners read **90+** | ch.5 | ❌ we take a flat 80 floor with no preference above it |
| RS **line** sinking 7 months, or a sharp 4-month decline, is disqualifying | ch.5 | ❌ we use a single number, no trend |
| The stock must be in a base when you buy, not extended | ch.5 | ✅ |
| Buy among the best two or three in the group; avoid sympathy plays | ch.5 | ⚠️ partially — group rank exists now, within-group ranking does not |

## I — Institutional sponsorship

| Rule | Book | Us |
|---|---|---|
| At least a few quality sponsors, "three to ten" funds | ch.6 | ❌ nothing |
| Beware the "overowned" stock | ch.6 | ❌ nothing |

On NSE this is obtainable: quarterly shareholding patterns give FII/DII/MF holdings per symbol.

## M — Market direction

| Rule | Book | Us |
|---|---|---|
| Follow the general market averages daily | ch.7 | ✅ `market_filter.py`, Nifty 50 |
| Distribution: "heavy volume without further price progress" / a fall on higher volume | ch.7 | ✅ `distribution_days()`, −0.2% on rising volume over 25 sessions |
| 4 distribution days = pressure, 6 = correction | IBD convention, consistent with ch.7 | ✅ `DIST_PRESSURE`, `DIST_CORRECTION` |
| Distribution days should **expire** on a strong rally, not only by age | ch.7 in spirit | ❌ we only use the 25-session window |
| **Follow-through day** — day 4–7 of an attempted rally, index up 1%+ on higher volume, is the buy signal | ch.7, "How You Can Spot Stock Market Bottoms" | ❌ **not implemented**. We can tell you to stop buying and have no rule for starting again. |
| Leading stocks topping is the second-best market indicator | ch.7 | ❌ |

The verdict is published as a banner and **gates nothing** — signals go out in a
correction with the warning attached. Deliberate, and stated here so it is not
mistaken for an oversight.

---

## Chart construction (ch.15)

| Rule | Book | Us |
|---|---|---|
| Cup duration 7 to 65 weeks | ch.15 | `cup_min = 30` bars, `cup_max = 250` ⚠️ (rejects 50–65 week cups) |
| Depth 12–15% to 33%; up to 40–50% for a few volatile leaders; over 50% fails more | ch.15 | `depth_min = 0.12`, `depth_max = 0.35` ✅ |
| Depth relative to the market: normal is 1.5–2.5× the index decline, over 2.5× is "too wide and loose" | ch.15 | ❌ we have the Nifty data and don't use it |
| Bottom rounded, a "U" not a narrow "V" | ch.15 | ✅ mean normalised depth score, `round_min = 0.41`, calibrated in `calibrate_shape.py` |
| Handle forms in the **upper half** of the base | ch.15 | ✅ `h_low < mid` → "handle broke down" |
| Handle should be **above the 200-day line** | ch.15 | ⚠️ we check the last price and the breakout bar against the 200 DMA, not the handle low |
| Handle more than one or two weeks | ch.15 | `handle_min = 5` (1 week) ⚠️ looser |
| Handle depth contained within 10–15% | ch.15 | `handle_depth = 0.12` ✅ |
| Handle drifts **down**, with a shakeout below a prior low | ch.15 | ⚠️ drift ✅ (`handle_quality` slope + t-stat); **shakeout ❌** |
| Handles wedging upward fail more | ch.15 | ✅ |
| Volume dry-up near the lows of the base and the handle | ch.15 | ⚠️ see S above |
| Tight price areas; "wide-and-loose price structures… almost always fail" | ch.15 | ❌ **no tightness measure anywhere** |
| Prior uptrend of at least **30%** before the base | ch.15, under saucer-with-handle | `prior_pct = 0.25` ⚠️ **looser than the book** |
| 3rd/4th stage bases are late and failure-prone | ch.10 rule 30, ch.15 | ✅ `base_stage()` counts and the dashboard shows it; flagged, never dropped |
| Buy exactly at the pivot | ch.15 | ✅ |

## Selling and risk (ch.9, ch.10)

| Rule | Book | Us |
|---|---|---|
| Cut every loss at 7–8% below **what you paid**, absolute limit | ch.9 | ✅ `oneil_stop()` = `max(handle_low, entry × 0.92)` — correctly the **tighter** of the structural level and the 8% rule |
| Do not confuse that with 7–8% off the peak | ch.9 | ✅ measured from entry |
| Take profits at **20–25%** off the pivot | ch.10, "A Revised Profit-and-Loss Plan" | ✅ **FIXED** — `profit_take = 0.20`, `profit_take_max = 0.25`. Previously we published `buy + (buy − cup_low)`, a measured move, which is a chartists' convention and not in the book: it gave +13.6% on a 12% cup (selling below his threshold) and +54% on a 35% cup. The depth of a base does not predict the size of the move. The old number is still shown as `Measured_Move`, labelled as not-a-rule. |
| Exception: 20% inside eight weeks → hold the eight weeks | ch.10 | ✅ **FIXED** — `hold_sessions = 40`, surfaced as `Hold_Days_Left` and in the dashboard tooltip |
| Sell your worst stock first | ch.5 | n/a — position management, not a scanner function |
| Sell when RS drops below 70 | ch.10 rule 36 | ❌ no position monitoring |
| Climax top, largest one-day drop, 200 DMA turning down, etc. (ch.10 rules 3–35) | ch.10 | ❌ none of the sell-side monitoring exists |

## Industry groups (ch.18)

| Rule | Book | Us |
|---|---|---|
| "37% of a stock's price movement is due to subgroup influence and 12% to major group influence" | ch.18 | ✅ **FIXED** — `industry_groups.py` |
| Winners' median group rank 61/200, i.e. the top third; Ryan's picks top 30% | ch.18, ch.14 | ✅ groups percentile-ranked 1–99, `LEADING = 70`, shown on the dashboard and flagged in the scan output |

The map is built from Yahoo one symbol at a time and cached in `sector_map.csv`,
which is committed; each run tops up 250 uncached names, so the full universe
fills in over about two weeks and the daily cost then falls to nothing. Its
limits are in that module's docstring and they are real: Yahoo's industries are
US-shaped categories, ~150 of them rather than his 200 subgroups, patchy for
Indian small caps, and a group with fewer than 4 mapped members is named but
**not** ranked. A missing rank shows as "–", never as weakness.

---

## What is still open, ranked by cost

1. **No follow-through day.** We can say "stop buying" and have no rule for
   resuming. In practice this is the difference between re-entering at the
   bottom of a correction and re-entering three weeks late.
2. **The "A" test can pass a broken company.** A 3-year endpoint CAGR instead of
   five consecutive up years, and no stability rank.
3. **Only one quarter is checked for "C"**, with no acceleration test and no
   deceleration warning.
4. **No tightness / wide-and-loose measure.** He calls this the characteristic
   that makes patterns "almost always fail", including measuring the base
   against the index's own decline — which we have the data to do.
5. **Three loose calibrations:** prior advance 25% vs his 30%; breakout volume
   1.4× vs his 1.5×; handle minimum 1 week vs his "more than one or two".
6. **No handle shakeout test** (undercut of a prior low).
7. **"I" is entirely absent** — institutional sponsorship.
8. **"S" is mostly absent** — shares outstanding, debt-to-equity, buybacks.
9. **RS line trend unchecked**, and no preference for 90+ over 80.
10. **₹20 / 50,000-share floors** are a 1995 US-dollar rule transplanted
    literally; rupee turnover is the honest equivalent.
11. **Handle low is not checked against the 200 DMA**, only the last price and
    the breakout bar.
12. **Distribution days never expire on a rally**, only by age.
13. **No sell-side monitoring** of open positions at all.

## What the fixes changed

| | Before | After |
|---|---|---|
| Target | `buy + (buy − cup_low)`, drifting +13.6% to +54% with cup depth | `buy × 1.20`, with `Target_Max` at 1.25 and the 8-week hold exception surfaced |
| Buy point vs the 52-week high | unchecked | must sit within 15% of it (`overhead`) |
| Industry | not in the pipeline | group rank 1–99 on every signal, top 30% flagged |

`pine_parity.py` reports **0 disagreements** over 5,208 generated series after
these changes, with the overhead-supply branch firing on 154 of 640 sampled
series — so the new gate is genuinely exercised, not merely present.

## What the 10-stock audit found afterwards

Run against real Yahoo data on 24 Sep 2026 (`explain.yml`, symbols NPST
SOMANYCERA MCX SPLPETRO ANDHRSUGAR MANINDS OPTIEMUS SUBEXLTD MARINE SANSERA):

1. **The overhead gate was set too tight, by me, that morning.** `rim_down`
   lets the right rim sit 8% under the left rim, and the left rim is itself
   inside the 52-week window — so an ordinary cup already reads "8% under the
   52-week high" before any older peak is considered. MCX measured 2% under a
   "52-week high" that *was its own left rim*. At a 10% budget the two rules
   were fighting each other. Raised to 15%.
2. **The handle-drift line in `explain_pattern.py` contradicted itself**,
   printing `handle drifts DOWN, not up  +0.184%/day  needs <= 0.0  PASS`. The
   rule is a conjunction — a handle wedges only if it rises *and* the rise
   beats its own standard error — and printing half a conjunction as though it
   were the whole test makes a correct verdict look like a bug. Now one line.
3. **The report never printed the stop.** The one number O'Neil calls
   unbreakable was missing from the page whose job is to explain the decision.

Known live consequence: NPST's buy point (1775.00) sits 20% under its 52-week
high of 2208.60, so it is rejected even at 15% and drops off the list. That is
the gate working as intended, not a defect.
