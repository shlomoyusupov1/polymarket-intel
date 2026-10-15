# UI redesign — milestone 1 (2026-10-16)

## Audit of the v0.1 interface (before)

| # | Problem | Impact |
|---|---|---|
| 1 | Four flat tabs; no information architecture for the 9 product areas | users can't find research, health or opportunities |
| 2 | Home page answered none of the key questions (what changed, any supported edge, data freshness) | the most important screen carried the least decision value |
| 3 | "Movers" relied only on our own snapshots → empty for the first 24 h | first impression was an empty page |
| 4 | Market detail was a modal with one chart of stored snapshots, no outcome prices, no live book, no rules | not enough to evaluate a market |
| 5 | Only the reference outcome's price was shown; multi-outcome markets were unreadable | misleading for non-binary markets |
| 6 | Dense tables on iPhone; mixed RTL/LTR numbers rendered in the wrong order | hard to read on mobile |
| 7 | No empty/coming-soon states — missing features were silently absent | unclear what is real |
| 8 | No watchlist, no deep links (no URL per market) | can't return to a market or share it |

## What changed

**Shell** — desktop sidebar (9 areas, "בקרוב" marks unbuilt ones), iPhone bottom navigation
(5 tabs + "עוד"), sticky top bar with a live freshness pill, hash routes (`#/m/<id>` deep links),
skip link, visible focus rings, reduced-motion support.

**Command Center** answers the five questions directly:
1. *What changed in 24 h* — Polymarket's own 1-day change (labelled as such) + our snapshot movers once available.
2. *What attracts attention* — top 24 h volume among tradable markets.
3. *Any statistically supported opportunity* — a one-word answer from the historical research and the
   structural scanner, with counts and report age.
4. *Data freshness/reliability* — price age, book age and validity %, last full scan, resolved-market count.
5. *Investigate next* — an explicit, labelled filter (sharp move + high volume), "not a recommendation".

**Market Explorer** — search, sort (24 h volume, daily change, liquidity, closing soon), liquidity floor,
"tradable only", ★ watchlist, paging.

**Market Detail** — every outcome with its price, live CLOB price chart with 1D/1W/1M/All and a
hover/touch cursor, volume/liquidity/bid-ask/spread/daily change, live order book (executable average
price to buy/sell $100 and $1,000, 8 levels of depth per side, validity), data-quality assessment,
recommendation card (WAIT, why, limitations), original resolution rules and source link.

**Opportunity Scanner** — mathematical → theoretical → live-checked → executable funnel, the "why
theoretical ≠ executable" explanation, every live-checked candidate with its 100-share result and blockers.

**Research Lab** — reliability diagram (price vs realised frequency with Wilson CIs, dot size = n) and the
strategy table per horizon. **System Health** — job table with errors, integration-check results.
**Paper Portfolio / Alerts** — honest "not active" pages that state what is required before they exist.

## API additions (read-only, existing endpoints unchanged)

- `GET /api/overview` — extended (`changed`, `attention`, `opportunities`, `freshness`, `own_movers`).
- `GET /api/markets?ids=…&tradable=…&sort=change` — watchlist and new sort.
- `GET /api/markets/{id}/history?range=1d|1w|1m|max` — live CLOB history, 60 s cache, 502 on upstream failure.
- `GET /api/markets/{id}/book` — live CLOB book summary, 30 s cache.
- `GET /api/report/latest` — latest structural/universe research report.

## Verification (Playwright, Chromium)

Local stack: real API + Postgres seeded through the real pipeline with **clearly labelled `[TEST]`
markets** and a mock CLOB (no internet in the build sandbox). Screenshots in `docs/screenshots/` are
therefore test data, not Polymarket data.

| Check | Desktop 1440×900 | iPhone 390×844 |
|---|---|---|
| JS errors (excluding the blocked web font) | 0 | 0 |
| Horizontal overflow on 7 pages | none | none |
| Buttons/links without an accessible name | 0 | 0 |
| Interactive elements nested inside links | 0 | 0 |
| Watchlist add → list | ✅ | — |
| Frontend payload (HTML+CSS+JS, uncompressed) | 55 KB, no dependencies | same |

Security: all market text is inserted with `textContent`; external links pass a protocol allow-list
(http/https) and open with `rel="noopener noreferrer"`; the site stays behind Caddy Basic Auth.

Known limitations: watchlist is per browser (localStorage); the chart shows the reference outcome only;
Heebo is loaded from Google Fonts (falls back to system fonts if blocked).
