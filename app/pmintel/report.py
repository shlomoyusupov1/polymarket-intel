"""First Real Research Report: market universe, data quality, structural inconsistencies, calibration.

Run on the server (prints Markdown, saves JSON to research_reports kind='research_report_v1'):
  docker compose run --rm --entrypoint python research -m pmintel.report > reports/research-$(date +%F).md

Structural findings are split into three levels and only the last one may ever be called an opportunity:
  1. mathematical inconsistency  — prices of a mutually exclusive set don't sum to ~1 (mid prices)
  2. theoretical opportunity      — buying every leg at the best ask costs < $1 per full set
  3. executable opportunity       — still positive after walking LIVE order books for every leg at a
                                    real size, with no fees, no catch-all "Other" leg, all legs accepting orders
"""
import json
import logging
from datetime import datetime, timezone

from . import config, db, research
from .sources import Clob

log = logging.getLogger("report")

SIZES = (10, 100)  # shares per leg for the executable check


def cost_for_shares(asks: list[tuple[float, float]], shares: float) -> float | None:
    """Dollar cost to buy `shares` walking asks (best first). None if the book is too thin."""
    remaining, cost = shares, 0.0
    for price, size in asks:
        take = min(remaining, size)
        cost += take * price
        remaining -= take
        if remaining <= 1e-9:
            return cost
    return None


def universe(conn) -> dict:
    q = conn.execute("""
        SELECT count(*),
               count(*) FILTER (WHERE closed),
               count(*) FILTER (WHERE active AND NOT closed),
               count(*) FILTER (WHERE active AND NOT closed AND accepting_orders),
               count(*) FILTER (WHERE active AND NOT closed AND accepting_orders AND enable_order_book),
               count(*) FILTER (WHERE active AND NOT closed AND accepting_orders AND enable_order_book
                                  AND liquidity >= %s AND best_ask IS NOT NULL),
               count(*) FILTER (WHERE archived),
               count(*) FILTER (WHERE active AND NOT closed AND neg_risk),
               count(*) FILTER (WHERE active AND NOT closed AND end_date < now()),
               count(*) FILTER (WHERE accepting_orders IS NULL)
        FROM markets""", (config.SNAPSHOT_MIN_LIQUIDITY,)).fetchone()
    keys = ["total_rows", "closed", "open_active", "open_accepting_orders", "open_orderbook_enabled",
            "tradable", "archived", "open_neg_risk", "open_past_end_date", "accepting_orders_unknown"]
    out = dict(zip(keys, q))
    out["tradable_definition"] = (f"active, not closed, accepting orders, order book enabled, "
                                  f"liquidity >= ${config.SNAPSHOT_MIN_LIQUIDITY:,.0f}, has a best ask")
    buckets = [(0, 1), (1, 100), (100, 1000), (1000, 10000), (10000, 100000), (100000, None)]
    out["open_by_liquidity"] = []
    out["open_by_volume_24h"] = []
    for col, dest in (("liquidity", "open_by_liquidity"), ("volume_24h", "open_by_volume_24h")):
        for lo, hi in buckets:
            n = conn.execute(
                f"SELECT count(*) FROM markets WHERE active AND NOT closed AND coalesce({col},0) >= %s"
                + (f" AND coalesce({col},0) < %s" if hi else ""),
                (lo, hi) if hi else (lo,)).fetchone()[0]
            out[dest].append({"from": lo, "to": hi, "markets": n})
    out["last_full_scan"] = conn.execute(
        "SELECT max(finished_at) FROM job_runs WHERE job = 'collect_full' AND status = 'ok'").fetchone()[0]
    return out


