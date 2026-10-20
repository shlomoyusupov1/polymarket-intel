"""Read-only JSON API for the dashboard."""
import threading
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from . import config, db
from .orderbook import summarize
from .sources import Clob

_clob: Clob | None = None
_cache: dict = {}
_cache_lock = threading.Lock()


def cached(key, ttl: float, fn):
    """Tiny in-process TTL cache for live upstream calls (per worker)."""
    now = time.monotonic()
    with _cache_lock:
        hit = _cache.get(key)
        if hit and now - hit[0] < ttl:
            return hit[1]
    value = fn()
    with _cache_lock:
        if len(_cache) > 2000:
            _cache.clear()
        _cache[key] = (now, value)
    return value


def clob() -> Clob:
    global _clob
    if _clob is None:
        _clob = Clob()
    return _clob

pool: ConnectionPool | None = None

DECISION = {"decision": "WAIT",
            "reason": "אין עדיין מודל הסתברות עצמאי מאומת — מחיר השוק אינו תחזית עצמאית"}


def data_quality(m: dict, ob: dict | None) -> dict:
    """Per-market data quality. A YES/NO may only ever be shown when this is 'good'."""
    now = datetime.now(timezone.utc)
    stale_after = config.COLLECT_INTERVAL_MIN * 3
    issues = []
    if m.get("ref_price") is None:
        issues.append("אין מחיר שוק")
    seen = m.get("updated_at")
    if not seen or (now - seen).total_seconds() / 60 > stale_after:
        issues.append("הנתון ישן")
    if ob is None:
        issues.append("אין ספר פקודות")
    else:
        if (now - ob["ts"]).total_seconds() / 60 > stale_after:
            issues.append("ספר הפקודות ישן")
        if not ob.get("valid"):
            issues.append("ספר פקודות לא תקין (חד-צדדי/חוצה)")
        elif ob["spread"] is not None and ob["spread"] > 0.10:
            issues.append("מרווח רחב מ-10¢")
        if ob.get("buy_vwap_1000") is None:
            issues.append("אין עומק ל-$1,000")
    level = "good" if not issues else ("limited" if len(issues) == 1 and "אין עומק ל-$1,000" in issues else "bad")
    return {"level": level, "issues": issues}


def price_types(m: dict, ob: dict | None) -> dict:
    """Four different "prices" that must never be confused. Each carries its source and time."""
    book_ok = bool(ob and ob.get("valid"))
    return {
        "display": {"value": m.get("ref_price"), "what": "outcomePrices from Gamma (what polymarket.com shows; "
                    "not necessarily tradable)", "source": "gamma", "source_time": m.get("source_updated_at"),
                    "ingested_at": m.get("updated_at")},
        "quoted": {"bid": m.get("best_bid"), "ask": m.get("best_ask"), "what": "Gamma's best bid/ask for the "
                   "first outcome (top of book as reported by Gamma, no size)", "source": "gamma",
                   "source_time": m.get("source_updated_at")},
        "book": None if not ob else {"valid": ob.get("valid"), "bid": ob.get("best_bid"), "ask": ob.get("best_ask"),
                                     "levels": [ob.get("n_bids"), ob.get("n_asks")], "what": "CLOB order book "
                                     "snapshot; valid = both sides present and bid < ask", "source": "clob",
                                     "source_time": ob.get("book_ts"), "ingested_at": ob.get("fetched_at")},
        "executable": None if not book_ok else {
            "buy_100": ob.get("buy_vwap_100"), "buy_1000": ob.get("buy_vwap_1000"),
            "sell_100": ob.get("sell_vwap_100"), "sell_1000": ob.get("sell_vwap_1000"),
            "what": "volume-weighted price to fill $100 / $1,000 by walking the book (None = not enough depth)",
            "source": "clob", "source_time": ob.get("book_ts"), "ingested_at": ob.get("fetched_at")},
    }


@asynccontextmanager
async def lifespan(_app):
    global pool
    with db.connect() as c:
        db.init_schema(c)
    pool = ConnectionPool(config.DATABASE_URL, min_size=1, max_size=5, open=True,
                          kwargs={"autocommit": True, "row_factory": dict_row})
    yield
    pool.close()


app = FastAPI(title="pmintel", lifespan=lifespan, docs_url="/api/docs", openapi_url="/api/openapi.json")


def q(sql: str, params=()):
    with pool.connection() as c:
        return c.execute(sql, params).fetchall()


