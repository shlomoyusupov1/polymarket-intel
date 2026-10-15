"""Live collector: every N minutes, discover all open markets and snapshot the liquid ones.

Also runs a daily job that syncs recently closed markets (to capture resolutions)
and refreshes the research report.
"""
import logging
import time
from datetime import datetime, timedelta, timezone

from . import config, db, research
from .normalize import normalize_market
from .sources import Gamma

log = logging.getLogger("collector")


def collect_once(conn, gamma: Gamma, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    scan_started = conn.execute("SELECT now()").fetchone()[0]
    with db.job_run(conn, "collect") as run:
        seen: dict[str, dict] = {}
        for raw in gamma.iter_markets(active="true", closed="false"):
            if "id" not in raw:
                continue
            rec = normalize_market(raw)
            seen[rec["id"]] = rec  # dedupe across pages
        records = list(seen.values())
        db.upsert_markets(conn, records)
        if records:  # never deactivate everything because of an empty/failed scan
            conn.execute("UPDATE markets SET active = false WHERE active AND NOT closed AND updated_at < %s",
                         (scan_started,))
        liquid = [r for r in records if (r["liquidity"] or 0) >= config.SNAPSHOT_MIN_LIQUIDITY
                  and r["ref_price"] is not None]
        db.insert_snapshots(conn, now, liquid)
        run["items"] = len(liquid)
    log.info("collected %d open markets, snapshotted %d", len(records), len(liquid))
    return {"markets": len(records), "snapshots": len(liquid)}


def sync_recently_closed(conn, gamma: Gamma, days: int = 30) -> int:
    since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")
    with db.job_run(conn, "sync_closed") as run:
        recs = {}
        for raw in gamma.iter_markets(closed="true", end_date_min=since):
            if "id" in raw:
                r = normalize_market(raw)
                recs[r["id"]] = r
        db.upsert_markets(conn, list(recs.values()))
        run["items"] = len(recs)
    return len(recs)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    conn = db.connect()
    db.init_schema(conn)
    gamma = Gamma()
    last_daily = None
    while True:
        started = time.monotonic()
        try:
            collect_once(conn, gamma)
        except Exception:
            log.exception("collect failed")
        today = datetime.now(timezone.utc)
        if today.hour >= config.RESEARCH_HOUR_UTC and last_daily != today.date():
            last_daily = today.date()
            for name, fn in (("sync_closed", lambda: sync_recently_closed(conn, gamma)),
                             ("research", lambda: research.run(conn))):
                try:
                    fn()
                except Exception:
                    log.exception("%s failed", name)
        elapsed = time.monotonic() - started
        time.sleep(max(30, config.COLLECT_INTERVAL_MIN * 60 - elapsed))


if __name__ == "__main__":
    main()
