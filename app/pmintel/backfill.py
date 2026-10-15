"""Historical backfill: resolved markets + their price history from the CLOB.

Resumable: tokens that already have history are skipped.

  docker compose run --rm backfill                       # defaults from .env
  docker compose run --rm backfill --since 2025-01-01 --min-volume 50000 --limit 500
"""
import argparse
import logging

from . import config, db
from .normalize import normalize_market
from .sources import Clob, Gamma

log = logging.getLogger("backfill")


def backfill(conn, gamma: Gamma, clob: Clob, since: str, min_volume: float, fidelity: int,
             limit: int | None = None, with_history: bool = True) -> dict:
    stats = {"seen": 0, "resolved": 0, "history_fetched": 0, "history_points": 0, "history_empty": 0}
    with db.job_run(conn, "backfill") as run:
        batch: dict[str, dict] = {}
        for raw in gamma.iter_markets(closed="true", end_date_min=since, volume_num_min=min_volume,
                                      order="volumeNum", ascending="false"):
            if "id" not in raw:
                continue
            rec = normalize_market(raw)
            if (rec["volume"] or 0) < min_volume:  # don't trust the server-side filter blindly
                continue
            stats["seen"] += 1
            batch[rec["id"]] = rec
            if rec["resolved_outcome"] is not None:
                stats["resolved"] += 1
            if len(batch) >= 500:
                db.upsert_markets(conn, list(batch.values()))
                batch.clear()
            if limit and stats["seen"] >= limit:
                break
        db.upsert_markets(conn, list(batch.values()))

        if with_history:
            rows = conn.execute(
                """SELECT m.id, m.token_ids->>m.ref_index, m.start_date, m.closed_time
                   FROM markets m
                   WHERE m.closed AND m.resolved_outcome IS NOT NULL AND m.ref_index IS NOT NULL
                     AND jsonb_array_length(m.token_ids) > m.ref_index
                     AND m.volume >= %s
                     AND NOT EXISTS (SELECT 1 FROM price_history p
                                     WHERE p.token_id = m.token_ids->>m.ref_index)
                   ORDER BY m.volume DESC""", (min_volume,)).fetchall()
            for i, (market_id, token_id, start, closed) in enumerate(rows):
                if limit and i >= limit:
                    break
                pts = clob.price_history(token_id, fidelity)
                if not pts and start and closed:
                    pts = clob.price_history(token_id, fidelity, start, closed)
                stats["history_fetched"] += 1
                if not pts:
                    stats["history_empty"] += 1
                stats["history_points"] += db.insert_price_history(conn, market_id, token_id, pts)
                if (i + 1) % 100 == 0:
                    log.info("history %d/%d", i + 1, len(rows))
        run["items"] = stats["seen"]
    log.info("backfill done: %s", stats)
    return stats


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    p = argparse.ArgumentParser()
    p.add_argument("--since", default=config.BACKFILL_SINCE)
    p.add_argument("--min-volume", type=float, default=config.BACKFILL_MIN_VOLUME)
    p.add_argument("--fidelity", type=int, default=config.BACKFILL_FIDELITY_MIN)
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--no-history", action="store_true")
    a = p.parse_args()
    conn = db.connect()
    db.init_schema(conn)
    backfill(conn, Gamma(), Clob(), a.since, a.min_volume, a.fidelity, a.limit, not a.no_history)


if __name__ == "__main__":
    main()