@app.get("/api/health")
def health():
    jobs = q("""SELECT DISTINCT ON (job) job, started_at, finished_at, status, items, error
                FROM job_runs ORDER BY job, started_at DESC""")
    last_ok = q("""SELECT job, max(finished_at) AS last_ok FROM job_runs
                   WHERE status = 'ok' GROUP BY job""")
    counts = q("""SELECT (SELECT count(*) FROM markets WHERE active AND NOT closed) AS open_markets,
                         (SELECT count(*) FROM markets WHERE closed AND resolved_outcome IS NOT NULL) AS resolved_markets,
                         (SELECT count(DISTINCT token_id) FROM price_history) AS tokens_with_history,
                         (SELECT max(ts) FROM snapshots) AS last_snapshot,
                         (SELECT count(*) FROM orderbooks) AS orderbook_rows,
                         (SELECT max(ts) FROM orderbooks) AS last_orderbook,
                         (SELECT round(100.0 * avg(valid::int), 1) FROM orderbooks
                           WHERE ts > now() - interval '24 hours') AS orderbook_valid_pct_24h""")[0]
    lag = None
    if counts["last_snapshot"]:
        lag = (datetime.now(timezone.utc) - counts["last_snapshot"]).total_seconds() / 60
    stale = lag is None or lag > config.COLLECT_INTERVAL_MIN * 3
    return {"status": "stale" if stale else "ok", "snapshot_lag_min": lag,
            "collect_interval_min": config.COLLECT_INTERVAL_MIN,
            "counts": counts, "jobs": jobs, "last_ok": {r["job"]: r["last_ok"] for r in last_ok}}


LIST_COLS = """id, question, category, outcomes, outcome_prices, ref_outcome, ref_price, best_bid, best_ask,
               spread, volume, volume_24h, liquidity, end_date, url, updated_at, one_day_change,
               accepting_orders, event_slug"""
# Tradable only when Gamma explicitly says so: acceptingOrders = NULL is "unknown", not "tradable".
TRADABLE = "active AND NOT closed AND accepting_orders IS TRUE AND enable_order_book IS TRUE"


@app.get("/api/overview")
def overview():
    """Command Center: what changed, what draws attention, any supported opportunity,
    how fresh/reliable the data is, what to investigate next."""
    liq = config.SNAPSHOT_MIN_LIQUIDITY
    stats = q(f"""SELECT count(*) AS open_markets,
                        count(*) FILTER (WHERE {TRADABLE} AND liquidity >= %s) AS tradable_markets,
                        sum(volume_24h) AS volume_24h, sum(liquidity) AS liquidity,
                        max(updated_at) AS last_update
                 FROM markets WHERE active AND NOT closed""", (liq,))[0]
    # 1d change as reported by Polymarket (Gamma oneDayPriceChange of the first outcome)
    changed = q(f"""SELECT {LIST_COLS} FROM markets
                    WHERE {TRADABLE} AND liquidity >= 5000 AND one_day_change IS NOT NULL
                    ORDER BY abs(one_day_change) DESC NULLS LAST LIMIT 8""")
    attention = q(f"""SELECT {LIST_COLS} FROM markets WHERE {TRADABLE}
                      ORDER BY volume_24h DESC NULLS LAST LIMIT 8""")
    rep = q("""SELECT created_at, result->'structural'->'executable' AS executable,
                      jsonb_array_length(coalesce(result->'structural'->'theoretical', '[]'::jsonb)) AS theoretical
               FROM research_reports WHERE kind = 'research_report_v1' ORDER BY id DESC LIMIT 1""")
    hist = q("""SELECT created_at, result FROM research_reports WHERE kind = 'historical_v1'
                ORDER BY id DESC LIMIT 1""")
    supported = []
    if hist:
        # only walk-forward verdicts count (out of sample + Holm + 2x costs + live execution check)
        for hz, res in (hist[0]["result"] or {}).items():
            wf = res.get("walk_forward") or {}
            if wf.get("edge_supported"):
                supported.append({"horizon_h": hz, "verdict": wf.get("verdict"), **(wf.get("oos_trading") or {})})
    health_now = health()
    return {
        "stats": stats,
        "changed": changed, "changed_source": "Polymarket (Gamma oneDayPriceChange)",
        "own_movers": movers(hours=24, limit=8),
        "attention": attention,
        "opportunities": {
            "structural_report_at": rep[0]["created_at"] if rep else None,
            "structural_theoretical": rep[0]["theoretical"] if rep else None,
            "structural_executable": len(rep[0]["executable"] or []) if rep else None,
            "historical_report_at": hist[0]["created_at"] if hist else None,
            "statistically_supported": supported,
        },
        "freshness": {"status": health_now["status"], "snapshot_lag_min": health_now["snapshot_lag_min"],
                      "counts": health_now["counts"], "last_ok": health_now["last_ok"]},
    }


SORTS = {"volume_24h": "volume_24h", "liquidity": "liquidity", "volume": "volume", "end_date": "end_date",
         "change": "abs(one_day_change)"}


@app.get("/api/markets")
def markets(search: str | None = None, category: str | None = None,
            sort: str = Query("volume_24h", enum=list(SORTS)), min_liquidity: float = 0,
            tradable: bool = True, ids: str | None = None, open_only: bool = False,
            limit: int = Query(50, le=200), offset: int = 0):
    where, params = [TRADABLE if tradable else "active AND NOT closed"], []
    if open_only:  # hide practically decided markets (>=97% / <=3%) and those closing within 24 h
        where.append("ref_price BETWEEN 0.03 AND 0.97 AND end_date > now() + interval '1 day'")
    if ids:
        id_list = [i for i in ids.split(",") if i][:200]
        where = ["id = ANY(%s)"]
        params.append(id_list)
    if search:
        where.append("question ILIKE %s")
        params.append(f"%{search}%")
    if category:
        where.append("category = %s")
        params.append(category)
    if min_liquidity:
        where.append("liquidity >= %s")
        params.append(min_liquidity)
    order = SORTS[sort]
    direction = "ASC" if sort == "end_date" else "DESC"
    rows = q(f"""SELECT {LIST_COLS}
                 FROM markets WHERE {' AND '.join(where)}
                 ORDER BY {order} {direction} NULLS LAST LIMIT %s OFFSET %s""",
             (*params, limit, offset))
    for r in rows:
        r.update(DECISION)
    return rows


