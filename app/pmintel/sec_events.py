"""Two more SEC-sourced signals, measured like every other tip source.

1. "SEC: משקיע אקטיביסט (13D)" — a Schedule 13D (an investor crossed 5% with intent to influence).
   Tip time = SEC acceptance time.
2. "SEC: קפיצה אחרי דוח (+5%)" — an earnings 8-K (Item 2.02) after which the stock closed >= 5% above
   the previous close AND >= 3 points above SPY on the reaction day (post-earnings drift). Tip time =
   the close of the reaction day, so the measured entry is the NEXT open, after the jump is known.

Tickers come from SEC's own company_tickers.json (CIK -> ticker). Same EDGAR daily index, pacing and
User-Agent as sec_insider.

  docker compose run --rm tips python -m pmintel.sec_events --days 30
"""
from __future__ import annotations

import argparse
import logging
import re
from datetime import date, datetime, time, timedelta

from . import db, stockcheck, tips
from .sec_insider import ET_TZ, Sec

log = logging.getLogger("sec.events")
SOURCE_13D = "SEC: משקיע אקטיביסט (13D)"
SOURCE_PEAD = "SEC: קפיצה אחרי דוח (+5%)"
TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
JUMP, EXCESS = 0.05, 0.03
MIN_DOLLAR_VOLUME = 5_000_000


def index_paths(index_text: str, forms: tuple[str, ...]) -> list[str]:
    """Paths of filings whose form type is exactly one of `forms` (amendments excluded), de-duplicated."""
    out, seen = [], set()
    for line in index_text.splitlines():
        for f in forms:
            if line.startswith(f + " ") and not line[len(f):].startswith("/"):
                path = line.split()[-1]
                if path.startswith("edgar/") and path not in seen:
                    seen.add(path)
                    out.append(path)
    return out


def header(text: str) -> dict:
    """Acceptance time, subject/filer CIKs and 8-K items from the SGML header of a submission."""
    h = text.split("</SEC-HEADER>", 1)[0]
    acc = re.search(r"<ACCEPTANCE-DATETIME>(\d{14})", h)
    subject = re.search(r"SUBJECT COMPANY:.*?CENTRAL INDEX KEY:\s*(\d+)", h, re.S)
    filer = re.search(r"(?:FILER|FILED BY):.*?CENTRAL INDEX KEY:\s*(\d+)", h, re.S)
    return {"accepted": datetime.strptime(acc.group(1), "%Y%m%d%H%M%S").replace(tzinfo=ET_TZ) if acc else None,
            "subject_cik": int(subject.group(1)) if subject else None,
            "filer_cik": int(filer.group(1)) if filer else None,
            "items": re.findall(r"ITEM INFORMATION:\s*(.+)", h)}


def load_tickers(sec: Sec) -> dict[int, str]:
    import json
    raw = sec.get(TICKERS_URL)
    if not raw:
        return {}
    out: dict[int, str] = {}
    for row in json.loads(raw).values():
        t = str(row.get("ticker", "")).upper()
        if re.fullmatch(r"[A-Z]{1,5}", t):
            out.setdefault(int(row["cik_str"]), t)  # first listed = primary class
    return out


def reaction_day(bars_days: list[str], accepted: datetime) -> str | None:
    """First session whose close is after the filing: same day if filed before 16:00 New York."""
    a = accepted.astimezone(ET_TZ)
    d = a.date().isoformat()
    for day in bars_days:
        if day > d or (day == d and a.time() < time(16, 0)):
            return day
    return None


