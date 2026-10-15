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
    assert s["trades"] == 1 and s["events"] == 1 and s["edge_supported"] is False


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
    assert s["edge_supported"] is False  # zero variance -> no stderr -> not claimed
    few = strategy(rows([(0.02, 0, "a"), (0.02, 0, "b"), (0.02, 1, "c")]), "NO", 0, 0.05, 0, 0)
    assert few["edge_supported"] is False