@app.get("/api/categories")
def categories():
    return q("""SELECT COALESCE(category, '') AS category, count(*) AS n FROM markets
                WHERE active AND NOT closed GROUP BY 1 ORDER BY 2 DESC""")


@app.get("/api/markets/{market_id}")
def market(market_id: str, days: int = Query(30, le=365)):
    rows = q("SELECT * FROM markets WHERE id = %s", (market_id,))
    if not rows:
        raise HTTPException(404, "market not found")
    m = rows[0]
    m["snapshots"] = q("""SELECT ts, ref_price, best_bid, best_ask, volume_24h, liquidity FROM snapshots
                          WHERE market_id = %s AND ts > now() - make_interval(days => %s)
                          ORDER BY ts""", (market_id, days))
    m["history"] = q("SELECT ts, price FROM price_history WHERE market_id = %s ORDER BY ts", (market_id,))
    ob = q("""SELECT ts, best_bid, best_ask, spread, bid_depth_5c, ask_depth_5c, buy_vwap_100,
                     buy_vwap_1000, sell_vwap_100, sell_vwap_1000, valid, n_bids, n_asks, book_ts, fetched_at
              FROM orderbooks WHERE market_id = %s ORDER BY ts DESC LIMIT 1""", (market_id,))
    m["orderbook"] = ob[0] if ob else None
    m["data_quality"] = data_quality(m, m["orderbook"])
    m["prices"] = price_types(m, m["orderbook"])
    m["rules_versions"] = q("""SELECT rules_hash, first_seen_at FROM market_rules WHERE market_id = %s
                               ORDER BY first_seen_at""", (market_id,))
    m.update(DECISION)
    return m


@app.get("/api/movers")
def movers(hours: int = Query(24, le=24 * 30), limit: int = Query(20, le=100), min_liquidity: float = 5000):
    """Largest observed price changes. Based only on our own snapshots — empty until collected."""
    return q("""
        WITH latest AS (
          SELECT DISTINCT ON (market_id) market_id, ts, ref_price FROM snapshots
          WHERE ts > now() - interval '2 hours' ORDER BY market_id, ts DESC),
        past AS (
          SELECT DISTINCT ON (market_id) market_id, ts, ref_price FROM snapshots
          WHERE ts <= now() - make_interval(hours => %s)
            AND ts > now() - make_interval(hours => %s) - interval '2 hours'
          ORDER BY market_id, ts DESC)
        SELECT m.id, m.question, m.ref_outcome, m.url, m.liquidity, m.volume_24h,
               p.ref_price AS price_then, l.ref_price AS price_now,
               l.ref_price - p.ref_price AS change, p.ts AS ts_then, l.ts AS ts_now
        FROM latest l JOIN past p USING (market_id) JOIN markets m ON m.id = l.market_id
        WHERE m.liquidity >= %s AND m.active AND NOT m.closed AND l.ref_price IS DISTINCT FROM p.ref_price
        ORDER BY abs(l.ref_price - p.ref_price) DESC NULLS LAST LIMIT %s""",
             (hours, hours, min_liquidity, limit))


@app.get("/api/research/latest")
def research_latest():
    rows = q("""SELECT id, created_at, kind, params, result FROM research_reports
                WHERE kind = 'historical_v1' ORDER BY id DESC LIMIT 1""")
    return rows[0] if rows else None


