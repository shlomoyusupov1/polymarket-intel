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
