"""ETF paper study v2 job: fetch daily bars, seal next-session intents, store the replay report.

  docker compose run --rm etf --once     # one run now
  (service `etf` loops: runs every 30 min after 17:15 New York time on weekdays until done)

Data: Yahoo Finance chart API, raw (unadjusted) daily OHLCV + dividend events.
Paper only; there is no broker code anywhere in this project.
"""
from __future__ import annotations

import argparse
import logging
import time as _time
from datetime import date, datetime, time, timedelta, timezone
from email.utils import parsedate_to_datetime
from zoneinfo import ZoneInfo

import httpx
from psycopg.types.json import Jsonb

from . import db
from .etf_paper import ACCOUNTS, NY, PROTOCOL, UNIVERSE, build_intent, canonical_hash, replay, valid_bar

log = logging.getLogger("etf")
YAHOO = "https://query1.finance.yahoo.com/v8/finance/chart/"
READY_AFTER = time(17, 15)  # NY time: the day's bar is final well after the 16:00 close


def parse_chart(js: dict) -> tuple[list[dict], list[dict]]:
    res = js["chart"]["result"][0]
    tz = ZoneInfo(res["meta"].get("exchangeTimezoneName") or "America/New_York")
    q = res["indicators"]["quote"][0]
    bars = []
    for i, ts in enumerate(res.get("timestamp") or []):
        b = {"day": datetime.fromtimestamp(ts, tz).date().isoformat(),
             "o": q["open"][i], "h": q["high"][i], "l": q["low"][i], "c": q["close"][i], "v": q["volume"][i]}
        if valid_bar(b):
            bars.append(b)
    divs = [{"ex_date": datetime.fromtimestamp(int(d["date"]), tz).date().isoformat(), "amount": float(d["amount"])}
            for d in ((res.get("events") or {}).get("dividends") or {}).values()]
    return bars, divs


def fetch(client: httpx.Client, symbol: str) -> tuple[list[dict], list[dict], datetime | None]:
    r = None
    for attempt in range(4):
        r = client.get(YAHOO + symbol, params={"range": "2y", "interval": "1d", "events": "div"})
        if r.status_code not in (429, 500, 502, 503, 504):
            break
        _time.sleep(5 * 2 ** attempt)
    r.raise_for_status()
    server_date = parsedate_to_datetime(r.headers["Date"]) if "Date" in r.headers else None
    bars, divs = parse_chart(r.json())
    return bars, divs, server_date


def complete_bars(bars: list[dict], now_ny: datetime) -> list[dict]:
    today = now_ny.date().isoformat()
    ready = now_ny.time() >= READY_AFTER
    return [b for b in bars if b["day"] < today or (b["day"] == today and ready)]


def store(conn, symbol, bars, divs) -> int:
    with conn.cursor() as cur:
        cur.executemany("INSERT INTO etf_bars (symbol, day, o, h, l, c, v) VALUES (%s,%s,%s,%s,%s,%s,%s) "
                        "ON CONFLICT DO NOTHING",
                        [(symbol, b["day"], b["o"], b["h"], b["l"], b["c"], b["v"]) for b in bars])
        n = cur.rowcount
        cur.executemany("INSERT INTO etf_dividends (symbol, ex_date, amount) VALUES (%s,%s,%s) ON CONFLICT DO NOTHING",
                        [(symbol, d["ex_date"], d["amount"]) for d in divs])
    return n


def load(conn):
    bars: dict[str, dict[str, dict]] = {s: {} for s in UNIVERSE}
    for sym, day, o, h, l, c, v in conn.execute("SELECT symbol, day, o, h, l, c, v FROM etf_bars ORDER BY day"):
        bars.setdefault(sym, {})[day.isoformat()] = {"o": o, "h": h, "l": l, "c": c, "v": v}
    divs = [{"symbol": s, "ex_date": d.isoformat(), "amount": a}
            for s, d, a in conn.execute("SELECT symbol, ex_date, amount FROM etf_dividends")]
    intents = {(a, b.isoformat()): {"created_at": c, "payload": p}
               for a, b, c, p in conn.execute("SELECT account, based_on, created_at, payload FROM etf_intents")}
    return bars, divs, intents


