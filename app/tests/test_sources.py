import httpx
import pytest

from pmintel.http import ApiClient
from pmintel.sources import Clob, Gamma

from keyset_mock import keyset_response


def make(handler):
    return ApiClient("https://x", client=httpx.Client(transport=httpx.MockTransport(handler)),
                     sleep=lambda s: None, min_interval=0)


def test_keyset_pagination_follows_cursor_to_the_end():
    data = [{"id": i} for i in range(250)]
    calls = []

    def handler(req):
        assert req.url.path == "/markets/keyset"
        calls.append(req.url.params.get("after_cursor"))
        return keyset_response(req, data, page=100)

    out = list(Gamma(make(handler)).iter_markets(active="true"))
    assert [m["id"] for m in out] == list(range(250))
    assert calls == [None, "100", "200"]


def test_ignored_cursor_is_detected():
    g = Gamma(make(lambda req: httpx.Response(200, json={"markets": [{"id": 1}], "next_cursor": "x" + str(len(req.url.params))})))
    with pytest.raises(RuntimeError):
        list(g.iter_markets())


def test_retry_on_429_then_success():
    n = {"c": 0}

    def handler(req):
        n["c"] += 1
        if n["c"] < 3:
            return httpx.Response(429, headers={"Retry-After": "1"})
        return httpx.Response(200, json=[])

    assert make(handler).get("/markets") == []
    assert n["c"] == 3


def test_gives_up_after_max_retries():
    api = make(lambda req: httpx.Response(503))
    api.max_retries = 2
    with pytest.raises(httpx.HTTPStatusError):
        api.get("/markets")


def test_runaway_pagination_guard():
    n = {"i": 0}

    def handler(req):
        n["i"] += 1
        return httpx.Response(200, json={"markets": [{"id": n["i"]}], "next_cursor": f"c{n['i']}"})

    g = Gamma(make(handler), page_size=1, max_pages=5)
    with pytest.raises(RuntimeError):
        list(g.iter_markets())


def test_price_history_parsing_skips_bad_points():
    def handler(req):
        assert req.url.params["market"] == "tok"
        return httpx.Response(200, json={"history": [{"t": 1700000000, "p": 0.42}, {"t": "bad"}, {"p": 1}]})

    pts = Clob(make(handler)).price_history("tok", 60)
    assert len(pts) == 1 and pts[0][1] == 0.42 and pts[0][0].year == 2023
