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
    # history alone is only ever a candidate; only forward evidence on a historical candidate confirms
    assert by["lab:rsi2_pullback"]["status"] == "candidate" and by["lab:ma_stack"]["status"] == "failed"
    assert by["lab:breakout_volume"]["status"] == "insufficient" and "50" in by["lab:breakout_volume"]["why"]
    assert "INSUFFICIENT DATA" in by["lab:breakout_volume"]["why"] and by["lab:breakout_volume"]["missing"]
    assert by["fwd:rsi2_pullback"]["status"] == "confirmed" and by["fwd:rsi2_pullback"]["missing"] == []
    # forward-positive but historically failed is never "confirmed"
    assert by["fwd:ma_stack"]["status"] == "validating" and "מעבר בבדיקה על העבר" in by["fwd:ma_stack"]["missing"]
    assert by["src:Group A"]["status"] == "failed" and by["src:Group A"]["kind"] == "measured"
    assert by["pm:wf:24"]["engine"] == "polymarket" and by["pm:wf:24"]["status"] == "failed"
    assert by["etf:trend"]["status"] == "validating" and abs(by["etf:trend"]["mean_net"] - 0.01) < 1e-12
    s = P.summarize(e)
    assert s["stocks"]["candidate"] == 1 and s["stocks"]["confirmed"] == 1 and s["sources"]["failed"] == 1
    assert s["polymarket"]["failed"] == 1 and set(s["stocks"]) == set(P.STATUSES)
    pm = P.from_polymarket({"6": {"walk_forward": {"verdict": "oos_positive_needs_execution_check", "oos_trading": {"events": 90}}}})[0]
    assert pm["status"] == "candidate" and "אימות ביצוע" in pm["missing"][0]  # a theoretical gap is never "confirmed"
    assert P.from_stocklab(None) == [] and P.from_polymarket(None) == [] and P.from_etf(None) == []


import os
import pytest

DB = os.environ.get("PMINTEL_TEST_DB")


@pytest.mark.skipif(not DB, reason="PMINTEL_TEST_DB not set")
def test_performance_and_stocks_endpoints(monkeypatch):
    from pmintel import performance as P
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
        assert all(e["status"] in P.STATUSES for e in p["entries"])
        s = tc.get("/api/stocks/overview").json()
        assert len(s["etfs"]) == len(api.SECTOR_ETFS) and not s["missing"]
        spy = next(r for r in s["etfs"] if r["symbol"] == "SPY")
        assert abs(spy["r1w"] - (bars[-1]["c"] / bars[-6]["c"] - 1)) < 1e-12 and spy["above_ma200"] is True


def test_crypto_entries_are_their_own_engine():
    from pmintel import performance as P
    rep = {"market": "crypto", "first_day": "2018-01-01", "last_day": "2026-10-18", "universe": ["BTC-USD"],
           "strategies": {"rsi2_pullback": {"label": "x", "n": 80, "verdict": "candidate_for_paper", "success_rate": 0.62}}}
    e = P.from_stocklab(rep, None, "crypto")[0]
    assert e["engine"] == "crypto" and e["id"] == "lab:crypto:rsi2_pullback" and e["status"] == "candidate"
    assert "ביטקוין" in e["benchmark"] and e["version"] == "cryptolab_v1"
    f = P.from_lab_forward({"rsi2_pullback": {"n": 3, "success_rate": 1.0, "mean_excess": 0.01, "mean_lo95": None,
                                             "status": "collecting"}}, rep, "crypto")[0]
    assert f["engine"] == "crypto" and f["id"] == "fwd:crypto:rsi2_pullback" and f["status"] == "validating"
    assert P.from_stocklab(rep)[0]["version"] == "stocklab_v1"
