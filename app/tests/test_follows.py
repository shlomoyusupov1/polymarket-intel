import os
from datetime import datetime, timedelta, timezone

import pytest

from pmintel import follows as F

DB = os.environ.get("PMINTEL_TEST_DB")


def test_parse_links_symbols_and_coins():
    assert F.parse("https://polymarket.com/event/next-israeli-prime-minister?tid=1") == ("polymarket", "next-israeli-prime-minister")
    assert F.parse("polymarket.com/he/event/a-b/c-d") == ("polymarket", "a-b/c-d")
    assert F.parse(" amd ") == ("stock", "AMD")
    assert F.parse("$PLTR") == ("stock", "PLTR")
    assert F.parse("BRK.B") == ("stock", "BRK.B")
    assert F.parse("btc") == ("crypto", "BTC-USD") and F.parse("ריפל") == ("crypto", "XRP-USD")
    with pytest.raises(F.FollowError):
        F.parse("מה נשמע")


def event(prices):
    return [{"title": "Prime Minister of Israel after the next election?", "slug": "pm",
             "markets": [{"id": i, "groupItemTitle": n, "outcomes": '["Yes", "No"]', "outcomePrices": f'["{p}", "{1 - p}"]', "closed": False}
                         for i, (n, p) in enumerate(prices.items())] + [{"id": 99, "groupItemTitle": "old", "closed": True}]}]


def test_snapshot_reads_yes_prices_sorted():
    s = F.snapshot("polymarket", "pm", gamma=lambda path, params: event({"Bennett": 0.13, "Eizenkot": 0.50}))
    assert [x["name"] for x in s["items"]] == ["Eizenkot", "Bennett"] and s["items"][0]["price"] == 0.5
    assert s["url"] == "https://polymarket.com/event/pm" and "Prime Minister" in s["label"]
    st = F.snapshot("stock", "AMD", stock_fetch=lambda sym: ([{"c": 150.0}], {"regularMarketPrice": 151.0, "longName": "AMD Inc"}))
    assert st["items"][0]["price"] == 151.0 and "AMD Inc" in st["label"]


@pytest.mark.skipif(not DB, reason="PMINTEL_TEST_DB not set")
def test_follow_alerts_once_per_move_and_survives_errors():
    from pmintel import db
    conn = db.connect(DB)
    db.init_schema(conn)
    conn.execute("DELETE FROM follows")
    now = datetime(2026, 10, 20, 12, tzinfo=timezone.utc)
    prices = {"Eizenkot": 0.50, "Netanyahu": 0.28}
    g = lambda path, params: event(prices)
    f = F.add(conn, "https://polymarket.com/event/pm", now=now, gamma=g)
    assert F.add(conn, "polymarket.com/event/pm", now=now, gamma=g)["id"] == f["id"]  # no duplicates
    assert F.check(conn, now + timedelta(minutes=5), gamma=g) == []      # not due yet
    prices["Eizenkot"] = 0.53
    assert F.check(conn, now + timedelta(minutes=15), gamma=g) == []     # 3 points: below the 5-point bar
    prices["Eizenkot"] = 0.56
    msgs = F.check(conn, now + timedelta(minutes=30), gamma=g)
    assert len(msgs) == 1 and "Eizenkot: מ-50% ל-56% (עלה 6 נקודות)" in msgs[0] and "Netanyahu" not in msgs[0]
    assert F.check(conn, now + timedelta(minutes=45), gamma=g) == []     # measured from the last alert now
    def boom(path, params):
        raise RuntimeError("gamma down")
    assert F.check(conn, now + timedelta(minutes=60), gamma=boom) == []
    row = F.listing(conn)[0]
    assert row["last_error"] == "gamma down" and abs(row["state"][0]["since_start"] - 0.06) < 1e-9
    assert F.remove(conn, f["id"]) and F.listing(conn) == []
    conn.close()
