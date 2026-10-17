# Reliability & Research Quality Sprint — report (2026-10-18)

Branch `sprint/reliability`. No new agents or features beyond the four tasks. Nothing places real trades,
nothing paid was enabled. **Production evidence is pending deployment** (this sandbox cannot reach the
server); the exact commands that produce it are below and nothing in this report is estimated.

## Status per requirement

| # | Requirement | Done | Automated tests | Production evidence |
|---|---|---|---|---|
| 1a | No auto-deploy of code that failed CI | `auto-update.sh` deploys a commit only if **every** GitHub check run on that exact SHA succeeded; pending/missing → wait; failed → logged, never deployed; GitHub API down → no deploy; fast-forward only | `test_deploy_scripts.py` runs the real script against a throwaway git remote with fake GitHub/docker | after merge: `cat ~/.pmintel/deploys.log` |
| 1b | Rollback | automatic: post-deploy `healthcheck.sh` (API answers + all services running) → on failure reset to previous commit, redeploy, **pause**; manual: `rollback.sh [sha]`, `resume-auto-update.sh` | same file: failed health → back on previous commit and paused; manual rollback; unknown SHA rejected | `scripts/rollback.sh` on the server |
| 1c | Backup restore from external storage | `offsite-backup.sh`: pg_dump → AES-256 (`BACKUP_KEY`) → Oracle Object Storage via pre-authenticated URL; `offsite-restore-test.sh`: download → SHA-256 → decrypt → restore into scratch DB → compare counts; cron daily backup + weekly restore test | `test_offsite_scripts.py`: real openssl/sha256; encryption at rest, tamper detection, too-few-rows detection, upload failure | **needs owner**: create a bucket + PAR (Always Free), then `scripts/install-offsite-backup.sh` and one manual run |
| 1d | 24–72 h server facts | `pmintel.prod_report --hours 72`: job runs (failures, stuck > 2 h, p50/p95/max duration), collection gaps, hours without snapshots, order-book validity, coverage, backfill status, host CPU/RAM/disk/DB size | `test_prod_report.py` on real tables | job/collection data exist already; **CPU/RAM/disk start being sampled with this release** → full 72 h host window 3 days after deploy |
| 2a | Partial backfill ≠ complete | job status `ok / partial / error / interrupted` + `detail` JSON; backfill is `ok` only if every candidate token got history and no limit; stale `running` → `interrupted`; research reports `data_status` | `test_partial_backfill_is_not_marked_complete` | `prod_report` "last backfill" line |
| 2b | Coverage + validation vs Data API v2 | `dataapi.py` (`/v2/status`, `/v2/prices-history`, `/v2/resolutions`) — confirmed in Polymarket's published OpenAPI; history fallback source in backfill; 3 new checks in `pmintel.validate` | `test_validate.py` (healthy, disagreement fails, outage recorded); partial→Data-API-fill→ok | `docker compose run --rm --entrypoint python research -m pmintel.validate` |
| 2c | Source time, ingest time, rules versions, data source | `markets.source_updated_at` vs `updated_at`; `snapshots.source_ts` vs `ts`; `orderbooks.book_ts` vs `fetched_at`; `price_history.source`; `market_rules` keeps every resolution-terms version | integration test asserts each | visible on market detail |
| 2d | Display vs bid/ask vs valid book vs executable by size | `/api/markets/{id}.prices` with four labelled blocks, each with source and time; UI card "ארבעה סוגי מחיר" | integration test | screenshot below |
| 2e | `acceptingOrders = NULL` is not tradable | tradable requires `IS TRUE` (and order book enabled) everywhere | integration test (NULL hidden, shown when tradable filter off) | `prod_report` shows the unknown count |
| 3 | Walk-forward, Brier/log loss vs market, dependence, multiple testing, costs, no premature edge | `walkforward.py`: time-ordered folds, train ends `horizon` before the test fold, test-fold events purged from train; recalibration vs raw price (Brier, log loss, event-clustered CI); rule chosen on train, scored on test; Holm; 2× slippage; verdict can reach only `oos_positive_needs_execution_check` from history — `supported` also requires live execution validation | `test_walkforward.py`: calibrated market → no edge; planted mispricing found OOS but not "supported" without execution; embargo/purge; insufficient data; Holm | Research Lab after the backfill finishes |
| 4 | Evidence dashboard | `/api/evidence` + page `#/evidence`: per finding official source + link (SEC filing URL, Telegram message link), publication time, close before publication, price now, 5-day and vs-SPY result, data quality, "what is still unknown"; Polymarket markets linked only by company-name keyword, with resolution-risk flags; watchlist PLTR NVDA MSFT AMD ANET AFRM SHOP SOFI QBTS (`WATCHLIST` env) | `test_evidence.py` | screenshots below |

Tests: **79 passed** locally (Postgres 16); CI runs the same suite.

## Added after review: reliability gate for every signal source

The owner's target is a source that wins about 60% of the time. "Win" is defined strictly: the tip beat
SPY over the same 5 trading days after a 0.2% round-trip cost. A source is **reliable** only with ≥ 50
measured tips, ≥ 60% wins whose 95% Wilson lower bound is above 50%, and a positive average edge whose
lower bound is above 0 when each symbol counts once. Otherwise: collecting (< 30), promising (≥ 55% and
positive mean, not proven) or not reliable. Shown in the source leaderboard
(`docs/screenshots/sprint-*-source-gate.png`); tests in `test_tips.py`.

## Screenshots (local stack, `[TEST]`-labelled data — not production)

- `docs/screenshots/sprint-desktop-evidence.png`, `sprint-mobile-evidence.png`
- `docs/screenshots/sprint-desktop-research.png`, `sprint-mobile-research.png` (walk-forward card: verdict "not supported", every gate ✗ on random test data — the expected result)
- `docs/screenshots/sprint-desktop-market-price-types.png`, `sprint-mobile-market-price-types.png`

In the test stack every symbol shares one price series, so "vs SPY" reads 0.0% there; that is a fixture artifact.

## Deploying and collecting production evidence

1. Merge the PR. The server's current cron still runs the old updater once; that run pulls the new
   `auto-update.sh`, and from then on only CI-passed commits deploy.
2. On the server:
   ```bash
   cd ~/polymarket-intel && bash scripts/install-auto-update.sh   # refresh the cron line
   sudo docker compose run --rm --entrypoint python research -m pmintel.validate
   sudo docker compose run --rm --entrypoint python research -m pmintel.prod_report --hours 72
   ```
3. Off-site backup (owner): Oracle console → Storage → Buckets → create `pmintel-backups` (Standard) →
   Pre-Authenticated Requests → "Permit object reads and writes", expiry ≥ 1 year → copy the URL, add
   `OFFSITE_PAR_URL=<url>` to `.env`, then `bash scripts/install-offsite-backup.sh`,
   `bash scripts/offsite-backup.sh`, `bash scripts/offsite-restore-test.sh`. **Copy `BACKUP_KEY` off the server.**
   Add a lifecycle rule (delete objects older than 30 days) to stay inside the free 20 GB.
4. 72 hours later, `prod_report --hours 72` gives the complete host-resource window.

## Known limits

- The repository is public; secrets are not in it. If it is made private, set `GITHUB_TOKEN` (read-only)
  for the CI gate.
- Data API v2 response field names for `/resolutions` were not fully readable from the spec; the check
  stores the keys it saw, so a schema mismatch shows as "unrecognised", not as agreement.
- Walk-forward on historical prices can never prove executability; live order-book checks are required
  before any edge is called supported.
