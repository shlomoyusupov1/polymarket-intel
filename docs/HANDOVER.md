# Handover — state of the project (2026-10-21)

## What runs
Docker Compose on Oracle Cloud (free tier): db (TimescaleDB), api (FastAPI), web (Caddy, basic auth), collector
(Polymarket), etf (paper ETF room), tips (Telegram listener + alerts), labpaper (forward paper tracking),
backup, and — from PR #16 — `live` (WebSocket market monitor). Jobs (profile `jobs`): stocklab, cryptolab,
research, backfill. Auto-deploy: cron every 10 min fast-forwards to green `main`, health check, auto-rollback.

## Engines
- Stocks lab (`stocklab.py`): 15 pre-declared strategies incl. 7 candlestick patterns; walk-forward by year,
  random-entry baseline, Holm correction, gate 50 trades / 60% / positive lower bound. Forward paper: `lab_paper.py`.
- Crypto lab (`cryptolab.py`): same method, 20 coins vs holding BTC, 1-day entry lag, cost sensitivity 0.2–2%.
- Polymarket: collection, walk-forward with official taker fee, order books; no proven edge yet.
- Tip sources (`tips.py`): every Telegram tip measured vs SPY after cost.
- Research states everywhere: confirmed / validating / candidate / failed / insufficient (`performance.py`).

## Owner-facing
Home, Opportunities (`plans.py`), Trade plan, Budget & risk settings, Follow (paste link/symbol -> Telegram),
Results (comparison), Virtual portfolio + decision history (`portfolio.py`), Live market (`live.py`).

## Rules (from the owner)
No real trades, deposits, paid services or cloud cost increases without explicit approval. Never invent prices,
targets, stops or probabilities: show "not set — not enough evidence". Secrets stay in the server's .env.
No push/merge without approval (the owner merges in the GitHub UI).

## Open items
1. Merge PR #15 (portfolio + hourly report) and PR #16 (live monitor; includes #15).
2. Server: set SEC_USER_AGENT (SEC returns 403 without a contact); `sudo docker compose up -d --build`.
3. Hourly report: create private repo `pmintel-reports` + fine-grained token (Contents RW, that repo only),
   put REPORT_REPO / REPORT_TOKEN in .env, `bash scripts/install-report-push.sh`.
4. First real research results: `bash scripts/evidence.sh --research` — nothing has been judged on real data yet.
5. Broker fee (ONE ZERO minimum per US order) into "התקציב שלי".
6. Stocks live data: Yahoo quote each minute (measured delay). Upgrade option: Alpaca free (IEX only, 30 symbols,
   free account + keys) — needs the owner's approval.
