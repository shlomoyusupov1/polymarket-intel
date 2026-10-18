import os
from datetime import datetime, timedelta, timezone

import pytest

from pmintel import alerts as A

DB = os.environ.get("PMINTEL_TEST_DB")
NOW = datetime(2026, 10, 18, 12, 0, tzinfo=timezone.utc)


def test_rules():
    jobs = {"collect": {"status": "ok"}, "sec_events": {"status": "error", "error": "HTTP 503"}}
    f = {"last_ok": {"collect": NOW - timedelta(hours=2)}, "last_snapshot": NOW - timedelta(hours=2),
         "host": {"ts": NOW, "disk_pct": 90.0, "mem_pct": 50.0}}
    p = A.problems(jobs, f, NOW)
    assert set(p) == {"fail:sec_events", "stale:collect", "stale:snapshots", "host:disk"}
    assert "HTTP 503" in p["fail:sec_events"]
    assert A.problems({"collect": {"status": "ok"}}, {"last_ok": {"collect": NOW}, "last_snapshot": NOW}, NOW) == {}
    g = A.good_news({"lab_candidates": [("rsi2_pullback", "תיקון קצר")], "reliable_sources": ["SEC: קניית מנהל"]})
    assert set(g) == {"good:lab:rsi2_pullback", "good:source:SEC: קניית מנהל"}


@pytest.mark.skipif(not DB, reason="PMINTEL_TEST_DB not set")
def test_each_problem_is_announced_once_and_its_recovery_once():
    from pmintel import db
    conn = db.connect(DB)
    conn.execute("DROP TABLE IF EXISTS alerts_state")
    db.init_schema(conn)
    m1 = A.diff(conn, {"fail:x": "x נכשל"}, {"good:lab:a": "a עברה"}, NOW)
    assert m1 == ["⚠️ x נכשל", "🟢 a עברה"]
    assert A.diff(conn, {"fail:x": "x נכשל"}, {"good:lab:a": "a עברה"}, NOW) == []  # no repeats
    assert A.diff(conn, {}, {"good:lab:a": "a עברה"}, NOW) == ["✅ חזר לעבוד: x נכשל"]
    assert A.diff(conn, {}, {}, NOW) == []
    assert A.diff(conn, {"fail:x": "x נכשל"}, {}, NOW) == ["⚠️ x נכשל"]  # broke again -> announced again
    # end to end on real tables: a failed job becomes one alert
    conn.execute("INSERT INTO job_runs (job, started_at, finished_at, status, error) VALUES ('sec_insider', now(), now(), 'error', 'boom')")
    msgs = A.check(conn)
    assert any("קניות מנהלים (SEC) נכשל: boom" in m for m in msgs)
    assert not any("קניות מנהלים" in m for m in A.check(conn))
    conn.close()
