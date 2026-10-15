"""End-to-end against a real Postgres (plain PG works; Timescale is optional).

Run:  PMINTEL_TEST_DB=postgresql://... pytest
All upstream API calls are mocked; this verifies our pipeline, SQL and API, not Polymarket.
"""
import json
import os
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from pmintel import backfill, collector, config, db, research
from pmintel.http import ApiClient
from pmintel.sources import Clob, Gamma

from keyset_mock import keyset_response

DB = os.environ.get("PMINTEL_TEST_DB")
pytestmark = pytest.mark.skipif(not DB, reason="PMINTEL_TEST_DB not set")

NOW = datetime.now(timezone.utc).replace(microsecond=0)


def api_of(handler):
    return ApiClient("https://x", client=httpx.Client(transport=httpx.MockTransport(handler)),
                     sleep=lambda s: None, min_interval=0)


def open_market(i, liq, price):
    return {"id": str(i), "question": f"Open market {i}?", "outcomes": '["Yes","No"]',
            "outcomePrices": json.dumps([str(price), str(1 - price)]), "clobTokenIds": f'["t{i}y","t{i}n"]',
            "liquidityNum": liq, "volumeNum": liq * 10, "volume24hr": liq, "active": True, "closed": False,
            "bestBid": price - 0.01, "bestAsk": price + 0.01, "spread": 0.02,
            "endDate": "2026-12-31T00:00:00Z", "events": [{"id": 1000 + i, "slug": f"ev-{i}"}]}


def closed_market(i, price_before, yes_won, event):
    close = NOW - timedelta(days=10)
    return {"id": f"c{i}", "question": f"Closed {i}?", "outcomes": '["Yes","No"]',
            "outcomePrices": '["1","0"]' if yes_won else '["0","1"]', "clobTokenIds": f'["c{i}y","c{i}n"]',
            "volumeNum": 50000, "closed": True, "active": True, "umaResolutionStatus": "resolved",
            "startDate": (close - timedelta(days=60)).isoformat(), "endDate": close.isoformat(),
            "closedTime": close.strftime("%Y-%m-%d %H:%M:%S+00"), "events": [{"id": event, "slug": f"e{event}"}],
            "_p": price_before}


@pytest.fixture()
def conn():
    c = db.connect(DB)
    c.execute("DROP TABLE IF EXISTS markets, snapshots, price_history, orderbooks, job_runs, research_reports CASCADE")
    db.init_schema(c)
    db.init_schema(c)  # idempotent
    yield c
    c.close()


