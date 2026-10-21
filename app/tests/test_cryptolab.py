import os
import random
from datetime import date, datetime, time, timedelta, timezone

import pytest
from psycopg.types.json import Jsonb

from pmintel import cryptolab as C, lab_paper as P, stocklab as L

DB = os.environ.get("PMINTEL_TEST_DB")
M = C.CRYPTO


def coin(seed, n, drift=0.001, start=date(2018, 1, 1)):
    """Every calendar day, like a coin."""
    rnd = random.Random(seed)
    c, out = 100.0, []
    for i in range(n):
        o = c * (1 + rnd.gauss(0, 0.001))
        c = o * (1 + drift + rnd.gauss(0, 0.03))
        out.append({"day": (start + timedelta(days=i)).isoformat(), "o": o, "h": max(o, c) * 1.01,
                    "l": min(o, c) * 0.99, "c": c, "dv": 1e9 * (1 + rnd.random())})
    return out


def test_market_spec():
    assert "turn_of_month" not in M.strategies and M.bench == "BTC-USD" and M.lag == 1
    assert M.holds == (7, 14, 30) and M.year == 365
    f = L.features(coin(1, 500), M)
    assert set(f["cost"]) == {M.fixed_cost}
    assert f["max252"][363] is None and f["max252"][364] is not None  # a one-year high needs 365 days
    assert f["mom"][364] is None and f["mom"][365] is not None


def test_entry_waits_one_full_day():
    """A signal on bar i is entered at the open of i+2 (bar i+1 opens the instant i closes)."""
    b, btc = coin(2, 60), coin(3, 60)
    f = L.features(b, M)
    sidx = L._spy_index(btc)
    x, entry, cost, _raw = L._excess(f, 10, 7, btc, sidx, lag=M.lag)
    assert entry == b[12]["day"]
    want = (b[18]["c"] / b[12]["o"] - 1) - (btc[18]["c"] / btc[12]["o"] - 1) - M.fixed_cost
    assert abs(x - want) < 1e-12


def test_run_reports_crypto_against_bitcoin():
    data = {s: coin(10 + k, 2200) for k, s in enumerate(C.UNIVERSE)}
    r = C.run(fetcher=lambda s: data[s], symbols=C.UNIVERSE[:12], pause=0)
    assert r["market"] == "crypto" and r["benchmark"] == "BTC-USD"
    assert set(r["strategies"]) == set(M.strategies)
    for v in r["strategies"].values():
        assert v["verdict"] in ("insufficient_data", "not_supported", "edge_below_target", "candidate_for_paper")
        assert set(v["holds"]) == set(M.holds)
    # random walks: nothing may pass the gate
    assert not [k for k, v in r["strategies"].items() if v["verdict"] == "candidate_for_paper"]


def test_deadline_is_the_entry_bar_open():
    assert P.deadline("2026-10-19", M) == datetime(2026, 10, 21, 0, 0, tzinfo=timezone.utc)


def test_split_keeps_markets_apart():
    rows = [("rsi2_pullback", 0.1, "2026-01-01"), ("crypto:rsi2_pullback", -0.1, "2026-01-02")]
    assert P.split(rows) == [rows[0]]
    assert P.split(rows, M) == [("rsi2_pullback", -0.1, "2026-01-02")]


@pytest.mark.skipif(not DB, reason="PMINTEL_TEST_DB not set")
def test_crypto_forward_is_sealed_and_settled_with_the_lag(monkeypatch):
    from pmintel import db
    conn = db.connect(DB)
    conn.execute("DROP TABLE IF EXISTS lab_signals CASCADE")
    db.init_schema(conn)
    conn.execute("DELETE FROM research_reports WHERE kind = 'cryptolab_v1'")
    lab = {"strategies": {"rsi2_pullback": {"verdict": "not_supported", "current_params": {"params": {"t": 101}, "hold": 7}}}}
    conn.execute("INSERT INTO research_reports (kind, params, result) VALUES ('cryptolab_v1', '{}', %s)", (Jsonb(lab),))
    syms = ["BTC-USD", "ETH-USD", "XRP-USD"]
    full = {s: coin(k, 520, drift=0.004) for k, s in enumerate(syms)}
    m = C.CRYPTO.__class__(**{**C.CRYPTO.__dict__, "universe": tuple(syms)})
    cut = 500
    day0 = full["BTC-USD"][cut - 1]["day"]
    fetch = lambda n: (lambda s, range_="2y": full[s][:n + 1])  # bar n is today's, still forming
    now = lambda day, hh: datetime.combine(date.fromisoformat(day) + timedelta(days=1), time(hh, 20), timezone.utc)
    # after the entry bar opened: too late
    only_day0 = lambda s, range_="2y": full[s][:cut]
    assert P.run_once(conn, now=now(day0, 0) + timedelta(days=1), fetcher=only_day0, pause=0, m=m)["recorded"] == 0
    rep = P.run_once(conn, now=now(day0, 0), fetcher=fetch(cut), pause=0, m=m)
    assert rep["recorded"] >= 1 and rep["latest_bar"] == day0  # RSI < 101 fires wherever price > 200-day average
    names = {r[0] for r in conn.execute("SELECT strategy FROM lab_signals")}
    assert names == {"crypto:rsi2_pullback"}
    # the stock tracker never touches crypto rows
    assert P.settle(conn, {}, [], L.STOCKS) == 0
    later = full["BTC-USD"][cut + 8]["day"]
    P.run_once(conn, now=now(later, 0), fetcher=fetch(cut + 9), pause=0, m=m)
    sym, st, ex, entry, exit_day = conn.execute("""SELECT symbol, status, excess, entry_day, exit_day FROM lab_signals
                                                   ORDER BY symbol LIMIT 1""").fetchone()
    b, btc = full[sym], full["BTC-USD"]
    want = (b[cut + 7]["c"] / b[cut + 1]["o"] - 1) - (btc[cut + 7]["c"] / btc[cut + 1]["o"] - 1) - M.fixed_cost
    assert st == "closed" and entry.isoformat() == b[cut + 1]["day"] and exit_day.isoformat() == b[cut + 7]["day"]
    assert abs(ex - want) < 1e-9
    conn.close()