@app.get("/api/agents")
def agents_room():
    """Every background job as a worker with a live status line (see agents.py)."""
    from . import agents as A, tips as T
    jobs = {r["job"]: r for r in q("""SELECT DISTINCT ON (job) job, started_at, finished_at, status, items, error
                                     FROM job_runs ORDER BY job, started_at DESC""")}
    f = q("""SELECT (SELECT count(*) FROM markets WHERE active AND NOT closed) AS open_markets,
                    (SELECT max(ts) FROM snapshots) AS last_snapshot,
                    (SELECT round(100.0 * avg(valid::int), 1) FROM orderbooks
                      WHERE ts > now() - interval '24 hours') AS orderbook_valid_pct_24h,
                    (SELECT max(ts) FROM tips WHERE kind = 'telegram') AS last_tip,
                    (SELECT count(*) FROM tips WHERE ts > now() - interval '24 hours') AS tips_24h,
                    (SELECT count(*) FROM tips WHERE ts > now() - interval '7 days' AND verdict IS NOT NULL) AS checked_7d,
                    (SELECT count(*) FROM tips WHERE ts > now() - interval '7 days' AND verdict = 'red') AS red_7d,
                    (SELECT count(*) FROM sec_buys WHERE accepted > now() - interval '30 days') AS sec_buys_30d,
                    (SELECT count(*) FROM sec_events WHERE accepted > now() - interval '30 days') AS sec_events_30d""")[0]
    f["open_markets"] = int(f["open_markets"] or 0)
    for k in ("orderbook_valid_pct_24h",):
        f[k] = float(f[k]) if f[k] is not None else None
    with db.connect() as c:
        board = T.leaderboard(c)
    f["sources_total"] = len(board)
    f["sources_tested"] = sum(1 for b in board if b["gate"]["n"] >= 30)
    f["sources_reliable"] = sum(1 for b in board if b["gate"]["status"] == "reliable")
    etf = etf_latest()
    if etf:
        acc = (etf["result"] or {}).get("accounts") or {}
        spy = ((etf["result"] or {}).get("benchmark_spy") or {}).get("return")
        parts = [f"{k}: {v.get('return', 0) * 100:+.1f}%" for k, v in acc.items()]
        if parts:
            f["etf_line"] = " · ".join(parts) + (f" · SPY {spy * 100:+.1f}%" if spy is not None else "")
    WF = {"supported": "נמצא יתרון שעבר הכול", "oos_positive_needs_execution_check": "חיובי מחוץ למדגם, ממתין לאימות ביצוע",
          "not_supported": "לא נמצא יתרון", "insufficient_data": "אין עדיין מספיק נתוני עבר"}
    res = research_latest()
    if res:
        wf = next(iter((res["result"] or {}).values()), {}).get("walk_forward") or {}
        f["research_line"] = WF.get(wf.get("verdict"), wf.get("verdict") or "רץ, בלי מסקנה")
    lab = lab_latest()
    if lab:
        st = (lab["result"] or {}).get("strategies") or {}
        cands = [v["label"] for v in st.values() if v.get("verdict") == "candidate_for_paper"]
        f["lab_line"] = (f"{len(cands)} מועמדות: " + ", ".join(cands)) if cands else f"0 מתוך {len(st)} אסטרטגיות עברו"
    clab = lab_latest("crypto")
    if clab:
        st = (clab["result"] or {}).get("strategies") or {}
        cands = [v["label"] for v in st.values() if v.get("verdict") == "candidate_for_paper"]
        f["crypto_line"] = (f"{len(cands)} מועמדות: " + ", ".join(cands)) if cands else f"0 מתוך {len(st)} אסטרטגיות עברו מול ביטקוין"
    hm = q("SELECT * FROM host_metrics ORDER BY ts DESC LIMIT 1")
    if hm:
        h = hm[0]
        f.update(cpu_pct=h["cpu_pct"], host_ts=h["ts"],
                 disk_pct=100.0 * h["disk_used"] / h["disk_total"] if h["disk_total"] else None,
                 mem_pct=100.0 * (1 - h["mem_available"] / h["mem_total"]) if h["mem_total"] else None,
                 db_gb=(h["db_bytes"] or 0) / 1e9)
    feed = q("""SELECT job, status, started_at, finished_at, items FROM job_runs
                ORDER BY started_at DESC LIMIT 20""")
    for r in feed:
        r["agent"] = A.JOB_AGENT.get(r["job"])
    return {"agents": A.build(jobs, f), "feed": feed}


class FollowIn(BaseModel):
    text: str


@app.get("/api/follows")
def follows_list():
    from . import follows
    with db.connect() as c:
        return follows.listing(c)


@app.post("/api/follows")
def follows_add(body: FollowIn):
    from . import follows
    try:
        with db.connect() as c:
            return follows.add(c, body.text)
    except follows.FollowError as e:
        raise HTTPException(400, str(e)) from None
    except Exception as e:  # noqa: BLE001 - upstream (Gamma / Yahoo) unreachable
        raise HTTPException(502, "לא הצלחתי לקרוא את המחיר כרגע. נסה שוב בעוד דקה.") from e


@app.delete("/api/follows/{fid}")
def follows_remove(fid: int):
    from . import follows
    with db.connect() as c:
        if not follows.remove(c, fid):
            raise HTTPException(404, "not found")
    return {"ok": True}


class SettingsIn(BaseModel):
    budget: float | None = None
    profile: str | None = None
    fee_min: float | None = None
    fee_pct: float | None = None
    fractional: bool | None = None


@app.get("/api/settings")
def settings_get():
    from . import plans
    with db.connect() as c:
        s = plans.get_settings(c)
    return {"settings": s, "limits": plans.limits(s), "profiles": plans.PROFILES}


@app.put("/api/settings")
def settings_put(body: SettingsIn):
    from . import plans
    try:
        with db.connect() as c:
            s = plans.save_settings(c, {k: v for k, v in body.model_dump().items() if k in body.model_fields_set})
    except ValueError as e:
        raise HTTPException(400, str(e)) from None
    return {"settings": s, "limits": plans.limits(s), "profiles": plans.PROFILES}


