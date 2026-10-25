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
         "cryptolab": "מעבדת הקריפטו", "crypto_paper": "מעקב הקריפטו",
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
    for name, label in facts.get("crypto_candidates", []):
        out[f"good:crypto:{name}"] = f"קריפטו: \"{label}\" ניצחה את ביטקוין בבדיקה על העבר. מתחיל מעקב קדימה בכסף מדומה."
    for name, label in facts.get("crypto_confirmed", []):
        out[f"good:cryptofwd:{name}"] = (f"קריפטו: \"{label}\" אומתה גם קדימה (50+ עסקאות, רווח נטו מוכח מול ביטקוין אחרי עלויות). "
                                        "זה הרגע לדבר על סכום קטן — אחרי בירור חוקי ומס.")
    for name, label in facts.get("forward_confirmed", []):
        out[f"good:forward:{name}"] = (f"\"{label}\" אומתה גם קדימה (50+ עסקאות, רווח נטו מוכח מול SPY אחרי עלויות). "
                                      "זה הרגע לדבר על כסף אמיתי וקטן — עם סטופ ו-1%–2% סיכון לעסקה.")
    for rule in facts.get("pm_validated", []):
        out[f"good:pmexec:{rule}"] = (f"Polymarket: הכלל \"{rule}\" הרוויח גם במחיר קנייה אמיתי (30+ אירועים, גבול תחתון חיובי). "
                                      "עדיין כסף מדומה — לפני כל כסף אמיתי צריך בירור חוקי.")
    for src in facts.get("reliable_sources", []):
        out[f"good:source:{src}"] = f"המקור \"{src}\" עבר את שער האמינות (50+ טיפים, רווח נטו מוכח אחרי עלויות ותיקון למספר המקורות)."
    return out


PM_LABEL = {"24": "יום לפני סגירה בפועל", "24_scheduled": "יום לפני מועד הסיום (הבדיקה ההוגנת)",
            "168": "שבוע לפני", "720": "חודש לפני"}
PM_VERDICT = {"supported": "נמצא יתרון שעבר הכול", "oos_positive_needs_execution_check": "חיובי על העבר, ממתין לבדיקה במחיר אמיתי",
              "not_supported": "אין יתרון", "insufficient_data": "אין מספיק נתונים"}
FWD_STEPS = (1, 10, 25, 50, 100, 200)


def news(facts: dict) -> dict[str, str]:
    """New research findings, good or bad, each announced once (a changed verdict is a new finding)."""
    out = {}
    for h, v in facts.get("pm_verdicts", {}).items():
        out[f"news:pm:{h}:{v}"] = f"Polymarket — {PM_LABEL.get(h, h)}: {PM_VERDICT.get(v, v)}"
    for rule, st in facts.get("pm_rules", {}).items():
        if st["status"] == "failed":
            out[f"news:pmexec:failed:{rule}"] = (f"Polymarket: הכלל \"{rule}\" לא מחזיק במחיר קנייה אמיתי "
                                                 f"({st['events']} אירועים, ממוצע {st['mean_real'] * 100:+.1f}%). הרווח מהעבר היה אשליה.")
        step = max((x for x in FWD_STEPS if st["events"] >= x), default=None)
        if step:
                out[f"news:pmexec:{rule}:{step}"] = (f"Polymarket בדיקת ביצוע \"{rule}\": {st['events']} אירועים נסגרו, "
                                                     f"ממוצע במחיר אמיתי {st['mean_real'] * 100:+.1f}% לדולר (צריך 30 וגבול תחתון חיובי)")
    for name, label in facts.get("lab_edge", []):
        out[f"news:lab:{name}"] = f"מניות: \"{label}\" מנצחת כניסה אקראית, אבל עוד לא הוכיחה רווח נטו מול SPY"
    for name, label in facts.get("crypto_edge", []):
        out[f"news:crypto:{name}"] = f"קריפטו: \"{label}\" מנצחת כניסה אקראית, אבל עוד לא הוכיחה רווח נטו מול ביטקוין"
    for market, s in facts.get("fwd_closed", {}).items():
        step = max((x for x in FWD_STEPS if s["n"] >= x), default=None)
        if step:
                out[f"news:fwd:{market}:{step}"] = (f"מעקב קדימה ({market}): {s['n']} עסקאות נסגרו, {s['win'] * 100:.0f}% ניצחו את "
                                                    f"{'SPY' if market == 'מניות' else 'ביטקוין'}, ממוצע {s['mean'] * 100:+.2f}% לעסקה")
    return out


def name_of(job: str) -> str:
    return NAMES.get(job, job)


