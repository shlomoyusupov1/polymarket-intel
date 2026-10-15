"""Live collector: every N minutes, discover all open markets and snapshot the liquid ones.

Also runs a daily job that syncs recently closed markets (to capture resolutions)
and refreshes the research report.
"""
import logging
import time
from datetime import datetime, timedelta, timezone

from . import config, db, research
from .orderbook import summarize
from .normalize import normalize_market
from .sources import Clob, Gamma

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
    return {"markets": len(records), "snapshots": len(liquid), "records": records}


def collect_orderbooks(conn, clob: Clob, records: list[dict], now: datetime,
                       top_n: int | None = None) -> dict:
    """Snapshot the books of the most-traded open markets. One bad market never stops the rest."""
    top_n = config.ORDERBOOK_TOP_N if top_n is None else top_n
    cands = [r for r in records if r["ref_index"] is not None and r["ref_index"] < len(r["token_ids"])]
    cands.sort(key=lambda r: r["volume_24h"] or 0, reverse=True)
    stats = {"ok": 0, "empty": 0, "error": 0}
    with db.job_run(conn, "orderbooks") as run:
        for r in cands[:top_n]:
            token = r["token_ids"][r["ref_index"]]
            try:
                summary = summarize(clob.book(token))
            except Exception as e:  # noqa: BLE001 - logged and counted, visible in System Health
                log.warning("book failed for %s: %s", r["id"], e)
                stats["error"] += 1
                continue
            if summary is None:
                stats["empty"] += 1
                continue
            db.insert_orderbook(conn, now, r["id"], token, summary)
            stats["ok"] += 1
        run["items"] = stats["ok"]
    return stats


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
    gamma, clob = Gamma(), Clob()
    last_daily = None
    while True:
        started = time.monotonic()
        try:
            out = collect_once(conn, gamma)
            collect_orderbooks(conn, clob, out["records"], datetime.now(timezone.utc))
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
