"""Tip tracker: record stock tips from chat groups and measure what happened afterwards.

Privacy: only the symbol, an optional mentioned price, the group title, message id and time are
stored. No message text, no sender names or phone numbers.

Outcome rule (fixed in advance, same for every source): entry = open of the first US session that
starts AFTER the tip time; returns at the close of that session (1d) and of the 5th and 20th
sessions; worst drawdown and best run-up within 20 sessions; SPY over exactly the same sessions as the
benchmark ("excess" = tip minus SPY). No costs or slippage are added, so real results would be
worse — especially on OTC stocks.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, time, timezone
from zoneinfo import ZoneInfo

from . import stockcheck

log = logging.getLogger("tips")
NY = ZoneInfo("America/New_York")
STOP = {"OTC", "USD", "ETF", "IPO", "CEO", "CFO", "NYSE", "SEC", "FDA", "AI", "USA", "US", "EU", "UK", "OK",
        "PM", "AM", "EPS", "ATH", "DD", "TA", "PR", "NFT", "BTC", "ETH", "VIP", "ISIN", "ILS", "NIS", "API", "GDP"}
CASHTAG = re.compile(r"\$([A-Z]{1,5}(?:\.[A-Z])?)(?![A-Za-z0-9])")
LABELLED = re.compile(r"(?:סימול(?:\s+המניה)?|טיקר|Symbol|Ticker)\s*[:：\-]?\s*\$?([A-Z]{1,5})(?![A-Za-z])", re.I)
PAREN = re.compile(r"\(([A-Z]{2,5})\)")
PRICE = re.compile(r"(?:לימיט|limit|מחיר|price|entry|כניסה)[^\d\n]{0,25}?\$?\s*(\d{1,5}(?:\.\d{1,4})?)", re.I)


def extract(text: str) -> tuple[list[str], float | None]:
    """Symbols mentioned in a message (cashtags, 'Symbol: X', '(XXXX)') and a mentioned entry price."""
    if not text:
        return [], None
    found: list[str] = []
    for rx in (LABELLED, CASHTAG, PAREN):
        for m in rx.finditer(text):
            s = m.group(1).upper()
            if s not in STOP and s not in found:
                found.append(s)
    pm = PRICE.search(text)
    price = float(pm.group(1)) if pm else None
    return found[:5], price  # a message listing more than 5 symbols is a recap, keep the first 5


def record(conn, source: str, symbol: str, ts: datetime, mentioned_price: float | None = None,
           kind: str = "manual", chat_id: str | None = None, message_id: str | None = None,
           check: dict | None = None, source_url: str | None = None) -> int | None:
    """Insert one tip; the same source + symbol within 24 h counts once. Returns the id or None."""
    dup = conn.execute("""SELECT 1 FROM tips WHERE source = %s AND symbol = %s
                          AND ts BETWEEN %s - interval '24 hours' AND %s + interval '24 hours'""",
                       (source, symbol, ts, ts)).fetchone()
    if dup:
        return None
    verdict = flags = None
    if check is None:
        try:
            bars, meta = _cached_fetch(symbol)
            check = stockcheck.assess([b for b in bars if b["day"] <= ts.astimezone(NY).date().isoformat()], meta)
        except Exception as e:  # noqa: BLE001 - unknown/delisted symbols are still recorded
            log.info("check failed for %s: %s", symbol, e)
            check = None
    if check:
        verdict, flags = check.get("verdict"), [f["text"] for f in check.get("flags", [])]
    from psycopg.types.json import Jsonb
    if source_url is None and kind == "telegram" and chat_id and str(chat_id).startswith("-100") and message_id:
        source_url = f"https://t.me/c/{str(chat_id)[4:]}/{message_id}"  # opens for members of the group
    row = conn.execute("""INSERT INTO tips (source, kind, chat_id, message_id, ts, symbol, mentioned_price, verdict, flags,
                                            source_url)
                          VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
                       (source, kind, chat_id, message_id, ts, symbol, mentioned_price, verdict,
                        Jsonb(flags) if flags is not None else None, source_url)).fetchone()
    return row[0]


_FETCH: dict[str, tuple[float, tuple]] = {}


def _cached_fetch(symbol: str):
    """One Yahoo request per symbol per 10 minutes (a history import mentions the same symbols often)."""
    import time as _t
    hit = _FETCH.get(symbol)
    if hit and _t.monotonic() - hit[0] < 600:
        return hit[1]
    val = stockcheck.fetch(symbol)
    _FETCH[symbol] = (_t.monotonic(), val)
    return val


