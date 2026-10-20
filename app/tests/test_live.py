"""Live monitor service: parsing real feed shapes, alert levels, cooldown, persistence, disconnects, latency."""
import asyncio
import json
import os
import time

import pytest

from pmintel import live as LV, live_detect as D

DB = os.environ.get("PMINTEL_TEST_DB")


class FakeConn:
    def __init__(self):
        self.sql = []

    def execute(self, q, params=None):
        self.sql.append((q, params))

        class R:
            rowcount = 1

            def fetchall(self_inner):
                return []

            def fetchone(self_inner):
                return None
        return R()


def mon():
    m = LV.Monitor(FakeConn())
    m.roles = {}
    return m


def test_polymarket_messages_become_mid_prices_with_exchange_time():
    m = mon()
    s = m.series_for("pm:T1", "polymarket", "Eizenkot", "polymarket")
    now = time.time()
    ms = str(int((now - 0.4) * 1000))
    m.on_polymarket([{"event_type": "book", "asset_id": "T1", "timestamp": ms,
                      "bids": [{"price": "0.49", "size": "10"}, {"price": "0.48", "size": "5"}],
                      "asks": [{"price": "0.51", "size": "10"}]}], now)
    assert abs(s.last().price - 0.50) < 1e-9 and abs(s.last().t - (now - 0.4)) < 0.01
    m.on_polymarket({"event_type": "price_change", "timestamp": ms, "market": "x",
                     "price_changes": [{"asset_id": "T1", "best_bid": "0.55", "best_ask": "0.57"}]}, now + 3)
    assert abs(s.last().price - 0.56) < 1e-9
    m.on_polymarket({"event_type": "last_trade_price", "asset_id": "T1", "price": "0.57", "timestamp": ms}, now + 4)
    assert len(s.trades) == 1 and abs(s.last().price - 0.56) < 1e-9
    m.on_polymarket({"event_type": "price_change", "price_changes": [{"asset_id": "OTHER", "best_bid": "0.1", "best_ask": "0.2"}]}, now)
    assert m.feeds["polymarket"].msgs == 4 and abs(m.feeds["polymarket"].stats()["max"] - 4.4) < 0.01  # recv - exchange time


def test_coinbase_ticker_and_yahoo_quote():
    m = mon()
    cb = m.series_for("cx:BTC-USD", "crypto", "BTC", "coinbase")
    now = time.time()
    m.on_coinbase({"channel": "ticker", "timestamp": "2026-10-20T21:40:01.123456789Z",
                   "events": [{"type": "update", "tickers": [{"product_id": "BTC-USD", "price": "67000.5",
                                                              "best_bid": "67000", "best_ask": "67001"}]}]}, now)
    assert cb.last().price == 67000.5 and cb.last().bid == 67000.0
    m.on_coinbase({"channel": "heartbeats", "events": []}, now + 1)
    assert m.feeds["coinbase"].last_msg == now + 1
    st = m.series_for("st:SPY", "stocks", "SPY", "yahoo")
    m.on_yahoo("SPY", {"regularMarketPrice": 580.1, "regularMarketTime": int(now) - 15}, now)
    m.on_yahoo("SPY", {"regularMarketPrice": 580.1, "regularMarketTime": int(now) - 15}, now + 60)  # same quote time
    assert len(st.obs) == 1 and 14 < m.feeds["yahoo"].stats()["max"] < 76   # the real delay is measured


def drop(m, asset, cls, name, src="coinbase"):
    s = m.series_for(asset, cls, name, src)
    t = time.time() - 3600
    p = 100.0
    while t < time.time() - 40:
        s.add(D.Obs(t, t + 0.2, p))
        t += 5
    for k in range(1, 8):
        s.add(D.Obs(t, t + 0.2, p * (1 - 0.007 * k)))
        t += 5
    return s, t


def test_a_fall_on_a_paper_position_is_a_risk_alert_with_impact_and_no_sell_advice():
    m = mon()
    m.positions = {"ETH-USD": {"units": 1.0, "size": 100.0}}
    m.risk_usd = 2.0
    m.roles = {"cx:ETH-USD": {"position"}}
    s, t = drop(m, "cx:ETH-USD", "crypto", "ETH")
    assert m.evaluate(t) >= 1
    ins = [p for q, p in m.conn.sql if q.strip().startswith("INSERT INTO live_alerts")]
    lvl, kind, asset, text = ins[0][:4]
    assert lvl == "risk" and kind == "move" and asset == "cx:ETH-USD"
    assert "התראת סיכון" in text and "Coinbase WebSocket" in text and "זמן הנתון" in text
    assert "חורג ממגבלת הסיכון" in text and "לא המלצה למכור" in text and "לא לשקול כניסה חדשה" in text
    n = len(ins)
    m.evaluate(t + 10)                                      # cooldown: no repeat of the same alert
    assert len([1 for q, p in m.conn.sql if q.strip().startswith("INSERT INTO live_alerts")]) == n


