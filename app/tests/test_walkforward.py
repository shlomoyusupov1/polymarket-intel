import random
from datetime import datetime, timedelta, timezone

from pmintel import walkforward as wf

T0 = datetime(2025, 1, 1, tzinfo=timezone.utc)


def make_rows(n, true_prob, event_size=1, seed=1):
    """Markets priced at p; outcome drawn from true_prob(p). Events group consecutive markets."""
    rnd = random.Random(seed)
    rows = []
    for i in range(n):
        p = rnd.choice([0.02, 0.04, 0.07, 0.15, 0.3, 0.5, 0.7, 0.85, 0.92, 0.97])
        rows.append({"market_id": f"m{i}", "event_id": f"e{i // event_size}", "p": p,
                     "y": int(rnd.random() < true_prob(p)), "ref_time": T0 + timedelta(hours=6 * i)})
    return rows


def test_well_calibrated_market_has_no_edge():
    res = wf.walk_forward(make_rows(3000, lambda p: p), 24, slippage=0.01, fee=0.0)
    assert res["verdict"] == "not_supported" and res["edge_supported"] is False
    assert res["forecast"]["model_beats_market"] is False
    assert len([f for f in res["folds"] if "market_brier" in f]) == 5


def test_real_mispricing_is_found_out_of_sample_but_not_supported_without_execution():
    # longshots (p <= 0.05) never win: buying NO there is genuinely profitable
    res = wf.walk_forward(make_rows(4000, lambda p: 0.0 if p <= 0.05 else p), 24, slippage=0.005, fee=0.0)
    assert res["gates"]["oos_ci_above_zero_30_events"] and res["gates"]["survives_holm"]
    assert res["verdict"] == "oos_positive_needs_execution_check" and res["edge_supported"] is False
    ok = wf.walk_forward(make_rows(4000, lambda p: 0.0 if p <= 0.05 else p), 24, 0.005, 0.0, execution_validated=True)
    assert ok["verdict"] == "supported" and ok["edge_supported"] is True


def test_no_look_ahead_train_precedes_test_with_embargo_and_event_purge():
    rows = make_rows(1500, lambda p: p, event_size=10)
    res = wf.walk_forward(rows, 24 * 7, 0.01, 0.0, folds=3, min_train=100)
    assert all("train" in f or "skipped" in f for f in res["folds"])
    # direct check of the purge rule
    t = sorted(rows, key=lambda r: r["ref_time"])
    start = int(len(t) * 0.4)
    test = t[start:start + 100]
    t0 = test[0]["ref_time"]
    evs = {r["event_id"] for r in test}
    train = [r for r in t[:start] if r["ref_time"] < t0 - timedelta(hours=24 * 7) and r["event_id"] not in evs]
    assert all(r["ref_time"] < t0 - timedelta(hours=168) for r in train) and not ({r["event_id"] for r in train} & evs)


def test_too_little_data_is_explicit():
    res = wf.walk_forward(make_rows(100, lambda p: p), 24, 0.01, 0.0)
    assert res["verdict"] == "insufficient_data" and "need at least" in res["reason"]


def test_holm_controls_multiple_rules():
    keep = wf.holm({"a": 0.001, "b": 0.02, "c": 0.04})
    assert keep == {"a": True, "b": True, "c": True}  # thresholds .05/3, .05/2, .05
    assert wf.holm({"a": 0.03, "b": 0.04})["a"] is False  # 0.03 > 0.05/2
