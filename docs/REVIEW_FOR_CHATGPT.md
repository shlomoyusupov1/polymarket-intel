# Handoff for independent review (ChatGPT) — 2026-10-16

Written by Claude for the project owner's reviewer. Every number below is from real data
or a run described here; anything not verified is marked as such.

## 1. Polymarket platform (this repo) — production state

- Server: Oracle Cloud A1 (Jerusalem), 2 OCPU / 12 GB, Docker Compose, Caddy HTTPS + Basic Auth.
- Live URL `https://84-13-67-238.sslip.io` — verified from the server itself: `/api/health` returns **401**
  without credentials (TLS valid, auth enforced). Owner logged in from a browser successfully.
- All 5 services up (db and api healthy).

### Real-API integration check (`python -m pmintel.validate`, run on the server)

| Check | Result | Finding |
|---|---|---|
| gamma_open_first_page | PASS | `/markets` returns at most **100 rows per page** even with `limit=500` |
| gamma_full_open_scan | PASS | via `/markets/keyset`: **291,387 rows, 0 duplicates**, ~5 min |
| gamma_deep_offset | PASS | offset paging **returns HTTP 422 above ~2,000** → discovery moved to keyset |
| clob_price_history_resolved | PASS | history exists for **4 of 10** recently resolved markets (partial coverage) |
| clob_order_books | PASS | **5/5** books valid (bid < ask) |

The first run (before the keyset fix) failed 2/5 on the 422 offset cap — mocks had not caught it.

Open question: 291k "open" markets is plausible (many sports micro-markets) but unverified; a DB
count of `closed`/`active` flags is pending.

### Order books & data quality
- Each 15-min cycle stores the CLOB book of the top-50 markets by 24h volume: best bid/ask, spread,
  depth within 5¢, buy/sell VWAP for $100 and $1,000, validity flag, level counts, fetch time.
- Per-market data-quality gate (`api.data_quality`): stale price, missing/stale/invalid book,
  spread > 10¢, no $1,000 depth. A YES/NO can only ever display at level `good`; today everything is WAIT.

## 2. ETF prospective study (repo `ai-trading-room`) — opened after the 2026-10-09 21:00Z gate

- Protocol replay **fails closed**: sessions 2026-09-10, 09-11, 09-16 are missing (GitHub Actions
  schedule misses). By the protocol's own rule the holdout is **not valid**.
- Diagnostic replay (19 of 22 sessions; a missing next-session snapshot replaced by the next available
  one — this distorts fills around the gaps):

| Account | Result | Note |
|---|---|---|
| trend_full | 0.00% | 95/95 entries vetoed: `risk_health_unavailable` |
| breakout_full | 0.00% | 5/5 vetoed: `risk_health_unavailable` |
| breakout_strategy_only | 0.00% | 5/5 rejected: `integer_size_zero` |
| trend_strategy_only | **−0.73%** | 5 entries, 3 closed losers (EEM, EFA, EFA), max DD −0.72% |
| SPY benchmark | **+1.19%** | same 5 bps convention |

Root causes:
1. **Sizing**: $1,000 account, 20% max single position = $200, but one QQQ share > $700 →
   every QQQ signal impossible. QQQ was the best ETF of the window (+4.35%).
2. **Health gate**: full policy requires reconciliation, quote freshness, clock sync and correlation
   evidence the infrastructure never provides → it can never trade.

### Counterfactual of rejected decisions (buy next open, hold to 2026-10-08 close, no stop, 0.1% cost)
- Trend: 85 rejected entries → mean **−0.73%**, 28 positive. Best QQQ 2026-09-09 **+5.56%**; worst EEM 2026-10-05 **−3.95%**.
- Breakout: 5 rejected → mean **−0.42%**, 1 positive.
- One month; not evidence either way.

## 3. Long-term allocation example shown to the owner ($100/month, 10 years)

Twelve Data monthly closes, VOO and BND, 2016-10 → 2026-09, **price only (no dividends)**, annual rebalance:

| Mix | Invested | Final | Max drawdown (value/invested) | Worst 12m |
|---|---|---|---|---|
| 100% VOO | $12,000 | $24,785 | −27.4% | −19.5% |
| 80/20 | $12,000 | $20,987 | −25.3% | −18.7% |
| 60/40 | $12,000 | $17,758 | −23.0% | −17.8% |

**Total return (verified on the server, Yahoo adjusted closes, same method):**

| Mix | Price only | Total return | Max drawdown (TR) |
|---|---:|---:|---:|
| 100% VOO | $24,785 | **$27,136** (+126.1%) | −26.9% |
| 80/20 | $20,987 | **$23,299** (+94.2%) | −24.8% |
| 60/40 | $17,758 | **$19,988** (+66.6%) | −22.5% |

The price-only column was reproduced exactly from an independent source (Twelve Data vs Yahoo).
Bonds improved the worst drawdown by ~2 points per 20% allocation while costing ~$3.8k per 20% over
this decade — a single, unusually bond-unfriendly decade, so not a general conclusion.

