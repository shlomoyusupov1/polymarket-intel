"""Tip tracker: record stock tips from chat groups and measure what happened afterwards.

Privacy: only the symbol, an optional mentioned price, the group title, message id and time are
stored. No message text, no sender names or phone numbers.

Outcome rule (fixed in advance, same for every source): entry = open of the first US session that
starts AFTER the tip time; returns at the close of that session (1d) and of the 5th and 20th
sessions; worst drawdown and best run-up within 20 sessions; SPY over exactly the same sessions as the
benchmark ("excess" = tip minus SPY). No costs or slippage are added, so real results would be
worse — especially on OTC stocks.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, time, timezone
from zoneinfo import ZoneInfo

from . import costs, stockcheck

log = logging.getLogger("tips")
NY = ZoneInfo("America/New_York")
STOP = {"OTC", "USD", "ETF", "IPO", "CEO", "CFO", "NYSE", "SEC", "FDA", "AI", "USA", "US", "EU", "UK", "OK",
        "PM", "AM", "EPS", "ATH", "DD", "TA", "PR", "NFT", "BTC", "ETH", "VIP", "ISIN", "ILS", "NIS", "API", "GDP"}
CASHTAG = re.compile(r"\$([A-Z]{1,5}(?:\.[A-Z])?)(?![A-Za-z0-9])")
LABELLED = re.compile(r"(?:סימול(?:\s+המניה)?|טיקר|Symbol|Ticker)\s*[:：\-]?\s*\$?([A-Z]{1,5})(?![A-Za-z])", re.I)
PAREN = re.compile(r"\(([A-Z]{2,5})\)")
PRICE = re.compile(r"(?:לימיט|limit|מחיר|price|entry|כניסה)[^\d\n]{0,25}?\$?\s*(\d{1,5}(?:\.\d{1,4})?)", re.I)


def extract(text: str) -> tuple[list[str], float | None]:
    """Symbols mentioned in a message (cashtags, 'Symbol: X', '(XXXX)') and a mentioned entry price."""
    if not text:
        return [], None
    found: list[str] = []
    for rx in (LABELLED, CASHTAG, PAREN):
        for m in rx.finditer(text):
            s = m.group(1).upper()
            if s not in STOP and s not in found:
                found.append(s)
    pm = PRICE.search(text)
    price = float(pm.group(1)) if pm else None
    return found[:5], price  # a message listing more than 5 symbols is a recap, keep the first 5


def record(conn, source: str, symbol: str, ts: datetime, mentioned_price: float | None = None,
           kind: str = "manual", chat_id: str | None = None, message_id: str | None = None,
           check: dict | None = None, source_url: str | None = None) -> int | None:
    """Insert one tip; the same source + symbol within 24 h counts once. Returns the id or None."""
    dup = conn.execute("""SELECT 1 FROM tips WHERE source = %s AND symbol = %s
                          AND ts BETWEEN %s - interval '24 hours' AND %s + interval '24 hours'""",
                       (source, symbol, ts, ts)).fetchone()
    if dup:
        return None
    verdict = flags = None
    if check is None:
        try:
            bars, meta = _cached_fetch(symbol)
            check = stockcheck.assess([b for b in bars if b["day"] <= ts.astimezone(NY).date().isoformat()], meta)
        except Exception as e:  # noqa: BLE001 - unknown/delisted symbols are still recorded
            log.info("check failed for %s: %s", symbol, e)
            check = None
    if check:
        verdict, flags = check.get("verdict"), [f["text"] for f in check.get("flags", [])]
    from psycopg.types.json import Jsonb
    if source_url is None and kind == "telegram" and chat_id and str(chat_id).startswith("-100") and message_id:
        source_url = f"https://t.me/c/{str(chat_id)[4:]}/{message_id}"  # opens for members of the group
    row = conn.execute("""INSERT INTO tips (source, kind, chat_id, message_id, ts, symbol, mentioned_price, verdict, flags,
                                            source_url)
                          VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
                       (source, kind, chat_id, message_id, ts, symbol, mentioned_price, verdict,
                        Jsonb(flags) if flags is not None else None, source_url)).fetchone()
    return row[0]


_FETCH: dict[str, tuple[float, tuple]] = {}


def _cached_fetch(symbol: str):
    """One Yahoo request per symbol per 10 minutes (a history import mentions the same symbols often)."""
    import time as _t
    hit = _FETCH.get(symbol)
    if hit and _t.monotonic() - hit[0] < 600:
        return hit[1]
    val = stockcheck.fetch(symbol)
    _FETCH[symbol] = (_t.monotonic(), val)
    return val


