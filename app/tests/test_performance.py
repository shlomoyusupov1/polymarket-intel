from pmintel import performance as P


def test_entries_share_one_schema_and_status_vocabulary():
    lab = {"first_day": "2016-01-04", "last_day": "2026-10-16", "universe": ["A"] * 65, "strategies": {
        "rsi2_pullback": {"label": "תיקון קצר", "n": 900, "success_rate": 0.62, "edge_vs_baseline": 0.02, "edge_lo95": 0.01,
                          "verdict": "candidate_for_paper"},
        "ma_stack": {"label": "ממוצעים", "n": 2000, "success_rate": 0.55, "edge_vs_baseline": 0.001, "edge_lo95": -0.004,
                     "verdict": "not_supported"},
        "breakout_volume": {"label": "פריצה", "n": 12, "verdict": "insufficient_data"}}}
    fwd = {"rsi2_pullback": {"n": 60, "success_rate": 0.7, "mean_excess": 0.03, "mean_lo95": 0.01, "status": "confirmed"},
           "ma_stack": {"n": 60, "success_rate": 0.7, "mean_excess": 0.03, "mean_lo95": 0.01, "status": "confirmed"}}
    board = [{"source": "Group A", "last_tip": None,
              "gate": {"n": 120, "success_rate": 0.38, "edge_after_cost": -0.02, "edge_lo95": -0.04, "status": "not_reliable"}}]
    pm = {"24": {"walk_forward": {"verdict": "not_supported", "oos_trading": {"events": 400, "mean_return": -0.03, "ci": [-0.06, 0.0]}}}}
    etf = {"accounts": {"trend": {"return": 0.02, "max_drawdown": -0.03, "closed_trades": 3, "win_rate": 0.66}},
           "benchmark_spy": {"return": 0.01}}
    e = (P.from_stocklab(lab) + P.from_lab_forward(fwd, lab) + P.from_tips(board) + P.from_polymarket(pm) + P.from_etf(etf))
    keys = set(e[0])
    assert all(set(x) == keys for x in e)
    by = {x["id"]: x for x in e}
    assert by["lab:rsi2_pullback"]["status"] == "passed" and by["lab:ma_stack"]["status"] == "failed"
    assert by["lab:breakout_volume"]["status"] == "collecting" and "50" in by["lab:breakout_volume"]["why"]
    assert by["fwd:rsi2_pullback"]["status"] == "passed"
    # forward-positive but historically failed is never "passed"
    assert by["fwd:ma_stack"]["status"] == "promising"
    assert by["src:Group A"]["status"] == "failed" and by["src:Group A"]["kind"] == "measured"
    assert by["pm:wf:24"]["engine"] == "polymarket" and by["pm:wf:24"]["status"] == "failed"
    assert by["etf:trend"]["status"] == "collecting" and abs(by["etf:trend"]["mean_net"] - 0.01) < 1e-12
    s = P.summarize(e)
    assert s["stocks"]["passed"] == 2 and s["sources"]["failed"] == 1 and s["polymarket"]["failed"] == 1
    assert P.from_stocklab(None) == [] and P.from_polymarket(None) == [] and P.from_etf(None) == []


import os
import pytest

DB = os.environ.get("PMINTEL_TEST_DB")


@pytest.mark.skipif(not DB, reason="PMINTEL_TEST_DB not set")
def test_performance_and_stocks_endpoints(monkeypatch):
    from datetime import date, timedelta
    from fastapi.testclient import TestClient
    from pmintel import api, config, db, stockcheck
    conn = db.connect(DB)
    db.init_schema(conn)
    conn.close()
    days = [(date(2025, 1, 1) + timedelta(days=i)).isoformat() for i in range(300)]
    bars = [{"day": d, "o": 100 + i * 0.1, "h": 101 + i * 0.1, "l": 99 + i * 0.1, "c": 100 + i * 0.1, "v": 1e6} for i, d in enumerate(days)]
    monkeypatch.setattr(stockcheck, "fetch", lambda sym, client=None: (bars, {}))
    api._cache.clear()
    monkeypatch.setattr(config, "DATABASE_URL", DB)
    monkeypatch.setattr(db.config, "DATABASE_URL", DB)
    with TestClient(api.app) as tc:
        p = tc.get("/api/performance").json()
        assert "entries" in p and "summary" in p
        assert all(e["status"] in ("passed", "promising", "failed", "collecting") for e in p["entries"])
        s = tc.get("/api/stocks/overview").json()
        assert len(s["etfs"]) == len(api.SECTOR_ETFS) and not s["missing"]
        spy = next(r for r in s["etfs"] if r["symbol"] == "SPY")
        assert abs(spy["r1w"] - (bars[-1]["c"] / bars[-6]["c"] - 1)) < 1e-12 and spy["above_ma200"] is True
