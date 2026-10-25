from pmintel import portfolio as PF


def sig(i, sym, rec, entry, exit_, ret, status="closed", strategy="s", last=None):
    return {"id": i, "strategy": strategy, "symbol": sym, "signal_day": entry, "recorded_at": rec, "sha256": "x",
            "lab_verdict": None, "hold": 5, "cost_rt": 0.002, "status": status, "entry_day": entry, "entry_price": 100.0,
            "exit_day": exit_ if status == "closed" else None, "ret": ret if status == "closed" else None,
            "spy_ret": 0.01 if status == "closed" else None, "excess": None, "last_price": last, "last_day": None}


def test_replay_respects_cash_slots_and_costs():
    rows = [sig(1, "AMD", "2026-01-01", "2026-01-02", "2026-01-09", 0.10),
            sig(2, "NVDA", "2026-01-01T01", "2026-01-02", "2026-01-09", -0.05),
            sig(3, "MSFT", "2026-01-01T02", "2026-01-02", "2026-01-09", 0.20),     # no free slot (max_open=2)
            sig(4, "AMD", "2026-01-05", "2026-01-06", "2026-01-13", 0.0),          # AMD already open
            sig(5, "TSLA", "2026-01-10", "2026-01-12", None, None, status="open", last=110.0)]
    r = PF.replay(rows, 100.0, 40.0, 2)
    d = r["decisions"]
    assert d[1][0] == d[2][0] == "taken" and d[3][0] == "skipped" and "2" in d[3][1] and d[4][0] == "skipped"
    assert d[5][0] == "taken" and r["closed"] == 2 and r["wins"] == 1
    amd = 40 * (1 - 0.001) * 1.10 * (1 - 0.001) - 40
    nvda = 40 * (1 - 0.001) * 0.95 * (1 - 0.001) - 40
    assert abs(r["realized"] - (amd + nvda)) < 1e-9
    pos = r["positions"][0]
    assert pos["symbol"] == "TSLA" and abs(pos["value"] - 40 * 0.999 / 100 * 110) < 1e-9
    assert abs(r["value"] - (r["cash"] + pos["value"])) < 1e-9 and r["exposure"] == {"stocks": pos["value"]}
    assert abs(r["bench_same_trades"] - 0.8) < 1e-9


def test_scope_filter_and_history_reasons():
    rows = [sig(1, "AMD", "2026-01-01", "2026-01-02", "2026-01-09", 0.1, strategy="good"),
            sig(2, "ETH-USD", "2026-01-01", "2026-01-02", "2026-01-09", 0.1, strategy="crypto:bad"),
            sig(3, "PLTR", "2026-01-20", None, None, None, status="pending")]
    r = PF.replay(rows, 100.0, 50.0, 3, allowed=lambda k: k == "good")
    assert r["closed"] == 1
    h = {x["id"]: x for x in PF.history(rows, r["decisions"], {"good": "שיטה טובה"})}
    assert h[1]["decision"] == "נכנס לתיק" and h[1]["strategy"] == "שיטה טובה"
    assert h[2]["decision"] == "לא בתיק" and h[3]["decision"] == "ממתין לכניסה"
    assert abs(h[1]["ret_after_cost"] - 0.098) < 1e-12
    assert PF.replay([], 100.0, 50.0, 2)["max_drawdown"] is None


def test_same_day_exit_cannot_fund_earlier_entry():
    # AMD exits at 16:00 New York; MSFT wants to open at 09:30 that day.
    old = sig(10, "AMD", "2026-01-01", "2026-01-02", "2026-01-05", 0.10)
    new = sig(11, "MSFT", "2026-01-04", "2026-01-05", None, None, status="open", last=102)
    result = PF.replay([old, new], 100.0, 80.0, 1)
    assert result["decisions"][10][0] == "taken"
    assert result["decisions"][11][0] == "skipped"
    assert result["closed"] == 1
    assert not result["positions"]


def test_warning_and_late_signal_never_become_long_position():
    warning = sig(1, "AMD", "2026-01-01", "2026-01-02", None, None,
                  status="open", strategy="candle_shooting_star", last=105.0)
    late = sig(2, "MSFT", "2026-01-02", "2026-01-03", None, None, status="open")
    late["sealed_ok"] = False
    result = PF.replay([warning, late], 100.0, 60.0, 2)
    assert not result["positions"]
    assert result["cash"] == 100.0
    assert all(result["decisions"][i][0] == "skipped" for i in (1, 2))


def test_actual_drawdown_unknown_without_intratrade_marks():
    # A portfolio with only final entry/exit marks cannot report true peak-to-trough drawdown.
    row = sig(1, "AMD", "2026-01-01", "2026-01-02", "2026-01-09", 0.2)
    result = PF.replay([row], 100.0, 50.0, 1)
    assert result["max_drawdown"] is None
    assert result["drawdown_note"]
    assert "closed_trade_drawdown" in result


def test_eligibility_is_pinned_to_the_signal_not_later_strategy_result():
    old = sig(1, "AMD", "2026-01-01", "2026-01-02", "2026-01-09", 0.2)
    old["strategy_state"] = "not_eligible"
    new = sig(2, "MSFT", "2026-01-11", "2026-01-12", None, None, status="open")
    new["strategy_state"] = "confirmed"
    result = PF.replay([old, new], 100.0, 10.0, 2,
                       eligible_at_signal=lambda r: r.get("strategy_state") == "confirmed")
    assert result["closed"] == 0
    assert result["decisions"][1][0] == "skipped"
    assert [p["symbol"] for p in result["positions"]] == ["MSFT"]


def test_marked_to_market_drawdown_sees_losses_inside_open_and_closed_trades():
    # bought at 100, fell to 70 during the hold, recovered to close +5%: closed-trade drawdown would be 0
    row = sig(1, "AMD", "2026-01-01", "2026-01-02", "2026-01-09", 0.05)
    row["cost_rt"] = 0.0
    marks = {1: {"2026-01-02": 100.0, "2026-01-05": 70.0, "2026-01-06": 90.0, "2026-01-09": 105.0}}
    r = PF.replay([row], 100.0, 50.0, 1, marks=marks)
    assert abs(r["max_drawdown"] - (85.0 / 100.0 - 1)) < 1e-9          # equity 100 -> 85 at the low
    assert r["marked_curve"][-1]["equity"] == 102.5 and "יומי" in r["drawdown_note"]
    still_open = sig(2, "MSFT", "2026-01-01", "2026-01-02", None, None, status="open", last=80.0)
    still_open["cost_rt"] = 0.0
    r2 = PF.replay([still_open], 100.0, 50.0, 1, marks={2: {"2026-01-02": 100.0, "2026-01-05": 80.0}})
    assert abs(r2["max_drawdown"] - (-0.10)) < 1e-9 and r2["positions"][0]["pnl"] == -10.0