@app.get("/api/size")
def size_calc(price: float, stop: float | None = None):
    """The owner's own entry/stop -> position size, fees and planned loss under the saved risk settings."""
    from . import plans
    with db.connect() as c:
        s = plans.get_settings(c)
    return plans.size(s, price, stop)


@app.get("/api/live/status")
def live_status():
    """Feeds, latest prices with their source time and age, and measured latency (source -> alert)."""
    from . import live_detect as D
    if not _has_table("live_prices"):
        return {"feeds": {}, "prices": [], "latency": None, "running": False}
    now = datetime.now(timezone.utc)
    feeds = {r["feed"]: r for r in q("SELECT * FROM live_feeds")}
    prices = q("SELECT * FROM live_prices ORDER BY cls, name")
    for r in prices:
        r["age_s"] = (now - r["recv_ts"]).total_seconds() if r["recv_ts"] else None
        r["src_age_s"] = (now - r["src_ts"]).total_seconds() if r["src_ts"] else None
        r["fresh"] = bool(r["fresh"] and r["age_s"] is not None and r["age_s"] <= D.STALE[r["cls"]])
    rows = q("""SELECT extract(epoch FROM sent_at - src_ts) AS e2e, extract(epoch FROM detected_at - src_ts) AS det,
                       status FROM live_alerts WHERE detected_at > now() - interval '7 days' AND src_ts IS NOT NULL""")
    lat = {"alerts": D.latency_stats([float(r["e2e"]) for r in rows if r["e2e"] is not None]),
           "detect": D.latency_stats([float(r["det"]) for r in rows if r["det"] is not None]),
           "failed": sum(1 for r in rows if r["status"] == "failed"),
           "pending": sum(1 for r in rows if r["status"] == "pending")}
    running = any(f["updated_at"] and (now - f["updated_at"]).total_seconds() < 60 for f in feeds.values())
    return {"feeds": feeds, "prices": prices, "latency": lat, "running": running, "now": now}


@app.get("/api/live/alerts")
def live_alerts(limit: int = Query(50, le=200)):
    if not _has_table("live_alerts"):
        return []
    return q("""SELECT id, level, kind, asset, text, payload, src_ts, recv_ts, detected_at, sent_at, status
                FROM live_alerts ORDER BY id DESC LIMIT %s""", (limit,))


@app.get("/api/portfolio")
def virtual_portfolio(scope: str = "all"):
    """Paper account replayed from signals sealed before entry, under the owner's budget and risk profile."""
    from . import lab_paper as LP, plans, portfolio as PF
    from .cryptolab import CRYPTO
    from .stocklab import STOCKS
    if scope not in ("all", "proven"):
        raise HTTPException(400, "scope must be all or proven")
    cols = ["id", "strategy", "symbol", "signal_day", "recorded_at", "sha256", "lab_verdict", "hold", "cost_rt", "status",
            "entry_day", "entry_price", "exit_day", "ret", "spy_ret", "excess", "last_price", "last_day"]
    rows = q(f"SELECT {', '.join(cols)} FROM lab_signals ORDER BY id") if _has_table("lab_signals") else []
    live = {}
    if _has_table("live_prices"):
        from . import live_detect as D
        now = datetime.now(timezone.utc)
        for lp in q("SELECT asset, cls, price, src_ts, recv_ts, fresh FROM live_prices"):
            ok = lp["fresh"] and lp["recv_ts"] and (now - lp["recv_ts"]).total_seconds() <= D.STALE[lp["cls"]]
            live[lp["asset"].split(":", 1)[1]] = (lp, bool(ok))
    for r in rows:
        m = CRYPTO if r["strategy"].startswith("crypto:") else STOCKS
        r["sealed_ok"] = r["recorded_at"] < LP.deadline(str(r["signal_day"]), m)
        lp = live.get(r["symbol"])
        r["price_live"] = bool(lp and lp[1])
        if lp and lp[1]:
            r["last_price"], r["last_day"] = lp[0]["price"], lp[0]["src_ts"]
    entries = performance()["entries"]
    ok = {e["id"].split(":", 1)[1] for e in entries if e["id"].startswith("lab:") and e["status"] == "candidate"}
    ok |= {e["id"].split(":", 1)[1] for e in entries if e["id"].startswith("fwd:") and e["status"] == "confirmed"}
    labs = {"": (lab_latest() or {}).get("result") or {}, "crypto:": (lab_latest("crypto") or {}).get("result") or {}}
    labels = {pre + k: v.get("label", k) for pre, rep in labs.items() for k, v in (rep.get("strategies") or {}).items()}
    with db.connect() as c:
        s = plans.get_settings(c)
    L = plans.limits(s)
    res = PF.replay(rows, L["budget"], L["max_position_usd"], L["max_open"],
                    allowed=(lambda k: k in ok) if scope == "proven" else None)
    by_id = {r["id"]: r for r in rows}
    for p in res["positions"]:
        p["label"] = labels.get(p["strategy"], p["strategy"])
        p["price_live"] = by_id.get(p["id"], {}).get("price_live", False)
    return {**res, "history": PF.history(rows, res["decisions"], labels)[:300], "limits": L, "scope": scope,
            "decisions": None, "proven_strategies": sorted(ok)}


