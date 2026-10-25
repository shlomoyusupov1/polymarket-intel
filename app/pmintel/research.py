"""Historical research: how well did Polymarket prices predict outcomes, and would simple
rule-based strategies have made money after costs?

Honesty rules baked in:
- Reference time = min(closed_time, end_date) to reduce leakage from markets that closed
  after the outcome was already public.
- Only prices observed at or before (reference - horizon) are used, and they must be fresh
  (within MAX_STALENESS of that cutoff).
- Strategy returns are aggregated per *event* first, so 30 correlated markets of one
  event don't count as 30 independent wins.
- History prices are not executable; a slippage haircut is applied to every entry.
"""
import json
import logging
import math
from collections import defaultdict
from datetime import timedelta

from . import config

log = logging.getLogger("research")

HORIZONS_H = [24, 24 * 7, 24 * 30]
MAX_STALENESS = timedelta(hours=48)
BUCKETS = [0, 0.05, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 0.95, 1.0000001]


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return (0.0, 1.0)
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (max(0.0, c - h), min(1.0, c + h))


def calibration(rows: list[dict]) -> dict:
    """rows: {p, y, event_id, category}. p = market-implied prob of ref outcome, y in {0,1}."""
    n = len(rows)
    if n == 0:
        return {"n": 0, "buckets": [], "brier": None, "log_loss": None}
    brier = sum((r["p"] - r["y"]) ** 2 for r in rows) / n
    eps = 1e-4
    ll = -sum(r["y"] * math.log(min(max(r["p"], eps), 1 - eps))
              + (1 - r["y"]) * math.log(1 - min(max(r["p"], eps), 1 - eps)) for r in rows) / n
    buckets = []
    for lo, hi in zip(BUCKETS, BUCKETS[1:]):
        b = [r for r in rows if lo <= r["p"] < hi]
        if not b:
            continue
        k = sum(r["y"] for r in b)
        ci = wilson(k, len(b))
        buckets.append({
            "lo": lo, "hi": min(hi, 1.0), "n": len(b),
            "events": len({r["event_id"] or r["market_id"] for r in b}),
            "mean_price": sum(r["p"] for r in b) / len(b),
            "freq": k / len(b), "ci_lo": ci[0], "ci_hi": ci[1],
        })
    return {"n": n, "brier": brier, "log_loss": ll, "buckets": buckets}


# Polymarket taker fee (docs.polymarket.com/trading/fees, in force for new markets since 2026-03-30):
# fee = shares x feeRate x p x (1 - p)  ->  as a share of the stake (shares x p): feeRate x (1 - p).
# Cheap contracts are expensive to buy: at 10 cents a 4% rate costs 3.6% of the stake.
TAKER_RATES = {"geopolit": 0.0, "world": 0.0, "crypto": 0.07, "sports": 0.05, "finance": 0.04, "politic": 0.04, "election": 0.04,
               "econom": 0.05, "culture": 0.05, "pop": 0.05, "weather": 0.05, "mention": 0.04,
               "tech": 0.04, "science": 0.04, "geopolit": 0.0, "world": 0.0}
DEFAULT_TAKER_RATE = 0.05  # "Other / General"


def taker_rate(category: str | None) -> float:
    c = (category or "").lower()
    for key, rate in TAKER_RATES.items():
        if key in c:
            return rate
    return DEFAULT_TAKER_RATE


def taker_fee_frac(price: float, category: str | None) -> float:
    """Taker fee as a fraction of the amount staked when buying at `price`."""
    return taker_rate(category) * (1 - min(max(price, 0.0), 1.0))


def trade_return(won: bool, price: float, slippage: float, fee: float, category: str | None, taker: bool = True) -> float:
    """Per $1 staked. The taker fee is taken in shares at the buy, so it shrinks the payoff of a winner
    and a loser still loses exactly the stake. Applied to every historical market too, even ones that
    predate the fee: conservative, and what the same trade would pay today. `fee` is an extra flat cost."""
    cost = min(price + slippage, 0.999)
    kept = 1 - (taker_fee_frac(cost, category) if taker else 0.0)
    return (kept if won else 0.0) / cost - 1 - fee


