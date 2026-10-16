import math
import os
from datetime import date, datetime, timedelta, timezone

import httpx
import pytest

from pmintel import etf_paper as ep

START = date(2025, 9, 1)


def bdays(start, n):
    out, d = [], start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d += timedelta(days=1)
    return out


DAYS = bdays(START, 320)  # warm-up + a few window-like sessions


def series(base, drift, vol=1_000_000, crash_at=None):
    bars, p = {}, base
    for i, d in enumerate(DAYS):
        p *= 1 + drift
        o = p * 0.999
        lo, hi = p * 0.99, p * 1.01
        if crash_at is not None and i >= crash_at:
            o, lo, p, hi = p * 0.8, p * 0.75, p * 0.8, p * 0.81
        bars[d] = {"o": o, "h": hi, "l": lo, "c": p, "v": vol}
    return bars


def universe(qqq_price=700.0):
    b = {s: series(100, 0.0) for s in ep.UNIVERSE}  # flat: no entries
    b["QQQ"] = series(qqq_price / 1.0015 ** 320, 0.0015)  # steady uptrend ending ~ $700
    return b


def intents_for(bars, sessions, prev, clock_ok=True, late=False):
    out = {}
    for s in sessions:
        based = prev[s]
        hist = {sym: [bars[sym][d] | {"day": d} for d in DAYS if d <= based][-300:] for sym in ep.UNIVERSE}
        for acct in ep.ACCOUNTS:
            payload = ep.build_intent(hist, date.fromisoformat(based), acct, {"clock_ok": clock_ok})
            opened = ep.market_open(date.fromisoformat(s))
            out[(acct, based)] = {"created_at": opened + (timedelta(minutes=1) if late else -timedelta(hours=12)),
                                  "payload": payload}
    return out


def window(n=5):
    sessions = DAYS[-n:]
    prev = {d: DAYS[i - 1] for i, d in enumerate(DAYS) if i}
    return sessions, prev


def test_fractional_sizing_trades_an_expensive_etf():
    bars = universe(700.0)
    sessions, prev = window()
    rep = ep.replay(bars, intents_for(bars, sessions, prev), [], sessions, prev)
    t = rep["accounts"]["trend"]
    fills = [d for d in t["decisions"] if d["status"] == "filled"]
    assert fills and fills[0]["symbol"] == "QQQ"
    q = fills[0]["quantity"]
    assert 0 < q < 1  # under v1's whole-share rule this was integer_size_zero
    assert fills[0]["notional"] <= 3000 * 0.20 + 1e-6
    assert fills[0]["initial_risk"] <= 3000 * 0.0025 + 1e-6
    assert math.isclose(q * 1000, round(q * 1000))  # 0.001-share steps
    assert rep["orders_submitted"] is False


def test_late_or_missing_intent_blocks_entries_and_is_recorded():
    bars = universe()
    sessions, prev = window()
    rep = ep.replay(bars, intents_for(bars, sessions, prev, late=True), [], sessions, prev)
    t = rep["accounts"]["trend"]
    assert t["filled_entries"] == 0
    assert [m["reason"] for m in t["missed_sessions"]] == ["intent_late"] * len(sessions)
    rep2 = ep.replay(bars, {}, [], sessions, prev)
    assert rep2["accounts"]["trend"]["missed_sessions"][0]["reason"] == "intent_missing"
    assert rep2["accounts"]["trend"]["liquidated_equity"] == 3000


def test_failed_health_check_vetoes_with_reason():
    bars = universe()
    sessions, prev = window()
    rep = ep.replay(bars, intents_for(bars, sessions, prev, clock_ok=False), [], sessions, prev)
    t = rep["accounts"]["trend"]
    assert t["filled_entries"] == 0 and t["rejected_reasons"].get("health_clock")


def test_missing_bar_marks_intent_data_not_ok():
    bars = universe()
    del bars["IEF"][DAYS[-2]]
    hist = {s: [bars[s][d] | {"day": d} for d in DAYS if d <= DAYS[-2] and d in bars[s]] for s in ep.UNIVERSE}
    it = ep.build_intent(hist, date.fromisoformat(DAYS[-2]), "trend", {"clock_ok": True})
    assert it["health"]["data_ok"] is False and "IEF:bar_missing_or_invalid" in it["health"]["data_problems"]


def test_stop_exit_and_accounting():
    bars = universe()
    crash = len(DAYS) - 2
    bars["QQQ"] = series(700 / 1.0015 ** 320, 0.0015, crash_at=crash)
    sessions, prev = window(5)
    rep = ep.replay(bars, intents_for(bars, sessions, prev), [], sessions, prev)
    t = rep["accounts"]["trend"]
    assert t["closed_trades"] >= 1 and t["trades"][0]["reason"] in ("gap_stop", "stop")
    assert t["trades"][0]["pnl"] < 0
    # no open positions -> liquidation equals cash equals initial + realized pnl
    assert not t["open_positions"]
    assert math.isclose(t["liquidated_equity"], 3000 + t["realized_pnl"], abs_tol=1e-6)


