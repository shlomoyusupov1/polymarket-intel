# ETF paper study v2 — pre-registered protocol (2026-10-16)

A new, separate prospective study. **The v1 holdout (repo `ai-trading-room`) stays FAILED**
(three missed sessions); nothing in v1 is modified or re-scored.

## Why v2 exists
v1 could not trade by construction:
1. Whole-share sizing: $1,000 × 20% cap = $200 < one QQQ share (> $700) → `integer_size_zero`.
2. The full-policy health gate required reconciliation / quote-freshness / clock / correlation
   attestations the collector never produced → 100% `risk_health_unavailable` vetoes.

## Frozen design (code: `app/pmintel/etf_paper.py`, constants in `PROTOCOL`)
- Window: sessions **2026-10-19 → 2027-04-16** (first intent sealed after the 2026-10-16 close).
- Accounts: `trend`, `breakout` — **$3,000 each**, isolated; benchmark SPY buy-and-hold $3,000.
- Universe, signals, risk limits, stops and costs **unchanged from v1**: SPY QQQ IWM EFA EEM IEF TLT;
  trend = close > SMA200 and 126-day momentum > 0 (exit below SMA200); breakout = close > SMA200 and
  > prior 55-day high (exit below prior 20-day low); ≤5 positions, ≤20% single, ≤60% gross, 0.25%
  risk per trade, stop 2×ATR20, 126-session time exit, 5 bp per side.
- Changes vs v1 (the only ones): capital $3,000; fractional quantities in 0.001-share steps, min
  order $10; health gate = checks this server can perform: complete valid bars for all 7 symbols
  on the decision day, intent sealed before 09:30 New York of the next session, server clock within
  60 s of an external HTTP `Date` header. Plus v1's drawdown halt (−8% from peak, no new entries for
  the rest of the window) and daily-loss limit (−1%).
- Data: Yahoo Finance chart API, raw daily OHLCV and dividends; bars are stored **first-seen** and
  never overwritten; intents are stored once with a SHA-256 of the canonical payload.
- Missed/late intent: no new entries that session (recorded); exits and stops still apply,
  computed only from bars through the prior session.
- Dividends are credited on the ex-date (approximation; payable dates are not modelled).
- Results are **visible during the window** (unlike v1). Seeing them does not permit any change:
  a discovered correctness bug ends v2 and requires a new pre-registered v3.
- No broker connection and no orders exist anywhere in this code.

## What it can and cannot show
Six months of two rule sets on 7 ETFs is a small sample; it cannot prove an edge. It answers:
can these rules trade at a realistic size, and how do they compare with simply holding SPY,
after costs.
