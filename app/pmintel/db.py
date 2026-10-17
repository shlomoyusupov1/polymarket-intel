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
    "description", "url", "outcome_prices", "accepting_orders", "enable_order_book", "archived",
    "neg_risk_market_id", "fees_enabled", "one_day_change", "one_week_change", "volume_1wk",
    "group_item_title", "neg_risk_other", "source_updated_at", "rules_hash",
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
        out.append(Jsonb(v) if c in ("outcomes", "token_ids", "outcome_prices") and v is not None else v)
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
        # every distinct version of the resolution terms, with the time we first saw it
        cur.executemany("""INSERT INTO market_rules (market_id, rules_hash, description, resolution_source, end_date, outcomes)
                           VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT (market_id, rules_hash) DO NOTHING""",
                        [(r["id"], r.get("rules_hash"), r.get("description"), r.get("resolution_source"),
                          r.get("end_date"), Jsonb(r.get("outcomes") or [])) for r in records if r.get("rules_hash")])
    return len(records)


def insert_snapshots(conn: psycopg.Connection, ts, records: list[dict]) -> int:
    if not records:
        return 0
    # ts = our ingest time; source_ts = Gamma's updatedAt for that market (source time)
    sql = (f"INSERT INTO snapshots (ts, market_id, {', '.join(SNAP_COLS)}, source_ts) "
           f"VALUES (%s, %s, {', '.join(['%s'] * len(SNAP_COLS))}, %s) ON CONFLICT DO NOTHING")
    with conn.cursor() as cur:
        cur.executemany(sql, [[ts, r["id"], *[r.get(c) for c in SNAP_COLS], r.get("source_updated_at")] for r in records])
    return len(records)


def insert_price_history(conn: psycopg.Connection, market_id: str, token_id: str, points,
                         source: str = "clob") -> int:
    if not points:
        return 0
    with conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO price_history (token_id, ts, market_id, price, source) VALUES (%s, %s, %s, %s, %s) "
            "ON CONFLICT DO NOTHING",
            [(token_id, ts, market_id, p, source) for ts, p in points],
        )
    return len(points)


@contextlib.contextmanager
def job_run(conn: psycopg.Connection, job: str):
    """Record a job execution in job_runs so the System Health page reflects reality."""
    # a previous run of the same job still marked 'running' after 12 h was killed: never count it as done
    conn.execute("""UPDATE job_runs SET status = 'interrupted', finished_at = now()
                    WHERE job = %s AND status = 'running' AND started_at < now() - interval '12 hours'""", (job,))
    run_id = conn.execute("INSERT INTO job_runs (job) VALUES (%s) RETURNING id", (job,)).fetchone()[0]
    state = {"items": None, "status": "ok", "detail": None}
    try:
        yield state
    except BaseException as e:  # incl. KeyboardInterrupt/SystemExit: the run did not complete
        conn.execute("UPDATE job_runs SET finished_at = now(), status = %s, error = %s, items = %s, detail = %s "
                     "WHERE id = %s", ("error" if isinstance(e, Exception) else "interrupted",
                                       f"{type(e).__name__}: {e}"[:2000], state["items"],
                                       Jsonb(state["detail"]) if state["detail"] is not None else None, run_id))
        raise
    conn.execute("UPDATE job_runs SET finished_at = now(), status = %s, items = %s, detail = %s WHERE id = %s",
                 (state["status"], state["items"], Jsonb(state["detail"]) if state["detail"] is not None else None,
                  run_id))


OB_COLS = ["best_bid", "best_ask", "spread", "bid_depth_5c", "ask_depth_5c",
           "buy_vwap_100", "buy_vwap_1000", "sell_vwap_100", "sell_vwap_1000", "valid", "n_bids", "n_asks"]


def insert_orderbook(conn: psycopg.Connection, ts, market_id: str, token_id: str, summary: dict) -> None:
    conn.execute(
        f"INSERT INTO orderbooks (ts, market_id, token_id, {', '.join(OB_COLS)}, levels, fetched_at, book_ts) "
        f"VALUES (%s, %s, %s, {', '.join(['%s'] * len(OB_COLS))}, %s, now(), %s) ON CONFLICT DO NOTHING",
        (ts, market_id, token_id, *[summary[c] for c in OB_COLS], Jsonb(summary["levels"]), summary.get("book_ts")))
