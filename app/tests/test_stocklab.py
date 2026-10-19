import random
from datetime import date, timedelta

from pmintel import stocklab as L


def days(n, start=date(2015, 1, 2)):
    out, d = [], start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d += timedelta(days=1)
    return out


def walk(seed, n, drift=0.0003, vol=0.015, plant=None):
    """Random walk bars. plant(i, closes) may return a jump applied from the next day's open."""
    rnd = random.Random(seed)
    D = days(n)
    c, bars, pend = 50.0, [], {}
    for i, d in enumerate(D):
        o = c * (1 + rnd.gauss(0, 0.002))
        c = o * (1 + drift + pend.pop(i, 0.0) + rnd.gauss(0, vol))
        h, l = max(o, c) * 1.004, min(o, c) * 0.996
        bars.append({"day": d, "o": o, "h": h, "l": l, "c": c, "dv": 5e8 * (1 + rnd.random())})
        if plant and plant(i, [b["c"] for b in bars]):
            pend[i + 2] = 0.03  # rises during the day after entry: tradable at the next open
    return bars


def test_signals_never_look_ahead():
    b = walk(1, 600)
    f = L.features(b)
    for i in range(260, 590, 7):
        cut = L.features(b[: i + 1])
        for name, spec in L.STRATEGIES.items():
            for p in spec["grid"]:
                assert L.signal(name, f, i, p) == L.signal(name, cut, i, p), (name, p, i)


def test_random_market_finds_nothing():
    spy = walk(0, 1800)
    feats = {f"S{k}": L.features(walk(100 + k, 1800)) for k in range(12)}
    L.add_ranks(feats)
    for name in L.STRATEGIES:
        wf = L.walk_forward(name, feats, spy)
        s = L.summarize(wf["oos"])
        s["p_holm"] = s["p_edge"]
        assert L.verdict(s) in ("insufficient_data", "not_supported"), (name, s["edge_lo95"])


def test_planted_pullback_edge_is_found_out_of_sample():
    def plant(i, c):  # after a 3-day drop of 4%+, the stock rises 3% the day after entry
        return i >= 4 and c[-1] / c[-4] - 1 <= -0.04
    spy = walk(0, 1800)
    feats = {f"S{k}": L.features(walk(200 + k, 1800, vol=0.02, plant=plant)) for k in range(15)}
    wf = L.walk_forward("rsi2_pullback", feats, spy)
    s = L.summarize(wf["oos"])
    assert s["n"] >= 50 and s["edge_lo95"] > 0 and s["edge_vs_baseline"] > 0.005
    assert set(wf["choices"]) and all(int(y) >= 2018 for y in wf["choices"])  # first 3 years train only


def test_costs_baseline_and_holm():
    flat = [{"day": d, "o": 10, "h": 10.2, "l": 9.8, "c": 10, "dv": 1e6} for d in days(40)]
    assert abs(L.features(flat)["cost"][30] - (0.04 + L.costs.FEE_RT)) < 0.002
    h = L.holm({"a": 0.01, "b": 0.04, "c": None})
    assert h == {"a": 0.02, "b": 0.04, "c": None}
    m, se = L.clustered_mean([1.0, 1.0, -1.0, -1.0], ["w1", "w1", "w2", "w2"])
    assert m == 0 and se > 0.9  # same-week trades move together -> wide CI


def test_fetch_adjusts_splits(monkeypatch):
    class R:
        status_code = 200
        def raise_for_status(self): pass
        def json(self):
            return {"chart": {"result": [{"meta": {"exchangeTimezoneName": "America/New_York"},
                    "timestamp": [1700000000, 1700086400],
                    "indicators": {"quote": [{"open": [400, 101], "high": [410, 103], "low": [390, 99],
                                              "close": [404, 102], "volume": [10, 40]}],
                                   "adjclose": [{"adjclose": [101, 102]}]}}]}}
    class C:
        def get(self, url, params=None): return R()
    b = L.fetch("X", client=C())
    assert abs(b[0]["c"] - 101) < 1e-9 and abs(b[0]["o"] - 100) < 1e-9  # 4:1 split removed
    assert b[0]["dv"] == 404 * 10 and b[1]["dv"] == 102 * 40


