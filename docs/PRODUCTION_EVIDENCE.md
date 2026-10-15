# Production Evidence Report — run 1

Source: `scripts/evidence.sh` executed by the owner on the Oracle VM at **2026-10-15 23:35 UTC**,
commit `4c0f652`. Raw output was pasted into the session; figures below are copied from it, not estimated.
Caveat: the run happened ~30 s after a redeploy (api health "starting") and while the first
`collect_full` scan was still running, so DB-derived counts are mid-scan.

## Verdict

| Area | Result | Evidence |
|---|---|---|
| HTTPS | ✅ PASS | public address: `/api/health` → 401, `/` → 401 without credentials; `http://` → 308 redirect; certificate Let's Encrypt (YE2), valid until 2027-01-13 |
| Containers | ✅ PASS | api, backup, collector, db (healthy), web all up |
| API | ✅ PASS | internal `/api/health` status `ok`, snapshot lag 15.6 min |
| Database | ✅ PASS | 291,527 market rows; 119,924 snapshots; 96 order books; size 1,004 MB |
| Collector | ⚠️ PARTIAL | fast cycle ok (avg 232 s); first full scan in progress; **`sync_closed` failed 2/2** (see bug 1) |
| Order books | ⚠️ PARTIAL | 96 books, 54 markets, last 10 min old; **79.2% valid** (20.8% one-sided or crossed) |
| Security | ❌ 3 FAIL → 1 real | see findings 2–4 |
| Backup restore | ❌ FAIL (check bug) | see finding 4 |

Cross-check: `scripts/total_return.py` reproduced the price-only DCA figures computed earlier from
Twelve Data **exactly** from an independent source (Yahoo): $24,785 / $20,987 / $17,758.

## Findings and fixes (commit after `4c0f652`)

1. **`sync_closed` cannot run** — `RuntimeError: pagination exceeded 5000 pages`. Verified with live
   requests: `/markets/keyset` *does* honour `end_date_min/max` and `volume_num_min`; the real cause is
   volume — more than 500,000 markets closed in 30 days (sports micro-markets). Fix: one-day windows
   for the last 7 days with `volume_num_min=1000`.
2. **Port 111 listening** — rpcbind shipped with Oracle's image. Not reachable from the internet (VCN
   security list allows 22/80/443 only), but unnecessary. Fix: `oracle-setup.sh` disables it on new
   installs; one command for this VM is in the runbook below.
3. **"db port 5432 is published" — false positive** in the check: the host's public listeners were
   22, 80, 111, 443 only (no 5432). `docker compose port` prints `:0` for unpublished ports and the
   check treated its exit status as "published". Fixed to inspect the output.
4. **Restore check failed** — the newest scheduled dump was taken when the container first started,
   before markets were loaded, so a correct restore of an empty DB counted as failure. The check now
   dumps the live DB on the spot, restores it into a scratch DB with TimescaleDB pre/post hooks,
   requires ≥95% of live market rows, and keeps `pg_restore` messages in `backups/restore-check.log`.
5. **One-sided books** (20.8%) — expected for markets close to resolution; already flagged by
   `valid=false` and excluded from executable prices. To be measured per market in the research report.

## Not yet verified (needs the next run)

- Market universe split by accepting-orders / tradable (new fields populate during the first full scan).
- Backup restore with the corrected check.
- `sync_closed` with the new windows.
- Order-book freshness across a full day.

## Runbook for run 2

```bash
cd ~/polymarket-intel && git pull && sudo docker compose up -d --build
sudo systemctl disable --now rpcbind rpcbind.socket
# wait until the full scan reports status ok:
sudo docker compose exec -T db psql -U pmintel -d pmintel -c "SELECT job, status, finished_at FROM job_runs WHERE job='collect_full' ORDER BY id DESC LIMIT 1"
bash scripts/evidence.sh > /dev/null
sudo docker compose run --rm --entrypoint python research -m pmintel.report > reports/research.md
cat reports/evidence-*.md | tail -n 80; cat reports/research.md
```
