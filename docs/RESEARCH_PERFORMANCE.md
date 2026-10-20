# Financial Intelligence: one scoreboard, two engines (2026-10-19)

The project is a financial research platform with two equal research engines — stocks/ETF and
Polymarket — plus measured tip sources. Nothing places trades.

## Research performance (`/api/performance`, page `#/performance`)
`pmintel/performance.py` turns every engine's own output into one schema: engine, kind
(`historical` out-of-sample test · `forward` paper signals sealed before the fact · `measured` tip
sources), sample size, success vs the benchmark after costs, mean net result with its 95% lower bound,
benchmark, a normalised status (passed / promising / failed / collecting) and a one-line reason.
A strategy that is positive forward but failed its historical test is never "passed".

## Stocks overview (`/api/stocks/overview`, page `#/stocks`)
SPY, QQQ, IWM and the 11 SPDR sector ETFs from daily bars (1 h cache): 1w / 1m / 3m / YTD / 1y,
relative to SPY, above/below the 200-day average; open lab signals; latest insider purchases.
Navigation: היום · מניות (סקירה, המניות שלי, בדיקת מניה, מעבדה, מקורות, חדר מסחר) · Polymarket · ביצועים.

## Polymarket taker fees in research
Official schedule (docs.polymarket.com/trading/fees): fee = shares × rate × p × (1 − p), rate by
category (crypto 7%, sports/economics/culture/weather/other 5%, politics/finance/tech/mentions 4%,
geopolitics 0). As a share of the stake that is rate × (1 − p): buying at 10¢ costs 3.6% in politics.
The fee is taken in shares, so it shrinks a winner's payoff; a loser still loses exactly the stake.
Applied to all historical markets (conservative; what the same trade costs today).

## Research states (one vocabulary on every screen)

| State | Meaning |
|---|---|
| Forward Confirmed | passed pre-registered criteria on signals sealed before the fact **and** passed the historical test. Still not a promise of real-money profit |
| Forward Validation | being tracked forward with paper money; no conclusion yet |
| Historical Candidate | passed out of sample on past data only — a hypothesis |
| Not Supported / Failed | no edge after costs, or the forward gate failed |
| Insufficient Data | too few observations for any conclusion |

A historical result alone can never reach "confirmed". A Polymarket rule that is positive on mid prices
is at most a candidate until an executable ask confirms it. Each entry lists the criteria still missing.

## Production evidence

    bash scripts/evidence.sh             # deployed commit vs main, containers, 72h jobs and errors, data, backups, research
    bash scripts/evidence.sh --research  # first runs the stock lab, crypto lab and Polymarket research