def data_quality(conn) -> dict:
    stale_min = config.COLLECT_INTERVAL_MIN * 3
    r = conn.execute(f"""
        WITH t AS (SELECT id FROM markets WHERE active AND NOT closed AND accepting_orders
                   AND enable_order_book AND liquidity >= %s AND best_ask IS NOT NULL)
        SELECT (SELECT count(*) FROM t),
               (SELECT count(*) FROM t JOIN markets m USING (id)
                 WHERE m.updated_at > now() - interval '{stale_min} minutes'),
               (SELECT count(DISTINCT market_id) FROM orderbooks WHERE ts > now() - interval '1 hour'),
               (SELECT max(ts) FROM orderbooks),
               (SELECT count(*) FROM orderbooks WHERE ts > now() - interval '24 hours'),
               (SELECT round(100.0 * avg(valid::int), 1) FROM orderbooks WHERE ts > now() - interval '24 hours'),
               (SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY spread) FROM orderbooks
                 WHERE ts > now() - interval '1 hour' AND valid),
               (SELECT percentile_cont(0.9) WITHIN GROUP (ORDER BY spread) FROM orderbooks
                 WHERE ts > now() - interval '1 hour' AND valid),
               (SELECT round(100.0 * avg((buy_vwap_1000 IS NOT NULL)::int), 1) FROM orderbooks
                 WHERE ts > now() - interval '1 hour')
        """, (config.SNAPSHOT_MIN_LIQUIDITY,)).fetchone()
    keys = ["tradable", "tradable_fresh_price", "books_markets_last_hour", "last_book_at", "books_last_24h",
            "book_valid_pct_24h", "spread_median_1h", "spread_p90_1h", "pct_books_with_1000_depth_1h"]
    out = dict(zip(keys, r))
    out["fresh_threshold_min"] = stale_min
    out["book_coverage_note"] = (f"books are collected for the top {config.ORDERBOOK_TOP_N} markets by 24h "
                                 f"volume per cycle, not for every tradable market")
    return out


def structural(conn, clob: Clob | None, max_live_checks: int = 10) -> dict:
    groups = conn.execute("""
        SELECT neg_risk_market_id, count(*) AS n,
               count(*) FILTER (WHERE best_ask IS NOT NULL) AS n_ask,
               sum(best_ask) AS sum_ask, sum(best_bid) AS sum_bid, sum(ref_price) AS sum_mid,
               bool_or(coalesce(neg_risk_other, false)) AS has_other,
               bool_or(coalesce(fees_enabled, false)) AS any_fees,
               bool_and(coalesce(accepting_orders, false)) AS all_accepting,
               min(liquidity) AS min_liq, min(event_slug) AS slug, min(question) AS sample_q,
               json_agg(json_build_object('id', id, 'token', token_ids->>0, 'q', question,
                                          'yes_first', ref_index = 0)) AS legs
        FROM markets
        WHERE neg_risk AND active AND NOT closed AND neg_risk_market_id IS NOT NULL
          AND (end_date IS NULL OR end_date > now())
        GROUP BY 1 HAVING count(*) >= 2""").fetchall()
    out = {"groups_checked": len(groups), "groups_complete_quotes": 0, "mathematical": [],
           "theoretical": [], "executable": [], "live_checked": 0, "notes": []}
    candidates = []
    for (gid, n, n_ask, sum_ask, sum_bid, sum_mid, has_other, any_fees, all_acc,
         min_liq, slug, sample_q, legs) in groups:
        if n_ask != n or not all(l["yes_first"] for l in legs):
            continue
        out["groups_complete_quotes"] += 1
        base = {"group": gid, "legs": n, "event": slug, "sample": sample_q,
                "sum_mid": round(sum_mid, 4) if sum_mid is not None else None,
                "sum_ask": round(sum_ask, 4), "sum_bid": round(sum_bid, 4) if sum_bid else None,
                "has_other_leg": has_other, "fees_enabled": any_fees, "all_accepting": all_acc,
                "min_leg_liquidity": min_liq}
        if sum_mid is not None and abs(sum_mid - 1) > 0.02:
            out["mathematical"].append(base)
        if sum_ask < 1:
            base["gross_edge_per_set"] = round(1 - sum_ask, 4)
            out["theoretical"].append(base)
            candidates.append((1 - sum_ask, base, legs))
    out["mathematical"].sort(key=lambda g: -abs(g["sum_mid"] - 1))
    out["mathematical"] = out["mathematical"][:25]
    out["theoretical"].sort(key=lambda g: -g["gross_edge_per_set"])

    for _, base, legs in sorted(candidates, key=lambda c: -c[0])[:max_live_checks]:
        if clob is None:
            break
        out["live_checked"] += 1
        res = {**base, "sizes": {}, "blockers": []}
        if base["has_other_leg"]:
            res["blockers"].append("catch-all 'Other' leg: set is not exhaustive, new outcomes can be added")
        if base["fees_enabled"]:
            res["blockers"].append("fees enabled on a leg: fee model not implemented")
        if not base["all_accepting"]:
            res["blockers"].append("a leg is not accepting orders")
        books = []
        for leg in legs:
            try:
                b = clob.book(leg["token"])
                asks = sorted(((float(a["price"]), float(a["size"])) for a in b.get("asks") or []),
                              key=lambda x: x[0])
            except Exception as e:  # noqa: BLE001
                res["blockers"].append(f"book fetch failed for {leg['id']}: {type(e).__name__}")
                asks = []
            books.append(asks)
        for size in SIZES:
            costs = [cost_for_shares(a, size) for a in books]
            if any(c is None for c in costs):
                res["sizes"][size] = {"fillable": False}
                continue
            total = sum(costs)
            res["sizes"][size] = {"fillable": True, "cost": round(total, 4),
                                  "payout": size, "net": round(size - total, 4)}
        res["live_checked_at"] = datetime.now(timezone.utc).isoformat()
        if not res["blockers"] and any(v.get("fillable") and v["net"] > 0 for v in res["sizes"].values()):
            out["executable"].append(res)
        else:
            out.setdefault("rejected_after_live_check", []).append(res)
    out["notes"] = [
        "Gamma bestAsk/bestBid are top-of-book only; live books are walked only for the top candidates.",
        "Partial-fill risk: legs are separate orders; a set is only hedged once every leg fills.",
        "Resolution risk: read each event's rules; disputes or 'Other' legs break the sum-to-1 assumption.",
        "Not checked: gas/withdrawal costs, settlement delay (capital locked until resolution).",
    ]
    return out