def strategy(rows: list[dict], side: str, lo: float, hi: float,
             slippage: float, fee: float, taker: bool = True) -> dict:
    """Buy `side` ('YES'=ref outcome, 'NO'=complement) on every market whose price is in [lo, hi).

    Return per $1 staked = payoff/cost - 1 - fee - taker fee, where cost includes slippage.
    """
    per_event: dict[str, list[float]] = defaultdict(list)
    wins = trades = 0
    for r in rows:
        if not (lo <= r["p"] < hi):
            continue
        price = r["p"] if side == "YES" else 1 - r["p"]
        won = r["y"] == 1 if side == "YES" else r["y"] == 0
        ret = trade_return(won, price, slippage, fee, r.get("category"), taker)
        per_event[r["event_id"] or r["market_id"]].append(ret)
        trades += 1
        wins += won
    ev = [sum(v) / len(v) for v in per_event.values()]
    m = len(ev)
    if m == 0:
        return {"side": side, "lo": lo, "hi": hi, "trades": 0, "events": 0}
    mean = sum(ev) / m
    sd = math.sqrt(sum((x - mean) ** 2 for x in ev) / (m - 1)) if m > 1 else None
    se = sd / math.sqrt(m) if sd is not None else None
    return {
        "side": side, "lo": lo, "hi": hi, "trades": trades, "events": m,
        "win_rate": wins / trades, "mean_return": mean, "stderr": se,
        "t_stat": mean / se if se else None,
        "ci_lo": mean - 1.96 * se if se else None, "ci_hi": mean + 1.96 * se if se else None,
        # in-sample only: never an edge claim by itself (see walkforward.py for the out-of-sample gate)
        "insample_ci_positive": bool(se and m >= 30 and mean - 1.96 * se > 0),
    }


STRATEGIES = [
    ("NO", 0.0, 0.05), ("NO", 0.05, 0.10), ("NO", 0.10, 0.20),
    ("YES", 0.0, 0.05), ("YES", 0.05, 0.10),
    ("YES", 0.80, 0.90), ("YES", 0.90, 0.95), ("YES", 0.95, 0.99),
]


def load_rows(conn, horizon_h: int, scheduled: bool = False) -> list[dict]:
    """scheduled=False: reference = min(actual close, scheduled end) — the original study.
    scheduled=True: reference = the scheduled end date only, which is all a trader knows in advance, and the
    market must still have been open at the cutoff. The actual close time is NOT knowable beforehand: a market
    that closes early usually closes because the event just happened, so anchoring on it picks the hours right
    before the news (look-ahead). This variant is the honest one and is what the forward check reproduces."""
    ref = "end_date" if scheduled else "LEAST(closed_time, COALESCE(end_date, closed_time))"
    extra = "AND end_date IS NOT NULL AND closed_time >= end_date - make_interval(hours => %s)" if scheduled else ""
    q = f"""
    WITH m AS (
      SELECT id, event_id, category, ref_outcome, resolved_outcome, volume,
             token_ids->>ref_index AS token_id,
             {ref} - make_interval(hours => %s) AS cutoff
      FROM markets
      WHERE closed AND resolved_outcome IS NOT NULL AND ref_index IS NOT NULL AND closed_time IS NOT NULL {extra}
    )
    SELECT m.id, m.event_id, m.category, (m.resolved_outcome = m.ref_outcome)::int AS y,
           m.cutoff, ph.ts, ph.price, m.cutoff + make_interval(hours => %s) AS ref_time
    FROM m
    JOIN LATERAL (SELECT ts, price FROM price_history
                  WHERE token_id = m.token_id AND ts <= m.cutoff
                  ORDER BY ts DESC LIMIT 1) ph ON TRUE
    """
    out = []
    params = (horizon_h, horizon_h, horizon_h) if scheduled else (horizon_h, horizon_h)
    for mid, event_id, cat, y, cutoff, ts, price, ref_time in conn.execute(q, params).fetchall():
        if cutoff - ts > MAX_STALENESS or price is None or not (0 <= price <= 1):
            continue
        out.append({"market_id": mid, "event_id": event_id, "category": cat, "y": y, "p": float(price),
                    "ref_time": ref_time})
    return out


