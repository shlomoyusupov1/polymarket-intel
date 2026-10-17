"""Historical backfill: resolved markets + their price history from the CLOB.

Resumable: tokens that already have history are skipped.

  docker compose run --rm backfill                       # defaults from .env
  docker compose run --rm backfill --since 2025-01-01 --min-volume 50000 --limit 500
"""
import argparse
import logging
from datetime import date, datetime, timedelta, timezone

import httpx

from . import config, db
from .normalize import normalize_market
from .sources import Clob, Gamma

log = logging.getLogger("backfill")


def month_windows(since: str, today: date | None = None):
    """[start, end) month windows from `since` to today. Small windows keep pagination
    offsets low, so a server-side offset cap can't silently truncate the history."""
    start = date.fromisoformat(since).replace(day=1)
    today = today or datetime.now(timezone.utc).date()
    while start <= today:
        nxt = (start.replace(day=28) + timedelta(days=4)).replace(day=1)
        yield max(start, date.fromisoformat(since)), nxt
        start = nxt


def iter_closed(gamma: Gamma, since: str, min_volume: float):
    for lo, hi in month_windows(since):
        base = {"closed": "true", "end_date_min": lo.isoformat(), "end_date_max": hi.isoformat()}
        try:
            yield from gamma.iter_markets(**base, volume_num_min=min_volume)
        except httpx.HTTPStatusError as e:
            if e.response.status_code not in (400, 422):
                raise
            log.warning("server rejected volume filter (%s); filtering client-side", e.response.status_code)
            yield from gamma.iter_markets(**base)


def fetch_history(clob: Clob, token_id: str, fidelity: int, start, closed):
    """Production 2026-10-16: some tokens answer 400 for interval=max. Try max, then the market's
    own [start, close] window, then daily fidelity. None = rejected every time (never fatal)."""
    attempts = [(fidelity, None, None)]
    if start and closed:
        attempts += [(fidelity, start, closed), (1440, start, closed)]
    got_empty = False
    for fid, a, b in attempts:
        try:
            pts = clob.price_history(token_id, fid, a, b)
        except httpx.HTTPStatusError as e:
            if 400 <= e.response.status_code < 500:
                continue
            raise
        if pts:
            return pts
        got_empty = True
    return [] if got_empty else None


def backfill(conn, gamma: Gamma, clob: Clob, since: str, min_volume: float, fidelity: int,
             limit: int | None = None, with_history: bool = True, discover: bool = True,
             data_api=None) -> dict:
    stats = {"seen": 0, "resolved": 0, "history_fetched": 0, "history_points": 0, "history_empty": 0,
             "history_error": 0, "history_from_data_api": 0}
    with db.job_run(conn, "backfill") as run:
        batch: dict[str, dict] = {}
        seen_ids: set[str] = set()
        for raw in (iter_closed(gamma, since, min_volume) if discover else ()):
            if "id" not in raw or str(raw["id"]) in seen_ids:
                continue
            seen_ids.add(str(raw["id"]))
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

        rows = []
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
                pts = fetch_history(clob, token_id, fidelity, start, closed)
                source = "clob"
                if not pts and data_api is not None:  # independent second source (Data API v2)
                    try:
                        alt = data_api.price_history(token_id, start, closed) if start and closed else \
                            data_api.price_history(token_id)
                    except httpx.HTTPError:
                        alt = None
                    if alt:
                        pts, source = alt, "data_api_v2"
                        stats["history_from_data_api"] += 1
                if pts is None:  # rejected everywhere: record and move on
                    stats["history_error"] += 1
                    continue
                stats["history_fetched"] += 1
                if not pts:
                    stats["history_empty"] += 1
                stats["history_points"] += db.insert_price_history(conn, market_id, token_id, pts, source)
                if (i + 1) % 100 == 0:
                    log.info("history %d/%d", i + 1, len(rows))
        run["items"] = stats["seen"]
        # 'ok' only when every candidate token got a history answer and nothing was cut short
        candidates = len(rows) if with_history else 0
        stats["history_candidates"] = candidates
        complete = with_history and not limit and stats["history_error"] == 0 and \
            stats["history_fetched"] >= candidates
        run["status"] = "ok" if complete else "partial"
        run["detail"] = {**stats, "since": since, "min_volume": min_volume, "limit": limit,
                         "with_history": with_history, "discover": discover}
    log.info("backfill %s: %s", run["status"], stats)
    return stats


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    p = argparse.ArgumentParser()
    p.add_argument("--since", default=config.BACKFILL_SINCE)
    p.add_argument("--min-volume", type=float, default=config.BACKFILL_MIN_VOLUME)
    p.add_argument("--fidelity", type=int, default=config.BACKFILL_FIDELITY_MIN)
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--no-history", action="store_true")
    p.add_argument("--history-only", action="store_true", help="skip market discovery (already stored)")
    a = p.parse_args()
    conn = db.connect()
    db.init_schema(conn)
    from .dataapi import DataApi
    backfill(conn, Gamma(), Clob(), a.since, a.min_volume, a.fidelity, a.limit, not a.no_history,
             discover=not a.history_only, data_api=DataApi())


if __name__ == "__main__":
    main()
