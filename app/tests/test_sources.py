import httpx
import pytest

from pmintel.http import ApiClient
from pmintel.sources import Clob, Gamma


def make(handler):
    return ApiClient("https://x", client=httpx.Client(transport=httpx.MockTransport(handler)),
                     sleep=lambda s: None, min_interval=0)


def test_pagination_until_empty_page_even_if_server_caps_page_size():
    data = [{"id": i} for i in range(250)]
    calls = []

    def handler(req):
        off = int(req.url.params["offset"])
        calls.append(off)
        return httpx.Response(200, json=data[off:off + 100])  # server caps at 100 despite limit=500

    out = list(Gamma(make(handler), page_size=500).iter_markets(active="true"))
    assert [m["id"] for m in out] == list(range(250))
    assert calls == [0, 100, 200, 250]


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
    g = Gamma(make(lambda req: httpx.Response(200, json=[{"id": 1}])), page_size=1, max_pages=5)
    with pytest.raises(RuntimeError):
        list(g.iter_markets())


def test_price_history_parsing_skips_bad_points():
    def handler(req):
        assert req.url.params["market"] == "tok"
        return httpx.Response(200, json={"history": [{"t": 1700000000, "p": 0.42}, {"t": "bad"}, {"p": 1}]})

    pts = Clob(make(handler)).price_history("tok", 60)
    assert len(pts) == 1 and pts[0][1] == 0.42 and pts[0][0].year == 2023