def _opps():
    from . import plans
    perf = performance()
    labs = {"stocks": (lab_latest() or {}).get("result"), "crypto": (lab_latest("crypto") or {}).get("result")}
    with db.connect() as c:
        return plans.opportunities(c, perf["entries"], labs)


@app.get("/api/opportunities")
def opportunities_list():
    """Everything worth a look across stocks, crypto and Polymarket, with its research state. No invented numbers."""
    return {"rows": _opps(), "generated_at": datetime.now(timezone.utc)}


@app.get("/api/plan/{kind}/{rid}")
def trade_plan(kind: str, rid: int):
    from . import follows as F, plans
    from .orderbook import summarize as book_summary
    row = next((r for r in _opps() if r["kind"] == kind and r["id"] == rid), None)
    if row is None:
        raise HTTPException(404, "ההזדמנות הזו כבר לא פתוחה")
    extra = {}
    if kind == "follow" and row["domain"] == "polymarket":
        try:
            snap = F.snapshot("polymarket", row["symbol"])
        except Exception as e:  # noqa: BLE001
            raise HTTPException(502, "לא הצלחתי לקרוא את השוק מ-Polymarket כרגע.") from e
        outs = []
        for it in snap["items"][:6]:
            asks = None
            if it.get("token"):
                try:
                    b = book_summary(cached(("book", it["token"]), 30, lambda t=it["token"]: clob().book(t)))
                    asks = (b or {}).get("levels", {}).get("asks")
                except Exception:  # noqa: BLE001 - fall back to the display price, and say so
                    asks = None
            outs.append({"name": it["name"], "price": it["price"], "asks": asks})
        extra = {"outcomes": outs, "end": snap.get("end"), "rules": snap.get("rules"),
                 "resolution_source": snap.get("resolution_source"), "category": snap.get("category")}
    with db.connect() as c:
        s = plans.get_settings(c)
    return plans.plan(row, s, extra)


def _market(name: str):
    from . import cryptolab, stocklab
    if name not in ("stocks", "crypto"):
        raise HTTPException(400, "market must be stocks or crypto")
    return cryptolab.CRYPTO if name == "crypto" else stocklab.STOCKS


@app.get("/api/lab/latest")
def lab_latest(market: str = "stocks"):
    m = _market(market)
    rows = q("""SELECT id, created_at, result FROM research_reports
                WHERE kind = %s ORDER BY id DESC LIMIT 1""", (m.report_kind,))
    return rows[0] if rows else None


@app.get("/api/lab/paper")
def lab_paper(market: str = "stocks"):
    """Forward paper record of the lab strategies (signals sealed before the entry)."""
    from . import lab_paper as P
    m = _market(market)
    like, want = "crypto:%", m.key == "crypto"
    with db.connect() as c:
        closed = c.execute("SELECT strategy, excess, entry_day FROM lab_signals WHERE status = 'closed'").fetchall()
    rows = q("""SELECT strategy, symbol, signal_day, recorded_at, hold, status, entry_day, entry_price, exit_day,
                       ret, spy_ret, excess, cost_rt, lab_verdict FROM lab_signals WHERE (strategy LIKE %s) = %s
                ORDER BY signal_day DESC, id DESC LIMIT 60""", (like, want))
    for r in rows:
        r["strategy"] = r["strategy"][len(m.prefix):]
    first = q("""SELECT min(signal_day) AS first_day, count(*) AS total FROM lab_signals
                 WHERE (strategy LIKE %s) = %s""", (like, want))[0]
    return {"summary": P.summary(P.split(closed, m)), "recent": rows, "first_day": first["first_day"],
            "total": first["total"], "gate": P.GATE, "market": m.key, "benchmark": m.bench_label}


@app.get("/api/performance")
def performance():
    """Every strategy and source on one scoreboard: historical tests, forward paper, measured sources."""
    from . import lab_paper as LP, performance as P, tips as T
    from .cryptolab import CRYPTO
    lab = lab_latest()
    clab = lab_latest("crypto")
    etf = etf_latest()
    res = research_latest()
    with db.connect() as c:
        board = T.leaderboard(c)
        try:
            closed = c.execute("SELECT strategy, excess, entry_day FROM lab_signals WHERE status = 'closed'").fetchall()
        except Exception:  # noqa: BLE001 - forward table not created yet
            closed = []
    entries = (P.from_stocklab(lab["result"] if lab else None, lab["created_at"] if lab else None)
               + P.from_lab_forward(LP.summary(LP.split(closed)), lab["result"] if lab else None)
               + P.from_stocklab(clab["result"] if clab else None, clab["created_at"] if clab else None, "crypto")
               + P.from_lab_forward(LP.summary(LP.split(closed, CRYPTO)), clab["result"] if clab else None, "crypto")
               + P.from_etf(etf["result"] if etf else None, etf["created_at"] if etf else None)
               + P.from_polymarket(res["result"] if res else None, res["created_at"] if res else None)
               + P.from_tips(board))
    ctx = {}
    for key, rep, bench in (("stocks", lab, "SPY"), ("crypto", clab, "ביטקוין")):
        r = (rep or {}).get("result") or {}
        ctx[key] = {"bench": bench, "bench_stats": r.get("benchmark_stats"), "assumed_cost": r.get("assumed_cost"),
                    "updated": rep["created_at"] if rep else None, "missing": r.get("missing")}
    return {"entries": entries, "summary": P.summarize(entries), "context": ctx}


