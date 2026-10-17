import os
from datetime import date, datetime, timezone

import httpx
import pytest

from pmintel import sec_insider as si

IDX = """Description:           Daily Index of EDGAR Dissemination Feed by Form Type
Last Data Received:    October 15, 2026
Form Type   Company Name                                                  CIK         Date Filed  File Name
---------------------------------------------------------------------------------------------------------------------------------------------
3                ACME CORP                                                     1111111     20261015    edgar/data/1111111/0000000001-26-000001.txt
4                ACME CORP                                                     1111111     20261015    edgar/data/1111111/0000000002-26-000002.txt
4                DOE JOHN                                                      2222222     20261015    edgar/data/1111111/0000000002-26-000002.txt
4/A              ACME CORP                                                     1111111     20261015    edgar/data/1111111/0000000003-26-000003.txt
40-F             BIG CO                                                        3333333     20261015    edgar/data/3333333/0000000004-26-000004.txt
4                ACME CORP                                                     1111111     20261015    edgar/data/1111111/0000000005-26-000005.txt
"""


def filing(code="P", ad="A", shares="10000", price="5.00", officer="1", symbol="ACME", owner="2222222",
           accepted="20261015163001"):
    return f"""<SEC-DOCUMENT>0000000002-26-000002.txt : 20261015
<SEC-HEADER>0000000002-26-000002.hdr.sgml : 20261015
<ACCEPTANCE-DATETIME>{accepted}
ACCESSION NUMBER:		0000000002-26-000002
</SEC-HEADER>
<DOCUMENT>
<TYPE>4
<TEXT>
<XML>
<?xml version="1.0"?>
<ownershipDocument>
  <issuer><issuerCik>1111111</issuerCik><issuerName>ACME CORP</issuerName><issuerTradingSymbol>{symbol}</issuerTradingSymbol></issuer>
  <reportingOwner>
    <reportingOwnerId><rptOwnerCik>{owner}</rptOwnerCik><rptOwnerName>DOE JOHN</rptOwnerName></reportingOwnerId>
    <reportingOwnerRelationship><isDirector>0</isDirector><isOfficer>{officer}</isOfficer><officerTitle>CEO</officerTitle></reportingOwnerRelationship>
  </reportingOwner>
  <nonDerivativeTable>
    <nonDerivativeTransaction>
      <transactionCoding><transactionFormType>4</transactionFormType><transactionCode>{code}</transactionCode></transactionCoding>
      <transactionAmounts>
        <transactionShares><value>{shares}</value></transactionShares>
        <transactionPricePerShare><value>{price}</value></transactionPricePerShare>
        <transactionAcquiredDisposedCode><value>{ad}</value></transactionAcquiredDisposedCode>
      </transactionAmounts>
    </nonDerivativeTransaction>
  </nonDerivativeTable>
</ownershipDocument>
</XML>
</TEXT>
</DOCUMENT>
</SEC-DOCUMENT>"""


def test_index_keeps_plain_form4_once():
    assert si.form4_paths(IDX) == ["edgar/data/1111111/0000000002-26-000002.txt",
                                   "edgar/data/1111111/0000000005-26-000005.txt"]


def test_parse_open_market_purchase_by_officer():
    b = si.parse_filing(filing())
    assert b["symbol"] == "ACME" and b["value"] == 50000 and b["price"] == 5.0 and b["title"] == "CEO"
    assert b["accepted"].isoformat() == "2026-10-15T16:30:01-04:00"


@pytest.mark.parametrize("kw", [dict(code="S"), dict(code="A"), dict(ad="D"), dict(shares="100"),
                                dict(officer="0"), dict(symbol="")])
def test_not_a_signal(kw):
    assert si.parse_filing(filing(**kw)) is None  # sale, grant, disposal, < $25k, not insider, no ticker


DB = os.environ.get("PMINTEL_TEST_DB")


@pytest.mark.skipif(not DB, reason="PMINTEL_TEST_DB not set")
def test_day_processing_and_cluster(monkeypatch):
    from pmintel import db, stockcheck, tips
    conn = db.connect(DB)
    conn.execute("DROP TABLE IF EXISTS tips, sec_buys, sec_index_done, job_runs CASCADE")
    db.init_schema(conn)
    monkeypatch.setattr(stockcheck, "fetch", lambda sym, client=None: ([], {}))
    tips._FETCH.clear()
    docs = {"0000000002-26-000002": filing(owner="2222222"),
            "0000000005-26-000005": filing(owner="9999999", accepted="20261015170000")}

    def handler(req):
        path = req.url.path
        assert req.headers["User-Agent"]
        if path.endswith("form.20261015.idx"):
            return httpx.Response(200, text=IDX)
        for acc, body in docs.items():
            if path.endswith(acc + ".txt"):
                return httpx.Response(200, text=body)
        return httpx.Response(404)
    sec = si.Sec(httpx.Client(transport=httpx.MockTransport(handler), headers={"User-Agent": "test"}),
                 min_interval=0, sleep=lambda s: None)
    out = si.run(conn, sec, days=3, today=date(2026, 10, 16))
    day = next(s for s in out if s["day"] == "2026-10-15")
    assert day["filings"] == 2 and day["buys"] == 2
    srcs = dict(conn.execute("SELECT source, count(*) FROM tips GROUP BY 1").fetchall())
    assert srcs == {si.SOURCE_ONE: 1, si.SOURCE_CLUSTER: 1}  # second insider -> cluster; one tip per source/day
    assert next(s for s in out if s["day"] == "2026-10-14").get("missing_index")
    again = si.run(conn, sec, days=3, today=date(2026, 10, 16))
    assert all(s["day"] != "2026-10-15" for s in again)  # a finished day is never reprocessed
    conn.close()
