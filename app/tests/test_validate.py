import httpx

from pmintel import validate
from pmintel.http import ApiClient
from pmintel.sources import Clob, Gamma

from keyset_mock import keyset_response

MKT = {"id": "1", "question": "Q?", "outcomes": '["Yes","No"]', "outcomePrices": '["0.4","0.6"]',
       "clobTokenIds": '["a","b"]', "volume24hr": 10, "events": [{"id": 9, "slug": "ev"}]}
CLOSED = {**MKT, "id": "2", "closed": True, "outcomePrices": '["1","0"]'}


def api(handler):
    return ApiClient("https://x", client=httpx.Client(transport=httpx.MockTransport(handler)),
                     sleep=lambda s: None, min_interval=0, max_retries=0)


def gamma_handler(r):
    if r.url.path == "/markets/keyset":
        return keyset_response(r, [MKT, {**MKT, "id": "3"}], page=1)
    if r.url.params.get("offset") == "20000":
        return httpx.Response(422)  # the real production behaviour
    if r.url.params.get("closed") == "true":
        return httpx.Response(200, json=[CLOSED])
    return httpx.Response(200, json=[MKT])


def clob_handler(r):
    if r.url.path == "/book":
        return httpx.Response(200, json={"bids": [{"price": "0.39", "size": "100"}],
                                         "asks": [{"price": "0.41", "size": "100"}]})
    return httpx.Response(200, json={"history": [{"t": 1700000000, "p": 0.5}]})


def test_all_checks_pass_on_healthy_api():
    res = validate.run(Gamma(api(gamma_handler)), Clob(api(clob_handler)))
    assert res["passed"] == res["total"] == 5, res


def test_failures_are_recorded_not_raised():
    res = validate.run(Gamma(api(lambda r: httpx.Response(503))), Clob(api(lambda r: httpx.Response(503))))
    assert res["passed"] < res["total"]
    assert any("HTTPStatusError" in c.get("error", "") for c in res["checks"])
