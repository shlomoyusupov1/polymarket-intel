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

## Decisions (2026-10-21, review of PR #17 by Claude, on the owner's request)
Kept from ChatGPT's PR #17:
- chronological fills by real session time (a 16:00 exit cannot fund a 09:30 entry; crypto in UTC);
- warning patterns (side = -1) never become long paper positions; late-recorded signals are skipped;
- closed-trade drawdown is not called "max drawdown"; honest notes that the portfolio is re-simulated and that
  the SHA has no external timestamp.
Changed:
- **Sizing.** "Cap every stopless position at the full-loss risk budget" made positions ~1% of the budget, made
  every plan "not OK" once a fee was entered (position + fee > risk by construction) and silenced the live risk
  alert. Now four separate figures: exposure (profile cap — how the paper portfolio measures a strategy), planned
  loss (only with a stop; fees included), stress loss (the strategy's own 5th-percentile hold-period return after
  cost from the out-of-sample lab, `raw_p5`; sizes a stopless plan so that loss stays within the risk limit) and
  max loss (the whole position — always shown). Trades are "not practical" only when fees exceed 1% of the
  position or exceed the risk limit outright.
- **Eligibility.** `lab_verdict` only ever held the historical verdict; nothing wrote "forward_confirmed", so the
  "proven" portfolio would stay empty forever. Now `lab_signals.strategy_state` is sealed with each signal (in the
  SHA body): confirmed / validating / not_eligible, computed at record time. Portfolio scopes: all · validating
  (the forward test itself) · confirmed. Old signals without a state are not eligible.
- **Drawdown.** `lab_paper.settle` writes daily closes of every held position into `lab_marks`; the replay builds
  a marked-to-market daily equity curve and reports the real drawdown (null only without marks).
- **Hourly report.** Light only (database reads; error counts per service, no raw log text). Backup/restore test
  daily at 04:10; research on demand. Token: one private repo, Contents RW, 90-day expiry.
Still open: a locked decision journal (`paper_orders` written at decision time with the settings of that moment);
external timestamping of the daily signal hash (e.g. publish it to the private repo).
