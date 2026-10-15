import json

from pmintel.normalize import normalize_market, resolved_outcome

# Shape taken from a real Gamma /markets response (closed, 2024 election market).
REAL_CLOSED = {
    "id": "253591",
    "question": "Will Donald Trump win the 2024 US Presidential Election?",
    "conditionId": "0xabc",
    "slug": "will-donald-trump-win-the-2024-us-presidential-election",
    "outcomes": '["Yes", "No"]',
    "outcomePrices": '["1", "0"]',
    "volumeNum": 1531479284.504353,
    "closedTime": "2024-11-06 15:17:41+00",
    "endDate": "2024-11-05T12:00:00Z",
    "clobTokenIds": '["2174", "4833"]',
    "umaResolutionStatus": "resolved",
    "closed": True,
    "active": True,
    "events": [{"id": 903193, "slug": "presidential-election-winner-2024"}],
}


def test_real_closed_market():
    r = normalize_market(REAL_CLOSED)
    assert r["id"] == "253591"
    assert r["outcomes"] == ["Yes", "No"]
    assert r["token_ids"] == ["2174", "4833"]
    assert r["ref_index"] == 0 and r["ref_outcome"] == "Yes"
    assert r["resolved_outcome"] == "Yes"
    assert r["event_id"] == "903193"
    assert r["url"] == "https://polymarket.com/event/presidential-election-winner-2024"
    assert r["closed_time"].isoformat() == "2024-11-06T15:17:41+00:00"
    assert r["end_date"].isoformat() == "2024-11-05T12:00:00+00:00"
    assert r["volume"] > 1.5e9


def test_yes_not_first():
    r = normalize_market({"id": 1, "outcomes": '["No","Yes"]', "outcomePrices": '["0.3","0.7"]',
                          "bestBid": "0.29", "bestAsk": "0.31"})
    assert r["ref_index"] == 1 and r["ref_outcome"] == "Yes" and r["ref_price"] == 0.7
    # Gamma bid/ask refer to the first token, which is not our reference -> must not be shown
    assert r["best_bid"] is None and r["best_ask"] is None


def test_non_yes_no_market_keeps_label():
    r = normalize_market({"id": 2, "outcomes": json.dumps(["Lakers", "Celtics"]),
                          "outcomePrices": json.dumps(["0.45", "0.55"])})
    assert r["ref_outcome"] == "Lakers" and r["ref_price"] == 0.45


def test_missing_and_garbage_fields():
    r = normalize_market({"id": 3, "outcomes": "not json", "outcomePrices": None, "volume": "abc"})
    assert r["outcomes"] == [] and r["ref_index"] is None and r["ref_price"] is None
    assert r["volume"] is None and r["url"] is None


def test_url_falls_back_to_market_slug():
    assert normalize_market({"id": 4, "slug": "x"})["url"] == "https://polymarket.com/event/x"


def test_open_market_is_never_resolved():
    r = normalize_market({"id": 5, "closed": False, "outcomes": '["Yes","No"]', "outcomePrices": '["1","0"]'})
    assert r["resolved_outcome"] is None


def test_resolved_outcome_rules():
    assert resolved_outcome(["Yes", "No"], [0.0, 1.0]) == "No"
    assert resolved_outcome(["Yes", "No"], [0.5, 0.5]) is None  # 50/50 split
    assert resolved_outcome(["Yes", "No"], [0.7, 0.3]) is None  # not settled
    assert resolved_outcome(["A", "B", "C"], [0, 1, 0]) == "B"
    assert resolved_outcome(["Yes", "No"], [1.0]) is None  # length mismatch
