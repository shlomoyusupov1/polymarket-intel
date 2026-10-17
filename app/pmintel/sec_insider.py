"""Insider open-market purchases from SEC EDGAR Form 4 filings -> tips (measured like any other source).

Two sources in the tip leaderboard:
  "SEC: קניית מנהל"              one officer/director bought >= $25k on the open market (code P)
  "SEC: קנייה קבוצתית (2+ מנהלים)"  a second distinct insider bought the same stock within 10 days

The tip time is the filing's SEC acceptance time, so the outcome rule (entry at the next US open)
only uses information that was public. Data: the free EDGAR daily index + one request per filing,
paced under SEC's fair-access limit. SEC asks for a contact in the User-Agent (SEC_USER_AGENT).

  docker compose run --rm tips python -m pmintel.sec_insider --days 30   # backfill
"""
from __future__ import annotations

import argparse
import logging
import os
import re
import time
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import httpx

from . import db, tips

log = logging.getLogger("sec")
ET_TZ = ZoneInfo("America/New_York")
BASE = "https://www.sec.gov/Archives/"
SOURCE_ONE = "SEC: קניית מנהל"
SOURCE_CLUSTER = "SEC: קנייה קבוצתית (2+ מנהלים)"
MIN_VALUE = 25_000
UA = os.environ.get("SEC_USER_AGENT") or "pmintel personal research (github.com/shlomoyusupov1/polymarket-intel)"


class Sec:
    def __init__(self, client: httpx.Client | None = None, min_interval: float = 0.15, sleep=time.sleep):
        self.client = client or httpx.Client(timeout=30, headers={"User-Agent": UA, "Accept-Encoding": "gzip"})
        self.min_interval, self.sleep, self._last = min_interval, sleep, 0.0

    def get(self, path: str) -> str | None:
        for attempt in range(4):
            wait = self.min_interval - (time.monotonic() - self._last)
            if wait > 0:
                self.sleep(wait)
            self._last = time.monotonic()
            r = self.client.get(path if path.startswith("http") else BASE + path)
            if r.status_code == 404:
                return None
            if r.status_code in (429, 503):
                self.sleep(10 * (attempt + 1))
                continue
            r.raise_for_status()
            return r.text
        return None


def form4_paths(index_text: str) -> list[str]:
    """Paths of Form 4 filings (not amendments) from an EDGAR daily form.idx, de-duplicated."""
    out, seen = [], set()
    for line in index_text.splitlines():
        if not line.startswith("4 "):
            continue
        path = line.split()[-1]
        if path.startswith("edgar/") and path not in seen:
            seen.add(path)
            out.append(path)
    return out


def _t(el, path):
    x = el.find(path)
    return (x.text or "").strip() if x is not None and x.text else ""


def parse_filing(text: str) -> dict | None:
    """Open-market purchases by an officer/director in one Form 4 submission (.txt), or None."""
    m = re.search(r"<ACCEPTANCE-DATETIME>(\d{14})", text)
    x = re.search(r"<XML>\s*(.*?)\s*</XML>", text, re.S)
    if not m or not x:
        return None
    try:
        root = ET.fromstring(x.group(1))
    except ET.ParseError:
        return None
    symbol = _t(root, "issuer/issuerTradingSymbol").upper()
    owner = root.find("reportingOwner")
    if not symbol or owner is None or not re.fullmatch(r"[A-Z]{1,5}", symbol):
        return None
    rel = owner.find("reportingOwnerRelationship")
    is_dir = _t(rel, "isDirector") in ("1", "true") if rel is not None else False
    is_off = _t(rel, "isOfficer") in ("1", "true") if rel is not None else False
    if not (is_dir or is_off):
        return None  # 10% holders / funds are a different signal
    value = shares = 0.0
    for tr in root.findall("nonDerivativeTable/nonDerivativeTransaction"):
        if _t(tr, "transactionCoding/transactionCode") != "P":
            continue
        if _t(tr, "transactionAmounts/transactionAcquiredDisposedCode/value") != "A":
            continue
        try:
            sh = float(_t(tr, "transactionAmounts/transactionShares/value") or 0)
            px = float(_t(tr, "transactionAmounts/transactionPricePerShare/value") or 0)
        except ValueError:
            continue
        shares += sh
        value += sh * px
    if value < MIN_VALUE:
        return None
    accepted = datetime.strptime(m.group(1), "%Y%m%d%H%M%S").replace(tzinfo=ET_TZ)
    return {"symbol": symbol, "accepted": accepted, "value": value, "shares": shares,
            "price": value / shares if shares else None,
            "owner_cik": _t(owner, "reportingOwnerId/rptOwnerCik"),
            "title": _t(rel, "officerTitle") if rel is not None else "", "director": is_dir, "officer": is_off}


