import os
from datetime import datetime, timezone

import pytest

from pmintel import evidence

DAYS = ["2026-10-12", "2026-10-13", "2026-10-14", "2026-10-15", "2026-10-16"]
BARS = [{"day": d, "o": c, "h": c, "l": c, "c": c, "v": 1e6} for d, c in zip(DAYS, [10, 11, 12, 13, 14])]


def test_price_context_uses_the_close_before_publication():
    during = datetime(2026, 10, 14, 15, 0, tzinfo=timezone.utc)  # 11:00 New York, session open
    c = evidence.price_context(BARS, during)
    assert c["prior_day"] == "2026-10-13" and c["prior_close"] == 11 and c["current"] == 14
    after_close = datetime(2026, 10, 14, 21, 0, tzinfo=timezone.utc)  # 17:00 New York
    assert evidence.price_context(BARS, after_close)["prior_day"] == "2026-10-14"


def test_resolution_risk_flags():
    m = {"neg_risk_other": True, "resolution_source": "", "end_date": datetime(2020, 1, 1, tzinfo=timezone.utc),
         "closed": False, "accepting_orders": None}
    r = evidence.resolution_risk(m, 2)
    assert len(r) == 5


DB = os.environ.get("PMINTEL_TEST_DB")


@pytest.mark.skipif(not DB, reason="PMINTEL_TEST_DB not set")
def test_board_links_by_symbol_and_name_only(monkeypatch):
    from pmintel import db, stockcheck, tips
    conn = db.connect(DB)
    conn.execute("DROP TABLE IF EXISTS tips, markets, market_rules CASCADE")
    db.init_schema(conn)
    monkeypatch.setattr(stockcheck, "fetch", lambda s, client=None: (BARS, {}))
    tips._FETCH.clear()
    ts = datetime(2026, 10, 14, 15, 0, tzinfo=timezone.utc)
    tips.record(conn, "SEC: קניית מנהל", "PLTR", ts, 12.0, kind="sec",
                source_url="https://www.sec.gov/Archives/edgar/data/1/0001-26-1.txt")
    tips.record(conn, "Group X", "PLTR", ts, kind="telegram", chat_id="-1001234", message_id="77")
    conn.execute("""INSERT INTO markets (id, question, active, closed, ref_price, accepting_orders, resolution_source)
                    VALUES ('p1', 'Will Palantir be added to the Dow?', true, false, 0.2, true, 'https://x'),
                           ('p2', 'Will Bitcoin hit 200k?', true, false, 0.1, true, 'https://y')""")
    b = evidence.board(conn, fetch=lambda s: (BARS, {}), symbols=["PLTR", "NVDA"])
    pltr = b["watchlist"][0]
    assert pltr["price"] == 14 and len(pltr["findings"]) == 2
    sec = next(f for f in pltr["findings"] if f["kind"] == "sec")
    assert sec["official"] and sec["source_url"].startswith("https://www.sec.gov/") and sec["prior_close"] == 11
    tg = next(f for f in pltr["findings"] if f["kind"] == "telegram")
    assert tg["source_url"] == "https://t.me/c/1234/77" and tg["unknown"]
    assert [m["id"] for m in pltr["polymarket"]] == ["p1"] and pltr["polymarket"][0]["link"] == "keyword"
    assert b["watchlist"][1]["findings"] == [] and b["watchlist"][1]["polymarket"] == []
    conn.close()
