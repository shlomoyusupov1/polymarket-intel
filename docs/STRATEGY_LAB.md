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
Five strategies with small grids fixed in advance: breakout with volume, dip after selling exhaustion,
stacked moving averages (all from the course), near the 52-week high (George & Hwang 2004), and a
2-day RSI pullback in an uptrend (short-term reversal).

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

## Running on the server
```bash
cd ~/polymarket-intel && git pull && sudo docker compose up -d --build
sudo docker compose run --rm stocklab      # a few minutes; prints one line per strategy
```
Results: page **מעבדה** (`#/lab`). The screenshots in `docs/screenshots/lab-*.png` use synthetic data with
a planted edge — they show the layout, not a finding.
