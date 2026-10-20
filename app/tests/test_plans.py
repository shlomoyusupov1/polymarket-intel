import os
from datetime import datetime, timezone

import pytest

from pmintel import plans as P

DB = os.environ.get("PMINTEL_TEST_DB")
S = {**P.DEFAULTS, "budget": 1000.0, "profile": "moderate"}


def test_size_by_cap_without_stop_and_says_loss_is_not_capped():
    r = P.size(S, 50.0, typical_loss=-0.06)
    assert r["sized_by"] == "תקרה" and abs(r["position_usd"] - 330) < 1e-9 and abs(r["units"] - 6.6) < 1e-9
    assert abs(r["bad_case_loss"] - 330 * 0.06) < 1e-9 and r["planned_loss"] is None
    assert any("לא הוגדרה עצירת הפסד" in n for n in r["notes"]) and any("עמלת הברוקר" in n for n in r["notes"])


def test_size_by_owner_stop_respects_risk_limit_and_flags_fees():
    s = {**S, "fee_min": 2.5}
    r = P.size(s, 50.0, stop=48.0)           # risk $10 / $2 per share = 5 shares = $250 < $330 cap
    assert r["sized_by"] == "סיכון" and abs(r["units"] - 5) < 1e-9 and abs(r["position_usd"] - 250) < 1e-9
    assert abs(r["planned_loss"] - (10 + 5.0)) < 1e-9 and r["fee_round_trip"] == 5.0
    assert r["ok"] is False and any("לא מעשית" in n for n in r["notes"])   # $5 on $250 = 2% > 1%
    assert P.size(S, 50.0, stop=51.0)["ok"] is False
    whole = P.size({**S, "budget": 100.0, "fractional": False}, 500.0)
    assert whole["ok"] is False and whole["units"] == 0


def test_polymarket_buy_uses_the_book_and_takes_the_fee_in_shares():
    r = P.polymarket_buy(10.0, [(0.50, 10.0), (0.52, 100.0)], 0.5, "politics")
    assert r["executable"] and 0.50 < r["price"] < 0.52
    fee = 0.04 * (1 - r["price"])
    assert abs(r["payout_if_yes"] - 10 / r["price"] * (1 - fee)) < 1e-9 and r["loss_if_no"] == 10.0
    thin = P.polymarket_buy(10.0, None, 0.30, None)
    assert not thin["executable"] and "אין ספר פקודות" in thin["notes"][0]


@pytest.mark.skipif(not DB, reason="PMINTEL_TEST_DB not set")
def test_opportunities_and_plan_never_invent_targets(monkeypatch):
    from fastapi.testclient import TestClient
    from pmintel import api, config, db
    conn = db.connect(DB)
    db.init_schema(conn)
    for t in ("lab_signals", "follows", "owner_settings"):
        conn.execute(f"DELETE FROM {t}")
    conn.execute("""INSERT INTO lab_signals (strategy, params, hold, symbol, signal_day, recorded_at, sha256, cost_rt, status,
                                             last_price, last_day, atr_pct)
                    VALUES ('rsi2_pullback', '{}', 5, 'AMD', current_date, now(), 'x', 0.002, 'pending', 150, current_date, 0.03)""")
    conn.close()
    monkeypatch.setattr(config, "DATABASE_URL", DB)
    monkeypatch.setattr(db.config, "DATABASE_URL", DB)
    api._cache.clear()
    with TestClient(api.app) as tc:
        assert tc.put("/api/settings", json={"budget": 500, "profile": "conservative", "fee_min": 1}).status_code == 200
        assert tc.put("/api/settings", json={"profile": "yolo"}).status_code == 400
        rows = tc.get("/api/opportunities").json()["rows"]
        sig = next(r for r in rows if r["kind"] == "signal")
        assert sig["asset"] == "AMD" and sig["target"] is None and sig["stop"] is None and sig["action"] == "להמתין"
        p = tc.get(f"/api/plan/signal/{sig['id']}").json()
        titles = [x["title"] for x in p["sections"]]
        assert titles[:4] == ["מה אנחנו בוחנים", "מתי להיכנס", "כמה כסף", "מתי לצאת"]
        assert abs(p["sizing"]["position_usd"] - 125) < 1e-9          # 25% of $500
        assert any("להמתין" in l for x in p["sections"] for l in x["lines"])
        size = tc.get("/api/size", params={"price": 100, "stop": 95}).json()
        assert abs(size["units"] - 0.5) < 1e-9                       # $2.50 risk / $5 per share
        assert tc.get("/api/plan/signal/999999").status_code == 404
        pf = tc.get("/api/portfolio").json()
        assert pf["value"] == 500 and pf["history"][0]["decision"] == "ממתין לכניסה" and pf["history"][0]["sealed_before_entry"]
        assert tc.get("/api/portfolio", params={"scope": "proven"}).json()["history"][0]["decision"] == "ממתין לכניסה"
        assert tc.get("/api/portfolio", params={"scope": "x"}).status_code == 400
