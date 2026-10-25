import json
import os
from datetime import datetime, timedelta, timezone

import pytest

from pmintel import pm_paper as PP, research, stocklab as L
from pmintel.research import trade_return

DB = os.environ.get("PMINTEL_TEST_DB")
NOW = datetime(2026, 10, 26, 12, 0, tzinfo=timezone.utc)


class FakeClob:
    def __init__(self, books):
        self.books, self.calls = books, []

    def book(self, token):
        self.calls.append(token)
        return self.books.get(token, {})


def test_rules_and_keys_match_the_study():
    assert PP.rules_for(0.03) == [("NO", 0.0, 0.05), ("YES", 0.0, 0.05)]
    assert PP.rules_for(0.5) == []
    assert PP.rule_key("NO", 0.0, 0.05) == "NO 0.00-0.05"


def test_entry_walks_the_asks():
    e = PP.entry_from_book({"asks": [{"price": "0.97", "size": "50"}, {"price": "0.98", "size": "1000"}],
                            "bids": [{"price": "0.96", "size": "10"}]}, stake=100)
    assert e["fillable"] and e["best_ask"] == 0.97 and 0.97 < e["vwap"] < 0.98
    thin = PP.entry_from_book({"asks": [{"price": "0.04", "size": "10"}]}, stake=100)
    assert not thin["fillable"] and thin["vwap"] is None


def test_apply_execution_check_upgrades_only_a_validated_chosen_rule():
    wf = {"verdict": "oos_positive_needs_execution_check", "oos_trading": {"rules_chosen": ["NO 0.00-0.05"]}, "gates": {}}
    PP.apply_execution_check(wf, {"rules": {"NO 0.00-0.05": {"validated": False}, "YES 0.90-0.95": {"validated": True}}})
    assert wf["verdict"] == "oos_positive_needs_execution_check" and wf["gates"]["execution_validated"] is False
    PP.apply_execution_check(wf, {"rules": {"NO 0.00-0.05": {"validated": True}}})
    assert wf["verdict"] == "supported" and wf["edge_supported"] and wf["gates"]["execution_validated"]
    bad = {"verdict": "not_supported", "oos_trading": {"rules_chosen": ["NO 0.00-0.05"]}}
    PP.apply_execution_check(bad, {"rules": {"NO 0.00-0.05": {"validated": True}}})
    assert bad["verdict"] == "not_supported"  # a forward result never rescues a failed study


# ---------------------------------------------------------------- stock/crypto gate: net profit, not hit rate
def _s(**kw):
    base = {"n": 200, "edge_lo95": 0.01, "p_holm": 0.01, "mean_excess_lo95": 0.005, "success_rate": 0.44,
            "success_lo95": 0.40, "by_year": {"2020": {"n": 30, "edge_vs_baseline": 0.02},
                                              "2021": {"n": 30, "edge_vs_baseline": 0.03},
                                              "2022": {"n": 30, "edge_vs_baseline": -0.01}}}
    base.update(kw)
    return base


def test_low_hit_rate_with_net_profit_passes():
    assert L.verdict(_s()) == "candidate_for_paper"  # 44% wins, but proven net profit: the new gate


def test_gate_criteria():
    assert L.verdict(_s(n=40)) == "insufficient_data"
    assert L.verdict(_s(edge_lo95=-0.001)) == "not_supported"
    assert L.verdict(_s(p_holm=0.2)) == "not_supported"
    assert L.verdict(_s(mean_excess_lo95=-0.001)) == "edge_below_target"      # beats random, not the index
    assert L.verdict(_s(lo95_at_2x_cost=-0.001)) == "edge_below_target"       # dies at double cost
    one_lucky_year = {"2020": {"n": 30, "edge_vs_baseline": 0.2}, "2021": {"n": 30, "edge_vs_baseline": -0.01},
                      "2022": {"n": 30, "edge_vs_baseline": -0.01}}
    assert L.verdict(_s(by_year=one_lucky_year)) == "edge_below_target"
    # years with too few trades don't vote
    assert L.years_positive(_s(by_year={**one_lucky_year, "2023": {"n": 2, "edge_vs_baseline": 1.0}})) == pytest.approx(1 / 3)
    assert L.verdict(_s(success_rate=0.70, mean_excess_lo95=-0.01)) == "edge_below_target"  # 70% wins, losing money


# ---------------------------------------------------------------- against Postgres
def _market(conn, mid, p, end, vol=50000, event=None, closed=False, resolved=None, closed_time=None, cat="politics"):
    conn.execute("""INSERT INTO markets (id, event_id, question, category, outcomes, token_ids, ref_index, ref_outcome,
                                         ref_price, volume, active, closed, end_date, resolved_outcome, closed_time)
                    VALUES (%s, %s, %s, %s, '["Yes","No"]', %s, 0, 'Yes', %s, %s, true, %s, %s, %s, %s)""",
                 (mid, event or "e" + mid, f"Q {mid}?", cat, json.dumps([mid + "y", mid + "n"]), p, vol, closed, end,
                  resolved, closed_time))


@pytest.fixture()
def conn():
    from pmintel import db
    c = db.connect(DB)
    c.execute("DROP TABLE IF EXISTS markets, price_history, pm_paper, research_reports, job_runs CASCADE")
    db.init_schema(c)
    yield c
    c.close()