def outcome(bars: list[dict], tip_ts: datetime) -> dict | None:
    """Apply the fixed outcome rule. bars: daily bars with 'day','o','h','l','c' (NY dates)."""
    tip_ny = tip_ts.astimezone(NY)
    # a tip before the open of day D can be acted on at D's open; otherwise the next session
    first = tip_ny.date().isoformat() if tip_ny.time() < time(9, 30) else None
    after = [b for b in bars if (b["day"] >= first if first else b["day"] > tip_ny.date().isoformat())]
    if not after:
        return None
    entry = float(after[0]["o"])
    if entry <= 0:
        return None
    out = {"entry_day": after[0]["day"], "entry": entry, "sessions": len(after[:20])}
    before = [b for b in bars if b["day"] < after[0]["day"] and "h" in b and "l" in b]
    out["cost_rt"], out["cost_src"] = costs.round_trip(before) if before else (costs.fallback(entry), "price_tier")
    for n, key in ((1, "r1"), (5, "r5"), (20, "r20")):
        out[key] = float(after[n - 1]["c"]) / entry - 1 if len(after) >= n else None
    win = after[:20]
    out["max_drawdown_20"] = min(float(b["l"]) for b in win) / entry - 1
    out["max_runup_20"] = max(float(b["h"]) for b in win) / entry - 1
    return out


def spy_window(spy: list[dict], entry_day: str) -> dict:
    """SPY over the same sessions as the tip: open of entry_day to the close of sessions 1/5/20."""
    after = [b for b in spy if b["day"] >= entry_day]
    if not after or after[0]["day"] != entry_day:
        return {"spy_r1": None, "spy_r5": None, "spy_r20": None}
    o = float(after[0]["o"])
    return {f"spy_r{n}": (float(after[n - 1]["c"]) / o - 1 if len(after) >= n else None) for n in (1, 5, 20)}


def update_outcomes(conn, fetch=stockcheck.fetch) -> int:
    """Fill or refresh outcomes (and the same-window SPY benchmark) for recent or incomplete tips."""
    rows = conn.execute("""SELECT id, symbol, ts FROM tips
                           WHERE ((r20 IS NULL OR spy_r20 IS NULL) AND ts > now() - interval '60 days')
                              OR (cost_rt IS NULL AND r5 IS NOT NULL AND ts > now() - interval '300 days')
                           ORDER BY symbol""").fetchall()
    cache: dict[str, list] = {}
    try:
        spy = fetch("SPY")[0]
    except Exception as e:  # noqa: BLE001
        log.info("no SPY bars: %s", e)
        spy = []
    n = 0
    for tid, sym, ts in rows:
        if sym not in cache:
            try:
                cache[sym] = fetch(sym)[0]
            except Exception as e:  # noqa: BLE001
                log.info("no bars for %s: %s", sym, e)
                cache[sym] = []
        o = outcome(cache[sym], ts) if cache[sym] else None
        if o is None:
            continue
        b = spy_window(spy, o["entry_day"]) if spy else {"spy_r1": None, "spy_r5": None, "spy_r20": None}
        conn.execute("""UPDATE tips SET entry_day = %s, entry_price = %s, r1 = %s, r5 = %s, r20 = %s,
                        max_drawdown_20 = %s, max_runup_20 = %s, spy_r1 = %s, spy_r5 = %s, spy_r20 = %s,
                        cost_rt = %s, cost_src = %s, outcome_at = now() WHERE id = %s""",
                     (o["entry_day"], o["entry"], o["r1"], o["r5"], o["r20"], o["max_drawdown_20"],
                      o["max_runup_20"], b["spy_r1"], b["spy_r5"], b["spy_r20"], o["cost_rt"], o["cost_src"], tid))
        n += 1
    return n


def leaderboard(conn) -> list[dict]:
    cols = ["source", "tips", "measured_5d", "avg_r5", "median_r5", "win_rate_5d", "excess_r5", "beat_spy_5d",
            "measured_20d", "avg_r20", "excess_r20", "worst_drawdown_20", "red_share", "last_tip"]
    rows = conn.execute("""
        SELECT source, count(*),
               count(r5), avg(r5), percentile_cont(0.5) WITHIN GROUP (ORDER BY r5),
               avg(CASE WHEN r5 > 0 THEN 1.0 WHEN r5 IS NOT NULL THEN 0.0 END),
               avg(r5 - spy_r5), avg(CASE WHEN r5 > spy_r5 THEN 1.0 WHEN r5 IS NOT NULL AND spy_r5 IS NOT NULL THEN 0.0 END),
               count(r20), avg(r20), avg(r20 - spy_r20), min(max_drawdown_20),
               avg(CASE WHEN verdict = 'red' THEN 1.0 WHEN verdict IS NOT NULL THEN 0.0 END), max(ts)
        FROM tips GROUP BY source ORDER BY count(*) DESC""").fetchall()
    per_source: dict[str, list] = {}
    for src, sym, r5, spy, cost, price in conn.execute(
            "SELECT source, symbol, r5, spy_r5, cost_rt, entry_price FROM tips").fetchall():
        per_source.setdefault(src, []).append((sym, r5, spy, cost, price))
    gates = apply_holm({r[0]: source_gate(per_source.get(r[0], [])) for r in rows})
    return [{**dict(zip(cols, r)), "gate": gates[r[0]]} for r in rows]


