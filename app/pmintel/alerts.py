"""Alerts to the owner's Telegram 'Saved Messages' when something breaks, recovers, or passes a gate.

Sent through the same read-only Telegram session the tip listener already uses (no bot, no new
secret). Each problem is announced once when it starts and once when it clears; good news (a lab
strategy passes, a strategy is confirmed forward, a tip source becomes reliable) is announced once
ever. Set ALERTS=0 in .env to turn alerts off.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

NAMES = {"collect": "איסוף Polymarket", "collect_full": "סריקה מלאה", "orderbooks": "ספרי פקודות",
         "sync_closed": "סנכרון שווקים שנסגרו", "sec_insider": "קניות מנהלים (SEC)", "sec_events": "13D ודוחות (SEC)",
         "etf_paper": "חדר המסחר", "lab_paper": "מעקב המעבדה", "stocklab": "מעבדת האסטרטגיות",
         "research": "מחקר Polymarket", "backfill": "Backfill היסטורי"}
STALE = {"collect": timedelta(minutes=45), "orderbooks": timedelta(minutes=90), "sec_insider": timedelta(hours=18)}


def enabled() -> bool:
    return os.environ.get("ALERTS", "1") != "0"


def problems(jobs: dict[str, dict], facts: dict, now: datetime) -> dict[str, str]:
    """Currently active problems as {key: text}. jobs: latest run per job; facts: see gather()."""
    out = {}
    for job, r in jobs.items():
        name = NAMES.get(job, job)
        if r["status"] in ("error", "interrupted"):
            err = str(r.get("error") or "")[:120]
            out[f"fail:{job}"] = f"{name} נכשל" + (f": {err}" if err else "")
    for job, limit in STALE.items():
        ok = facts.get("last_ok", {}).get(job)
        if job in jobs and ok and now - ok > limit:
            out[f"stale:{job}"] = f"{name_of(job)} לא הצליח כבר {_dur(now - ok)}"
    snap = facts.get("last_snapshot")
    if snap and now - snap > timedelta(hours=1):
        out["stale:snapshots"] = f"אין נתוני Polymarket חדשים כבר {_dur(now - snap)}"
    h = facts.get("host")
    if h and now - h["ts"] < timedelta(hours=1):
        if h.get("disk_pct") is not None and h["disk_pct"] > 85:
            out["host:disk"] = f"הדיסק בשרת מלא ב-{h['disk_pct']:.0f}%"
        if h.get("mem_pct") is not None and h["mem_pct"] > 92:
            out["host:mem"] = f"הזיכרון בשרת מלא ב-{h['mem_pct']:.0f}%"
    return out


def good_news(facts: dict) -> dict[str, str]:
    out = {}
    for name, label in facts.get("lab_candidates", []):
        out[f"good:lab:{name}"] = f"המעבדה: \"{label}\" עברה את הבדיקה על 10 שנים. מתחיל מעקב קדימה בכסף מדומה."
    for name, label in facts.get("forward_confirmed", []):
        out[f"good:forward:{name}"] = (f"\"{label}\" אומתה גם קדימה (50+ עסקאות, 60%+ ניצחו את SPY אחרי עלויות). "
                                      "זה הרגע לדבר על כסף אמיתי וקטן — עם סטופ ו-1%–2% סיכון לעסקה.")
    for src in facts.get("reliable_sources", []):
        out[f"good:source:{src}"] = f"המקור \"{src}\" עבר את שער האמינות (50+ טיפים, 60%+ אחרי עלויות ותיקון למספר המקורות)."
    return out


def name_of(job: str) -> str:
    return NAMES.get(job, job)


def _dur(d: timedelta) -> str:
    m = int(d.total_seconds() // 60)
    return f"{m} דק׳" if m < 120 else (f"{m // 60} שעות" if m < 48 * 60 else f"{m // 1440} ימים")


def diff(conn, active: dict[str, str], good: dict[str, str], now: datetime) -> list[str]:
    """Compare with what was already announced; return the messages to send and record them."""
    msgs = []
    state = {k: (kind, resolved, text) for k, kind, resolved, text in
             conn.execute("SELECT key, kind, resolved_at, text FROM alerts_state").fetchall()}
    for k, text in active.items():
        if k not in state or state[k][1] is not None:
            msgs.append("⚠️ " + text)
            conn.execute("""INSERT INTO alerts_state (key, kind, first_at, resolved_at, text) VALUES (%s, 'problem', %s, NULL, %s)
                            ON CONFLICT (key) DO UPDATE SET first_at = EXCLUDED.first_at, resolved_at = NULL, text = EXCLUDED.text""",
                         (k, now, text))
    for k, (kind, resolved, text) in state.items():
        if kind == "problem" and resolved is None and k not in active:
            msgs.append("✅ חזר לעבוד: " + text)
            conn.execute("UPDATE alerts_state SET resolved_at = %s WHERE key = %s", (now, k))
    for k, text in good.items():
        if k not in state:
            msgs.append("🟢 " + text)
            conn.execute("INSERT INTO alerts_state (key, kind, first_at, text) VALUES (%s, 'good', %s, %s)", (k, now, text))
    return msgs


def gather(conn) -> tuple[dict, dict]:
    """Latest run per job and the facts the rules need, straight from the database."""
    cols = ["job", "started_at", "finished_at", "status", "error"]
    jobs = {r[0]: dict(zip(cols, r)) for r in conn.execute(
        "SELECT DISTINCT ON (job) job, started_at, finished_at, status, error FROM job_runs ORDER BY job, started_at DESC")}
    last_ok = dict(conn.execute("SELECT job, max(finished_at) FROM job_runs WHERE status IN ('ok', 'partial') GROUP BY job").fetchall())
    facts: dict = {"last_ok": last_ok, "last_snapshot": conn.execute("SELECT max(ts) FROM snapshots").fetchone()[0]}
    h = conn.execute("""SELECT ts, disk_used, disk_total, mem_available, mem_total FROM host_metrics
                        ORDER BY ts DESC LIMIT 1""").fetchone()
    if h:
        facts["host"] = {"ts": h[0], "disk_pct": 100.0 * h[1] / h[2] if h[2] else None,
                         "mem_pct": 100.0 * (1 - h[3] / h[4]) if h[4] else None}
    lab = conn.execute("SELECT result FROM research_reports WHERE kind = 'stocklab_v1' ORDER BY id DESC LIMIT 1").fetchone()
    st = (lab[0] or {}).get("strategies", {}) if lab else {}
    facts["lab_candidates"] = [(k, v.get("label", k)) for k, v in st.items() if v.get("verdict") == "candidate_for_paper"]
    try:
        from . import lab_paper
        closed = conn.execute("SELECT strategy, excess, entry_day FROM lab_signals WHERE status = 'closed'").fetchall()
        fwd = lab_paper.summary(closed)
        facts["forward_confirmed"] = [(k, st.get(k, {}).get("label", k)) for k, v in fwd.items()
                                      if v["status"] == "confirmed" and st.get(k, {}).get("verdict") == "candidate_for_paper"]
    except Exception:  # noqa: BLE001 - table absent before the forward tracker is deployed
        facts["forward_confirmed"] = []
    from . import tips
    facts["reliable_sources"] = [b["source"] for b in tips.leaderboard(conn) if b["gate"]["status"] == "reliable"]
    return jobs, facts


def check(conn, now: datetime | None = None) -> list[str]:
    now = now or datetime.now(timezone.utc)
    jobs, facts = gather(conn)
    return diff(conn, problems(jobs, facts, now), good_news(facts), now)
