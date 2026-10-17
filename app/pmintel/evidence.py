"""Evidence board for the owner's watchlist stocks.

For every finding: the official source and link, publication time, the stock price before publication
and now, data quality, and what is still unknown. Polymarket markets are linked to a stock only by an
explicit keyword match on the company name, labelled as such — never as a cause.
"""
from __future__ import annotations

import os
from datetime import datetime, time, timezone
from zoneinfo import ZoneInfo

from . import tips

NY = ZoneInfo("America/New_York")
WATCHLIST = [s.strip().upper() for s in os.environ.get(
    "WATCHLIST", "PLTR,NVDA,MSFT,AMD,ANET,AFRM,SHOP,SOFI,QBTS").split(",") if s.strip()]
# company names used to find Polymarket markets that mention the company (keyword match only)
NAMES = {"PLTR": ["palantir"], "NVDA": ["nvidia"], "MSFT": ["microsoft"], "AMD": ["amd", "advanced micro"],
         "ANET": ["arista"], "AFRM": ["affirm"], "SHOP": ["shopify"], "SOFI": ["sofi"], "QBTS": ["d-wave", "dwave"]}

UNKNOWN = {
    "sec": ["למה בדיוק נעשתה הפעולה (הדיווח לא מסביר מניע)",
            "האם התגובה במחיר כבר קרתה לפני הדיווח",
            "האם הדפוס הזה עובד גם היום — נמדד בדירוג המקורות"],
    "telegram": ["מי עומד מאחורי הקבוצה והאם הוא מחזיק במניה",
                 "האם ההודעה היא המלצה, חדשה או פרסומת",
                 "האם המקור מנצח את SPY — ראה דירוג המקורות"],
    "manual": ["מקור הטיפ לא מאומת", "האם המקור מנצח את SPY — ראה דירוג המקורות"],
    "polymarket": ["אין קשר מוכח בין השוק הזה למחיר המניה — זו רק התאמה בשם החברה",
                   "מחיר השוק הוא הערכת המשתתפים, לא תחזית מאומתת"],
}


def price_context(bars: list[dict], ts: datetime) -> dict:
    """Close before publication (last session that closed before ts) and the latest close."""
    pub = ts.astimezone(NY)
    before = [b for b in bars if b["day"] < pub.date().isoformat() or
              (b["day"] == pub.date().isoformat() and pub.time() >= time(16, 0))]
    prior = before[-1] if before else None
    last = bars[-1] if bars else None
    return {"prior_close": prior["c"] if prior else None, "prior_day": prior["day"] if prior else None,
            "current": last["c"] if last else None, "current_day": last["day"] if last else None,
            "change_since": (last["c"] / prior["c"] - 1) if prior and last and prior["c"] else None}


def resolution_risk(m: dict, versions: int) -> list[str]:
    r = []
    if versions > 1:
        r.append(f"תנאי ההכרעה השתנו ({versions} גרסאות)")
    if m.get("neg_risk_other"):
        r.append("יש רגל 'אחר' — יכולות להתווסף תוצאות חדשות")
    if not m.get("resolution_source"):
        r.append("לא צוין מקור הכרעה")
    if m.get("end_date") and m["end_date"] < datetime.now(timezone.utc) and not m.get("closed"):
        r.append("תאריך הסיום עבר והשוק עוד לא הוכרע")
    if m.get("accepting_orders") is not True:
        r.append("לא אומת שהשוק מקבל פקודות")
    return r


def board(conn, fetch=None, symbols: list[str] | None = None, days: int = 60) -> dict:
    fetch = fetch or tips._cached_fetch
    out = []
    for sym in symbols or WATCHLIST:
        try:
            bars = fetch(sym)[0]
            price_ok = bool(bars)
        except Exception:  # noqa: BLE001 - shown as a data-quality problem, not hidden
            bars, price_ok = [], False
        last, prev = (bars[-1] if bars else None), (bars[-2] if len(bars) > 1 else None)
        findings = []
        for row in conn.execute("""SELECT id, source, kind, ts, source_url, mentioned_price, entry_day, entry_price,
                                          r1, r5, r20, spy_r5, verdict, flags
                                   FROM tips WHERE symbol = %s AND ts > now() - make_interval(days => %s)
                                   ORDER BY ts DESC LIMIT 25""", (sym, days)).fetchall():
            (tid, source, kind, ts, url, mentioned, entry_day, entry, r1, r5, r20, spy_r5, verdict, flags) = row
            ctx = price_context(bars, ts) if bars else {}
            quality = []
            if not price_ok:
                quality.append("אין נתוני מחיר למניה")
            if kind == "telegram":
                quality.append("זמן = זמן ההודעה בטלגרם")
            if kind == "sec":
                quality.append("זמן = זמן קבלת הדיווח ב-SEC (מדויק)")
            if r5 is None:
                quality.append("עוד לא עברו 5 ימי מסחר — התוצאה לא נמדדה")
            findings.append({"id": tid, "source": source, "kind": kind, "published_at": ts.isoformat(),
                             "source_url": url, "official": kind == "sec", "mentioned_price": mentioned,
                             **ctx, "entry_day": entry_day.isoformat() if entry_day else None, "entry_price": entry,
                             "r1": r1, "r5": r5, "r20": r20, "excess_r5": (r5 - spy_r5) if r5 is not None and spy_r5 is not None else None,
                             "risk_verdict": verdict, "data_quality": quality,
                             "unknown": UNKNOWN.get(kind, UNKNOWN["manual"])})
        markets = []
        names = NAMES.get(sym, [])
        if names:
            pats = [f"%{n}%" for n in names]
            for m in conn.execute("""SELECT id, question, ref_price, one_day_change, end_date, url, resolution_source,
                                            neg_risk_other, accepting_orders, closed, updated_at, source_updated_at,
                                            (SELECT count(*) FROM market_rules r WHERE r.market_id = markets.id)
                                     FROM markets WHERE active AND NOT closed AND question ILIKE ANY(%s)
                                     ORDER BY volume_24h DESC NULLS LAST LIMIT 5""", (pats,)).fetchall():
                d = dict(zip(["id", "question", "price", "one_day_change", "end_date", "url", "resolution_source",
                              "neg_risk_other", "accepting_orders", "closed", "ingested_at", "source_time", "versions"], m))
                markets.append({**{k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in d.items()},
                                "link": "keyword", "resolution_risk": resolution_risk(d, d["versions"] or 0),
                                "unknown": UNKNOWN["polymarket"]})
        out.append({"symbol": sym, "price": last["c"] if last else None, "price_day": last["day"] if last else None,
                    "day_change": (last["c"] / prev["c"] - 1) if last and prev else None,
                    "price_source": "Yahoo Finance daily bars" if price_ok else None,
                    "findings": findings, "polymarket": markets})
    return {"generated_at": datetime.now(timezone.utc).isoformat(), "watchlist": out,
            "note": "מקושרים למניה רק לפי סימול (טיפים, SEC) או שם החברה (Polymarket). שום קשר סיבתי לא נטען."}