Presented as history and an opinion, not advice. Proposed split of $100: 70 index / 20 bonds /
10 active trading / 0 Polymarket (paper only until research shows a post-cost edge).

## 3a. Production evidence (run 1) — see `docs/PRODUCTION_EVIDENCE.md`

HTTPS/auth/TLS verified from the public address; containers and API healthy; DB 291,527 market rows.
Real defects found and fixed: `sync_closed` (>500k markets closed in 30 days), a false-positive port
check, a restore check that tested an empty early dump. Port 111 (rpcbind) disabled going forward.

## 3b. Round 2 — implemented 2026-10-16, awaiting the server run

Code is in `main`; numbers below get filled only from the server outputs (nothing estimated here).

| Request | Implementation | Status |
|---|---|---|
| Market universe audit | `pmintel.report` §1: total / closed / open / accepting orders / order book enabled / *tradable* + liquidity and 24h-volume buckets. New stored fields: `acceptingOrders`, `enableOrderBook`, `archived`, `negRiskMarketID`, `negRiskOther`, `feesEnabled`, outcome prices, Gamma's 1d/1w change | code + tests ✅, server run ⏳ |
| Order-book retention | raw depth up to 50 levels/side with `token_id`, `ts`, `fetched_at`; freshness, 24h validity and coverage in the report and evidence | ✅ / ⏳ |
| Structural scanner | negRisk sets split into mathematical (\|Σmid−1\|>2%) → theoretical (Σ best ask < 1) → executable (live books walked for every leg at 10 and 100 shares; blockers: catch-all *Other* leg, fees enabled, leg not accepting orders, thin book). Partial-fill, resolution and settlement-delay risks listed, not modelled | ✅ / ⏳ |
| Collector load | full 290k scan every 6 h (lifecycle + deactivation); 15-min cycles scan only markets with liquidity ≥ $1K | ✅ / ⏳ |
| ETF protocol | original holdout **stays failed** (3 missed sessions). Diagnostic numbers above are labelled as such. A new, separately pre-registered study will run on the Oracle VM (cron, not Actions), with sizing that can actually trade; nothing in the original artifacts is modified | design pending |
| VOO/BND total return | Twelve Data dividends need a paid plan (not purchased). `scripts/total_return.py` uses Yahoo adjusted closes in a throwaway container, same method as the price-only run, and prints both for comparison | ✅ / ⏳ |
| Production evidence | `scripts/evidence.sh`: HTTPS via public address (status codes, certificate issuer/expiry), containers, API health, DB counts and size, job runs/errors 24h, order-book freshness per hour, security check incl. backup restore into a scratch DB | ✅ / ⏳ |

## 3c. UI redesign milestone 1 — see `docs/UI_REDESIGN.md`

Audit + implementation of the shell, Command Center, Market Explorer, Market Detail, Opportunity
Scanner, Research Lab, System Health; honest "not active" Portfolio/Alerts. Live CLOB history and book
endpoints. Verified with Playwright on desktop and iPhone sizes against a `[TEST]`-labelled local stack
(screenshots in `docs/screenshots/`); production screenshots pending the next deploy.

## 3d. ETF paper study v2 — pre-registered, see `docs/ETF_PAPER_V2_PROTOCOL.md`

New separate study (v1 stays failed): $3,000 per account (owner's choice), fractional shares, a
health gate the VM can actually satisfy, first-seen bar storage, sealed intents, SPY total-return
benchmark. Service `etf` runs after each US close; UI page `#/etf`. Window 2026-10-19 → 2027-04-16.
Tests: sizing of a >$700 ETF, late/missing intents, health veto, stops/accounting, dividends,
first-seen bars and one-time sealing against Postgres. Real-data run pending on the server.

## 3f. Tip tracker (2026-10-17)

`pmintel/tips.py`, service `tips`, page `#/tips`. Tips come from a read-only Telethon listener on the
owner's own Telegram account (whitelisted groups only, nothing sent; only symbol, mentioned price,
group title, message id and time stored) or are logged manually (`POST /api/tips`, JSON only).
Fixed outcome rule for every source: entry at the first US open after the tip, returns at the close
of sessions 1/5/20, worst drawdown / best run-up within 20 sessions, no costs (stated as optimistic).
Each tip also stores the stockcheck verdict at tip time. Leaderboard per source.

## 4. What I'd like challenged
1. Keyset `after_cursor` name was inferred from a third-party Go client; production scan passed, but
   please confirm against official docs.
2. Is the 291k open-market count real? Should the 15-min cycle scan only liquid markets
   (e.g. `liquidity_num_min`) with a daily full scan?
3. ETF next test: fractional shares vs larger paper capital; which health checks are realistic on our VM.
4. Whether bonds belong in the owner's plan given the decade's data.
