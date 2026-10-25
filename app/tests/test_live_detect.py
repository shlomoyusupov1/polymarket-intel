"""Failure and stress tests for the Market Risk Monitor (live_detect): every scenario the owner listed."""
import math
import random

from pmintel import live_detect as D

T0 = 1_760_000_000.0


def feed(s, prices, start=T0, step=5.0, bid_ask=None, trade=False):
    t = start
    for i, p in enumerate(prices):
        ba = bid_ask(i, p) if bid_ask else (None, None)
        s.add(D.Obs(t, t + 0.3, p, ba[0], ba[1], trade))
        t += step
    return t


def calm(n, p0=100.0, vol=0.0005, seed=1):
    r, p, out = random.Random(seed), p0, []
    for _ in range(n):
        p *= 1 + r.gauss(0, vol)
        out.append(p)
    return out


def test_fast_drop_is_detected_within_the_window_with_its_source_time():
    s = D.Series("cx:BTC-USD", "crypto", "BTC", "coinbase")
    t = feed(s, calm(720))                                     # one quiet hour
    p = s.last().price
    t = feed(s, [p * (1 - 0.008 * k) for k in range(1, 7)], start=t)   # -4.7% in 30 s
    ev = [e for e in D.detect(s, t) if e.kind == "move"]
    assert ev and ev[0].window == 60 and ev[0].size < -0.02 and "ירד" in ev[0].text
    assert ev[0].src_t == s.last().t and ev[0].recv_t - ev[0].src_t < 1


def test_quiet_market_raises_nothing():
    s = D.Series("cx:ETH-USD", "crypto", "ETH", "coinbase")
    t = feed(s, calm(2000, vol=0.0003))
    assert D.detect(s, t) == [] and D.stale(s, t) is None


def test_single_bad_tick_is_quarantined_but_a_confirmed_jump_counts():
    s = D.Series("st:AMD", "stocks", "AMD", "yahoo")
    t = feed(s, calm(300, vol=0.0002))
    p = s.last().price
    assert s.add(D.Obs(t, t, p * 0.5)) is False                  # -50% in one tick: quarantined
    assert s.add(D.Obs(t + 5, t + 5, p)) is True                 # next tick back to normal: it was noise
    assert s.bad == 1 and s.last().price == p and D.detect(s, t + 5) == []
    s.add(D.Obs(t + 10, t + 10, p * 0.6))                         # a real crash: two ticks agree
    s.add(D.Obs(t + 15, t + 15, p * 0.61))
    assert s.last().price == p * 0.61 and any(e.kind == "move" for e in D.detect(s, t + 15))


def test_invalid_values_are_rejected():
    s = D.Series("pm:1", "polymarket", "X", "polymarket")
    for v in (None, float("nan"), float("inf"), -1, 0, 1.7):
        assert s.add(D.Obs(T0, T0, v)) is False
    assert s.bad == 6 and s.last() is None
    s.add(D.Obs(T0 + 10, T0 + 10, 0.5))
    assert s.add(D.Obs(T0, T0, 0.5)) is False                    # out of order


def test_stuck_stream_is_reported_and_never_shown_fresh():
    s = D.Series("cx:SOL-USD", "crypto", "SOL", "coinbase")
    t = feed(s, calm(50))
    assert D.is_fresh(s, t) and D.stale(s, t) is None
    later = t + D.STALE["crypto"] + 60                            # no message for 3 minutes
    ev = D.stale(s, later)
    assert ev and ev.kind == "stale" and not D.is_fresh(s, later)
    stocks = D.Series("st:SPY", "stocks", "SPY", "yahoo")
    feed(stocks, calm(10))
    assert D.stale(stocks, later + 10_000, market_open=False) is None   # closed market is not a fault
    assert D.stale(D.Series("cx:X", "crypto", "X"), T0).text.endswith("עוד לא התקבל מחיר")


def test_spread_blowout_on_polymarket():
    s = D.Series("pm:9", "polymarket", "Eizenkot", "polymarket")
    t = feed(s, [0.50] * 400, bid_ask=lambda i, p: (0.495, 0.505))
    s.add(D.Obs(t, t, 0.50, 0.42, 0.58))
    ev = [e for e in D.detect(s, t) if e.kind == "spread"]
    assert ev and "נקודות" in ev[0].text and ev[0].size > 0.1


def test_contract_repricing_in_points():
    s = D.Series("pm:9", "polymarket", "Eizenkot", "polymarket")
    t = feed(s, [0.50] * 100)
    t = feed(s, [0.50 + 0.01 * k for k in range(1, 13)], start=t)     # +12 points in a minute
    ev = [e for e in D.detect(s, t) if e.kind == "move"]
    assert ev and "עלה" in ev[0].text and "נקודות" in ev[0].text


def test_volatility_spike_needs_a_real_baseline():
    s = D.Series("cx:XRP-USD", "crypto", "XRP", "coinbase")
    t = feed(s, calm(3000, vol=0.0003, seed=3))                        # ~4 hours calm
    r = random.Random(9)
    p = s.last().price
    noisy = []
    for _ in range(180):                                               # 15 minutes of wild swings, no trend
        p *= 1 + r.choice([-1, 1]) * 0.004
        noisy.append(p)
    t = feed(s, noisy, start=t)
    assert any(e.kind == "vol_spike" for e in D.detect(s, t))
    short = D.Series("cx:Y", "crypto", "Y")
    t2 = feed(short, noisy)
    assert not any(e.kind == "vol_spike" for e in D.detect(short, t2))  # no baseline -> no claim


def test_breadth_needs_several_fresh_assets_falling_together():
    group = []
    for k, name in enumerate(["SPY", "QQQ", "IWM", "XLK"]):
        s = D.Series(f"st:{name}", "stocks", name, "yahoo")
        prices = calm(200, seed=k) + [calm(200, seed=k)[-1] * (1 - 0.0004 * i) for i in range(1, 181)]
        t = feed(s, prices)
        group.append(s)
    ev = D.breadth(group, t)
    assert ev and ev.kind == "breadth" and "4 מתוך 4" in ev.text
    assert D.breadth(group[:2], t) is None                             # too few assets to call it broad
    assert D.breadth(group, t + 10_000) is None                        # stale data is never "broad"


def test_rarity_compares_with_the_assets_own_history():
    s = D.Series("cx:BTC-USD", "crypto", "BTC", "coinbase")
    t = feed(s, calm(4000, vol=0.0004))                                 # > 5 hours
    p = s.last().price
    t = feed(s, [p * (1 - 0.007 * k) for k in range(1, 9)], start=t)
    ev = [e for e in D.detect(s, t) if e.kind == "move"][0]
    assert ev.rarity is not None and ev.rarity < 0.01


def test_latency_stats():
    st = D.latency_stats([0.2, 0.4, 0.3, 5.0] + [0.25] * 96)
    assert st["n"] == 100 and st["max"] == 5.0 and st["p95"] < 1 and abs(st["mean"] - (0.9 + 5.0 + 24) / 100) < 1e-9
    assert D.latency_stats([])["mean"] is None


def test_long_history_stays_fast():
    import time
    s = D.Series("cx:BTC-USD", "crypto", "BTC", "coinbase")
    t = feed(s, calm(40_000, vol=0.0003), step=2.2)                    # a full day of 2-second ticks
    t0 = time.perf_counter()
    for _ in range(20):
        D.detect(s, t)
    assert (time.perf_counter() - t0) / 20 < 0.25 and len(s.obs) <= 40_000
    assert not math.isnan(s.last().price)
