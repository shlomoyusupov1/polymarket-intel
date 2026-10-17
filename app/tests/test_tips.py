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
    tips._FETCH.clear()
    ts = datetime(2026, 10, 12, 21, 0, tzinfo=timezone.utc)
    assert tips.record(conn, "Group A", "PSIG", ts, 4.21, kind="telegram", chat_id="1", message_id="9")
    assert tips.record(conn, "Group A", "PSIG", ts, 4.21) is None  # same source+symbol within 24 h
    assert tips.record(conn, "Group B", "PSIG", ts) is not None
    spy = bars(DAYS, [100] * 10, [101, 102, 103, 104, 105, 106, 107, 108, 109, 110])
    assert tips.update_outcomes(conn, fetch=lambda s: (spy if s == "SPY" else b, {})) == 2
    spy_r5 = conn.execute("SELECT spy_r5 FROM tips WHERE source='Group A'").fetchone()[0]
    assert abs(spy_r5 - (106 / 100 - 1)) < 1e-9  # same sessions as the tip: 13.10 open -> 19.10 close
    r5 = conn.execute("SELECT r5 FROM tips WHERE source='Group A'").fetchone()[0]
    assert abs(r5 - (1.2 / 4.2 - 1)) < 1e-9  # entry 13.10 open, 5th session close 19.10
    monkeypatch.setattr(config, "DATABASE_URL", DB)
    monkeypatch.setattr(db.config, "DATABASE_URL", DB)
    from fastapi.testclient import TestClient
    with TestClient(api.app) as tc:
        ov = tc.get("/api/tips").json()
        assert {r["source"] for r in ov["leaderboard"]} == {"Group A", "Group B"}
        a = next(r for r in ov["leaderboard"] if r["source"] == "Group A")
        assert abs(a["excess_r5"] - ((1.2 / 4.2 - 1) - 0.06)) < 1e-9 and a["beat_spy_5d"] == 0
        assert ov["recent"][0]["symbol"] == "PSIG"
        assert tc.post("/api/tips", json={"symbol": "psig", "source": "וואטסאפ הילה", "price": 4.21}).status_code == 201
        assert tc.post("/api/tips", json={"symbol": "psig", "source": "וואטסאפ הילה"}).status_code == 409
        assert tc.post("/api/tips", json={"symbol": "bad sym!", "source": "x"}).status_code == 400
    conn.close()


def test_group_matching_uses_the_marked_id_from_list_chats():
    from pmintel.tips_telegram import matches
    wanted = ["-1001209544486", "שוק ההון כלכליסט"]
    assert matches(-1001209544486, "אינטראקטיב ישראל", wanted)
    assert matches(-1001239039686, "שוק ההון כלכליסט", wanted)
    assert not matches(1209544486, "x", wanted)  # bare entity id is not what --list-chats prints


def test_source_gate_needs_sample_consistency_and_breadth():
    import random
    rnd = random.Random(3)
    assert tips.source_gate([("A", 0.05, 0.0)] * 10)["status"] == "collecting"
    # 60 tips on 30 symbols, ~75% beat SPY by 2% after costs -> reliable
    good = [(f"S{i % 30}", (0.03 if rnd.random() < 0.75 else -0.01), 0.0) for i in range(60)]
    g = tips.source_gate(good)
    assert g["status"] == "reliable" and g["success_rate"] >= 0.6 and g["success_lo95"] > 0.5
    # 60% raw win rate but tiny sample of one hyped symbol -> not reliable (breadth/CI)
    one = [("PUMP", 0.10 if i % 5 < 3 else -0.15, 0.0) for i in range(60)]
    assert tips.source_gate(one)["status"] != "reliable"
    # coin flip -> not reliable
    flip = [(f"S{i}", (0.01 if rnd.random() < 0.5 else -0.01), 0.0) for i in range(80)]
    assert tips.source_gate(flip)["status"] in ("not_reliable", "promising")
    assert tips.source_gate(flip)["status"] != "reliable"


def test_spread_estimator_recovers_a_pure_spread_and_costs_flow_into_outcomes():
    from pmintel import costs
    # zero volatility, every day trades between bid and ask 4% apart -> estimator returns ~4%
    flat = [{"day": f"d{i}", "o": 10, "h": 10.2, "l": 9.8, "c": 10} for i in range(20)]
    assert abs(costs.cs_spread(flat) - 0.04) < 0.001
    c, src = costs.round_trip(flat)
    assert src == "corwin_schultz" and abs(c - (0.04 + costs.FEE_RT)) < 0.001
    assert costs.round_trip(flat[:2]) == (costs.fallback(10), "price_tier")
    assert costs.fallback(0.5) > costs.fallback(3) > costs.fallback(50)
    b = bars(DAYS, [10] * 10, [10] * 10)
    o = tips.outcome(b, datetime(2026, 10, 19, 15, 0, tzinfo=timezone.utc))
    assert o["cost_src"] == "corwin_schultz" and o["cost_rt"] > costs.FEE_RT


def test_wide_spread_turns_a_paper_winner_into_a_loser():
    # beats SPY by 3% before costs, but each stock costs 5% to trade -> not reliable, negative edge
    rows = [(f"S{i % 30}", 0.03, 0.0, 0.05, 0.8) for i in range(60)]
    g = tips.source_gate(rows)
    assert g["edge_after_cost"] < 0 and g["status"] == "not_reliable" and g["success_rate"] == 0
    # unknown cost on a sub-$1 stock falls back to the conservative tier, not to 0.2%
    assert tips.source_gate([("P", 0.03, 0.0, None, 0.5)] * 40)["edge_after_cost"] < 0


def test_holm_downgrades_a_lucky_source_among_many():
    good = {"n": 60, "p_edge": 0.02, "status": "reliable"}
    gates = {"A": dict(good)} | {f"N{i}": {"n": 60, "p_edge": 0.5, "status": "not_reliable"} for i in range(9)}
    out = tips.apply_holm(gates)
    assert out["A"]["status"] == "promising" and out["A"]["downgraded"] == "multiple_testing"
    assert abs(out["A"]["p_holm"] - 0.2) < 1e-12 and out["A"]["sources_tested"] == 10
    strong = tips.apply_holm({"A": {"n": 60, "p_edge": 0.001, "status": "reliable"},
                              "B": {"n": 60, "p_edge": 0.4, "status": "not_reliable"}})
    assert strong["A"]["status"] == "reliable" and strong["B"]["p_holm"] == 0.4