def sessions_and_prev(bars):
    days = sorted(bars["SPY"])
    prev = {d: days[i - 1] for i, d in enumerate(days) if i > 0}
    window = [d for d in days if PROTOCOL["window_start"] <= d <= PROTOCOL["window_end"] and d in prev]
    return window, prev


def seal_intents(conn, bars, based_on: str, health: dict, now: datetime) -> list[str]:
    """Seal intents decided on `based_on`'s bar, only while the next session hasn't opened yet."""
    d = date.fromisoformat(based_on)
    if not (date.fromisoformat(PROTOCOL["window_start"]) - timedelta(days=7) <= d < date.fromisoformat(PROTOCOL["window_end"])):
        return []
    nxt = d + timedelta(days=1)
    while nxt.weekday() >= 5:
        nxt += timedelta(days=1)
    if now >= datetime.combine(nxt, time(9, 30), NY):  # too late for the next open: record nothing
        log.warning("too late to seal intents based on %s", based_on)
        return []
    sealed = []
    for acct in ACCOUNTS:
        hist = {s: [bars[s][x] | {"day": x} for x in sorted(bars[s]) if x <= based_on][-300:] for s in UNIVERSE}
        payload = build_intent(hist, d, acct, health)
        cur = conn.execute("INSERT INTO etf_intents (account, based_on, sha256, payload) VALUES (%s,%s,%s,%s) "
                           "ON CONFLICT DO NOTHING", (acct, based_on, canonical_hash(payload), Jsonb(payload)))
        if cur.rowcount:
            sealed.append(acct)
    return sealed


def run_once(conn, client: httpx.Client, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    now_ny = now.astimezone(NY)
    with db.job_run(conn, "etf_paper") as run:
        skews, problems, new_rows = [], [], 0
        for sym in UNIVERSE:
            try:
                bars, divs, server_date = fetch(client, sym)
            except Exception as e:  # noqa: BLE001 - recorded as a data problem
                problems.append(f"{sym}:fetch_failed:{type(e).__name__}")
                continue
            if server_date is not None:
                skews.append(abs((now - server_date).total_seconds()))
            new_rows += store(conn, sym, complete_bars(bars, now_ny), divs)
        bars, divs, intents = load(conn)
        latest = max(bars["SPY"]) if bars["SPY"] else None
        clock_ok = bool(skews) and max(skews) <= PROTOCOL["max_clock_skew_s"]
        health = {"clock_ok": clock_ok, "clock_skew_s": max(skews) if skews else None,
                  "fetch_problems": problems, "checked_at": now.isoformat()}
        sealed = seal_intents(conn, bars, latest, health, now) if latest and not problems else []
        if problems:
            log.warning("fetch problems, no intents sealed: %s", problems)
        bars, divs, intents = load(conn)
        sessions, prev = sessions_and_prev(bars)
        report = replay(bars, intents, divs, sessions, prev)
        report.update(generated_at=now.isoformat(), latest_bar=latest, health=health, sealed_now=sealed,
                      intents_total=len(intents))
        conn.execute("INSERT INTO research_reports (kind, result) VALUES ('etf_paper_v2', %s)", (Jsonb(report),))
        run["items"] = new_rows
    log.info("etf: latest bar %s, new rows %d, sealed %s, sessions %d", latest, new_rows, sealed, len(sessions))
    return report


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true")
    args = ap.parse_args()
    conn = db.connect()
    db.init_schema(conn)
    client = httpx.Client(timeout=30, headers={"User-Agent": "Mozilla/5.0 (pmintel research)"})
    if args.once:
        run_once(conn, client)
        return
    done_for = None
    while True:
        now_ny = datetime.now(NY)
        if now_ny.weekday() < 5 and now_ny.time() >= READY_AFTER and done_for != now_ny.date():
            try:
                rep = run_once(conn, client)
                # done once today's bar is in (or by 21:00 NY on market holidays)
                if rep.get("latest_bar") == now_ny.date().isoformat() or now_ny.hour >= 21:
                    done_for = now_ny.date()
            except Exception:
                log.exception("etf run failed; retrying in 30 min")
        _time.sleep(1800)


if __name__ == "__main__":
    main()
