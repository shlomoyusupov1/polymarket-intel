import math

from pmintel.orderbook import summarize, vwap_for_notional

BOOK = {  # deliberately unsorted, with junk levels, like real-world input
    "bids": [{"price": "0.40", "size": "100"}, {"price": "0.45", "size": "200"},
             {"price": "0.30", "size": "1000"}, {"price": "bad", "size": "1"}],
    "asks": [{"price": "0.55", "size": "1000"}, {"price": "0.50", "size": "100"},
             {"price": "0.51", "size": "0"}],
}


def test_best_prices_and_spread():
    s = summarize(BOOK)
    assert s["best_bid"] == 0.45 and s["best_ask"] == 0.50
    assert math.isclose(s["spread"], 0.05)
    assert s["valid"] is True and s["n_bids"] == 3 and s["n_asks"] == 2


def test_crossed_book_is_invalid():
    s = summarize({"bids": [{"price": "0.6", "size": "10"}], "asks": [{"price": "0.5", "size": "10"}]})
    assert s["valid"] is False


def test_vwap_walks_the_book():
    # $100 buy: first level 0.50*100 = $50, remaining $50 at 0.55
    shares = 100 + 50 / 0.55
    assert math.isclose(summarize(BOOK)["buy_vwap_100"], 100 / shares)


def test_thin_book_returns_none():
    assert vwap_for_notional([(0.5, 10)], 100) is None  # only $5 available


def test_depth_within_5c():
    s = summarize(BOOK)
    assert math.isclose(s["bid_depth_5c"], 0.45 * 200 + 0.40 * 100)
    assert math.isclose(s["ask_depth_5c"], 0.50 * 100 + 0.55 * 1000)


def test_empty_book():
    assert summarize({"bids": [], "asks": []}) is None
    one_sided = summarize({"bids": [{"price": "0.2", "size": "5"}]})
    assert one_sided["best_ask"] is None and one_sided["spread"] is None and one_sided["valid"] is False