@pytest.mark.skipif(not DB, reason="PMINTEL_TEST_DB not set")
def test_record_seal_settle(conn):
    conn.execute("INSERT INTO research_reports (kind, params, result) VALUES ('historical_v1', '{}', %s)",
                 (json.dumps({"24": {"walk_forward": {"oos_trading": {"rules_chosen": ["NO 0.00-0.05"]}}}}),))
    _market(conn, "a", 0.03, NOW + timedelta(hours=24))       # longshot, in the window
    _market(conn, "b", 0.03, NOW + timedelta(hours=48))       # too far away
    _market(conn, "c", 0.50, NOW + timedelta(hours=24))       # no rule applies
    _market(conn, "d", 0.03, NOW + timedelta(hours=24), vol=500)  # too small for the study's universe
    clob = FakeClob({"an": {"asks": [{"price": "0.975", "size": "1000"}], "bids": [{"price": "0.96", "size": "100"}]},
                     "ay": {"asks": [{"price": "0.05", "size": "10"}]}})  # $0.50 of asks: unfillable
    assert PP.record(conn, clob, NOW) == 2
    assert sorted(clob.calls) == ["an", "ay"]
    rows = {r[0]: r[1:] for r in conn.execute("SELECT rule, status, fillable, vwap, chosen, sha256 FROM pm_paper WHERE market_id = 'a'")}
    assert rows["NO 0.00-0.05"][:4] == ("open", True, pytest.approx(0.975), True)
    assert rows["YES 0.00-0.05"][:3] == ("unfillable", False, None) and rows["YES 0.00-0.05"][3] is False
    assert all(len(r[4]) == 64 for r in rows.values())
    assert conn.execute("SELECT status FROM pm_paper WHERE market_id = 'c'").fetchone()[0] == "skipped"
    assert not conn.execute("SELECT 1 FROM pm_paper WHERE market_id IN ('b', 'd')").fetchone()
    # each market once: a later cycle looks at nothing new
    clob.calls.clear()
    assert PP.record(conn, clob, NOW + timedelta(minutes=15)) == 0 and clob.calls == []
    # resolution: NO won
    assert PP.settle(conn, NOW) == 0  # not closed yet
    conn.execute("UPDATE markets SET closed = true, resolved_outcome = 'No', closed_time = %s WHERE id = 'a'", (NOW,))
    assert PP.settle(conn, NOW + timedelta(days=1)) == 2
    no = conn.execute("SELECT won, ret_real, ret_hist FROM pm_paper WHERE rule = 'NO 0.00-0.05'").fetchone()
    assert no[0] is True and no[1] == pytest.approx(trade_return(True, 0.975, 0, 0, "politics"))
    assert no[2] == pytest.approx(trade_return(True, 0.97, 0.01, 0.0, "politics"))
    yes = conn.execute("SELECT won, ret_real FROM pm_paper WHERE rule = 'YES 0.00-0.05'").fetchone()
    assert yes == (False, None)
    s = PP.summary(conn)
    r = s["rules"]["NO 0.00-0.05"]
    assert r["events"] == 1 and r["chosen"] and not r["validated"] and r["status"] == "collecting"
    assert s["rules"]["YES 0.00-0.05"]["fill_rate"] == 0 and s["rules"]["YES 0.00-0.05"]["events"] == 0


@pytest.mark.skipif(not DB, reason="PMINTEL_TEST_DB not set")
def test_validated_needs_30_events_with_lower_bound_above_zero(conn):
    for i in range(30):
        conn.execute("""INSERT INTO pm_paper (market_id, event_id, rule, side, recorded_at, status, sha256, fillable,
                                              was_fillable, ret_real, ret_hist, p, best_ask)
                        VALUES (%s, %s, 'NO 0.00-0.05', 'NO', %s, 'settled', 'x', true, true, %s, 0.03, 0.03, 0.975)""",
                     (f"m{i}", f"e{i}", NOW, 0.02 if i % 10 else -0.05))
    r = PP.summary(conn)["rules"]["NO 0.00-0.05"]
    assert r["events"] == 30 and r["ci_real"][0] > 0 and r["validated"]
    assert r["gap"] == pytest.approx(r["mean_real"] - 0.03)


@pytest.mark.skipif(not DB, reason="PMINTEL_TEST_DB not set")
def test_unresolved_closed_market_is_voided(conn):
    _market(conn, "v", 0.03, NOW + timedelta(hours=24))
    PP.record(conn, FakeClob({"vn": {"asks": [{"price": "0.975", "size": "1000"}]}}), NOW)
    conn.execute("UPDATE markets SET closed = true, closed_time = %s WHERE id = 'v'", (NOW,))
    assert PP.settle(conn, NOW + timedelta(days=10)) == 0
    PP.settle(conn, NOW + timedelta(days=60))
    assert {r[0] for r in conn.execute("SELECT status FROM pm_paper WHERE market_id = 'v'")} == {"void"}


@pytest.mark.skipif(not DB, reason="PMINTEL_TEST_DB not set")
def test_scheduled_reference_drops_markets_that_closed_early(conn):
    end = datetime(2026, 9, 30, tzinfo=timezone.utc)
    # on time: closed at its scheduled end
    _market(conn, "ontime", 0.5, end, closed=True, resolved="Yes", closed_time=end)
    # closed 10 days early (the event happened): the actual-close study sees it, a trader at end-24h could not
    _market(conn, "early", 0.5, end, closed=True, resolved="Yes", closed_time=end - timedelta(days=10))
    for mid, at in (("ontime", end - timedelta(hours=30)), ("early", end - timedelta(days=10, hours=30))):
        conn.execute("INSERT INTO price_history (token_id, ts, market_id, price) VALUES (%s, %s, %s, 0.04)", (mid + "y", at, mid))
    actual = {r["market_id"] for r in research.load_rows(conn, 24)}
    sched = {r["market_id"] for r in research.load_rows(conn, 24, scheduled=True)}
    assert actual == {"ontime", "early"} and sched == {"ontime"}
