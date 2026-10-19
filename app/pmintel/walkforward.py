"""Walk-forward validation on resolved markets — no look-ahead.

For each fold (ordered by resolution time):
  train = markets resolved before (fold start - horizon), so every training outcome was already known
          when the test prices were observed; events that appear in the test fold are removed from train
  test  = markets resolving inside the fold

Two questions, answered only on test folds:
 1. Forecast quality: does a recalibration of the market price, fitted on train, beat the raw market price
    out of sample? (Brier score and log loss; difference averaged per event = cluster-robust.)
 2. Trading rules: the rule chosen on train (best lower confidence bound after costs) is evaluated on test.
    In-sample, all rules get a one-sided p-value with Holm correction for testing many rules at once.

An edge is "supported" only if: pooled out-of-sample event-level CI > 0 with >= 30 events, the rule also
survives Holm in-sample, it stays positive at double slippage, AND executability was validated on live
order books. Historical prices cannot validate execution, so historical data alone can reach at most
"oos_positive_needs_execution_check".
"""
from __future__ import annotations

import math
from collections import defaultdict
from datetime import timedelta

from .research import BUCKETS, STRATEGIES, strategy, trade_return

EPS = 1e-4
ALPHA = 20.0  # shrinkage of bucket frequencies toward the bucket's mean price


def _phi(x: float) -> float:
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def brier(p: float, y: int) -> float:
    return (p - y) ** 2


def logloss(p: float, y: int) -> float:
    p = min(max(p, EPS), 1 - EPS)
    return -(y * math.log(p) + (1 - y) * math.log(1 - p))


def fit_recalibration(train: list[dict]) -> list[tuple[float, float, float]]:
    """Binned recalibration with shrinkage: (lo, hi, calibrated probability)."""
    out = []
    for lo, hi in zip(BUCKETS, BUCKETS[1:]):
        b = [r for r in train if lo <= r["p"] < hi]
        mean_p = sum(r["p"] for r in b) / len(b) if b else (lo + min(hi, 1)) / 2
        k = sum(r["y"] for r in b)
        out.append((lo, hi, (k + ALPHA * mean_p) / (len(b) + ALPHA)))
    return out


def apply_recalibration(model, p: float) -> float:
    for lo, hi, q in model:
        if lo <= p < hi:
            return q
    return p


def event_mean_se(values_by_event: dict[str, list[float]]) -> tuple[float | None, float | None, int]:
    ev = [sum(v) / len(v) for v in values_by_event.values() if v]
    n = len(ev)
    if n == 0:
        return None, None, 0
    mean = sum(ev) / n
    if n < 2:
        return mean, None, n
    sd = math.sqrt(sum((x - mean) ** 2 for x in ev) / (n - 1))
    return mean, sd / math.sqrt(n), n


def holm(pvalues: dict[str, float], alpha: float = 0.05) -> dict[str, bool]:
    """Holm step-down: which hypotheses survive at family-wise error rate alpha."""
    order = sorted(pvalues.items(), key=lambda kv: kv[1])
    m, keep, stop = len(order), {}, False
    for i, (k, p) in enumerate(order):
        if stop or p > alpha / (m - i):
            stop = True
            keep[k] = False
        else:
            keep[k] = True
    return keep


def _key(s) -> str:
    return f"{s[0]} {s[1]:.2f}-{s[2]:.2f}"