def evaluate_pead(bars: list[dict], spy: list[dict], accepted: datetime) -> dict | None:
    """Reaction-day return vs previous close, excess over SPY, liquidity. None if not yet known."""
    days = [b["day"] for b in bars]
    rd = reaction_day(days, accepted)
    if rd is None:
        return None
    i = days.index(rd)
    if i == 0:
        return None
    ret = bars[i]["c"] / bars[i - 1]["c"] - 1
    sd = {b["day"]: b for b in spy}
    sdays = [b["day"] for b in spy]
    if rd not in sd or sdays.index(rd) == 0:
        return None
    j = sdays.index(rd)
    spy_ret = spy[j]["c"] / spy[j - 1]["c"] - 1
    dollar = sorted(b["c"] * b["v"] for b in bars[max(0, i - 20):i]) or [0]
    return {"reaction_day": rd, "ret": ret, "spy_ret": spy_ret, "excess": ret - spy_ret,
            "median_dollar_volume": dollar[len(dollar) // 2],
            "signal": ret >= JUMP and ret - spy_ret >= EXCESS and dollar[len(dollar) // 2] >= MIN_DOLLAR_VOLUME}


def process_day(conn, sec: Sec, day: date, tickers: dict[int, str]) -> dict:
    stats = {"day": day.isoformat(), "13d": 0, "earnings": 0}
    q = (day.month - 1) // 3 + 1
    idx = sec.get(f"edgar/daily-index/{day.year}/QTR{q}/form.{day:%Y%m%d}.idx")
    if idx is None:
        stats["missing_index"] = True
        return stats
    for path in index_paths(idx, ("SCHEDULE 13D", "SC 13D")):
        acc = path.rsplit("/", 1)[-1].removesuffix(".txt")
        text = sec.get(path) or ""
        hd = header(text)
        sym = tickers.get(hd["subject_cik"] or -1)
        if not sym or not hd["accepted"] or hd["filer_cik"] == hd["subject_cik"]:
            continue
        cur = conn.execute("INSERT INTO sec_events (accession, kind, symbol, accepted) VALUES (%s,'13d',%s,%s) "
                           "ON CONFLICT DO NOTHING", (acc, sym, hd["accepted"]))
        if cur.rowcount and tips.record(conn, SOURCE_13D, sym, hd["accepted"], kind="sec"):
            stats["13d"] += 1
    for path in index_paths(idx, ("8-K",)):
        cik = int(path.split("/")[2]) if path.count("/") >= 3 else None
        sym = tickers.get(cik or -1)
        if not sym:
            continue  # only listed companies; saves a request per filing
        acc = path.rsplit("/", 1)[-1].removesuffix(".txt")
        text = sec.get(path) or ""
        hd = header(text)
        if not hd["accepted"] or not any("Results of Operations" in i for i in hd["items"]):
            continue
        cur = conn.execute("INSERT INTO sec_events (accession, kind, symbol, accepted) VALUES (%s,'earnings',%s,%s) "
                           "ON CONFLICT DO NOTHING", (acc, sym, hd["accepted"]))
        stats["earnings"] += cur.rowcount
    conn.execute("INSERT INTO sec_events_done (day) VALUES (%s) ON CONFLICT DO NOTHING", (day,))
    return stats


def evaluate_pending(conn, fetch=stockcheck.fetch) -> int:
    """Turn earnings events into tips once their reaction day has closed."""
    rows = conn.execute("""SELECT accession, symbol, accepted FROM sec_events
                           WHERE kind = 'earnings' AND evaluated_at IS NULL
                             AND accepted > now() - interval '45 days'""").fetchall()
    if not rows:
        return 0
    spy = fetch("SPY")[0]
    n = 0
    for acc, sym, accepted in rows:
        try:
            bars = tips._cached_fetch(sym)[0] if fetch is stockcheck.fetch else fetch(sym)[0]
        except Exception as e:  # noqa: BLE001
            log.info("no bars for %s: %s", sym, e)
            continue
        ev = evaluate_pead(bars, spy, accepted)
        if ev is None:
            continue
        conn.execute("""UPDATE sec_events SET evaluated_at = now(), reaction_day = %s, reaction = %s,
                        excess = %s, signal = %s WHERE accession = %s""",
                     (ev["reaction_day"], ev["ret"], ev["excess"], ev["signal"], acc))
        if ev["signal"]:
            ts = datetime.combine(date.fromisoformat(ev["reaction_day"]), time(16, 1), ET_TZ)
            if tips.record(conn, SOURCE_PEAD, sym, ts, kind="sec"):
                n += 1
    return n


def run(conn, sec: Sec, days: int, today: date | None = None, fetch=stockcheck.fetch) -> dict:
    today = today or datetime.now(ET_TZ).date()
    done = {r[0] for r in conn.execute("SELECT day FROM sec_events_done WHERE day >= %s",
                                       (today - timedelta(days=days),))}
    out = {"days": [], "pead_tips": 0}
    with db.job_run(conn, "sec_events") as job:
        tickers = None
        for d in range(days, 0, -1):
            day = today - timedelta(days=d)
            if day.weekday() >= 5 or day in done:
                continue
            tickers = tickers if tickers is not None else load_tickers(sec)
            s = process_day(conn, sec, day, tickers)
            log.info("sec events %s", s)
            out["days"].append(s)
        out["pead_tips"] = evaluate_pending(conn, fetch)
        job["items"] = sum(s["13d"] for s in out["days"]) + out["pead_tips"]
    return out


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=5)
    a = ap.parse_args()
    conn = db.connect()
    db.init_schema(conn)
    res = run(conn, Sec(), a.days)
    print(f"days: {len(res['days'])}, 13D tips: {sum(s['13d'] for s in res['days'])}, "
          f"earnings events: {sum(s['earnings'] for s in res['days'])}, earnings-jump tips: {res['pead_tips']}")
    print(f"outcomes computed: {tips.update_outcomes(conn)}")


if __name__ == "__main__":
    main()
