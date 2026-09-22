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
| 2. Keep stocks above both MAs and near their highs | `screen_stocks.py` | shortlist |
| 3. Find cup-and-handle bases and breakouts | `find_patterns.py` | `cup_handle_signals.csv` |
| 4. Pack it for the dashboard | `build_site.py` | `docs/data.json` |

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

## Changing the rules

| What | Where |
|---|---|
| Screening filters (distance from high, min volume, min price) | `screen_stocks.py`, the `screen()` defaults |
| Cup-and-handle rules (depth, length, handle, volume) | `find_patterns.py`, the `P` dictionary at the top |
| Run time | `.github/workflows/daily.yml` — cron is in **UTC**, so 13:00 UTC = 18:30 IST |

Push any change and the next run uses it.

## Running it locally instead

```bash
pip3 install -r requirements.txt
python3 update_nse_data.py && python3 screen_stocks.py && python3 find_patterns.py && python3 build_site.py
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
