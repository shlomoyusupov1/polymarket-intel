import os
from datetime import datetime, timezone

import pytest

from pmintel import tips

DB = os.environ.get("PMINTEL_TEST_DB")


def test_extract_symbols_and_price_from_real_style_messages():
    msg = ("📌 הזדמנות המסחר שנבחרה: PS International Group (PSIG)\nסימול המניה: PSIG\n"
           "מספר ISIN: KYG7308J1132\nתשואה צפויה: 10%–20%\nאופן ביצוע: הוראת לימיט במחיר של 4.21 דולר")
    syms, price = tips.extract(msg)
    assert syms == ["PSIG"] and price == 4.21
    assert tips.extract("watch $TSNP and $HMBL today, OTC runners (USD)")[0] == ["TSNP", "HMBL"]
    assert tips.extract("price $4.21 only")[0] == []  # a price is not a ticker
    assert tips.extract("great day for the AI trade (ETF)")[0] == []
    assert tips.extract("") == ([], None)


def bars(days, opens, closes):
    return [{"day": d, "o": o, "h": max(o, c) * 1.01, "l": min(o, c) * 0.99, "c": c}
            for d, o, c in zip(days, opens, closes)]


DAYS = [f"2026-10-{d:02d}" for d in (12, 13, 14, 15, 16, 19, 20, 21, 22, 23)]


def test_outcome_enters_at_next_open_after_the_tip():
    b = bars(DAYS, [10, 11, 12, 13, 14, 15, 16, 17, 18, 19], [10.5, 11.5, 12.5, 13.5, 14.5, 15.5, 16.5, 17.5, 18.5, 19.5])
    during = datetime(2026, 10, 13, 15, 0, tzinfo=timezone.utc)  # 11:00 New York, market open
    o = tips.outcome(b, during)
    assert o["entry_day"] == "2026-10-14" and o["entry"] == 12  # no buying at a price already gone
    assert abs(o["r1"] - (12.5 / 12 - 1)) < 1e-12 and abs(o["r5"] - (16.5 / 12 - 1)) < 1e-12
    assert o["r20"] is None and o["sessions"] == 8
    before_open = datetime(2026, 10, 13, 12, 0, tzinfo=timezone.utc)  # 08:00 New York
    assert tips.outcome(b, before_open)["entry_day"] == "2026-10-13"
    assert tips.outcome(b, datetime(2026, 11, 1, tzinfo=timezone.utc)) is None


@pytest.mark.skipif(not DB, reason="PMINTEL_TEST_DB not set")
def test_record_dedup_outcomes_and_api(monkeypatch):
    from pmintel import api, config, db, stockcheck
    conn = db.connect(DB)
    conn.execute("DROP TABLE IF EXISTS tips CASCADE")
    db.init_schema(conn)
    b = bars(DAYS, [4.2] * 10, [4.3, 4.0, 3.0, 2.0, 1.5, 1.2, 1.1, 1.0, 0.9, 0.8])
    monkeypatch.setattr(stockcheck, "fetch", lambda sym, client=None: (b, {}))
    ts = datetime(2026, 10, 12, 21, 0, tzinfo=timezone.utc)
    assert tips.record(conn, "Group A", "PSIG", ts, 4.21, kind="telegram", chat_id="1", message_id="9")
    assert tips.record(conn, "Group A", "PSIG", ts, 4.21) is None  # same source+symbol within 24 h
    assert tips.record(conn, "Group B", "PSIG", ts) is not None
    assert tips.update_outcomes(conn, fetch=lambda s: (b, {})) == 2
    r5 = conn.execute("SELECT r5 FROM tips WHERE source='Group A'").fetchone()[0]
    assert abs(r5 - (1.2 / 4.2 - 1)) < 1e-9  # entry 13.10 open, 5th session close 19.10
    monkeypatch.setattr(config, "DATABASE_URL", DB)
    monkeypatch.setattr(db.config, "DATABASE_URL", DB)
    from fastapi.testclient import TestClient
    with TestClient(api.app) as tc:
        ov = tc.get("/api/tips").json()
        assert {r["source"] for r in ov["leaderboard"]} == {"Group A", "Group B"}
        assert ov["recent"][0]["symbol"] == "PSIG"
        assert tc.post("/api/tips", json={"symbol": "psig", "source": "וואטסאפ הילה", "price": 4.21}).status_code == 201
        assert tc.post("/api/tips", json={"symbol": "psig", "source": "וואטסאפ הילה"}).status_code == 409
        assert tc.post("/api/tips", json={"symbol": "bad sym!", "source": "x"}).status_code == 400
    conn.close()
