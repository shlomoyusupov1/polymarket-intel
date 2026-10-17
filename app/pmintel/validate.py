"""Production integration check against the REAL Gamma and CLOB APIs.

Mock tests prove our code; this proves our assumptions about Polymarket.
Every check records pass/fail, timing and samples; the report is saved to
research_reports (kind='integration') and printed as JSON.

  docker compose run --rm research python -m pmintel.validate
"""
import json
import logging
import sys
import time
from datetime import datetime, timedelta, timezone

from . import db
from .normalize import normalize_market
from .orderbook import summarize
from .sources import Clob, Gamma

log = logging.getLogger("validate")


def _check(report: list, name: str, fn):
    t0 = time.monotonic()
    entry = {"check": name, "at": datetime.now(timezone.utc).isoformat()}
    try:
        ok, detail = fn()
        entry.update(ok=bool(ok), detail=detail)
    except Exception as e:  # noqa: BLE001 - the whole point is to record failures
        entry.update(ok=False, error=f"{type(e).__name__}: {e}"[:500])
    entry["ms"] = round((time.monotonic() - t0) * 1000)
    report.append(entry)
    log.info("%s %s", "PASS" if entry["ok"] else "FAIL", name)
    return entry


def run(gamma: Gamma, clob: Clob, data_api=None) -> dict:
    from .dataapi import DataApi
    data_api = data_api or DataApi()
    report: list[dict] = []
    state: dict = {}

    def open_page():
        page = gamma.api.get("/markets", {"active": "true", "closed": "false", "limit": 500, "offset": 0})
        state["open_page"] = page
        recs = [normalize_market(m) for m in page]
        with_price = sum(r["ref_price"] is not None for r in recs)
        with_tokens = sum(len(r["token_ids"]) == len(r["outcomes"]) > 0 for r in recs)
        return len(page) > 0, {"page_size_returned": len(page), "with_ref_price": with_price,
                               "tokens_match_outcomes": with_tokens,
                               "sample": {k: recs[0][k] for k in ("id", "question", "outcomes", "ref_price",
                                                                    "event_slug", "url")} if recs else None}

    def full_scan():
        ids, n = set(), 0
        for m in gamma.iter_markets(active="true", closed="false"):
            n += 1
            ids.add(str(m.get("id")))
        return n > 0, {"rows": n, "unique_ids": len(ids), "duplicates": n - len(ids)}

    def deep_offset():
        # Documents the offset cap we discovered; we don't depend on offset paging any more.
        import httpx
        try:
            page = gamma.api.get("/markets", {"closed": "true", "limit": 100, "offset": 20000})
            return True, {"offset_20000_rows": len(page), "offset_cap": False}
        except httpx.HTTPStatusError as e:
            return e.response.status_code == 422, {"offset_cap": True, "status": e.response.status_code,
                                                    "note": "expected; discovery uses /markets/keyset"}

    def closed_history():
        since = (datetime.now(timezone.utc) - timedelta(days=60)).date().isoformat()
        page = gamma.api.get("/markets", {"closed": "true", "end_date_min": since, "limit": 100,
                                          "order": "volumeNum", "ascending": "false"})
        recs = [r for r in (normalize_market(m) for m in page) if r["resolved_outcome"] and r["token_ids"]]
        state["resolved_recs"] = recs
        tested, with_hist, samples = 0, 0, []
        for r in recs[:10]:
            pts = clob.price_history(r["token_ids"][r["ref_index"]], 360)
            tested += 1
            with_hist += bool(pts)
            samples.append({"id": r["id"], "points": len(pts),
                            "first": pts[0][0].isoformat() if pts else None})
        return with_hist > 0, {"resolved_in_page": len(recs), "tested": tested,
                               "with_history": with_hist, "samples": samples}

    def books():
        recs = [normalize_market(m) for m in state.get("open_page", [])]
        recs = sorted([r for r in recs if r["ref_index"] is not None and r["token_ids"]],
                      key=lambda r: r["volume_24h"] or 0, reverse=True)[:5]
        out = []
        for r in recs:
            s = summarize(clob.book(r["token_ids"][r["ref_index"]]))
            out.append({"id": r["id"], "gamma_bid": r["best_bid"], "gamma_ask": r["best_ask"],
                        **({k: s[k] for k in ("best_bid", "best_ask", "spread", "valid", "buy_vwap_100",
                                              "buy_vwap_1000")} if s else {"empty": True})})
        valid = sum(1 for o in out if o.get("valid"))
        return valid > 0, {"books": out, "valid": valid}

    def data_api_status():
        st = data_api.status()
        d = st.get("data", st) if isinstance(st, dict) else {}
        age = d.get("age_seconds")
        return isinstance(age, (int, float)) and age < 3600, {"age_seconds": age, "computed_at": d.get("computed_at"),
                                                             "keys": sorted(d) if isinstance(d, dict) else None}

    def data_api_history_coverage():
        recs = state.get("resolved_recs") or []
        rows = []
        for r in recs[:10]:
            tok = r["token_ids"][r["ref_index"]]
            try:
                clob_n = len(clob.price_history(tok, 360))
            except Exception as e:  # noqa: BLE001
                clob_n = f"error {type(e).__name__}"
            try:
                dapi_n = len(data_api.price_history(tok))
            except Exception as e:  # noqa: BLE001
                dapi_n = f"error {type(e).__name__}"
            rows.append({"id": r["id"], "clob_points": clob_n, "data_api_points": dapi_n})
        with_any = sum(1 for x in rows if (isinstance(x["clob_points"], int) and x["clob_points"]) or
                       (isinstance(x["data_api_points"], int) and x["data_api_points"]))
        return bool(rows) and with_any > 0, {"tested": len(rows), "with_history_any_source": with_any, "rows": rows}

    def data_api_resolution_agreement():
        from .dataapi import compare_resolutions
        recs = [r for r in (state.get("resolved_recs") or []) if r.get("condition_id")][:20]
        ours = {r["condition_id"]: r["resolved_outcome"] for r in recs}
        cmp = compare_resolutions(ours, data_api.resolutions(list(ours)))
        return cmp["disagree"] == 0 and cmp["agree"] > 0, cmp

    _check(report, "gamma_open_first_page", open_page)
    _check(report, "gamma_full_open_scan", full_scan)
    _check(report, "gamma_deep_offset", deep_offset)
    _check(report, "clob_price_history_resolved", closed_history)
    _check(report, "clob_order_books", books)
    _check(report, "data_api_v2_status", data_api_status)
    _check(report, "data_api_v2_history_coverage", data_api_history_coverage)
    _check(report, "data_api_v2_resolution_agreement", data_api_resolution_agreement)
    return {"checked_at": datetime.now(timezone.utc).isoformat(),
            "passed": sum(c["ok"] for c in report), "total": len(report), "checks": report}


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    result = run(Gamma(), Clob())
    try:
        conn = db.connect()
        db.init_schema(conn)
        conn.execute("INSERT INTO research_reports (kind, params, result) VALUES ('integration', '{}', %s)",
                     (json.dumps(result, default=str),))
    except Exception as e:  # noqa: BLE001
        log.warning("could not save report: %s", e)
    print(json.dumps(result, indent=2, ensure_ascii=False, default=str))
    sys.exit(0 if result["passed"] == result["total"] else 1)


if __name__ == "__main__":
    main()