def test_cross_sectional_ranks_and_calendar_never_look_ahead():
    bars = {f"S{k}": walk(300 + k, 700, drift=0.0002 * (k - 6)) for k in range(12)}
    full = {s: L.features(b) for s, b in bars.items()}
    L.add_ranks(full)
    fired = 0
    for i in range(300, 690, 9):
        cut = {s: L.features(b[: i + 1]) for s, b in bars.items()}
        L.add_ranks(cut)
        for name in ("momentum_12_1", "low_volatility", "turn_of_month"):
            for p in L.STRATEGIES[name]["grid"]:
                for s in bars:
                    a, b = L.signal(name, full[s], i, p), L.signal(name, cut[s], i, p)
                    assert a == b, (name, p, s, i)
                    fired += a
    assert fired > 0
    assert L._weekdays_left("2026-10-27") == 3 and L._weekdays_left("2026-10-30") == 0
    # the top-momentum names really are the ones with the best 12-1 return that day
    i = 400
    top = max(full, key=lambda s: full[s]["mom"][i])
    assert full[top]["mom_rank"][i] == 1.0


def _bars(ohlc, lead=30, start=100.0, step=-1.0):
    """`lead` steady bars moving by `step`, then the given (o, h, l, c) bars."""
    D = days(lead + len(ohlc))
    out, c = [], start
    for k in range(lead):
        o, c = c, c + step
        out.append({"day": D[k], "o": o, "h": max(o, c) + 0.2, "l": min(o, c) - 0.2, "c": c, "dv": 1e8})
    for k, (o, h, l, c) in enumerate(ohlc):
        out.append({"day": D[lead + k], "o": o, "h": h, "l": l, "c": c, "dv": 1e8})
    return L.features(out)


def test_candlestick_patterns_are_recognised():
    last = lambda f: len(f["c"]) - 1
    f = _bars([(70.5, 70.6, 66.0, 70.0)])            # falling, then a hammer: long lower wick, tiny upper
    assert L.signal("candle_hammer", f, last(f), {"k": 2}) and not L.signal("candle_doji", f, last(f), {"d": 0.05})
    f = _bars([(70.0, 70.5, 69.5, 70.02)])          # falling, then open == close
    assert L.signal("candle_doji", f, last(f), {"d": 0.05})
    f = _bars([(71.0, 71.2, 69.8, 70.0), (69.8, 71.6, 69.7, 71.4)])  # red, then green body covering it
    assert L.signal("candle_bull_engulf", f, last(f), {"trend": False})
    assert not L.signal("candle_bull_engulf", f, last(f), {"trend": True})  # below its 200-day: needs a long uptrend
    f = _bars([(72.0, 72.1, 68.0, 68.2), (68.0, 68.6, 67.6, 68.1), (68.3, 71.4, 68.2, 71.2)])
    assert L.signal("candle_morning_star", f, last(f), {"m": 0.75})
    f = _bars([(70.0, 71.1, 69.9, 71.0), (70.8, 72.1, 70.7, 72.0), (71.8, 73.1, 71.7, 73.0)])
    assert L.signal("candle_three_soldiers", f, last(f), {"u": 0.2})
    up = _bars([(130.0, 134.5, 129.9, 130.4)], start=100.0, step=1.0)  # rising, then a long upper wick
    assert L.signal("candle_shooting_star", up, last(up), {"k": 2}) and not L.signal("candle_hammer", up, last(up), {"k": 2})
    up = _bars([(129.0, 130.3, 128.9, 130.2), (130.4, 130.5, 128.6, 128.8)], start=100.0, step=1.0)
    assert L.signal("candle_bear_engulf", up, last(up), {"trend": False})


def test_warning_patterns_score_the_lag_behind_the_benchmark():
    assert L.side("candle_shooting_star") == -1 and L.side("candle_hammer") == 1
    b, spy = walk(5, 40), walk(6, 40)
    f = L.features(b)
    sidx = L._spy_index(spy)
    long_, _, cost = L._excess(f, 10, 5, spy, sidx)
    warn, _, _ = L._excess(f, 10, 5, spy, sidx, sd=-1)
    assert abs((long_ + cost) + (warn + cost)) < 1e-12  # mirror images before cost; both pay the cost
