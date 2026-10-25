"""Polymarket execution check: does the historical "24 h before close" result survive real prices?

The historical study (research.py) uses mid / last-trade prices, which nobody can actually buy at, and it
anchors on the actual close time, which nobody knows in advance. This forward check removes both:

  * every collector cycle (15 min), open markets whose SCHEDULED end is 23-25 h away are looked at once;
  * the market price p (the same kind of price the study used) decides which pre-declared rules apply
    (research.STRATEGIES: e.g. "buy NO when YES is under 5 cents");
  * for each rule that applies, the live order book of the token we would buy is fetched, and the paper
    entry is the average price of buying $STAKE walking the asks (VWAP). A book too thin to fill is
    recorded as unfillable — that is evidence too;
  * the row is sealed (SHA-256 of its content) before the outcome exists, and settled when the market
    resolves: return per $1 = Polymarket's taker fee taken in shares, payoff 1 or 0.

Each settled row also carries the return the historical method would have assigned to the same trade
(mid + slippage), so the gap between study and reality is measured directly.

A rule counts as execution-validated only with >= MIN_EVENTS settled, fillable events whose event-level
95% lower bound of the real return is above zero. Paper only: nothing here can place an order.
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from psycopg.rows import tuple_row

from . import config, db
from .orderbook import summarize
from .research import STRATEGIES, trade_return

log = logging.getLogger("pmintel.pm_paper")

HORIZON_H = 24
WINDOW_H = (23.0, 25.0)   # scheduled end this far ahead -> eligible (each market is recorded once)
STAKE = 100.0             # paper notional per trade, USD
MAX_BOOKS = 80            # order-book requests per cycle (one cycle every 15 min)
MIN_EVENTS = 30
VOID_AFTER = timedelta(days=45)  # closed but never resolved -> void, excluded


def rule_key(side: str, lo: float, hi: float) -> str:
    return f"{side} {lo:.2f}-{hi:.2f}"  # same key as walkforward._key


def rules_for(p: float) -> list[tuple[str, float, float]]:
    return [r for r in STRATEGIES if r[1] <= p < r[2]]


def chosen_rules(conn) -> set[str]:
    """Rules the latest historical walk-forward picked at 24 h (pre-registered: sealed with each row)."""
    r = conn.execute("""SELECT result FROM research_reports WHERE kind = 'historical_v1'
                        ORDER BY id DESC LIMIT 1""").fetchone()
    out: set[str] = set()
    for key in ("24", "24_scheduled"):
        wf = ((r[0] or {}).get(key) or {}).get("walk_forward") or {} if r else {}
        out |= set((wf.get("oos_trading") or {}).get("rules_chosen") or [])
    return out


def candidates(conn, now: datetime) -> list[dict]:
    rows = conn.execute(
        """SELECT id, event_id, category, question, ref_price, ref_index, ref_outcome, token_ids, end_date
           FROM markets
           WHERE active AND NOT closed AND end_date BETWEEN %s AND %s AND ref_price IS NOT NULL
             AND ref_index IS NOT NULL AND jsonb_array_length(token_ids) = 2 AND volume >= %s
             AND NOT EXISTS (SELECT 1 FROM pm_paper p WHERE p.market_id = markets.id)
           ORDER BY volume DESC NULLS LAST""",
        (now + timedelta(hours=WINDOW_H[0]), now + timedelta(hours=WINDOW_H[1]), config.BACKFILL_MIN_VOLUME)).fetchall()
    cols = ("id", "event_id", "category", "question", "p", "ref_index", "ref_outcome", "token_ids", "end_date")
    return [dict(zip(cols, r)) for r in rows]


def seal(row: dict) -> str:
    body = {k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in sorted(row.items()) if k != "levels"}
    return hashlib.sha256(json.dumps(body, sort_keys=True, default=str).encode()).hexdigest()


def entry_from_book(book: dict, stake: float = STAKE) -> dict:
    """What buying `stake` of this token would have cost right now."""
    s = summarize(book) or {}
    asks = (s.get("levels") or {}).get("asks") or []
    from .orderbook import vwap_for_notional
    vwap = vwap_for_notional(asks, stake) if asks else None
    return {"best_ask": s.get("best_ask"), "best_bid": s.get("best_bid"), "vwap": vwap,
            "ask_depth_5c": s.get("ask_depth_5c"), "fillable": vwap is not None, "book_ts": s.get("book_ts")}


def record(conn, clob, now: datetime | None = None, max_books: int = MAX_BOOKS) -> int:
    """Seal paper entries for markets entering the 24 h window. Returns rows written."""
    now = now or datetime.now(timezone.utc)
    chosen = chosen_rules(conn)
    books, written = 0, 0
    for m in candidates(conn, now):
        rules = rules_for(float(m["p"]))
        if not rules:
            # still mark the market as seen so it isn't re-examined every cycle
            _insert(conn, {"market_id": m["id"], "event_id": m["event_id"], "category": m["category"],
                           "question": (m["question"] or "")[:300], "rule": "none", "side": None, "token_id": None,
                           "recorded_at": now, "end_date": m["end_date"], "p": float(m["p"]), "chosen": False,
                           "best_ask": None, "best_bid": None, "vwap": None, "fillable": False, "status": "skipped"})
            continue
        if books + len(rules) > max_books:
            break  # the rest waits for the next cycle (all rules of a market are recorded together)
        tokens = m["token_ids"]
        for side, lo, hi in rules:
            idx = m["ref_index"] if side == "YES" else 1 - m["ref_index"]
            token = tokens[idx]
            try:
                e = entry_from_book(clob.book(token))
            except Exception as ex:  # noqa: BLE001 - one bad book never stops the cycle
                log.warning("book failed for %s: %s", m["id"], ex)
                continue
            finally:
                books += 1
            row = {"market_id": m["id"], "event_id": m["event_id"], "category": m["category"],
                   "question": (m["question"] or "")[:300], "rule": rule_key(side, lo, hi), "side": side,
                   "token_id": token, "recorded_at": now, "end_date": m["end_date"], "p": float(m["p"]),
                   "chosen": rule_key(side, lo, hi) in chosen, "best_ask": e["best_ask"], "best_bid": e["best_bid"],
                   "vwap": e["vwap"], "fillable": e["fillable"], "status": "open" if e["fillable"] else "unfillable"}
            written += _insert(conn, row)
    return written


def _insert(conn, row: dict) -> int:
    row = {**row, "sha256": seal(row)}
    cols = list(row)
    cur = conn.execute(f"INSERT INTO pm_paper ({', '.join(cols)}) VALUES ({', '.join(['%s'] * len(cols))}) "
                       "ON CONFLICT (market_id, rule) DO NOTHING", [row[c] for c in cols])
    return cur.rowcount or 0


def settle(conn, now: datetime | None = None) -> int:
    """Close paper rows whose market resolved. Unfillable rows are settled too (hist return only)."""
    now = now or datetime.now(timezone.utc)
    rows = conn.execute(
        """SELECT p.id, p.side, p.p, p.vwap, p.fillable, p.category, m.resolved_outcome, m.ref_outcome, m.closed,
                  m.closed_time
           FROM pm_paper p JOIN markets m ON m.id = p.market_id
           WHERE p.status IN ('open', 'unfillable') AND m.closed""").fetchall()
    n = 0
    for pid, side, p, vwap, fillable, cat, res, ref, closed, closed_time in rows:
        if res is None:
            if closed_time and now - closed_time > VOID_AFTER:
                conn.execute("UPDATE pm_paper SET status = 'void', settled_at = %s WHERE id = %s", (now, pid))
            continue
        won = (res == ref) if side == "YES" else (res != ref)
        price_hist = p if side == "YES" else 1 - p
        ret_hist = trade_return(won, price_hist, config.RESEARCH_SLIPPAGE, config.RESEARCH_FEE, cat)
        ret_real = trade_return(won, vwap, 0.0, 0.0, cat) if fillable and vwap else None
        conn.execute("""UPDATE pm_paper SET status = 'settled', won = %s, ret_real = %s, ret_hist = %s,
                        resolved_outcome = %s, settled_at = %s, was_fillable = %s WHERE id = %s""",
                     (won, ret_real, ret_hist, res, now, fillable, pid))
        n += 1
    return n


def _event_stats(by_event: dict[str, list[float]]) -> tuple[float | None, list[float] | None, int]:
    ev = [sum(v) / len(v) for v in by_event.values() if v]
    k = len(ev)
    if k == 0:
        return None, None, 0
    m = sum(ev) / k
    if k < 2:
        return m, None, k
    se = math.sqrt(sum((x - m) ** 2 for x in ev) / (k - 1)) / math.sqrt(k)
    return m, [m - 1.96 * se, m + 1.96 * se], k


def _rows(conn, sql: str, params=()):
    """Plain tuples whatever the connection's row factory (the API pool uses dict rows)."""
    with conn.cursor(row_factory=tuple_row) as cur:
        return cur.execute(sql, params).fetchall()