def outcome(bars: list[dict], tip_ts: datetime) -> dict | None:
    """Apply the fixed outcome rule. bars: daily bars with 'day','o','h','l','c' (NY dates)."""
    tip_ny = tip_ts.astimezone(NY)
    # a tip before the open of day D can be acted on at D's open; otherwise the next session
    first = tip_ny.date().isoformat() if tip_ny.time() < time(9, 30) else None
    after = [b for b in bars if (b["day"] >= first if first else b["day"] > tip_ny.date().isoformat())]
    if not after:
        return None
    entry = float(after[0]["o"])
    if entry <= 0:
        return None
    out = {"entry_day": after[0]["day"], "entry": entry, "sessions": len(after[:20])}
    for n, key in ((1, "r1"), (5, "r5"), (20, "r20")):
        out[key] = float(after[n - 1]["c"]) / entry - 1 if len(after) >= n else None
    win = after[:20]
    out["max_drawdown_20"] = min(float(b["l"]) for b in win) / entry - 1
    out["max_runup_20"] = max(float(b["h"]) for b in win) / entry - 1
    return out


def spy_window(spy: list[dict], entry_day: str) -> dict:
    """SPY over the same sessions as the tip: open of entry_day to the close of sessions 1/5/20."""
    after = [b for b in spy if b["day"] >= entry_day]
    if not after or after[0]["day"] != entry_day:
        return {"spy_r1": None, "spy_r5": None, "spy_r20": None}
    o = float(after[0]["o"])
    return {f"spy_r{n}": (float(after[n - 1]["c"]) / o - 1 if len(after) >= n else None) for n in (1, 5, 20)}


def update_outcomes(conn, fetch=stockcheck.fetch) -> int:
    """Fill or refresh outcomes (and the same-window SPY benchmark) for recent or incomplete tips."""
    rows = conn.execute("""SELECT id, symbol, ts FROM tips
                           WHERE (r20 IS NULL OR spy_r20 IS NULL) AND ts > now() - interval '60 days'
                           ORDER BY symbol""").fetchall()
    cache: dict[str, list] = {}
    try:
        spy = fetch("SPY")[0]
    except Exception as e:  # noqa: BLE001
        log.info("no SPY bars: %s", e)
        spy = []
    n = 0
    for tid, sym, ts in rows:
        if sym not in cache:
            try:
                cache[sym] = fetch(sym)[0]
            except Exception as e:  # noqa: BLE001
                log.info("no bars for %s: %s", sym, e)
                cache[sym] = []
        o = outcome(cache[sym], ts) if cache[sym] else None
        if o is None:
            continue
        b = spy_window(spy, o["entry_day"]) if spy else {"spy_r1": None, "spy_r5": None, "spy_r20": None}
        conn.execute("""UPDATE tips SET entry_day = %s, entry_price = %s, r1 = %s, r5 = %s, r20 = %s,
                        max_drawdown_20 = %s, max_runup_20 = %s, spy_r1 = %s, spy_r5 = %s, spy_r20 = %s,
                        outcome_at = now() WHERE id = %s""",
                     (o["entry_day"], o["entry"], o["r1"], o["r5"], o["r20"], o["max_drawdown_20"],
                      o["max_runup_20"], b["spy_r1"], b["spy_r5"], b["spy_r20"], tid))
        n += 1
    return n


def leaderboard(conn) -> list[dict]:
    cols = ["source", "tips", "measured_5d", "avg_r5", "median_r5", "win_rate_5d", "excess_r5", "beat_spy_5d",
            "measured_20d", "avg_r20", "excess_r20", "worst_drawdown_20", "red_share", "last_tip"]
    rows = conn.execute("""
        SELECT source, count(*),
               count(r5), avg(r5), percentile_cont(0.5) WITHIN GROUP (ORDER BY r5),
               avg(CASE WHEN r5 > 0 THEN 1.0 WHEN r5 IS NOT NULL THEN 0.0 END),
               avg(r5 - spy_r5), avg(CASE WHEN r5 > spy_r5 THEN 1.0 WHEN r5 IS NOT NULL AND spy_r5 IS NOT NULL THEN 0.0 END),
               count(r20), avg(r20), avg(r20 - spy_r20), min(max_drawdown_20),
               avg(CASE WHEN verdict = 'red' THEN 1.0 WHEN verdict IS NOT NULL THEN 0.0 END), max(ts)
        FROM tips GROUP BY source ORDER BY count(*) DESC""").fetchall()
    return [dict(zip(cols, r)) for r in rows]


def utcnow() -> datetime:
    return datetime.now(timezone.utc)
