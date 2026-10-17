import os

import pytest

from pmintel import hostmetrics, prod_report

DB = os.environ.get("PMINTEL_TEST_DB")


def test_hostmetrics_parsing(tmp_path):
    (tmp_path / "stat").write_text("cpu  100 0 100 800 0 0 0 0 0 0\n")
    (tmp_path / "loadavg").write_text("0.50 0.40 0.30 1/200 999\n")
    (tmp_path / "meminfo").write_text("MemTotal:       12000000 kB\nMemAvailable:    6000000 kB\n")
    hostmetrics._prev = None
    s1 = hostmetrics.sample(str(tmp_path), str(tmp_path))
    assert s1["cpu_pct"] is None and s1["load5"] == 0.4 and s1["mem_total"] == 12000000 * 1024
    (tmp_path / "stat").write_text("cpu  150 0 150 900 0 0 0 0 0 0\n")  # +100 busy, +200 total
    assert abs(hostmetrics.sample(str(tmp_path), str(tmp_path))["cpu_pct"] - 50.0) < 1e-9


@pytest.mark.skipif(not DB, reason="PMINTEL_TEST_DB not set")
def test_report_from_real_tables():
    from pmintel import db
    conn = db.connect(DB)
    conn.execute("DROP TABLE IF EXISTS job_runs, host_metrics, snapshots, orderbooks, markets, price_history CASCADE")
    db.init_schema(conn)
    conn.execute("""INSERT INTO job_runs (job, started_at, finished_at, status, error) VALUES
        ('collect', now() - interval '3 hours', now() - interval '3 hours' + interval '40 s', 'ok', NULL),
        ('collect', now() - interval '2 hours', now() - interval '2 hours' + interval '60 s', 'ok', NULL),
        ('collect', now() - interval '15 minutes', now() - interval '14 minutes', 'error', 'HTTPStatusError: 503'),
        ('backfill', now() - interval '5 hours', NULL, 'running', NULL)""")
    r = prod_report.gather(conn, 24)
    col = next(j for j in r["jobs"] if j["job"] == "collect")
    assert col["runs"] == 3 and col["ok"] == 2 and col["error"] == 1
    assert next(j for j in r["jobs"] if j["job"] == "backfill")["stuck"] == 1
    cad = next(c for c in r["cadence"] if c["job"] == "collect")
    assert cad["gaps_over_30m"] == 2 and round(cad["max_gap_min"]) == 105
    assert r["errors"][0]["error"].startswith("HTTPStatusError") and r["host"] is None
    md = prod_report.to_markdown(r)
    assert "no samples in the window" in md and "stuck" in md
    conn.execute("INSERT INTO host_metrics (cpu_pct, load5, cpus, mem_total, mem_available, disk_total, disk_used, db_bytes) "
                 "VALUES (20, 0.5, 2, 100, 40, 1000, 250, 5000000), (40, 0.9, 2, 100, 20, 1000, 260, 6000000)")
    h = prod_report.gather(conn, 24)["host"]
    assert h["samples"] == 2 and h["cpu_max_pct"] == 40 and abs(h["mem_used_max"] - 0.8) < 1e-9
    conn.close()
