import math

from pmintel.research import calibration, strategy, wilson


def rows(spec):
    """spec: list of (p, y, event)"""
    return [{"p": p, "y": y, "event_id": e, "market_id": f"m{i}", "category": None}
            for i, (p, y, e) in enumerate(spec)]


def test_brier_and_buckets():
    c = calibration(rows([(0.9, 1, "a"), (0.9, 0, "b"), (0.1, 0, "c")]))
    assert c["n"] == 3
    assert math.isclose(c["brier"], (0.01 + 0.81 + 0.01) / 3)
    top = [b for b in c["buckets"] if b["lo"] == 0.9][0]
    assert top["n"] == 2 and top["freq"] == 0.5 and top["events"] == 2


def test_price_one_lands_in_last_bucket():
    c = calibration(rows([(1.0, 1, "a")]))
    assert c["buckets"][0]["lo"] == 0.95


def test_wilson_bounds():
    lo, hi = wilson(5, 10)
    assert 0.2 < lo < 0.5 < hi < 0.8
    assert wilson(0, 0) == (0.0, 1.0)


def test_strategy_returns_include_slippage():
    # Buy NO at p=0.04 -> NO price 0.96 + 0.01 slippage = 0.97; NO wins -> 1/0.97 - 1
    s = strategy(rows([(0.04, 0, "a")]), "NO", 0.0, 0.05, slippage=0.01, fee=0.0)
    assert math.isclose(s["mean_return"], 1 / 0.97 - 1)
    assert s["trades"] == 1 and s["events"] == 1 and s["insample_ci_positive"] is False


def test_strategy_losing_trade_is_minus_100pct():
    s = strategy(rows([(0.04, 1, "a")]), "NO", 0.0, 0.05, slippage=0.0, fee=0.0)
    assert math.isclose(s["mean_return"], -1.0)


def test_correlated_markets_count_once_per_event():
    # 50 winning markets of one event + 1 losing market of another event
    spec = [(0.03, 0, "big")] * 50 + [(0.03, 1, "small")]
    s = strategy(rows(spec), "NO", 0.0, 0.05, slippage=0.0, fee=0.0)
    assert s["trades"] == 51 and s["events"] == 2
    expected = ((1 / 0.97 - 1) + (-1.0)) / 2
    assert math.isclose(s["mean_return"], expected)


def test_edge_requires_enough_events_and_positive_ci():
    spec = [(0.02, 0, f"e{i}") for i in range(40)]  # all win, tiny return, zero variance
    s = strategy(rows(spec), "NO", 0.0, 0.05, slippage=0.0, fee=0.0)
    assert s["events"] == 40
    assert s["insample_ci_positive"] is False  # zero variance -> no stderr -> not claimed
    few = strategy(rows([(0.02, 0, "a"), (0.02, 0, "b"), (0.02, 1, "c")]), "NO", 0, 0.05, 0, 0)
    assert few["insample_ci_positive"] is False


def test_month_windows_cover_range_without_gaps():
    from datetime import date
    from pmintel.backfill import month_windows
    w = list(month_windows("2024-11-15", today=date(2025, 2, 3)))
    assert w[0] == (date(2024, 11, 15), date(2024, 12, 1))
    assert w[-1] == (date(2025, 2, 1), date(2025, 3, 1))
    assert all(a[1] == b[0] for a, b in zip(w, w[1:])) and len(w) == 4


def test_history_fetch_survives_400_and_falls_back():
    from datetime import datetime, timezone
    import httpx
    from pmintel.backfill import fetch_history
    from pmintel.http import ApiClient
    from pmintel.sources import Clob
    seen = []

    def handler(req):
        p = req.url.params
        seen.append((p.get("interval"), p.get("fidelity")))
        if p.get("interval") == "max" or p.get("fidelity") == "360":
            return httpx.Response(400, json={"error": "bad request"})
        return httpx.Response(200, json={"history": [{"t": 1700000000, "p": 0.4}]})
    clob = Clob(ApiClient("https://x", client=httpx.Client(transport=httpx.MockTransport(handler)),
                          sleep=lambda s: None, min_interval=0))
    a, b = datetime(2026, 1, 1, tzinfo=timezone.utc), datetime(2026, 2, 1, tzinfo=timezone.utc)
    assert len(fetch_history(clob, "tok", 360, a, b)) == 1 and seen[-1] == (None, "1440")
    always400 = Clob(ApiClient("https://x", client=httpx.Client(transport=httpx.MockTransport(
        lambda r: httpx.Response(400))), sleep=lambda s: None, min_interval=0))
    assert fetch_history(always400, "tok", 360, a, b) is None


def test_data_api_point_parsing_is_tolerant():
    from pmintel.dataapi import _points
    assert len(_points({"data": [{"t": 1700000000, "p": 0.4}]})) == 1
    assert len(_points({"history": [{"timestamp": 1700000000000, "price": "0.4"}]})) == 1  # ms + strings
    assert _points({"data": [{"x": 1}]}) == [] and _points(None) == []


def test_partial_backfill_is_not_marked_complete():
    import os
    from datetime import datetime, timedelta, timezone
    import httpx
    import pytest
    db_url = os.environ.get("PMINTEL_TEST_DB")
    if not db_url:
        pytest.skip("PMINTEL_TEST_DB not set")
    from pmintel import backfill, db
    from pmintel.http import ApiClient
    from pmintel.sources import Clob, Gamma
    from keyset_mock import keyset_response
    conn = db.connect(db_url)
    conn.execute("DROP TABLE IF EXISTS markets, price_history, job_runs, market_rules CASCADE")
    db.init_schema(conn)
    close = datetime.now(timezone.utc) - timedelta(days=5)
    mk = lambda i: {"id": f"m{i}", "question": "q", "outcomes": '["Yes","No"]', "outcomePrices": '["1","0"]',  # noqa: E731
                    "clobTokenIds": f'["t{i}","n{i}"]', "volumeNum": 50000, "closed": True,
                    "startDate": (close - timedelta(days=30)).isoformat(), "endDate": close.isoformat()}
    rows = [mk(1), mk(2)]
    mock = lambda h: ApiClient("https://x", client=httpx.Client(transport=httpx.MockTransport(h)),  # noqa: E731
                               sleep=lambda s: None, min_interval=0, max_retries=0)
    gamma = Gamma(mock(lambda r: keyset_response(r, rows, page=10)))
    clob = Clob(mock(lambda r: httpx.Response(400) if r.url.params["market"] == "t2"
                     else httpx.Response(200, json={"history": [{"t": int(close.timestamp()), "p": 0.9}]})))
    st = backfill.backfill(conn, gamma, clob, "2024-01-01", 1000, 360)
    assert st["history_error"] == 1
    assert conn.execute("SELECT status FROM job_runs WHERE job='backfill' ORDER BY id DESC").fetchone()[0] == "partial"
    # Data API v2 fills the gap -> complete, and the source is recorded
    from pmintel.dataapi import DataApi
    dapi = DataApi(mock(lambda r: httpx.Response(200, json={"data": [{"t": int(close.timestamp()), "p": 0.8}]})))
    st2 = backfill.backfill(conn, gamma, clob, "2024-01-01", 1000, 360, discover=False, data_api=dapi)
    assert st2["history_from_data_api"] == 1
    assert conn.execute("SELECT status FROM job_runs WHERE job='backfill' ORDER BY id DESC").fetchone()[0] == "ok"
    assert conn.execute("SELECT source FROM price_history WHERE token_id='t2'").fetchone()[0] == "data_api_v2"
    conn.close()
