import httpx

from pmintel import validate
from pmintel.http import ApiClient
from pmintel.sources import Clob, Gamma

from keyset_mock import keyset_response

MKT = {"id": "1", "question": "Q?", "outcomes": '["Yes","No"]', "outcomePrices": '["0.4","0.6"]',
       "clobTokenIds": '["a","b"]', "volume24hr": 10, "events": [{"id": 9, "slug": "ev"}]}
CLOSED = {**MKT, "id": "2", "closed": True, "outcomePrices": '["1","0"]', "conditionId": "0xabc"}


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


def data_api_handler(r):
    p = r.url.path
    if p == "/v2/status":
        return httpx.Response(200, json={"data": {"computed_at": "2026-10-18T00:00:00Z", "age_seconds": 12,
                                                  "serving": {}, "ingestion": {}}})
    if p == "/v2/prices-history":
        return httpx.Response(200, json={"data": [{"t": 1700000000, "p": 0.5}, {"t": 1700003600, "p": 0.6}]})
    if p == "/v2/resolutions":
        return httpx.Response(200, json={"data": [{"condition_id": "0xabc", "winning_outcome": "Yes"}]})
    return httpx.Response(404)


def dapi(handler):
    from pmintel.dataapi import DataApi
    return DataApi(api(handler))


def test_all_checks_pass_on_healthy_api():
    res = validate.run(Gamma(api(gamma_handler)), Clob(api(clob_handler)), dapi(data_api_handler))
    assert res["passed"] == res["total"] == 8, res
    cov = next(c for c in res["checks"] if c["check"] == "data_api_v2_history_coverage")["detail"]
    assert cov["rows"][0]["data_api_points"] == 2


def test_resolution_disagreement_fails_the_check():
    bad = lambda r: httpx.Response(200, json={"data": [{"condition_id": "0xabc", "winning_outcome": "No"}]}) \
        if r.url.path == "/v2/resolutions" else data_api_handler(r)  # noqa: E731
    res = validate.run(Gamma(api(gamma_handler)), Clob(api(clob_handler)), dapi(bad))
    chk = next(c for c in res["checks"] if c["check"] == "data_api_v2_resolution_agreement")
    assert chk["ok"] is False and chk["detail"]["disagree"] == 1


def test_failures_are_recorded_not_raised():
    res = validate.run(Gamma(api(lambda r: httpx.Response(503))), Clob(api(lambda r: httpx.Response(503))),
                       dapi(lambda r: httpx.Response(503)))
    assert res["passed"] < res["total"]
    assert any("HTTPStatusError" in c.get("error", "") for c in res["checks"])
