import contextlib
import logging
from importlib import resources

import psycopg
from psycopg.types.json import Jsonb

from . import config

log = logging.getLogger(__name__)

MARKET_COLS = [
    "id", "condition_id", "event_id", "event_slug", "slug", "question", "category", "outcomes",
    "token_ids", "ref_index", "ref_outcome", "ref_price", "best_bid", "best_ask", "spread",
    "volume", "volume_24h", "liquidity", "start_date", "end_date", "closed_time",
    "active", "closed", "neg_risk", "resolution_status", "resolved_outcome", "resolution_source",
    "description", "url",
]

SNAP_COLS = ["ref_price", "best_bid", "best_ask", "spread", "volume", "volume_24h", "liquidity"]


def connect(url: str | None = None) -> psycopg.Connection:
    return psycopg.connect(url or config.DATABASE_URL, autocommit=True)


def init_schema(conn: psycopg.Connection) -> None:
    sql = resources.files("pmintel").joinpath("schema.sql").read_text()
    # api, collector and jobs may start together; serialize DDL to avoid catalog races.
    conn.execute("SELECT pg_advisory_lock(424242)")
    try:
        conn.execute(sql)
    finally:
        conn.execute("SELECT pg_advisory_unlock(424242)")


def _adapt(rec: dict) -> list:
    out = []
    for c in MARKET_COLS:
        v = rec.get(c)
        out.append(Jsonb(v) if c in ("outcomes", "token_ids") else v)
    return out


def upsert_markets(conn: psycopg.Connection, records: list[dict]) -> int:
    if not records:
        return 0
    cols = ", ".join(MARKET_COLS)
    ph = ", ".join(["%s"] * len(MARKET_COLS))
    upd = ", ".join(f"{c} = EXCLUDED.{c}" for c in MARKET_COLS if c != "id")
    sql = (f"INSERT INTO markets ({cols}) VALUES ({ph}) "
           f"ON CONFLICT (id) DO UPDATE SET {upd}, updated_at = now()")
    with conn.cursor() as cur:
        cur.executemany(sql, [_adapt(r) for r in records])
    return len(records)


def insert_snapshots(conn: psycopg.Connection, ts, records: list[dict]) -> int:
    if not records:
        return 0
    sql = (f"INSERT INTO snapshots (ts, market_id, {', '.join(SNAP_COLS)}) "
           f"VALUES (%s, %s, {', '.join(['%s'] * len(SNAP_COLS))}) ON CONFLICT DO NOTHING")
    with conn.cursor() as cur:
        cur.executemany(sql, [[ts, r["id"], *[r.get(c) for c in SNAP_COLS]] for r in records])
    return len(records)


def insert_price_history(conn: psycopg.Connection, market_id: str, token_id: str, points) -> int:
    if not points:
        return 0
    with conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO price_history (token_id, ts, market_id, price) VALUES (%s, %s, %s, %s) "
            "ON CONFLICT DO NOTHING",
            [(token_id, ts, market_id, p) for ts, p in points],
        )
    return len(points)


@contextlib.contextmanager
def job_run(conn: psycopg.Connection, job: str):
    """Record a job execution in job_runs so the System Health page reflects reality."""
    run_id = conn.execute("INSERT INTO job_runs (job) VALUES (%s) RETURNING id", (job,)).fetchone()[0]
    state = {"items": None}
    try:
        yield state
    except Exception as e:
        conn.execute("UPDATE job_runs SET finished_at = now(), status = 'error', error = %s, items = %s "
                     "WHERE id = %s", (f"{type(e).__name__}: {e}"[:2000], state["items"], run_id))
        raise
    conn.execute("UPDATE job_runs SET finished_at = now(), status = 'ok', items = %s WHERE id = %s",
                 (state["items"], run_id))
