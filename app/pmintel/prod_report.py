"""Production evidence for the last N hours, straight from the database (no estimates).

  docker compose run --rm --entrypoint python research -m pmintel.prod_report --hours 72

Sections: job runs (failures, stuck runs, durations), collection cadence and gaps, order-book
freshness, data coverage (prices, history, backfill status), host resources (CPU, load, RAM, disk,
DB size). Anything not measured in the window is printed as "no data" — never filled in.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone

from psycopg.types.json import Jsonb

from . import db


def _q(conn, sql, params=()):
    return conn.execute(sql, params).fetchall()


def gather(conn, hours: int) -> dict:
    w = (f"{int(hours)} hours",)
    jobs = _q(conn, """
        SELECT job, count(*), count(*) FILTER (WHERE status = 'ok'), count(*) FILTER (WHERE status = 'error'),
               count(*) FILTER (WHERE status = 'partial'),
               count(*) FILTER (WHERE status = 'running' AND started_at < now() - interval '2 hours'),
               percentile_cont(0.5) WITHIN GROUP (ORDER BY extract(epoch FROM finished_at - started_at)),
               percentile_cont(0.95) WITHIN GROUP (ORDER BY extract(epoch FROM finished_at - started_at)),
               max(extract(epoch FROM finished_at - started_at))
        FROM job_runs WHERE started_at > now() - %s::interval GROUP BY job ORDER BY job""", w)
    errors = _q(conn, """SELECT job, started_at, left(error, 240) FROM job_runs
                         WHERE status = 'error' AND started_at > now() - %s::interval
                         ORDER BY started_at DESC LIMIT 15""", w)
    cadence = _q(conn, """
        SELECT job, max(gap), count(*) FILTER (WHERE gap > interval '30 minutes') FROM (
          SELECT job, started_at - lag(started_at) OVER (PARTITION BY job ORDER BY started_at) AS gap
          FROM job_runs WHERE job IN ('collect', 'collect_full') AND started_at > now() - %s::interval) g
        GROUP BY job""", w)
    snaps = _q(conn, """
        SELECT count(DISTINCT ts), count(*), max(gap), count(*) FILTER (WHERE gap > interval '30 minutes') FROM (
          SELECT ts, ts - lag(ts) OVER (ORDER BY ts) AS gap FROM (SELECT DISTINCT ts FROM snapshots
          WHERE ts > now() - %s::interval) d) g""", w)[0]
    snap_rows = _q(conn, "SELECT count(*) FROM snapshots WHERE ts > now() - %s::interval", w)[0][0]
    empty_hours = _q(conn, """
        SELECT count(*) FROM generate_series(date_trunc('hour', now() - %s::interval), date_trunc('hour', now()) - interval '1 hour',
                                             interval '1 hour') h
        WHERE NOT EXISTS (SELECT 1 FROM snapshots s WHERE s.ts >= h AND s.ts < h + interval '1 hour')""", w)[0][0]
    books = _q(conn, """SELECT count(*), avg(CASE WHEN valid THEN 1.0 ELSE 0 END), max(ts)
                        FROM orderbooks WHERE ts > now() - %s::interval""", w)[0]
    coverage = _q(conn, """
        SELECT count(*) FILTER (WHERE active AND NOT closed),
               count(*) FILTER (WHERE active AND NOT closed AND accepting_orders IS TRUE AND enable_order_book IS TRUE),
               count(*) FILTER (WHERE active AND NOT closed AND accepting_orders IS NULL),
               count(*) FILTER (WHERE active AND NOT closed AND ref_price IS NULL),
               count(*) FILTER (WHERE resolved_outcome IS NOT NULL),
               count(*) FILTER (WHERE resolved_outcome IS NOT NULL AND EXISTS (
                   SELECT 1 FROM price_history p WHERE p.token_id = markets.token_ids->>markets.ref_index))
        FROM markets""")[0]
    backfill = _q(conn, """SELECT started_at, finished_at, status, items, left(error, 200) FROM job_runs
                           WHERE job = 'backfill' ORDER BY id DESC LIMIT 1""")
    host = _q(conn, """
        SELECT count(*), avg(cpu_pct), max(cpu_pct), max(load5), max(cpus),
               avg(1 - mem_available::float / mem_total), max(1 - mem_available::float / mem_total),
               (array_agg(disk_used::float / disk_total ORDER BY ts DESC))[1],
               (array_agg(db_bytes ORDER BY ts))[1], (array_agg(db_bytes ORDER BY ts DESC))[1], min(ts), max(ts)
        FROM host_metrics WHERE ts > now() - %s::interval""", w)[0]
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(), "hours": hours,
        "jobs": [dict(zip(["job", "runs", "ok", "error", "partial", "stuck", "p50_s", "p95_s", "max_s"], r)) for r in jobs],
        "errors": [{"job": j, "at": t.isoformat(), "error": e} for j, t, e in errors],
        "cadence": [{"job": j, "max_gap_min": g.total_seconds() / 60 if g else None, "gaps_over_30m": n} for j, g, n in cadence],
        "snapshots": {"cycles": snaps[0], "rows": snap_rows, "max_gap_min": snaps[2].total_seconds() / 60 if snaps[2] else None,
                      "gaps_over_30m": snaps[3], "hours_without_snapshots": empty_hours},
        "orderbooks": {"rows": books[0], "valid_share": float(books[1]) if books[1] is not None else None,
                       "last": books[2].isoformat() if books[2] else None},
        "coverage": dict(zip(["open", "tradable_verified", "accepting_orders_unknown", "open_without_price",
                              "resolved", "resolved_with_history"], coverage)),
        "backfill_last": dict(zip(["started", "finished", "status", "items", "error"],
                                  [x.isoformat() if hasattr(x, "isoformat") else x for x in backfill[0]])) if backfill else None,
        "host": None if not host[0] else {
            "samples": host[0], "cpu_avg_pct": host[1], "cpu_max_pct": host[2], "load5_max": host[3], "cpus": host[4],
            "mem_used_avg": host[5], "mem_used_max": host[6], "disk_used_last": host[7],
            "db_bytes_first": host[8], "db_bytes_last": host[9],
            "from": host[10].isoformat(), "to": host[11].isoformat()},
    }


def _f(v, fmt="{:.1f}", none="no data"):
    return none if v is None else fmt.format(v)


def to_markdown(r: dict) -> str:
    L = [f"# Production evidence — last {r['hours']} h", f"Generated {r['generated_at']} from the production database.", ""]
    L += ["## Job runs", "| job | runs | ok | error | partial | stuck >2h | p50 s | p95 s | max s |", "|---|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for j in r["jobs"]:
        L.append(f"| {j['job']} | {j['runs']} | {j['ok']} | {j['error']} | {j['partial']} | {j['stuck']} | "
                 f"{_f(j['p50_s'], '{:.0f}', '-')} | {_f(j['p95_s'], '{:.0f}', '-')} | {_f(j['max_s'], '{:.0f}', '-')} |")
    if not r["jobs"]:
        L.append("| no job runs in the window | | | | | | | | |")
    L += ["", "### Errors (latest 15)"] + ([f"- {e['at']} **{e['job']}**: {e['error']}" for e in r["errors"]] or ["- none"])
    L += ["", "## Collection cadence"] + [f"- {c['job']}: max gap {_f(c['max_gap_min'], '{:.0f} min')}, gaps > 30 min: {c['gaps_over_30m']}"
                                         for c in r["cadence"]]
    s = r["snapshots"]
    L += [f"- snapshot cycles: {s['cycles']}, rows: {s['rows']}, max gap: {_f(s['max_gap_min'], '{:.0f} min')}, "
          f"gaps > 30 min: {s['gaps_over_30m']}, hours with no snapshot: {s['hours_without_snapshots']}"]
    o = r["orderbooks"]
    L += ["", "## Order books", f"- rows: {o['rows']}, valid: {_f(o['valid_share'] and o['valid_share'] * 100, '{:.1f}%')}, last: {o['last'] or 'no data'}"]
    c = r["coverage"]
    L += ["", "## Data coverage",
          f"- open markets: {c['open']}; tradable (acceptingOrders = TRUE and order book enabled): {c['tradable_verified']}; "
          f"acceptingOrders unknown (NULL, not counted as tradable): {c['accepting_orders_unknown']}; open without a price: {c['open_without_price']}",
          f"- resolved markets: {c['resolved']}; with price history: {c['resolved_with_history']}"]
    b = r["backfill_last"]
    L += [f"- last backfill: {b['status']} (started {b['started']}, finished {b['finished'] or '-'}, items {b['items']})"
          + (f" — {b['error']}" if b and b.get("error") else "") if b else "- last backfill: never ran"]
    h = r["host"]
    L += ["", "## Host resources"]
    if not h:
        L.append("- no samples in the window (host metrics start with this release)")
    else:
        L += [f"- {h['samples']} samples {h['from']} → {h['to']}",
              f"- CPU avg {_f(h['cpu_avg_pct'])}%, max {_f(h['cpu_max_pct'])}%; load5 max {_f(h['load5_max'], '{:.2f}')} on {h['cpus']} CPUs",
              f"- RAM used avg {_f(h['mem_used_avg'] and h['mem_used_avg'] * 100)}%, max {_f(h['mem_used_max'] and h['mem_used_max'] * 100)}%",
              f"- disk used {_f(h['disk_used_last'] and h['disk_used_last'] * 100)}%; DB {_f(h['db_bytes_first'] and h['db_bytes_first'] / 1e6, '{:.0f} MB')} → "
              f"{_f(h['db_bytes_last'] and h['db_bytes_last'] / 1e6, '{:.0f} MB')}"]
    return "\n".join(L) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=int, default=72)
    a = ap.parse_args()
    conn = db.connect()
    db.init_schema(conn)
    r = gather(conn, a.hours)
    conn.execute("INSERT INTO research_reports (kind, result) VALUES ('prod_report', %s)",
                 (Jsonb(json.loads(json.dumps(r, default=str))),))
    print(to_markdown(r))


if __name__ == "__main__":
    main()
