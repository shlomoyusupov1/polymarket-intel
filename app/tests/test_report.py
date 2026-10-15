import json
import os

import httpx
import pytest

from pmintel import collector, db, report
from pmintel.http import ApiClient
from pmintel.sources import Clob, Gamma

from keyset_mock import keyset_response

DB = os.environ.get("PMINTEL_TEST_DB")


def api_of(handler):
    return ApiClient("https://x", client=httpx.Client(transport=httpx.MockTransport(handler)),
                     sleep=lambda s: None, min_interval=0, max_retries=0)


def leg(i, group, ask, bid, other=False, fees=False, liq=5000):
    mid = (ask + bid) / 2
    return {"id": f"{group}-{i}", "question": f"Leg {i} of {group}?", "outcomes": '["Yes","No"]',
            "outcomePrices": json.dumps([str(mid), str(1 - mid)]), "clobTokenIds": f'["{group}{i}y","{group}{i}n"]',
            "bestAsk": ask, "bestBid": bid, "liquidityNum": liq, "volume24hr": 100, "active": True,
            "closed": False, "acceptingOrders": True, "enableOrderBook": True, "negRisk": True,
            "negRiskMarketID": group, "negRiskOther": other, "feesEnabled": fees,
            "endDate": "2099-01-01T00:00:00Z", "events": [{"id": 1, "slug": f"ev-{group}"}]}


def test_cost_for_shares():
    assert report.cost_for_shares([(0.3, 5), (0.4, 10)], 10) == pytest.approx(0.3 * 5 + 0.4 * 5)
    assert report.cost_for_shares([(0.3, 5)], 10) is None


@pytest.mark.skipif(not DB, reason="PMINTEL_TEST_DB not set")
def test_report_separates_theoretical_from_executable():
    conn = db.connect(DB)
    conn.execute("DROP TABLE IF EXISTS markets, snapshots, price_history, orderbooks, job_runs, research_reports CASCADE")
    db.init_schema(conn)
    rows = (
        # A: sum ask 0.95, deep books -> executable
        [leg(i, "A", 0.30, 0.28) for i in range(2)] + [leg(2, "A", 0.35, 0.33)]
        # B: sum ask 0.90 but has an 'Other' leg -> theoretical only
        + [leg(0, "B", 0.40, 0.38), leg(1, "B", 0.50, 0.48, other=True)]
        # C: sum ask 0.96 but books too thin for 10 shares -> not executable
        + [leg(0, "C", 0.48, 0.46), leg(1, "C", 0.48, 0.46)]
        # D: sum ask 1.04 -> nothing
        + [leg(0, "D", 0.52, 0.50), leg(1, "D", 0.52, 0.50)]
        # a non-tradable market (not accepting orders)
        + [{**leg(9, "Z", 0.5, 0.4), "negRisk": False, "negRiskMarketID": None, "acceptingOrders": False}]
    )
    collector.collect_once(conn, Gamma(api_of(lambda r: keyset_response(r, rows, page=50))))

    def book(r):
        tok = r.url.params["token_id"]
        if tok.startswith("C"):
            return httpx.Response(200, json={"asks": [{"price": "0.48", "size": "2"}], "bids": []})
        price = {"A0y": "0.30", "A1y": "0.30", "A2y": "0.35", "B0y": "0.40", "B1y": "0.50"}[tok]
        return httpx.Response(200, json={"asks": [{"price": price, "size": "1000"}], "bids": []})

    r = report.run(conn, Clob(api_of(book)))
    u = r["universe"]
    assert u["total_rows"] == 10 and u["open_active"] == 10
    assert u["open_accepting_orders"] == 9 and u["tradable"] == 9
    s = r["structural"]
    assert {g["group"] for g in s["theoretical"]} == {"A", "B", "C"}
    assert [g["group"] for g in s["executable"]] == ["A"]
    a = s["executable"][0]["sizes"][100]
    assert a["fillable"] and a["cost"] == pytest.approx(95.0) and a["net"] == pytest.approx(5.0)
    rejected = {g["group"]: g for g in s["rejected_after_live_check"]}
    assert any("Other" in b for b in rejected["B"]["blockers"])
    assert rejected["C"]["sizes"][10] == {"fillable": False}
    md = report.to_markdown(r)
    assert "executable after depth & blockers: 1" in md and "No YES/NO recommendation" in md