def walk_forward(rows: list[dict], horizon_h: int, slippage: float, fee: float, folds: int = 5,
                 min_train: int = 200, execution_validated: bool = False) -> dict:
    """rows: {market_id, event_id, p, y, ref_time}. Returns per-fold and pooled out-of-sample results."""
    rows = sorted((r for r in rows if r.get("ref_time") is not None), key=lambda r: r["ref_time"])
    n = len(rows)
    embargo = timedelta(hours=horizon_h)
    res = {"n": n, "folds": [], "horizon_h": horizon_h, "slippage": slippage, "fee": fee,
           "execution_validated": execution_validated}
    if n < min_train + 50:
        res.update(verdict="insufficient_data", edge_supported=False,
                   reason=f"need at least {min_train + 50} resolved markets with clean prices, have {n}")
        return res
    start = int(n * 0.4)  # first 40% only ever trains
    bounds = [start + (n - start) * i // folds for i in range(folds + 1)]
    diff_brier: dict[str, list[float]] = defaultdict(list)
    diff_ll: dict[str, list[float]] = defaultdict(list)
    oos_ret: dict[str, list[float]] = defaultdict(list)
    oos_ret_2x: dict[str, list[float]] = defaultdict(list)
    tot = {"market_brier": 0.0, "model_brier": 0.0, "market_ll": 0.0, "model_ll": 0.0, "n": 0}
    for f in range(folds):
        test = rows[bounds[f]:bounds[f + 1]]
        if not test:
            continue
        t0 = test[0]["ref_time"]
        test_events = {r["event_id"] or r["market_id"] for r in test}
        train = [r for r in rows[:bounds[f]] if r["ref_time"] < t0 - embargo
                 and (r["event_id"] or r["market_id"]) not in test_events]
        if len(train) < min_train:
            res["folds"].append({"fold": f, "skipped": f"train {len(train)} < {min_train}"})
            continue
        model = fit_recalibration(train)
        fb = {"market_brier": 0.0, "model_brier": 0.0}
        for r in test:
            q = apply_recalibration(model, r["p"])
            e = r["event_id"] or r["market_id"]
            bm, bq, lm, lq = brier(r["p"], r["y"]), brier(q, r["y"]), logloss(r["p"], r["y"]), logloss(q, r["y"])
            diff_brier[e].append(bq - bm)
            diff_ll[e].append(lq - lm)
            tot["market_brier"] += bm; tot["model_brier"] += bq; tot["market_ll"] += lm; tot["model_ll"] += lq
            tot["n"] += 1
            fb["market_brier"] += bm; fb["model_brier"] += bq
        scored = []
        for s in STRATEGIES:
            st = strategy(train, s[0], s[1], s[2], slippage, fee)
            if st.get("events", 0) >= 30 and st.get("ci_lo") is not None:
                scored.append((st["ci_lo"], s))
        chosen = max(scored)[1] if scored else None
        fold = {"fold": f, "train": len(train), "test": len(test), "from": str(t0), "to": str(test[-1]["ref_time"]),
                "market_brier": fb["market_brier"] / len(test), "model_brier": fb["model_brier"] / len(test),
                "chosen_rule": _key(chosen) if chosen else None}
        if chosen:
            side, lo, hi = chosen
            for r in test:
                if lo <= r["p"] < hi:
                    price = r["p"] if side == "YES" else 1 - r["p"]
                    won = r["y"] == 1 if side == "YES" else r["y"] == 0
                    e = r["event_id"] or r["market_id"]
                    oos_ret[e].append(trade_return(won, price, slippage, fee, r.get("category")))
                    oos_ret_2x[e].append(trade_return(won, price, 2 * slippage, fee, r.get("category")))
            fold["oos"] = strategy(test, side, lo, hi, slippage, fee)
        res["folds"].append(fold)

    mb, sb, nb = event_mean_se(diff_brier)
    ml, sl, _ = event_mean_se(diff_ll)
    k = max(tot["n"], 1)
    res["forecast"] = {
        "n_test": tot["n"], "events": nb,
        "market_brier": tot["market_brier"] / k, "model_brier": tot["model_brier"] / k,
        "market_log_loss": tot["market_ll"] / k, "model_log_loss": tot["model_ll"] / k,
        "brier_diff_event_mean": mb, "brier_diff_ci": [mb - 1.96 * sb, mb + 1.96 * sb] if sb else None,
        "log_loss_diff_event_mean": ml, "log_loss_diff_ci": [ml - 1.96 * sl, ml + 1.96 * sl] if sl else None,
        "model_beats_market": bool(sb and mb + 1.96 * sb < 0),  # negative diff = model better
    }
    # in-sample multiple testing over all rules on everything up to the last fold's training data
    insample = [strategy(rows, s[0], s[1], s[2], slippage, fee) for s in STRATEGIES]
    pvals = {_key((x["side"], x["lo"], x["hi"])): (1 - _phi(x["t_stat"])) for x in insample if x.get("t_stat") is not None}
    surv = holm(pvals)
    res["insample"] = [{**{k2: x.get(k2) for k2 in ("side", "lo", "hi", "events", "mean_return", "ci_lo", "ci_hi")},
                        "p_one_sided": pvals.get(_key((x["side"], x["lo"], x["hi"]))),
                        "survives_holm": surv.get(_key((x["side"], x["lo"], x["hi"])), False)} for x in insample]
    om, ose, on = event_mean_se(oos_ret)
    om2, ose2, _ = event_mean_se(oos_ret_2x)
    chosen_rules = {fo.get("chosen_rule") for fo in res["folds"] if fo.get("chosen_rule")}
    oos_pos = bool(ose and on >= 30 and om - 1.96 * ose > 0)
    robust = bool(ose2 and om2 - 1.96 * ose2 > 0)
    holm_ok = any(surv.get(r, False) for r in chosen_rules)
    res["oos_trading"] = {"events": on, "mean_return": om, "ci": [om - 1.96 * ose, om + 1.96 * ose] if ose else None,
                          "mean_return_2x_slippage": om2, "rules_chosen": sorted(chosen_rules)}
    if oos_pos and robust and holm_ok and execution_validated:
        verdict = "supported"
    elif oos_pos and robust and holm_ok:
        verdict = "oos_positive_needs_execution_check"
    else:
        verdict = "not_supported"
    res.update(verdict=verdict, edge_supported=verdict == "supported",
               gates={"oos_ci_above_zero_30_events": oos_pos, "positive_at_2x_slippage": robust,
                      "survives_holm": holm_ok, "execution_validated": execution_validated})
    return res