SECTOR_ETFS = [("SPY", "S&P 500"), ("QQQ", "נאסד\"ק 100"), ("IWM", "מניות קטנות"), ("XLK", "טכנולוגיה"), ("XLC", "תקשורת"),
               ("XLY", "צריכה מחזורית"), ("XLF", "פיננסים"), ("XLV", "בריאות"), ("XLI", "תעשייה"), ("XLE", "אנרגיה"),
               ("XLP", "צריכה בסיסית"), ("XLU", "תשתיות"), ("XLB", "חומרים"), ("XLRE", "נדל\"ן")]


@app.get("/api/stocks/overview")
def stocks_overview():
    """Market and sector snapshot from daily bars (1-hour cache). Prices only, no forecasts."""
    from . import stockcheck

    def one(sym):
        bars, _ = cached(("bars1y", sym), 3600, lambda: stockcheck.fetch(sym))
        c = [b["c"] for b in bars]
        if len(c) < 30:
            return None
        ret = lambda n: c[-1] / c[-1 - n] - 1 if len(c) > n else None
        ytd = [b for b in bars if b["day"][:4] == bars[-1]["day"][:4]]
        ma200 = sum(c[-200:]) / 200 if len(c) >= 200 else None
        return {"day": bars[-1]["day"], "close": c[-1], "r1w": ret(5), "r1m": ret(21), "r3m": ret(63),
                "r1y": ret(len(c) - 1), "ytd": c[-1] / ytd[0]["o"] - 1 if ytd else None,
                "above_ma200": (c[-1] > ma200) if ma200 else None}
    rows, missing = [], []
    for sym, name in SECTOR_ETFS:
        try:
            r = one(sym)
        except Exception:  # noqa: BLE001
            r = None
        (rows.append({"symbol": sym, "name": name, **r}) if r else missing.append(sym))
    spy = next((r for r in rows if r["symbol"] == "SPY"), None)
    for r in rows:
        r["vs_spy_3m"] = (r["r3m"] - spy["r3m"]) if spy and r["r3m"] is not None and spy["r3m"] is not None else None
    signals = q("""SELECT strategy, symbol, signal_day, status, entry_day, excess FROM lab_signals
                   WHERE status IN ('pending', 'open') AND strategy NOT LIKE 'crypto:%%' ORDER BY signal_day DESC LIMIT 30""") if _has_table("lab_signals") else []
    sec = q("""SELECT symbol, accepted, value, title FROM sec_buys ORDER BY accepted DESC LIMIT 8""") if _has_table("sec_buys") else []
    return {"etfs": rows, "missing": missing, "signals": signals, "sec_buys": sec,
            "source": "Yahoo Finance daily bars (cached 1 h)"}


def _has_table(name: str) -> bool:
    return bool(q("SELECT to_regclass(%s) AS t", (name,))[0]["t"])


@app.get("/api/integration/latest")
def integration_latest():
    rows = q("""SELECT id, created_at, result FROM research_reports
                WHERE kind = 'integration' ORDER BY id DESC LIMIT 1""")
    return rows[0] if rows else None


HISTORY_RANGES = {"1d": ("1d", 5), "1w": ("1w", 60), "1m": ("1m", 240), "max": ("max", 1440)}


def _market_token(market_id: str) -> tuple[dict, str]:
    rows = q("SELECT id, token_ids, ref_index, ref_outcome FROM markets WHERE id = %s", (market_id,))
    if not rows:
        raise HTTPException(404, "market not found")
    m = rows[0]
    if m["ref_index"] is None or m["ref_index"] >= len(m["token_ids"] or []):
        raise HTTPException(409, "market has no order-book token")
    return m, m["token_ids"][m["ref_index"]]


@app.get("/api/markets/{market_id}/history")
def market_history(market_id: str, range: str = Query("1w", enum=list(HISTORY_RANGES))):
    """Live price history of the reference outcome from the CLOB (source: clob.polymarket.com)."""
    m, token = _market_token(market_id)
    interval, fidelity = HISTORY_RANGES[range]

    def fetch():
        data = clob().api.get("/prices-history", {"market": token, "interval": interval, "fidelity": fidelity}) or {}
        return [{"t": int(p["t"]), "p": float(p["p"])} for p in data.get("history", [])
                if isinstance(p, dict) and "t" in p and "p" in p]
    try:
        points = cached(("hist", token, range), 60, fetch)
    except Exception as e:  # noqa: BLE001 - surfaced to the UI as an honest error state
        raise HTTPException(502, f"history unavailable: {type(e).__name__}") from None
    return {"market_id": market_id, "outcome": m["ref_outcome"], "range": range,
            "source": "clob.polymarket.com/prices-history", "points": points}