ROUND_TRIP_COST = 0.002  # used only when neither a range-based estimate nor an entry price exists
GATE = {"min_n": 50, "win_rate": 0.60, "alpha": 0.05}
RULE = ("success = beat SPY over the same 5 trading days after the estimated round-trip cost of that stock "
        "(bid-ask spread from daily high/low + fees)")


def _wilson_lo(k: int, n: int, z: float = 1.96) -> float:
    if n == 0:
        return 0.0
    import math
    p = k / n
    d = 1 + z * z / n
    return max(0.0, (p + z * z / (2 * n) - z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))) / d)


def _cost(row: tuple) -> float:
    cost = row[3] if len(row) > 3 else None
    if cost is not None:
        return cost
    price = row[4] if len(row) > 4 else None
    return costs.fallback(price) if price is not None else ROUND_TRIP_COST


def source_gate(rows: list[tuple]) -> dict:
    """rows: (symbol, r5, spy_r5[, cost_rt, entry_price]) for one source.

    Success = beat SPY over the same 5 sessions after that stock's own round-trip cost. What decides
    whether a source makes money is the average edge, not the hit rate (60% small wins and 40% big
    losses lose money), so 'reliable' needs ALL of: >= 50 measured tips; >= 60% success with a 95%
    Wilson lower bound above 50%; a positive average edge whose 95% lower bound is above 0 when each
    symbol counts once (one hyped stock posted 20 times can't carry a source); and, applied across
    all sources in apply_holm, an edge p-value that survives the Holm correction for testing many
    sources (with enough groups one will look good by luck)."""
    import math
    m = [(r[0], r[1] - r[2] - _cost(r), _cost(r)) for r in rows if r[1] is not None and r[2] is not None]
    n = len(m)
    k = sum(1 for _, x, _c in m if x > 0)
    by_sym: dict[str, list[float]] = {}
    for sym, x, _c in m:
        by_sym.setdefault(sym, []).append(x)
    per = [sum(v) / len(v) for v in by_sym.values()]
    mean = sum(per) / len(per) if per else None
    se = (math.sqrt(sum((x - mean) ** 2 for x in per) / (len(per) - 1)) / math.sqrt(len(per))) if len(per) > 1 else None
    if se is not None and se > 0:
        p_edge = 0.5 * math.erfc((mean / se) / math.sqrt(2))
    elif se == 0 and mean is not None:
        p_edge = 0.0 if mean > 0 else 1.0
    else:
        p_edge = None
    wins = [x for _, x, _c in m if x > 0]
    losses = [x for _, x, _c in m if x <= 0]
    win = k / n if n else None
    lo = _wilson_lo(k, n)
    passes = bool(n >= GATE["min_n"] and win >= GATE["win_rate"] and lo > 0.5 and se and mean - 1.96 * se > 0)
    if n < 30:
        status = "collecting"
    elif passes:
        status = "reliable"  # may still be downgraded by apply_holm
    elif win >= 0.55 and mean and mean > 0:
        status = "promising"
    else:
        status = "not_reliable"
    return {"n": n, "symbols": len(per), "success_rate": win, "success_lo95": lo if n else None,
            "edge_after_cost": mean, "edge_lo95": (mean - 1.96 * se) if se else None,
            "avg_win": sum(wins) / len(wins) if wins else None, "avg_loss": sum(losses) / len(losses) if losses else None,
            "profit_factor": (sum(wins) / -sum(losses)) if losses and sum(losses) < 0 else None,
            "avg_cost": sum(c for _, _x, c in m) / n if n else None,
            "p_edge": p_edge, "status": status, "rule": RULE}


def apply_holm(gates: dict[str, dict]) -> dict[str, dict]:
    """Holm-Bonferroni across every source with >= 30 measured tips. A source stays 'reliable' only
    if its edge p-value survives the correction; the number of sources tested is recorded."""
    tested = sorted(((g["p_edge"], s) for s, g in gates.items() if g["n"] >= 30 and g["p_edge"] is not None))
    m = len(tested)
    running = 0.0
    for i, (p, s) in enumerate(tested):
        running = max(running, min(1.0, (m - i) * p))
        gates[s]["p_holm"] = running
    for s, g in gates.items():
        g["sources_tested"] = m
        g.setdefault("p_holm", None)
        if g["status"] == "reliable" and not (g["p_holm"] is not None and g["p_holm"] < GATE["alpha"]):
            g["status"] = "promising"
            g["downgraded"] = "multiple_testing"
    return gates


def utcnow() -> datetime:
    return datetime.now(timezone.utc)
