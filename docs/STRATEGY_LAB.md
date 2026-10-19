# Strategy lab and stricter source measurement (2026-10-18)

## Why
A self-review found three ways the system could fool us:
1. **Hit rate is not profit.** 60% small wins and 40% big losses lose money. The gate now also reports
   average win, average loss, profit factor, and requires a positive average edge after cost.
2. **0.2% cost was too low for cheap stocks.** Each tip now pays its own estimated round-trip cost:
   the Corwin–Schultz (2012) bid-ask spread estimated from the 20 daily high/low bars before entry,
   plus 0.1% fees (`pmintel/costs.py`). No data → conservative tiers (6% under $1, 2% under $5).
3. **Many sources → one looks good by luck.** Edge p-values are Holm-corrected across all sources with
   ≥ 30 measured tips; a source that fails the correction is shown as "promising", not "reliable".

## Strategy lab (`pmintel/stocklab.py`)
Eight strategies with small grids fixed in advance: breakout with volume, dip after selling exhaustion,
stacked moving averages (all from the course), near the 52-week high (George & Hwang 2004), and a
2-day RSI pullback in an uptrend (short-term reversal), 12-1 momentum (Jegadeesh & Titman 1993, top q% of
the universe on the first trading day of the month), low volatility (Frazzini & Pedersen 2014, calmest q%
by 60-day volatility, monthly) and turn of the month (Lakonishok & Smidt 1988, buy three trading days
before month end). Cross-sectional ranks use only that day's values across the universe.

- 65 liquid large caps + SPY, ~10 years of split/dividend-adjusted daily bars from Yahoo.
- Entry next open, exit at the close after 5/10/20 sessions, per-stock estimated cost, no overlapping
  trades in the same symbol.
- Walk-forward by year: parameters chosen on earlier years only (with an embargo), scored on the next;
  the first 3 years only train.
- Survivorship: the universe is today's survivors, so every trade is also compared with buying any
  universe stock on any day with the same hold in the same year (baseline). The edge that counts is
  over that baseline.
- CI clustered by entry week; Holm across strategies.
- Verdicts: `candidate_for_paper` (≥ 50 OOS trades, ≥ 60% beat SPY after cost with Wilson LB > 50%,
  baseline edge LB > 0, Holm p < 0.05), `edge_below_target`, `not_supported`, `insufficient_data`.
  A backtest never yields more than "candidate": forward paper tracking comes next.

Tests (`tests/test_stocklab.py`): signals use no future data; random markets find nothing; a planted,
tradable edge is found out of sample; split adjustment; costs, Holm and clustered CI.

## Forward paper tracking (`pmintel/lab_paper.py`, service `labpaper`)
Every weekday after 17:15 New York, for each strategy with the parameters the lab chose last: the
signals on the completed bar are written, timestamped and SHA-256 sealed — only if that happens before
the next 09:30 open. Entry next open, exit after the same hold, same per-stock cost, vs SPY on the same
sessions; one open position per strategy and symbol. Returns are computed from one adjusted series at
exit (dividends counted). A strategy is "confirmed forward" only with ≥ 50 closed trades, ≥ 60% beating
SPY after cost with Wilson LB > 50%, a positive week-clustered mean lower bound, **and** a passing
backtest. Shown on the lab page (`#/lab`). Tests: `tests/test_lab_paper.py`.

## Running on the server
```bash
cd ~/polymarket-intel && git pull && sudo docker compose up -d --build
sudo docker compose run --rm stocklab      # a few minutes; prints one line per strategy
# the labpaper service starts with `up -d` and records signals from the next US close
```
Results: page **מעבדה** (`#/lab`). The screenshots in `docs/screenshots/lab-*.png` use synthetic data with
a planted edge — they show the layout, not a finding.

## Crypto (cryptolab.py)

The same strategies (except turn-of-month), the same walk-forward, baseline, Holm correction and gate,
on 20 large coins (Yahoo `XXX-USD` daily bars), against **holding bitcoin** over the identical days.

| | Stocks | Crypto |
|---|---|---|
| Bars | trading sessions | calendar days (UTC) |
| Holds | 5 / 10 / 20 sessions | 7 / 14 / 30 days |
| Entry | next open | open one full day after the signal bar (the next bar opens the instant the signal bar closes) |
| Cost | per-stock spread estimate + 0.1% | fixed round trip, `CRYPTO_COST_RT` (default 1%) |
| Benchmark | SPY | BTC-USD |

Forward paper tracking shares `lab_signals` (strategy names prefixed `crypto:`); the `labpaper` service
seals crypto signals after 00:15 UTC each day, before the entry bar opens. No wallet, keys or exchange
account are involved.

    sudo docker compose run --rm cryptolab

## Candlestick patterns

Seven classic patterns, each with a small grid fixed in advance, run in both labs (stocks and crypto)
through the same walk-forward, baseline, Holm correction and gate:

| Pattern | Context | Side |
|---|---|---|
| Hammer | after a down move (close < 20-day average and < 5 days ago) | buy |
| Bullish engulfing | after a down move; variant: only above the 200-day average | buy |
| Doji | after a down move | buy |
| Morning star (3 bars) | after a down move | buy |
| Three white soldiers | — | buy |
| Shooting star | after an up move | warning |
| Bearish engulfing | after an up move; variant: only below the 200-day average | warning |

A **warning** pattern is scored as how much the asset lagged the benchmark after the signal, minus the
same cost: what not buying (or selling what you hold) would have saved. No short sale is assumed.
Published research (e.g. Marshall, Young & Rose 2006 on US large caps) found little value after costs;
the lab tests that claim on our own data.