def summary(conn) -> dict:
    rows = _rows(conn, """SELECT rule, event_id, market_id, status, fillable, was_fillable, ret_real, ret_hist, chosen,
                                  best_ask, p, side
                           FROM pm_paper WHERE rule <> 'none'""")
    by: dict[str, dict] = defaultdict(lambda: {"recorded": 0, "fillable": 0, "settled": 0, "open": 0, "chosen": False,
                                               "real": defaultdict(list), "hist": defaultdict(list), "spread_cost": []})
    for rule, ev, mid, st, fill, was_fill, rr, rh, chosen, ask, p, side in rows:
        b = by[rule]
        b["recorded"] += 1
        b["chosen"] = b["chosen"] or bool(chosen)
        fill_now = was_fill if st == "settled" else fill
        b["fillable"] += bool(fill_now)
        b["open"] += st in ("open", "unfillable")
        if ask is not None and p is not None:
            b["spread_cost"].append(ask - (p if side == "YES" else 1 - p))  # ask above the study's price
        if st == "settled":
            b["settled"] += 1
            key = ev or mid
            if rr is not None:
                b["real"][key].append(rr)
                b["hist"][key].append(rh)
    out = {}
    for rule, b in sorted(by.items()):
        mr, cr, kr = _event_stats(b["real"])
        mh, ch, _ = _event_stats(b["hist"])
        validated = bool(kr >= MIN_EVENTS and cr and cr[0] > 0)
        out[rule] = {"recorded": b["recorded"], "fill_rate": b["fillable"] / b["recorded"] if b["recorded"] else None,
                     "open": b["open"], "settled": b["settled"], "events": kr, "chosen": b["chosen"],
                     "mean_real": mr, "ci_real": cr, "mean_hist_same_trades": mh, "ci_hist": ch,
                     "gap": (mr - mh) if mr is not None and mh is not None else None,
                     "avg_ask_above_mid": sum(b["spread_cost"]) / len(b["spread_cost"]) if b["spread_cost"] else None,
                     "validated": validated,
                     "status": "validated" if validated else ("collecting" if kr < MIN_EVENTS else "failed")}
    last = _rows(conn, "SELECT max(recorded_at), count(*) FROM pm_paper")[0]
    return {"rules": out, "last_recorded": last[0], "markets_seen": last[1], "min_events": MIN_EVENTS,
            "stake_usd": STAKE, "window_h": list(WINDOW_H)}


def apply_execution_check(wf: dict, fwd: dict) -> dict:
    """Upgrade a walk-forward verdict to 'supported' only if a rule it chose is validated at real asks."""
    rules = set((wf.get("oos_trading") or {}).get("rules_chosen") or [])
    ok = any(fwd["rules"].get(r, {}).get("validated") for r in rules)
    gates = wf.setdefault("gates", {})
    gates["execution_validated"] = ok
    wf["execution_validated"] = ok
    wf["execution_check"] = {r: fwd["rules"].get(r) for r in sorted(rules)}
    if ok and wf.get("verdict") == "oos_positive_needs_execution_check":
        wf["verdict"], wf["edge_supported"] = "supported", True
    return wf


def run_once(conn, clob, now: datetime | None = None) -> dict:
    with db.job_run(conn, "pm_paper") as st:
        w = record(conn, clob, now)
        s = settle(conn, now)
        st["items"] = w + s
    return {"recorded": w, "settled": s}