def test_full_pipeline(conn, monkeypatch):
    # ---- live collection: 3 liquid, 1 illiquid, 1 duplicated across pages ----
    opens = [open_market(1, 50000, 0.30), open_market(2, 20000, 0.80), open_market(3, 5000, 0.50),
             open_market(4, 10, 0.50), open_market(1, 50000, 0.30)]
    live = {"rows": opens}
    gamma_live = Gamma(api_of(lambda r: keyset_response(r, live["rows"], page=2)))
    out = collector.collect_once(conn, gamma_live, now=NOW - timedelta(hours=25))
    assert out["markets"] == 4 and out["snapshots"] == 3

    # ---- order books: top-2 by 24h volume; one market's book errors, which must not stop others ----
    def book_handler(r):
        tok = r.url.params["token_id"]
        if tok == "t2y":
            return httpx.Response(500)
        return httpx.Response(200, json={"bids": [{"price": "0.29", "size": "1000"}],
                                         "asks": [{"price": "0.31", "size": "1000"}]})
    clob_live = Clob(api_of(book_handler))
    clob_live.api.max_retries = 0
    ob = collector.collect_orderbooks(conn, clob_live, out["records"], NOW, top_n=2)
    assert ob == {"ok": 1, "empty": 0, "error": 1}
    # second snapshot later with a price move on market 1
    opens[0] = opens[4] = open_market(1, 50000, 0.45)
    collector.collect_once(conn, gamma_live, now=NOW)
    assert conn.execute("SELECT count(*) FROM snapshots").fetchone()[0] == 6

    # ---- historical backfill: 60 resolved markets in 40 events + 1 unresolved ----
    closed = []
    for i in range(60):
        p = 0.04 if i % 2 == 0 else 0.92
        yes_won = (i % 2 == 1) and i % 7 != 0  # favourites mostly win, longshots never
        closed.append(closed_market(i, p, yes_won, event=i % 40))
    unresolved = closed_market(99, 0.5, True, 99)
    unresolved["outcomePrices"] = '["0.5","0.5"]'
    closed.append(unresolved)
    by_token = {f"c{i}y": m["_p"] for i, m in enumerate(closed[:60])}

    gamma_hist = Gamma(api_of(lambda r: keyset_response(r, closed, page=25)))

    def clob_handler(r):
        tok = r.url.params["market"]
        close = NOW - timedelta(days=10)
        hist = [{"t": int((close - timedelta(hours=h)).timestamp()), "p": by_token[tok]}
                for h in range(0, 24 * 40, 6)]
        return httpx.Response(200, json={"history": hist})

    stats = backfill.backfill(conn, gamma_hist, Clob(api_of(clob_handler)), "2024-01-01", 10000, 360)
    assert stats["seen"] == 61 and stats["resolved"] == 60 and stats["history_fetched"] == 60
    again = backfill.backfill(conn, gamma_hist, Clob(api_of(clob_handler)), "2024-01-01", 10000, 360)
    assert again["history_fetched"] == 0  # resumable: nothing re-downloaded

    # ---- research ----
    result = research.run(conn)
    day = result["24"]
    assert day["calibration"]["n"] == 60
    no_longshot = next(s for s in day["strategies"] if s["side"] == "NO" and s["lo"] == 0.0)
    assert no_longshot["trades"] == 30 and no_longshot["win_rate"] == 1.0
    assert no_longshot["events"] == 20  # 30 markets collapse into 20 events
    # month horizon: cutoff 30d before close still inside our 40d history
    assert result["720"]["calibration"]["n"] == 60

    # ---- API ----
    from fastapi.testclient import TestClient
    from pmintel import api
    monkeypatch.setattr(config, "DATABASE_URL", DB)
    monkeypatch.setattr(db.config, "DATABASE_URL", DB)
    with TestClient(api.app) as tc:
        h = tc.get("/api/health").json()
        assert h["counts"]["open_markets"] == 4 and h["counts"]["resolved_markets"] == 60
        assert {j["job"] for j in h["jobs"]} >= {"collect_full", "backfill", "research"}
        ms = tc.get("/api/markets", params={"min_liquidity": 1000, "sort": "liquidity"}).json()
        assert [m["id"] for m in ms] == ["1", "2", "3"] and all(m["decision"] == "WAIT" for m in ms)
        assert tc.get("/api/markets", params={"search": "market 2"}).json()[0]["id"] == "2"
        d = tc.get("/api/markets/1").json()
        assert d["url"] == "https://polymarket.com/event/ev-1" and len(d["snapshots"]) == 2
        assert d["orderbook"]["best_ask"] == 0.31 and abs(d["orderbook"]["buy_vwap_100"] - 0.31) < 1e-9
        assert h["counts"]["orderbook_rows"] == 1
        assert d["orderbook"]["valid"] is True
        assert d["data_quality"] == {"level": "limited", "issues": ["אין עומק ל-$1,000"]}
        d3 = tc.get("/api/markets/3").json()  # no order book collected -> never 'good'
        assert d3["data_quality"]["level"] == "bad" and "אין ספר פקודות" in d3["data_quality"]["issues"]
        assert tc.get("/api/markets/nope").status_code == 404
        r = tc.get("/api/research/latest").json()
        assert r["result"]["24"]["calibration"]["n"] == 60
        ov = tc.get("/api/overview").json()
        assert ov["stats"]["open_markets"] == 4
        mv = ov["own_movers"]
        assert mv[0]["id"] == "1" and abs(mv[0]["change"] - 0.15) < 1e-9
        assert ov["opportunities"]["statistically_supported"] == []
        assert {"changed", "attention", "freshness"} <= set(ov)
        # live endpoints fail honestly (502) when the upstream is unreachable in tests
        monkeypatch.setattr(api, "_clob", Clob(api_of(lambda r: httpx.Response(503))))
        api._cache.clear()
        assert tc.get("/api/markets/1/history?range=1w").status_code == 502
        assert tc.get("/api/markets/1/book").status_code == 502
        monkeypatch.setattr(api, "_clob", Clob(api_of(lambda r: httpx.Response(200, json=(
            {"history": [{"t": 1, "p": 0.4}, {"t": 2, "p": 0.5}]} if r.url.path == "/prices-history"
            else {"bids": [{"price": "0.4", "size": "10"}], "asks": [{"price": "0.5", "size": "10"}]})))))
        api._cache.clear()
        assert tc.get("/api/markets/1/history?range=1d").json()["points"][-1]["p"] == 0.5
        b = tc.get("/api/markets/1/book").json()
        assert b["valid"] and b["best_ask"] == 0.5
        assert tc.get("/api/markets/nope/book").status_code == 404
