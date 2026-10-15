"""Read-only JSON API for the dashboard."""
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException, Query
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from . import config, db

pool: ConnectionPool | None = None

DECISION = {"decision": "WAIT",
            "reason": "אין עדיין מודל הסתברות עצמאי מאומת — מחיר השוק אינו תחזית עצמאית"}


@asynccontextmanager
async def lifespan(_app):
    global pool
    with db.connect() as c:
        db.init_schema(c)
    pool = ConnectionPool(config.DATABASE_URL, min_size=1, max_size=5, open=True,
                          kwargs={"autocommit": True, "row_factory": dict_row})
    yield
    pool.close()


app = FastAPI(title="pmintel", lifespan=lifespan, docs_url="/api/docs", openapi_url="/api/openapi.json")


def q(sql: str, params=()):
    with pool.connection() as c:
        return c.execute(sql, params).fetchall()


@app.get("/api/health")
def health():
    jobs = q("""SELECT DISTINCT ON (job) job, started_at, finished_at, status, items, error
                FROM job_runs ORDER BY job, started_at DESC""")
    last_ok = q("""SELECT job, max(finished_at) AS last_ok FROM job_runs
                   WHERE status = 'ok' GROUP BY job""")
    counts = q("""SELECT (SELECT count(*) FROM markets WHERE NOT closed) AS open_markets,
                         (SELECT count(*) FROM markets WHERE closed AND resolved_outcome IS NOT NULL) AS resolved_markets,
                         (SELECT count(DISTINCT token_id) FROM price_history) AS tokens_with_history,
                         (SELECT max(ts) FROM snapshots) AS last_snapshot""")[0]
    lag = None
    if counts["last_snapshot"]:
        lag = (datetime.now(timezone.utc) - counts["last_snapshot"]).total_seconds() / 60
    stale = lag is None or lag > config.COLLECT_INTERVAL_MIN * 3
    return {"status": "stale" if stale else "ok", "snapshot_lag_min": lag,
            "collect_interval_min": config.COLLECT_INTERVAL_MIN,
            "counts": counts, "jobs": jobs, "last_ok": {r["job"]: r["last_ok"] for r in last_ok}}


@app.get("/api/overview")
def overview():
    stats = q("""SELECT count(*) AS open_markets,
                        count(*) FILTER (WHERE liquidity >= %s) AS tracked_markets,
                        sum(volume_24h) AS volume_24h, sum(liquidity) AS liquidity,
                        max(updated_at) AS last_update
                 FROM markets WHERE NOT closed""", (config.SNAPSHOT_MIN_LIQUIDITY,))[0]
    return {"stats": stats, "movers": movers(hours=24, limit=8)}


SORTS = {"volume_24h": "volume_24h", "liquidity": "liquidity", "volume": "volume", "end_date": "end_date"}


@app.get("/api/markets")
def markets(search: str | None = None, category: str | None = None,
            sort: str = Query("volume_24h", enum=list(SORTS)), min_liquidity: float = 0,
            limit: int = Query(50, le=200), offset: int = 0):
    where, params = ["NOT closed"], []
    if search:
        where.append("question ILIKE %s")
        params.append(f"%{search}%")
    if category:
        where.append("category = %s")
        params.append(category)
    if min_liquidity:
        where.append("liquidity >= %s")
        params.append(min_liquidity)
    order = SORTS[sort]
    direction = "ASC" if sort == "end_date" else "DESC"
    rows = q(f"""SELECT id, question, category, ref_outcome, ref_price, best_bid, best_ask, spread,
                        volume, volume_24h, liquidity, end_date, url, updated_at
                 FROM markets WHERE {' AND '.join(where)}
                 ORDER BY {order} {direction} NULLS LAST LIMIT %s OFFSET %s""",
             (*params, limit, offset))
    for r in rows:
        r.update(DECISION)
    return rows


@app.get("/api/categories")
def categories():
    return q("""SELECT COALESCE(category, '') AS category, count(*) AS n FROM markets
                WHERE NOT closed GROUP BY 1 ORDER BY 2 DESC""")


@app.get("/api/markets/{market_id}")
def market(market_id: str, days: int = Query(30, le=365)):
    rows = q("SELECT * FROM markets WHERE id = %s", (market_id,))
    if not rows:
        raise HTTPException(404, "market not found")
    m = rows[0]
    m["snapshots"] = q("""SELECT ts, ref_price, best_bid, best_ask, volume_24h, liquidity FROM snapshots
                          WHERE market_id = %s AND ts > now() - make_interval(days => %s)
                          ORDER BY ts""", (market_id, days))
    m["history"] = q("SELECT ts, price FROM price_history WHERE market_id = %s ORDER BY ts", (market_id,))
    m.update(DECISION)
    return m


@app.get("/api/movers")
def movers(hours: int = Query(24, le=24 * 30), limit: int = Query(20, le=100), min_liquidity: float = 5000):
    """Largest observed price changes. Based only on our own snapshots — empty until collected."""
    return q("""
        WITH latest AS (
          SELECT DISTINCT ON (market_id) market_id, ts, ref_price FROM snapshots
          WHERE ts > now() - interval '2 hours' ORDER BY market_id, ts DESC),
        past AS (
          SELECT DISTINCT ON (market_id) market_id, ts, ref_price FROM snapshots
          WHERE ts <= now() - make_interval(hours => %s)
            AND ts > now() - make_interval(hours => %s) - interval '2 hours'
          ORDER BY market_id, ts DESC)
        SELECT m.id, m.question, m.ref_outcome, m.url, m.liquidity, m.volume_24h,
               p.ref_price AS price_then, l.ref_price AS price_now,
               l.ref_price - p.ref_price AS change, p.ts AS ts_then, l.ts AS ts_now
        FROM latest l JOIN past p USING (market_id) JOIN markets m ON m.id = l.market_id
        WHERE m.liquidity >= %s AND NOT m.closed AND l.ref_price IS DISTINCT FROM p.ref_price
        ORDER BY abs(l.ref_price - p.ref_price) DESC NULLS LAST LIMIT %s""",
             (hours, hours, min_liquidity, limit))


@app.get("/api/research/latest")
def research_latest():
    rows = q("SELECT id, created_at, kind, params, result FROM research_reports ORDER BY id DESC LIMIT 1")
    return rows[0] if rows else None
