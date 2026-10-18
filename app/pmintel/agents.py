"""The 'agents room': every background job of the system shown as a worker with a live status line.

Nothing here is invented: each agent's state comes from job_runs (running / ok / error and when),
and its line from real tables. An agent is 'late' when its last success is older than three of its
expected intervals; agents run by hand (research, lab) are never late, only 'idle' until first run.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

# id, name, role, jobs it owns, expected interval (None = runs on demand)
AGENTS = [
    ("scout", "הסורק", "אוסף את כל שווקי Polymarket", ("collect", "collect_full", "sync_closed"), timedelta(minutes=15)),
    ("books", "בודק הספרים", "קורא ספרי פקודות: יש קונים ומוכרים אמיתיים?", ("orderbooks",), timedelta(minutes=15)),
    ("listener", "המאזין", "קורא את קבוצות הטלגרם ורושם כל טיפ", (), None),
    ("sec", "חוקר ה-SEC", "קניות של מנהלים, 13D ודוחות רבעוניים", ("sec_insider", "sec_events"), timedelta(hours=6)),
    ("guard", "השומר", "בודק כל מניה שהגיעה בטיפ: סימני ניפוח?", (), None),
    ("judge", "השופט", "מודד כל מקור מול SPY אחרי עלויות", (), None),
    ("trader", "הסוחר", "סוחר בכסף מדומה בקרנות סל לפי כללים קבועים", ("etf_paper",), timedelta(days=4)),
    ("researcher", "החוקר", "בודק אם מחירי Polymarket ניבאו נכון", ("research", "backfill"), None),
    ("lab", "המעבדה", "בודקת אסטרטגיות מניות על 10 שנים", ("stocklab",), None),
    ("keeper", "אב הבית", "מעבד, זיכרון, דיסק ומסד הנתונים", (), None),
]
JOB_AGENT = {j: a[0] for a in AGENTS for j in a[3]}


def _ago(ts: datetime | None, now: datetime) -> str:
    if ts is None:
        return "אף פעם"
    s = (now - ts).total_seconds()
    if s < 90:
        return "עכשיו"
    if s < 3600:
        return f"לפני {int(s // 60)} דק׳"
    if s < 86400 * 2:
        return f"לפני {int(s // 3600)} שע׳"
    return f"לפני {int(s // 86400)} ימים"


def job_state(jobs: dict[str, dict], names: tuple, interval: timedelta | None, now: datetime) -> tuple[str, dict | None]:
    """jobs: latest run per job name. Returns (state, latest run among this agent's jobs)."""
    runs = [jobs[n] for n in names if n in jobs]
    if not runs:
        return "idle", None
    if any(r["status"] == "running" for r in runs):
        return "working", next(r for r in runs if r["status"] == "running")
    failed = [r for r in runs if r["status"] in ("error", "interrupted")]
    if failed:  # any of this agent's jobs whose latest run failed
        return "error", max(failed, key=lambda r: r["started_at"])
    last = max(runs, key=lambda r: r["started_at"])
    ok = [r["finished_at"] for r in runs if r["status"] in ("ok", "partial") and r["finished_at"]]
    if interval and (not ok or now - max(ok) > 3 * interval):
        return "late", last
    return "ok", last


def build(jobs: dict[str, dict], facts: dict, now: datetime | None = None) -> list[dict]:
    """facts: plain numbers gathered from the database (see api.agents)."""
    now = now or datetime.now(timezone.utc)
    f = facts
    out = []
    for aid, name, role, names, interval in AGENTS:
        state, run = job_state(jobs, names, interval, now)
        when = run["finished_at"] or run["started_at"] if run else None
        line, href = "", None
        if aid == "scout":
            line = f"{f.get('open_markets') or 0:,} שווקים פתוחים · צילום אחרון {_ago(f.get('last_snapshot'), now)}"
            href = "#/pm/overview"
            snap = f.get("last_snapshot")
            if state == "ok" and (snap is None or now - snap > 3 * interval):
                state = "late"
        elif aid == "books":
            v = f.get("orderbook_valid_pct_24h")
            line = f"{v:.0f}% מהספרים תקינים ב-24 שע׳" if v is not None else "עוד אין ספרי פקודות"
            href = "#/markets"
        elif aid == "listener":
            last = f.get("last_tip")
            n = f.get("tips_24h") or 0
            line = f"{n} טיפים ב-24 שע׳ · אחרון {_ago(last, now)}"
            state = "ok" if last and now - last < timedelta(days=2) else ("idle" if not last else "quiet")
            when, href = last, "#/tips"
        elif aid == "sec":
            line = f"{f.get('sec_buys_30d') or 0} קניות מנהלים ב-30 יום · {f.get('sec_events_30d') or 0} אירועים"
            href = "#/evidence"
        elif aid == "guard":
            n, red = f.get("checked_7d") or 0, f.get("red_7d") or 0
            line = f"{red} מתוך {n} מניות מהשבוע סומנו באדום" if n else "אין טיפים לבדוק השבוע"
            state, href = ("ok" if n else "idle"), "#/check"
        elif aid == "judge":
            rel, tested = f.get("sources_reliable") or 0, f.get("sources_tested") or 0
            line = f"{rel} מקורות אמינים מתוך {tested} שנמדדו" if tested else "צובר נתונים, עוד אין מקור עם 30 טיפים"
            state, href = ("ok" if f.get("sources_total") else "idle"), "#/tips"
        elif aid == "trader":
            line = f.get("etf_line") or "מחכה ליום המסחר הראשון"
            href = "#/etf"
        elif aid == "researcher":
            line = f.get("research_line") or "המחקר עוד לא רץ"
            href = "#/research"
        elif aid == "lab":
            line = f.get("lab_line") or "עוד לא רצה"
            href = "#/lab"
        elif aid == "keeper":
            if f.get("cpu_pct") is not None:
                line = f"מעבד {f['cpu_pct']:.0f}% · דיסק {f.get('disk_pct') or 0:.0f}% · מסד {f.get('db_gb') or 0:.1f}GB"
                hot = (f.get("disk_pct") or 0) > 85 or (f.get("mem_pct") or 0) > 92
                state = "late" if hot else "ok"
                when = f.get("host_ts")
            else:
                line, state = "עוד אין מדידות", "idle"
            href = "#/system"
        if state == "error" and run and run.get("error"):
            line = "נכשל: " + str(run["error"])[:80]
        out.append({"id": aid, "name": name, "role": role, "state": state, "line": line, "href": href,
                    "when": when.isoformat() if when else None, "ago": _ago(when, now) if when else None,
                    "items": run.get("items") if run else None})
    return out