def _dur(d: timedelta) -> str:
    m = int(d.total_seconds() // 60)
    return f"{m} דק׳" if m < 120 else (f"{m // 60} שעות" if m < 48 * 60 else f"{m // 1440} ימים")


def diff(conn, active: dict[str, str], good: dict[str, str], now: datetime, info: dict[str, str] | None = None) -> list[str]:
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
    for k, text in (info or {}).items():
        if k not in state:
            msgs.append("📊 " + text)
            conn.execute("INSERT INTO alerts_state (key, kind, first_at, text) VALUES (%s, 'news', %s, %s)", (k, now, text))
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
    clab = conn.execute("SELECT result FROM research_reports WHERE kind = 'cryptolab_v1' ORDER BY id DESC LIMIT 1").fetchone()
    cst = (clab[0] or {}).get("strategies", {}) if clab else {}
    facts["crypto_candidates"] = [(k, v.get("label", k)) for k, v in cst.items() if v.get("verdict") == "candidate_for_paper"]
    try:
        from . import lab_paper
        from .cryptolab import CRYPTO
        closed = conn.execute("SELECT strategy, excess, entry_day FROM lab_signals WHERE status = 'closed'").fetchall()
        for key, lab_st, rows in (("forward_confirmed", st, lab_paper.split(closed)),
                                  ("crypto_confirmed", cst, lab_paper.split(closed, CRYPTO))):
            fwd = lab_paper.summary(rows)
            facts[key] = [(k, lab_st.get(k, {}).get("label", k)) for k, v in fwd.items()
                          if v["status"] == "confirmed" and lab_st.get(k, {}).get("verdict") == "candidate_for_paper"]
    except Exception:  # noqa: BLE001 - table absent before the forward tracker is deployed
        facts["forward_confirmed"], facts["crypto_confirmed"] = [], []
    facts["lab_edge"] = [(k, v.get("label", k)) for k, v in st.items() if v.get("verdict") == "edge_below_target"]
    facts["crypto_edge"] = [(k, v.get("label", k)) for k, v in cst.items() if v.get("verdict") == "edge_below_target"]
    try:
        fc = {}
        for market, prefix in (("מניות", False), ("קריפטו", True)):
            row = conn.execute(f"""SELECT count(*), avg((excess > 0)::int), avg(excess) FROM lab_signals
                                   WHERE status = 'closed' AND strategy {'LIKE' if prefix else 'NOT LIKE'} 'crypto:%%'""").fetchone()
            if row[0]:
                fc[market] = {"n": row[0], "win": float(row[1]), "mean": float(row[2])}
        facts["fwd_closed"] = fc
    except Exception:  # noqa: BLE001
        facts["fwd_closed"] = {}
    pm = conn.execute("SELECT result FROM research_reports WHERE kind = 'historical_v1' ORDER BY id DESC LIMIT 1").fetchone()
    facts["pm_verdicts"] = {h: (x.get("walk_forward") or {}).get("verdict") for h, x in ((pm[0] or {}) if pm else {}).items()
                            if (x.get("walk_forward") or {}).get("verdict")}
    try:
        from . import pm_paper
        rules = pm_paper.summary(conn)["rules"]
        facts["pm_rules"] = {k: v for k, v in rules.items() if v["events"] and v["mean_real"] is not None}
        facts["pm_validated"] = [k for k, v in rules.items() if v["validated"]]
    except Exception:  # noqa: BLE001 - table absent before deploy
        facts["pm_rules"], facts["pm_validated"] = {}, []
    from . import tips
    facts["reliable_sources"] = [b["source"] for b in tips.leaderboard(conn) if b["gate"]["status"] == "reliable"]
    return jobs, facts


def check(conn, now: datetime | None = None) -> list[str]:
    now = now or datetime.now(timezone.utc)
    jobs, facts = gather(conn)
    msgs = diff(conn, problems(jobs, facts, now), good_news(facts), now, news(facts))
    try:
        from . import follows
        msgs += follows.check(conn, now)
    except Exception:  # noqa: BLE001 - follows must never block system alerts
        import logging
        logging.getLogger("pmintel.alerts").exception("follow check failed")
    return msgs


# ---------------------------------------------------------------- daily morning digest
DIGEST_TZ = "Asia/Jerusalem"


def digest_text(conn, now: datetime) -> str:
    """One short morning summary built only from the database."""
    from . import tips
    lines = ["☀️ בוקר טוב — סיכום יומי"]
    board = tips.leaderboard(conn)
    reliable = [b["source"] for b in board if b["gate"]["status"] == "reliable"]
    lines.append("• היום: " + (f"יש מקור אמין — {reliable[0]}" if reliable
                               else "אין מה לעשות. אף מקור עוד לא הוכיח שהוא מנצח את SPY."))
    n, red = conn.execute("""SELECT count(*), count(*) FILTER (WHERE verdict = 'red') FROM tips
                             WHERE ts > now() - interval '24 hours'""").fetchone()
    syms = [r[0] for r in conn.execute("""SELECT symbol FROM tips WHERE ts > now() - interval '24 hours'
                                          GROUP BY symbol ORDER BY count(*) DESC LIMIT 5""").fetchall()]
    lines.append(f"• טיפים ב-24 שע׳: {n}" + (f" (אדומים: {red})" if n else "") + (f" — {', '.join(syms)}" if syms else ""))
    lab = conn.execute("SELECT result FROM research_reports WHERE kind = 'stocklab_v1' ORDER BY id DESC LIMIT 1").fetchone()
    if lab:
        st = (lab[0] or {}).get("strategies", {})
        cands = [v.get("label", k) for k, v in st.items() if v.get("verdict") == "candidate_for_paper"]
        line = f"• מעבדה: {len(cands)} מתוך {len(st)} אסטרטגיות עברו על העבר"
        try:
            o, c = conn.execute("""SELECT count(*) FILTER (WHERE status IN ('pending', 'open')),
                                          count(*) FILTER (WHERE status = 'closed') FROM lab_signals
                                   WHERE strategy NOT LIKE 'crypto:%'""").fetchone()
            line += f" · מעקב קדימה: {o} פתוחות, {c} נסגרו"
        except Exception:  # noqa: BLE001 - forward tracker not deployed yet
            pass
        lines.append(line)
    clab = conn.execute("SELECT result FROM research_reports WHERE kind = 'cryptolab_v1' ORDER BY id DESC LIMIT 1").fetchone()
    if clab:
        st = (clab[0] or {}).get("strategies", {})
        cands = [v.get("label", k) for k, v in st.items() if v.get("verdict") == "candidate_for_paper"]
        lines.append(f"• קריפטו: {len(cands)} מתוך {len(st)} אסטרטגיות ניצחו את ביטקוין על העבר")
    etf = conn.execute("SELECT result FROM research_reports WHERE kind = 'etf_paper_v2' ORDER BY id DESC LIMIT 1").fetchone()
    if etf and (etf[0] or {}).get("accounts"):
        acc = etf[0]["accounts"]
        spy = (etf[0].get("benchmark_spy") or {}).get("return")
        parts = [f"{k} {v.get('return', 0) * 100:+.1f}%" for k, v in acc.items()]
        lines.append("• חדר מסחר: " + ", ".join(parts) + (f" · SPY {spy * 100:+.1f}%" if spy is not None else ""))
    pm = conn.execute("SELECT result FROM research_reports WHERE kind = 'historical_v1' ORDER BY id DESC LIMIT 1").fetchone()
    if pm:
        v = ((pm[0] or {}).get("24_scheduled") or {}).get("walk_forward", {}).get("verdict")
        if v:
            lines.append(f"• Polymarket (בדיקה הוגנת, יום לפני): {PM_VERDICT.get(v, v)}")
    try:
        from . import pm_paper
        ps = pm_paper.summary(conn)
        rec = sum(r["recorded"] for r in ps["rules"].values())
        ev = sum(r["events"] for r in ps["rules"].values())
        if rec:
            lines.append(f"• בדיקת ביצוע Polymarket: {rec} עסקאות מדומות במחיר אמיתי, {ev} אירועים נסגרו")
    except Exception:  # noqa: BLE001 - not deployed yet
        pass
    open_problems = [t for (t,) in conn.execute("SELECT text FROM alerts_state WHERE kind = 'problem' AND resolved_at IS NULL")]
    lines.append("• מערכת: " + ("הכל תקין" if not open_problems else f"{len(open_problems)} בעיות פתוחות — " + "; ".join(open_problems[:3])))
    site = os.environ.get("SITE_URL")
    if site:
        lines.append(site)
    return "\n".join(lines)


def digest_due(conn, now: datetime, hour: int | None = None) -> bool:
    """True once per local day, at or after the digest hour (DIGEST_HOUR, default 8; -1 = off)."""
    from zoneinfo import ZoneInfo
    hour = int(os.environ.get("DIGEST_HOUR", "8")) if hour is None else hour
    if hour < 0:
        return False
    local = now.astimezone(ZoneInfo(DIGEST_TZ))
    if local.hour < hour:
        return False
    key = "digest:" + local.date().isoformat()
    cur = conn.execute("INSERT INTO alerts_state (key, kind, first_at) VALUES (%s, 'digest', %s) ON CONFLICT DO NOTHING",
                       (key, now))
    return cur.rowcount == 1
