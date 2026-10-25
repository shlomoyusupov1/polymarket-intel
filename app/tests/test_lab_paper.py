import os
from datetime import datetime, time
from zoneinfo import ZoneInfo

import pytest
from psycopg.types.json import Jsonb

from pmintel import lab_paper as P, stocklab as L
from test_stocklab import walk

DB = os.environ.get("PMINTEL_TEST_DB")
NY = ZoneInfo("America/New_York")


def at(day, hh=18):
    return datetime.combine(datetime.fromisoformat(day).date(), time(hh, 0), NY)


def test_deadline_and_summary_gate():
    assert P.next_open("2026-10-16") == datetime(2026, 10, 19, 9, 30, tzinfo=NY)  # Friday -> Monday open
    rows = [("s", 0.02 if i % 4 else -0.01, f"2026-{1 + i // 20:02d}-{1 + i % 20:02d}") for i in range(60)]
    s = P.summary(rows)["s"]
    assert s["n"] == 60 and s["status"] == "confirmed" and s["success_rate"] == 0.75
    assert P.summary(rows[:20])["s"]["status"] == "collecting"


@pytest.mark.skipif(not DB, reason="PMINTEL_TEST_DB not set")
def test_signals_are_sealed_before_the_open_and_settled_like_the_backtest(monkeypatch):
    from pmintel import db
    conn = db.connect(DB)
    conn.execute("DROP TABLE IF EXISTS lab_signals CASCADE")
    db.init_schema(conn)
    conn.execute("DELETE FROM research_reports WHERE kind = 'stocklab_v1'")
    conn.execute("DELETE FROM lab_marks")
    lab = {"strategies": {"rsi2_pullback": {"verdict": "not_supported", "current_params": {"params": {"t": 101}, "hold": 5}}}}
    conn.execute("INSERT INTO research_reports (kind, params, result) VALUES ('stocklab_v1', '{}', %s)", (Jsonb(lab),))
    full = {s: walk(k, 420, drift=0.002) for k, s in enumerate([L.BENCH] + list(L.UNIVERSE[:3]))}
    monkeypatch.setattr(L, "UNIVERSE", L.UNIVERSE[:3])
    cut = 400
    fetch = lambda n: (lambda s, range_="2y": full[s][:n])
    day0 = full[L.BENCH][cut - 1]["day"]
    # too late (after the next open): nothing may be recorded
    late = P.next_open(day0).replace(hour=10)
    assert P.run_once(conn, now=late, fetcher=fetch(cut), pause=0)["recorded"] == 0
    rep = P.run_once(conn, now=at(day0), fetcher=fetch(cut), pause=0)
    assert rep["recorded"] == 3  # every symbol is in an uptrend and RSI < 101
    assert P.run_once(conn, now=at(day0), fetcher=fetch(cut), pause=0)["recorded"] == 0  # no duplicate bets
    # six sessions later: entry at the next open, exit at the close of the 5th session
    day6 = full[L.BENCH][cut + 5]["day"]
    P.run_once(conn, now=at(day6), fetcher=fetch(cut + 6), pause=0)
    sym = L.UNIVERSE[0]
    st, ex, exit_day = conn.execute("SELECT status, excess, exit_day FROM lab_signals WHERE symbol = %s AND signal_day = %s",
                                    (sym, day0)).fetchone()
    b, spy = full[sym], full[L.BENCH]
    cost = L.features(b[:cut])["cost"][-1]
    want = (b[cut + 4]["c"] / b[cut]["o"] - 1) - (spy[cut + 4]["c"] / spy[cut]["o"] - 1) - cost
    assert st == "closed" and exit_day.isoformat() == b[cut + 4]["day"] and abs(ex - want) < 1e-9
    state = conn.execute("SELECT DISTINCT strategy_state FROM lab_signals").fetchall()
    assert state == [("not_eligible",)]                       # the lab verdict was not_supported when sealed
    sid = conn.execute("SELECT id FROM lab_signals WHERE symbol = %s AND signal_day = %s", (sym, day0)).fetchone()[0]
    days = [d.isoformat() for (d,) in conn.execute("SELECT day FROM lab_marks WHERE signal_id = %s ORDER BY day", (sid,))]
    assert days[0] == b[cut]["day"] and days[-1] == b[cut + 4]["day"] and len(days) == 5   # entry .. exit, daily
    conn.close()


def test_state_now_is_sealed_from_history_and_forward_record():
    assert P.state_now("not_supported", {"status": "confirmed"}) == "not_eligible"
    assert P.state_now("candidate_for_paper", None) == "validating"
    assert P.state_now("candidate_for_paper", {"status": "collecting"}) == "validating"
    assert P.state_now("candidate_for_paper", {"status": "confirmed"}) == "confirmed"