def test_benchmark_credits_dividends():
    bars = universe()
    sessions, prev = window(5)
    no_div = ep.replay(bars, {}, [], sessions, prev)["benchmark_spy"]
    div = ep.replay(bars, {}, [{"symbol": "SPY", "ex_date": sessions[2], "amount": 1.0}], sessions, prev)["benchmark_spy"]
    shares = 3000 * (1 - ep.COST) / bars["SPY"][sessions[0]]["o"]
    assert math.isclose(div["liquidated_equity"] - no_div["liquidated_equity"], shares, rel_tol=1e-9)


# ---------- job against a real Postgres, Yahoo mocked ----------
DB = os.environ.get("PMINTEL_TEST_DB")


def chart(sym, bars, divs=()):
    from zoneinfo import ZoneInfo
    tz = ZoneInfo("America/New_York")
    days = sorted(bars)
    ts = [int(datetime.combine(date.fromisoformat(d), datetime.min.time(), tz).replace(hour=9, minute=30).timestamp())
          for d in days]
    q = {k: [bars[d][k[0]] for d in days] for k in ("open", "high", "low", "close", "volume")}
    q = {"open": [bars[d]["o"] for d in days], "high": [bars[d]["h"] for d in days], "low": [bars[d]["l"] for d in days],
         "close": [bars[d]["c"] for d in days], "volume": [bars[d]["v"] for d in days]}
    ev = {str(i): {"date": int(datetime.fromisoformat(d + "T09:30:00-04:00").timestamp()), "amount": a}
          for i, (d, a) in enumerate(divs)}
    return {"chart": {"result": [{"meta": {"exchangeTimezoneName": "America/New_York"}, "timestamp": ts,
                                  "indicators": {"quote": [q]}, "events": {"dividends": ev}}]}}


@pytest.mark.skipif(not DB, reason="PMINTEL_TEST_DB not set")
def test_job_seals_once_and_keeps_first_seen_bars(monkeypatch):
    from pmintel import db, etf_job
    conn = db.connect(DB)
    conn.execute("DROP TABLE IF EXISTS etf_bars, etf_intents, etf_dividends, job_runs, research_reports CASCADE")
    db.init_schema(conn)
    bars = universe()
    last = DAYS[-1]
    now = datetime.fromisoformat(last + "T19:00:00-05:00").astimezone(timezone.utc)
    date_hdr = now.strftime("%a, %d %b %Y %H:%M:%S GMT")

    def handler(r):
        sym = r.url.path.rsplit("/", 1)[-1]
        return httpx.Response(200, json=chart(sym, bars[sym]), headers={"Date": date_hdr})
    monkeypatch.setattr(etf_job, "PROTOCOL", {**ep.PROTOCOL, "window_start": DAYS[-5], "window_end": "2099-01-01"})
    client = httpx.Client(transport=httpx.MockTransport(handler))
    rep = etf_job.run_once(conn, client, now=now)
    assert rep["latest_bar"] == last and sorted(rep["sealed_now"]) == ["breakout", "trend"]
    assert rep["health"]["clock_ok"] is True
    n_intents = conn.execute("SELECT count(*) FROM etf_intents").fetchone()[0]
    # a revised close for the same day must not overwrite the first-seen bar, and intents are sealed once
    orig = bars["SPY"][last]["c"]
    bars["SPY"][last] = dict(bars["SPY"][last], c=orig * 2)
    rep2 = etf_job.run_once(conn, client, now=now + timedelta(minutes=30))
    assert rep2["sealed_now"] == []
    assert conn.execute("SELECT count(*) FROM etf_intents").fetchone()[0] == n_intents
    assert conn.execute("SELECT c FROM etf_bars WHERE symbol='SPY' AND day=%s", (last,)).fetchone()[0] == orig
    # after the next open, nothing new can be sealed for that decision bar
    late = datetime.fromisoformat(last + "T09:31:00-04:00") + timedelta(days=3)
    assert etf_job.seal_intents(conn, etf_job.load(conn)[0], last, {"clock_ok": True}, late) == []
    from fastapi.testclient import TestClient
    from pmintel import api, config
    monkeypatch.setattr(config, "DATABASE_URL", DB)
    monkeypatch.setattr(db.config, "DATABASE_URL", DB)
    with TestClient(api.app) as tc:
        e = tc.get("/api/etf/latest").json()
        assert e["result"]["protocol"]["initial_capital"] == 3000 and e["result"]["orders_submitted"] is False
    conn.close()