def calibration_snapshot(conn) -> dict:
    out = {}
    for h in research.HORIZONS_H:
        rows = research.load_rows(conn, h)
        c = research.calibration(rows)
        out[str(h)] = {"n": c["n"], "brier": c["brier"],
                       "events": len({r["event_id"] or r["market_id"] for r in rows})}
    out["resolved_markets"] = conn.execute(
        "SELECT count(*) FROM markets WHERE closed AND resolved_outcome IS NOT NULL").fetchone()[0]
    out["tokens_with_history"] = conn.execute(
        "SELECT count(DISTINCT token_id) FROM price_history").fetchone()[0]
    return out


def run(conn, clob: Clob | None) -> dict:
    return {"generated_at": datetime.now(timezone.utc).isoformat(),
            "universe": universe(conn), "data_quality": data_quality(conn),
            "structural": structural(conn, clob), "calibration": calibration_snapshot(conn)}


def _pct(a, b):
    return f"{100 * a / b:.1f}%" if b else "—"


def to_markdown(r: dict) -> str:
    u, dq, s, c = r["universe"], r["data_quality"], r["structural"], r["calibration"]
    L = [f"# Research report — {r['generated_at'][:16]} UTC", "",
         "## 1. Market universe", "",
         f"Last full scan: {u['last_full_scan']}", "",
         "| Set | Markets | Share of rows |", "|---|---:|---:|"]
    for k in ("total_rows", "closed", "open_active", "open_accepting_orders", "open_orderbook_enabled",
              "tradable", "open_neg_risk", "open_past_end_date", "archived", "accepting_orders_unknown"):
        L.append(f"| {k} | {u[k]:,} | {_pct(u[k], u['total_rows'])} |")
    L += ["", f"*tradable* = {u['tradable_definition']}.", "",
          "| Liquidity (open) | Markets |", "|---|---:|"]
    L += [f"| ${b['from']:,}–{('$' + format(b['to'], ',')) if b['to'] else '∞'} | {b['markets']:,} |"
          for b in u["open_by_liquidity"]]
    L += ["", "| 24h volume (open) | Markets |", "|---|---:|"]
    L += [f"| ${b['from']:,}–{('$' + format(b['to'], ',')) if b['to'] else '∞'} | {b['markets']:,} |"
          for b in u["open_by_volume_24h"]]
    L += ["", "## 2. Data quality", "",
          f"- Tradable markets with a price fresher than {dq['fresh_threshold_min']} min: "
          f"{dq['tradable_fresh_price']:,} / {dq['tradable']:,} ({_pct(dq['tradable_fresh_price'], dq['tradable'])})",
          f"- Order books: {dq['books_markets_last_hour']} markets in the last hour; last book at {dq['last_book_at']}; "
          f"{dq['books_last_24h']:,} books in 24h, {dq['book_valid_pct_24h']}% valid",
          f"- Spread (valid books, last hour): median {dq['spread_median_1h']}, p90 {dq['spread_p90_1h']}",
          f"- Books with enough depth to fill $1,000: {dq['pct_books_with_1000_depth_1h']}%",
          f"- Coverage note: {dq['book_coverage_note']}", "",
          "## 3. Structural inconsistencies (negRisk sets)", "",
          f"- Sets checked: {s['groups_checked']:,}; with a complete top-of-book quote on every leg: "
          f"{s['groups_complete_quotes']:,}",
          f"- Mathematical inconsistencies (|Σ mid − 1| > 2%): {len(s['mathematical'])} shown (top 25)",
          f"- Theoretical (Σ best ask < $1): **{len(s['theoretical'])}**",
          f"- Live-checked top candidates: {s['live_checked']}; **executable after depth & blockers: "
          f"{len(s['executable'])}**", ""]
    if s["theoretical"]:
        L += ["| Event | Legs | Σ ask | Gross edge/set | Other leg | Fees |", "|---|---:|---:|---:|---|---|"]
        L += [f"| {g['event']} | {g['legs']} | {g['sum_ask']} | {g['gross_edge_per_set']} | "
              f"{g['has_other_leg']} | {g['fees_enabled']} |" for g in s["theoretical"][:20]]
        L.append("")
    for res in s.get("rejected_after_live_check", []) + s["executable"]:
        sizes = "; ".join(f"{k} sh: " + (f"cost {v['cost']} net {v['net']}" if v.get("fillable") else "not fillable")
                          for k, v in res["sizes"].items())
        L.append(f"- `{res['event']}` — {sizes}. Blockers: {', '.join(res['blockers']) or 'none'}")
    L += ["", "Notes:"] + [f"- {n}" for n in s["notes"]]
    L += ["", "## 4. Calibration (resolved markets)", "",
          f"Resolved markets stored: {c['resolved_markets']:,}; tokens with price history: {c['tokens_with_history']:,}", "",
          "| Horizon | Markets | Events | Brier |", "|---|---:|---:|---:|"]
    for h in research.HORIZONS_H:
        x = c[str(h)]
        L.append(f"| {h} h | {x['n']:,} | {x['events']:,} | "
                 f"{round(x['brier'], 4) if x['brier'] is not None else '—'} |")
    L += ["", "No YES/NO recommendation is made from this report."]
    return "\n".join(L)


def main() -> None:
    logging.basicConfig(level=logging.WARNING)
    conn = db.connect()
    db.init_schema(conn)
    r = run(conn, Clob())
    conn.execute("INSERT INTO research_reports (kind, params, result) VALUES ('research_report_v1', '{}', %s)",
                 (json.dumps(r, default=str),))
    print(to_markdown(r))


if __name__ == "__main__":
    main()
