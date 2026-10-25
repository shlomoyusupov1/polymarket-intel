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


@pytest.mark.skipif(not DB, reason="PMINTEL_TEST_DB not set")
def test_daily_digest_once_per_israel_day_and_built_from_data():
    from psycopg.types.json import Jsonb
    from pmintel import db
    conn = db.connect(DB)
    conn.execute("DROP TABLE IF EXISTS alerts_state")
    db.init_schema(conn)
    early = datetime(2026, 10, 18, 4, 0, tzinfo=timezone.utc)   # 07:00 in Israel
    later = datetime(2026, 10, 18, 5, 30, tzinfo=timezone.utc)  # 08:30 in Israel
    assert not A.digest_due(conn, early, hour=8)
    assert A.digest_due(conn, later, hour=8) and not A.digest_due(conn, later, hour=8)
    assert A.digest_due(conn, later + timedelta(days=1), hour=8)
    assert not A.digest_due(conn, later + timedelta(days=2), hour=-1)
    conn.execute("INSERT INTO research_reports (kind, params, result) VALUES ('etf_paper_v2', '{}', %s)",
                 (Jsonb({"accounts": {"trend": {"return": 0.012}}, "benchmark_spy": {"return": 0.004}}),))
    conn.execute("INSERT INTO alerts_state (key, kind, first_at, text) VALUES ('fail:x', 'problem', now(), 'x נכשל')")
    t = A.digest_text(conn, later)
    assert t.startswith("☀️") and "trend +1.2%" in t and "SPY +0.4%" in t and "1 בעיות פתוחות — x נכשל" in t
    conn.close()


def test_news_is_announced_once_and_only_the_highest_step():
    f = {"pm_verdicts": {"24_scheduled": "not_supported"},
         "pm_rules": {"NO 0.00-0.05": {"status": "collecting", "events": 12, "mean_real": 0.01}},
         "fwd_closed": {"מניות": {"n": 12, "win": 0.5, "mean": 0.003}},
         "lab_edge": [("ma_stack", "ערימת ממוצעים")]}
    n = A.news(f)
    assert "news:pm:24_scheduled:not_supported" in n and "news:fwd:מניות:10" in n and "news:fwd:מניות:1" not in n
    assert "news:pmexec:NO 0.00-0.05:10" in n and "news:lab:ma_stack" in n
    failed = A.news({"pm_rules": {"YES 0.90-0.95": {"status": "failed", "events": 40, "mean_real": -0.03}}})
    assert "news:pmexec:failed:YES 0.90-0.95" in failed
    assert "pmexec" in "".join(A.good_news({"pm_validated": ["NO 0.00-0.05"]}))