def analyze(rows: list[dict], slippage: float, fee: float) -> dict:
    by_cat: dict[str, list] = defaultdict(list)
    for r in rows:
        by_cat[r["category"] or "unknown"].append(r)
    return {
        "calibration": calibration(rows),
        "strategies": [strategy(rows, s, lo, hi, slippage, fee) for s, lo, hi in STRATEGIES],
        "by_category": {c: {"n": len(v), "brier": calibration(v)["brier"]}
                        for c, v in sorted(by_cat.items(), key=lambda kv: -len(kv[1])) if len(v) >= 30},
    }


def data_status(conn) -> dict:
    """Is the historical sample complete? A partial/interrupted backfill is reported, not hidden."""
    last = conn.execute("""SELECT status, finished_at FROM job_runs WHERE job = 'backfill'
                           ORDER BY id DESC LIMIT 1""").fetchone()
    cov = conn.execute("""SELECT count(*) FILTER (WHERE resolved_outcome IS NOT NULL),
                                 count(*) FILTER (WHERE resolved_outcome IS NOT NULL AND EXISTS (
                                   SELECT 1 FROM price_history p WHERE p.token_id = markets.token_ids->>markets.ref_index))
                          FROM markets""").fetchone()
    return {"backfill_status": last[0] if last else "never_ran",
            "backfill_finished": str(last[1]) if last and last[1] else None,
            "resolved_markets": cov[0], "with_history": cov[1],
            "complete": bool(last and last[0] == "ok")}


def run(conn) -> dict:
    from . import db
    from .walkforward import walk_forward
    params = {"horizons_h": HORIZONS_H, "slippage": config.RESEARCH_SLIPPAGE, "fee": config.RESEARCH_FEE,
              "taker_fee": "feeRate x (1 - price) of stake, rate by category (Polymarket fee schedule 2026)",
              "max_staleness_h": MAX_STALENESS.total_seconds() / 3600}
    with db.job_run(conn, "research") as run_state:
        result = {}
        status = data_status(conn)
        for h in HORIZONS_H:
            rows = load_rows(conn, h)
            result[str(h)] = analyze(rows, config.RESEARCH_SLIPPAGE, config.RESEARCH_FEE)
            result[str(h)]["walk_forward"] = walk_forward(rows, h, config.RESEARCH_SLIPPAGE, config.RESEARCH_FEE)
        # the honest variant of the one horizon that came out positive (see load_rows)
        rows = load_rows(conn, 24, scheduled=True)
        result["24_scheduled"] = analyze(rows, config.RESEARCH_SLIPPAGE, config.RESEARCH_FEE)
        result["24_scheduled"]["walk_forward"] = walk_forward(rows, 24, config.RESEARCH_SLIPPAGE, config.RESEARCH_FEE)
        # a positive walk-forward is upgraded only by the forward check at real asks (pm_paper)
        try:
            from . import pm_paper
            fwd = pm_paper.summary(conn)
            for key in ("24", "24_scheduled"):
                pm_paper.apply_execution_check(result[key]["walk_forward"], fwd)
            params["execution_check"] = {k: {kk: v.get(kk) for kk in ("events", "mean_real", "ci_real", "validated")}
                                         for k, v in fwd["rules"].items()}
        except Exception:  # noqa: BLE001 - the forward table may not exist yet; never blocks the study
            log.exception("execution check unavailable")
        params["data_status"] = status
        conn.execute("INSERT INTO research_reports (kind, params, result) VALUES (%s, %s, %s)",
                     ("historical_v1", json.dumps(params, default=str), json.dumps(result, default=str)))
        run_state["items"] = sum(r["calibration"]["n"] for r in result.values())
    log.info("research report saved")
    return result


if __name__ == "__main__":
    from . import db
    logging.basicConfig(level=logging.INFO)
    c = db.connect()
    db.init_schema(c)
    run(c)
