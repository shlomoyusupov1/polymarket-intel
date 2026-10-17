import json
import os
from datetime import date, datetime

import httpx
import pytest

from pmintel import sec_events as se
from pmintel.sec_insider import ET_TZ, Sec

IDX = """Form Type   Company Name                                                  CIK         Date Filed  File Name
---------------------------------------------------------------------------------------------------------------------------------------------
8-K              ACME CORP                                                     1111111     20261015    edgar/data/1111111/0001111111-26-000010.txt
8-K              ACME CORP                                                     1111111     20261015    edgar/data/1111111/0001111111-26-000011.txt
8-K/A            ACME CORP                                                     1111111     20261015    edgar/data/1111111/0001111111-26-000012.txt
8-K              PRIVATE CO                                                    7777777     20261015    edgar/data/7777777/0007777777-26-000001.txt
SCHEDULE 13D     ACME CORP                                                     1111111     20261015    edgar/data/1111111/0009999999-26-000001.txt
SCHEDULE 13D     ACTIVIST FUND LP                                              9999999     20261015    edgar/data/1111111/0009999999-26-000001.txt
SCHEDULE 13D/A   ACME CORP                                                     1111111     20261015    edgar/data/1111111/0009999999-26-000002.txt
"""


def hdr(accepted, subject=None, filer=None, items=()):
    parts = [f"<SEC-HEADER>x\n<ACCEPTANCE-DATETIME>{accepted}\n"]
    if subject:
        parts.append(f"SUBJECT COMPANY:\n\n\tCOMPANY DATA:\n\t\tCOMPANY CONFORMED NAME:\t\t\tACME CORP\n\t\tCENTRAL INDEX KEY:\t\t\t{subject:010d}\n")
    if filer:
        label = "FILED BY" if subject else "FILER"
        parts.append(f"{label}:\n\n\tCOMPANY DATA:\n\t\tCENTRAL INDEX KEY:\t\t\t{filer:010d}\n")
    parts += [f"ITEM INFORMATION:\t\t{i}\n" for i in items]
    return "".join(parts) + "</SEC-HEADER>\n<DOCUMENT>..."


def test_index_paths_exact_forms_only():
    assert se.index_paths(IDX, ("8-K",)) == ["edgar/data/1111111/0001111111-26-000010.txt",
                                            "edgar/data/1111111/0001111111-26-000011.txt",
                                            "edgar/data/7777777/0007777777-26-000001.txt"]
    assert se.index_paths(IDX, ("SCHEDULE 13D", "SC 13D")) == ["edgar/data/1111111/0009999999-26-000001.txt"]


def test_header_parsing():
    h = se.header(hdr("20261015083000", subject=1111111, filer=9999999))
    assert h["subject_cik"] == 1111111 and h["filer_cik"] == 9999999
    assert h["accepted"].isoformat() == "2026-10-15T08:30:00-04:00"
    e = se.header(hdr("20261015163500", filer=1111111, items=["Results of Operations and Financial Condition",
                                                               "Financial Statements and Exhibits"]))
    assert e["filer_cik"] == 1111111 and "Results of Operations and Financial Condition" in e["items"]


def bars(days, closes, vol=1_000_000):
    return [{"day": d, "o": c, "h": c, "l": c, "c": c, "v": vol} for d, c in zip(days, closes)]


DAYS = ["2026-10-12", "2026-10-13", "2026-10-14", "2026-10-15", "2026-10-16", "2026-10-19"]


def test_reaction_day_and_signal():
    after_close = datetime(2026, 10, 15, 16, 35, tzinfo=ET_TZ)
    assert se.reaction_day(DAYS, after_close) == "2026-10-16"
    assert se.reaction_day(DAYS, datetime(2026, 10, 15, 8, 0, tzinfo=ET_TZ)) == "2026-10-15"
    stock = bars(DAYS, [20, 20, 20, 20, 22, 22.5])            # +10% on the reaction day, $20M/day
    spy = bars(DAYS, [500, 500, 500, 500, 505, 505])          # +1%
    ev = se.evaluate_pead(stock, spy, after_close)
    assert ev["reaction_day"] == "2026-10-16" and abs(ev["ret"] - 0.10) < 1e-12 and ev["signal"] is True
    assert se.evaluate_pead(bars(DAYS, [20, 20, 20, 20, 20.6, 21]), spy, after_close)["signal"] is False  # +3%
    assert se.evaluate_pead(bars(DAYS, [20, 20, 20, 20, 22, 22], vol=1000), spy, after_close)["signal"] is False  # illiquid
    assert se.evaluate_pead(stock, spy, datetime(2026, 10, 19, 17, 0, tzinfo=ET_TZ)) is None  # not closed yet


DB = os.environ.get("PMINTEL_TEST_DB")


@pytest.mark.skipif(not DB, reason="PMINTEL_TEST_DB not set")
def test_run_records_13d_and_earnings_jump(monkeypatch):
    from pmintel import db, stockcheck, tips
    conn = db.connect(DB)
    conn.execute("DROP TABLE IF EXISTS tips, sec_events, sec_events_done, job_runs CASCADE")
    db.init_schema(conn)
    monkeypatch.setattr(stockcheck, "fetch", lambda sym, client=None: ([], {}))
    tips._FETCH.clear()
    docs = {
        "0009999999-26-000001": hdr("20261015090000", subject=1111111, filer=9999999),
        "0001111111-26-000010": hdr("20261015163500", filer=1111111, items=["Results of Operations and Financial Condition"]),
        "0001111111-26-000011": hdr("20261015170000", filer=1111111, items=["Other Events"]),
    }
    tick = json.dumps({"0": {"cik_str": 1111111, "ticker": "ACME", "title": "Acme"}})

    def handler(req):
        p = req.url.path
        if p.endswith("company_tickers.json"):
            return httpx.Response(200, text=tick)
        if p.endswith("form.20261015.idx"):
            return httpx.Response(200, text=IDX)
        for acc, body in docs.items():
            if p.endswith(acc + ".txt"):
                return httpx.Response(200, text=body)
        assert "7777777" not in p  # unlisted filer: never fetched
        return httpx.Response(404)
    sec = Sec(httpx.Client(transport=httpx.MockTransport(handler)), min_interval=0, sleep=lambda s: None)
    stock, spy = bars(DAYS, [20, 20, 20, 20, 22, 22.5], vol=1_000_000), bars(DAYS, [500, 500, 500, 500, 505, 505])
    res = se.run(conn, sec, days=3, today=date(2026, 10, 16), fetch=lambda s: (spy if s == "SPY" else stock, {}))
    day = next(d for d in res["days"] if d["day"] == "2026-10-15")
    assert day["13d"] == 1 and day["earnings"] == 1 and res["pead_tips"] == 1
    rows = dict(conn.execute("SELECT source, ts FROM tips").fetchall())
    assert rows[se.SOURCE_PEAD].astimezone(ET_TZ).isoformat() == "2026-10-16T16:01:00-04:00"  # after the jump
    assert se.SOURCE_13D in rows
    conn.close()