def record_buy(conn, buy: dict, accession: str, url: str | None = None) -> list[str]:
    """Store the purchase; add the single-insider tip and, if a different insider bought the same
    stock within the previous 10 days, the cluster tip. (tips dedups each source per symbol/24 h.)"""
    cur = conn.execute("""INSERT INTO sec_buys (accession, symbol, owner_cik, accepted, value, shares, title)
                          VALUES (%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (accession) DO NOTHING""",
                       (accession, buy["symbol"], buy["owner_cik"], buy["accepted"], buy["value"], buy["shares"],
                        buy["title"]))
    if not cur.rowcount:
        return []
    out = []
    if tips.record(conn, SOURCE_ONE, buy["symbol"], buy["accepted"], buy["price"], kind="sec", source_url=url):
        out.append(SOURCE_ONE)
    others = conn.execute("""SELECT count(DISTINCT owner_cik) FROM sec_buys WHERE symbol = %s AND owner_cik <> %s
                             AND accepted BETWEEN %s - interval '10 days' AND %s""",
                          (buy["symbol"], buy["owner_cik"], buy["accepted"], buy["accepted"])).fetchone()[0]
    if others >= 1 and tips.record(conn, SOURCE_CLUSTER, buy["symbol"], buy["accepted"], buy["price"], kind="sec",
                                   source_url=url):
        out.append(SOURCE_CLUSTER)
    return out


def process_day(conn, sec: Sec, day: date) -> dict:
    stats = {"day": day.isoformat(), "filings": 0, "buys": 0, "tips": 0}
    q = (day.month - 1) // 3 + 1
    idx = sec.get(f"edgar/daily-index/{day.year}/QTR{q}/form.{day:%Y%m%d}.idx")
    if idx is None:
        stats["missing_index"] = True  # weekend/holiday, or not published yet
        return stats
    for path in form4_paths(idx):
        stats["filings"] += 1
        accession = path.rsplit("/", 1)[-1].removesuffix(".txt")
        try:
            text = sec.get(path)
        except httpx.HTTPError as e:
            log.warning("filing %s: %s", path, e)
            continue
        buy = parse_filing(text or "")
        if buy:
            stats["buys"] += 1
            stats["tips"] += len(record_buy(conn, buy, accession, BASE + path))
    conn.execute("INSERT INTO sec_index_done (day, filings, buys) VALUES (%s,%s,%s) ON CONFLICT (day) DO UPDATE "
                 "SET filings = EXCLUDED.filings, buys = EXCLUDED.buys, done_at = now()",
                 (day, stats["filings"], stats["buys"]))
    return stats


def run(conn, sec: Sec, days: int, today: date | None = None) -> list[dict]:
    """Process the last `days` calendar days that are not done yet (weekdays only)."""
    today = today or datetime.now(ET_TZ).date()
    done = {r[0] for r in conn.execute("SELECT day FROM sec_index_done WHERE day >= %s",
                                       (today - timedelta(days=days),))}
    out = []
    with db.job_run(conn, "sec_insider") as job:
        for d in range(days, 0, -1):  # yesterday back to `days` ago; today's index isn't final yet
            day = today - timedelta(days=d)
            if day.weekday() >= 5 or day in done:
                continue
            s = process_day(conn, sec, day)
            log.info("sec %s", s)
            out.append(s)
        job["items"] = sum(s["tips"] for s in out)
    return out


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=5)
    a = ap.parse_args()
    conn = db.connect()
    db.init_schema(conn)
    res = run(conn, Sec(), a.days)
    print(f"days processed: {len(res)}, insider buys: {sum(s['buys'] for s in res)}, tips: {sum(s['tips'] for s in res)}")
    print(f"outcomes computed: {tips.update_outcomes(conn)}")


if __name__ == "__main__":
    main()