@app.get("/api/markets/{market_id}/book")
def market_book(market_id: str):
    """Live order book of the reference outcome: executable prices and depth (cached 30 s)."""
    m, token = _market_token(market_id)
    try:
        raw = cached(("book", token), 30, lambda: clob().book(token))
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"order book unavailable: {type(e).__name__}") from None
    s = summarize(raw)
    if s is None:
        return {"market_id": market_id, "outcome": m["ref_outcome"], "empty": True,
                "fetched_at": datetime.now(timezone.utc)}
    return {"market_id": market_id, "outcome": m["ref_outcome"], "empty": False,
            "fetched_at": datetime.now(timezone.utc), "source": "clob.polymarket.com/book", **s}


@app.get("/api/report/latest")
def report_latest():
    rows = q("""SELECT id, created_at, result FROM research_reports
                WHERE kind = 'research_report_v1' ORDER BY id DESC LIMIT 1""")
    return rows[0] if rows else None


@app.get("/api/etf/latest")
def etf_latest():
    """Latest ETF paper study v2 replay (paper only; see docs/ETF_PAPER_V2_PROTOCOL.md)."""
    rows = q("""SELECT id, created_at, result FROM research_reports
                WHERE kind = 'etf_paper_v2' ORDER BY id DESC LIMIT 1""")
    return rows[0] if rows else None


@app.get("/api/check/{symbol}")
def check_stock(symbol: str, isin: str | None = Query(None, max_length=12)):
    """Red-flag check of one stock from Yahoo daily bars (10-minute cache)."""
    from . import stockcheck
    sym = symbol.strip().upper()
    if not stockcheck.SYMBOL_RE.match(sym):
        raise HTTPException(400, "invalid symbol")
    try:
        bars, meta = cached(("check", sym), 600, lambda: stockcheck.fetch(sym))
    except LookupError:
        raise HTTPException(404, "symbol not found") from None
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"price data unavailable: {type(e).__name__}") from None
    return {"symbol": sym, "source": "Yahoo Finance daily bars (1y)", **stockcheck.assess(bars, meta, isin)}


class TipIn(BaseModel):
    symbol: str = Field(min_length=1, max_length=10)
    source: str = Field(min_length=1, max_length=80)
    price: float | None = Field(None, gt=0, lt=100000)


@app.get("/api/tips")
def tips_overview():
    from . import tips as T
    with db.connect() as c:  # tuple rows (the pool returns dicts)
        board = T.leaderboard(c)
    recent = q("""SELECT id, source, kind, ts, symbol, mentioned_price, verdict, flags, entry_day, entry_price,
                         r1, r5, r20, max_drawdown_20, max_runup_20 FROM tips ORDER BY ts DESC LIMIT 100""")
    return {"leaderboard": board, "recent": recent,
            "rule": "כניסה בפתיחה הראשונה אחרי הטיפ; תשואה בסגירה אחרי 1/5/20 ימי מסחר; \"מול SPY\" = הטיפ פחות SPY באותם ימים בדיוק. האמינות מחושבת אחרי עלות מסחר משוערת לכל מניה (מרווח קנייה-מכירה לפי טווח המחירים היומי + עמלות)"}


@app.post("/api/tips", status_code=201)
def tips_add(tip: TipIn):
    """Manually log a tip (e.g. from WhatsApp). JSON body only (forces a CORS preflight)."""
    from . import stockcheck, tips as T
    sym = tip.symbol.strip().upper()
    if not stockcheck.SYMBOL_RE.match(sym):
        raise HTTPException(400, "invalid symbol")
    with db.connect() as c:
        tid = T.record(c, "ידני: " + tip.source.strip(), sym, T.utcnow(), tip.price, kind="manual")
    if tid is None:
        raise HTTPException(409, "already logged in the last 24 h")
    return {"id": tid}


MACRO_PATTERNS = ["%fed %", "%federal reserve%", "%interest rate%", "%rate cut%", "%rate hike%", "%bps%",
                  "%recession%", "%inflation%", "%cpi%", "%unemployment%", "%gdp%", "%s&p 500%", "%nasdaq%"]


@app.get("/api/macro")
def macro(limit: int = Query(12, le=50)):
    """Polymarket's own odds on economic events (Fed, recession, inflation...). The market's view,
    not our forecast — shown as context for the stock side."""
    return q(f"""SELECT {LIST_COLS} FROM markets
                 WHERE {TRADABLE} AND liquidity >= 5000 AND ref_price BETWEEN 0.02 AND 0.98
                   AND question ILIKE ANY(%s)
                 ORDER BY volume_24h DESC NULLS LAST LIMIT %s""", (MACRO_PATTERNS, limit))


@app.get("/api/evidence")
def evidence_board(symbols: str | None = Query(None, max_length=200)):
    """Watchlist evidence: per finding source, link, time, price before/now, quality, unknowns."""
    from . import evidence, stockcheck
    syms = None
    if symbols:
        syms = [s.strip().upper() for s in symbols.split(",") if stockcheck.SYMBOL_RE.match(s.strip().upper())][:20]
    with db.connect() as c:
        return cached(("evidence", tuple(syms or [])), 300, lambda: evidence.board(c, symbols=syms))