def test_market_alert_without_position_and_data_alert_for_dead_feed():
    m = mon()
    m.roles = {"cx:BTC-USD": {"benchmark"}}
    s, t = drop(m, "cx:BTC-USD", "crypto", "BTC")
    m.crypto = {"BTC-USD"}
    m.feeds["coinbase"].connected = True
    m.feeds["coinbase"].last_msg = t
    m.evaluate(t)
    lv = [p[0] for q, p in m.conn.sql if q.strip().startswith("INSERT INTO live_alerts")]
    assert lv == ["market"]
    m.feeds["coinbase"].connected = False
    m.feeds["coinbase"].error = "ConnectionClosed"
    m.evaluate(t + 5)
    data = [p for q, p in m.conn.sql if q.strip().startswith("INSERT INTO live_alerts") and p[0] == "data"]
    assert data and "מנותק" in data[0][3] and "ConnectionClosed" in data[0][3]


def test_daily_cap_suppresses_market_noise_but_never_risk():
    m = mon()
    m.sent_today = (LV.datetime.fromtimestamp(time.time(), LV.IL).date().isoformat(), LV.DAILY_CAP)
    ev = D.Event("move", "cx:A", "crypto", "A", "A: ירד", 60, -0.03, 1.0, 1.1, time.time(), time.time(), "coinbase")
    m.raise_alert(ev, time.time(), "market")
    m.raise_alert(D.Event("move", "cx:B", "crypto", "B", "B: ירד", 60, -0.03, 1.0, 1.1, time.time(), time.time(), "coinbase"),
                  time.time(), "risk")
    st = [p[-1] for q, p in m.conn.sql if q.strip().startswith("INSERT INTO live_alerts")]
    assert st == ["suppressed", "pending"]


def test_websocket_disconnect_is_seen_and_the_feed_reconnects(monkeypatch):
    from websockets.asyncio.server import serve

    async def scenario():
        conns = []

        async def handler(ws):
            conns.append(ws)
            sub = json.loads(await ws.recv())
            assert sub["type"] == "market" and sub["assets_ids"] == ["T1"]
            await ws.send(json.dumps([{"event_type": "book", "asset_id": "T1", "timestamp": str(int(time.time() * 1000)),
                                       "bids": [{"price": "0.4", "size": "1"}], "asks": [{"price": "0.42", "size": "1"}]}]))
            if len(conns) == 1:
                await ws.close()                     # the exchange drops us
            else:
                await asyncio.sleep(5)

        async with serve(handler, "127.0.0.1", 0) as srv:
            port = srv.sockets[0].getsockname()[1]
            monkeypatch.setattr(LV, "PM_WS", f"ws://127.0.0.1:{port}")
            m = mon()
            m.pm_tokens = {"T1": "X"}
            m.series_for("pm:T1", "polymarket", "X", "polymarket")
            task = asyncio.create_task(m.polymarket_loop())
            for _ in range(60):
                await asyncio.sleep(0.1)
                if m.feeds["polymarket"].reconnects >= 1 and m.feeds["polymarket"].connected:
                    break
            task.cancel()
            f = m.feeds["polymarket"]
            assert f.reconnects >= 1 and f.connected and len(conns) >= 2
            assert abs(m.series["pm:T1"].last().price - 0.41) < 1e-9
    asyncio.run(scenario())


@pytest.mark.skipif(not DB, reason="PMINTEL_TEST_DB not set")
def test_persist_and_api_show_freshness_and_end_to_end_latency(monkeypatch):
    from fastapi.testclient import TestClient
    from pmintel import api, config, db
    conn = db.connect(DB)
    db.init_schema(conn)
    for t in ("live_prices", "live_feeds", "live_alerts", "live_ticks"):
        conn.execute(f"DELETE FROM {t}")
    m = LV.Monitor(conn)
    m.roles = {"cx:BTC-USD": {"benchmark"}}
    s, t = drop(m, "cx:BTC-USD", "crypto", "BTC")
    m.crypto = {"BTC-USD"}
    m.feeds["coinbase"].connected = True
    m.feeds["coinbase"].lag(t, t - 0.3)
    m.evaluate(t)
    m.persist(t, ticks=True)
    conn.execute("UPDATE live_alerts SET status = 'sent', sent_at = detected_at + interval '2 seconds'")
    monkeypatch.setattr(config, "DATABASE_URL", DB)
    monkeypatch.setattr(db.config, "DATABASE_URL", DB)
    with TestClient(api.app) as tc:
        st = tc.get("/api/live/status").json()
        assert st["feeds"]["coinbase"]["connected"] and st["prices"][0]["asset"] == "cx:BTC-USD"
        assert st["prices"][0]["fresh"] is True and st["prices"][0]["age_s"] < 120
        lat = st["latency"]
        # last tick 5 s before the evaluation, sent 2 s after detection: 5 s to detect, 7 s end to end
        assert lat["alerts"]["n"] == 1 and abs(lat["detect"]["mean"] - 5) < 0.5 and abs(lat["alerts"]["p95"] - 7) < 0.5
        assert lat["failed"] == 0
        assert tc.get("/api/live/alerts").json()[0]["level"] == "market"
    conn.close()
