from datetime import datetime, timedelta, timezone

from pmintel import agents as A

NOW = datetime(2026, 10, 18, 12, 0, tzinfo=timezone.utc)


def run(status, mins_ago, items=None, error=None):
    t = NOW - timedelta(minutes=mins_ago)
    return {"status": status, "started_at": t, "finished_at": None if status == "running" else t, "items": items, "error": error}


def test_states_come_only_from_real_job_runs():
    jobs = {"collect": run("ok", 10, 500), "orderbooks": run("ok", 600), "sec_insider": run("error", 30, error="HTTP 503"),
            "etf_paper": run("running", 1)}
    by = {a["id"]: a for a in A.build(jobs, {"open_markets": 1234, "last_snapshot": NOW - timedelta(minutes=10)}, NOW)}
    assert by["scout"]["state"] == "ok" and "1,234" in by["scout"]["line"]
    assert by["books"]["state"] == "late"           # 10 h since the last 15-minute run
    assert by["sec"]["state"] == "error" and "HTTP 503" in by["sec"]["line"]
    assert by["trader"]["state"] == "working"
    assert by["lab"]["state"] == "idle" and by["researcher"]["state"] == "idle"  # never ran: not faked as working
    assert by["keeper"]["state"] == "idle" and by["listener"]["state"] == "idle"
    # one of two SEC jobs failed -> the agent shows the failure even if the other job succeeded
    both = {"sec_insider": run("ok", 30), "sec_events": run("error", 30, error="x")}
    assert A.build(both, {}, NOW)[3]["state"] == "error"
    # the collector job finished but no fresh snapshot landed -> late, not ok
    stale = {"collect": run("ok", 5)}
    assert A.build(stale, {"last_snapshot": NOW - timedelta(hours=12)}, NOW)[0]["state"] == "late"


def test_on_demand_agents_are_never_late_and_lines_use_facts():
    jobs = {"stocklab": run("ok", 60 * 24 * 30)}
    f = {"lab_line": "0 מתוך 5 אסטרטגיות עברו", "last_tip": NOW - timedelta(hours=3), "tips_24h": 7,
         "cpu_pct": 12.0, "disk_pct": 91.0, "db_gb": 2.5, "host_ts": NOW, "sources_total": 3, "sources_tested": 2}
    by = {a["id"]: a for a in A.build(jobs, f, NOW)}
    assert by["lab"]["state"] == "ok" and by["lab"]["line"] == f["lab_line"]
    assert by["listener"]["state"] == "ok" and by["listener"]["line"].startswith("7 ")
    assert by["keeper"]["state"] == "late"           # disk above 85%
    assert "0 מקורות אמינים מתוך 2" in by["judge"]["line"]
