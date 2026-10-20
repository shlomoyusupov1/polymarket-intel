"""Follow anything the owner pastes: a Polymarket link, a stock symbol or a coin.

The server keeps the list (same on phone and computer). Every alert-loop pass (15 min) it re-reads
the prices — Polymarket from the public Gamma API, stocks and coins from Yahoo — and sends one
Telegram message when something moved enough since the last message:
  * Polymarket: an outcome moved MOVE_POINTS (default 5 percentage points);
  * stock / coin: the price moved MOVE_PCT (default 5%).
It only reports prices. It never buys, sells or predicts.
"""
from __future__ import annotations

import os
import re
from datetime import datetime, timedelta, timezone

import httpx
from psycopg.types.json import Jsonb

from . import config

MOVE_POINTS = float(os.environ.get("FOLLOW_MOVE_POINTS", "0.05"))
MOVE_PCT = float(os.environ.get("FOLLOW_MOVE_PCT", "0.05"))
EVERY = timedelta(minutes=14)

PM_RE = re.compile(r"polymarket\.com/(?:[a-z]{2}(?:-[a-z]{2})?/)?(?:event|market)/([A-Za-z0-9_\-]+)(?:/([A-Za-z0-9_\-]+))?", re.I)
COINS = {"BTC": "BTC-USD", "BITCOIN": "BTC-USD", "ביטקוין": "BTC-USD", "ETH": "ETH-USD", "ETHEREUM": "ETH-USD",
         "אתריום": "ETH-USD", "XRP": "XRP-USD", "ריפל": "XRP-USD", "SOL": "SOL-USD", "SOLANA": "SOL-USD",
         "DOGE": "DOGE-USD", "ADA": "ADA-USD", "BNB": "BNB-USD", "LTC": "LTC-USD", "AVAX": "AVAX-USD",
         "LINK": "LINK-USD", "DOT": "DOT-USD", "TRX": "TRX-USD", "XLM": "XLM-USD"}
SYMBOL_RE = re.compile(r"^\$?([A-Za-z]{1,6}(?:[.\-][A-Za-z]{1,4})?)$")


class FollowError(ValueError):
    """A message the owner can read (Hebrew)."""


def parse(text: str) -> tuple[str, str]:
    t = (text or "").strip()
    m = PM_RE.search(t)
    if m:
        return "polymarket", m.group(1) + ("/" + m.group(2) if m.group(2) else "")
    key = t.upper().replace("-USD", "")
    if key in COINS or t in COINS:
        return "crypto", COINS.get(key) or COINS[t]
    m = SYMBOL_RE.match(t)
    if m:
        return "stock", m.group(1).upper()
    raise FollowError("לא הבנתי מה לעקוב. הדבק קישור מ-Polymarket, או כתוב סימול כמו AMD או BTC.")


def _gamma(path: str, params: dict) -> list | dict:
    with httpx.Client(timeout=15, headers={"User-Agent": "pmintel follow"}) as c:
        r = c.get(config.GAMMA_BASE + path, params=params)
        r.raise_for_status()
        return r.json()


def _yes_price(m: dict) -> float | None:
    import json
    outs = m.get("outcomes")
    prices = m.get("outcomePrices")
    outs = json.loads(outs) if isinstance(outs, str) else (outs or [])
    prices = json.loads(prices) if isinstance(prices, str) else (prices or [])
    if not prices:
        return None
    i = next((k for k, o in enumerate(outs) if str(o).lower() == "yes"), 0)
    try:
        return float(prices[i])
    except (ValueError, IndexError):
        return None


def snapshot(kind: str, ref: str, gamma=_gamma, stock_fetch=None) -> dict:
    """{'label', 'url', 'items': [{'key', 'name', 'price'}]} — current prices only."""
    if kind == "polymarket":
        ev_slug, _, mk_slug = ref.partition("/")
        evs = gamma("/events", {"slug": ev_slug})
        if not evs:
            ms = gamma("/markets", {"slug": ev_slug})  # a link to a single market
            if not ms:
                raise FollowError("לא מצאתי את השוק הזה ב-Polymarket. בדוק שהקישור נכון.")
            evs = [{"title": ms[0].get("question"), "slug": ev_slug, "markets": ms}]
        ev = evs[0]
        ms = [m for m in ev.get("markets") or [] if not m.get("closed")]
        if mk_slug:
            ms = [m for m in ms if m.get("slug") == mk_slug] or ms
        items = [{"key": str(m["id"]), "name": m.get("groupItemTitle") or m.get("question") or "", "price": _yes_price(m)}
                 for m in ms]
        items = sorted([x for x in items if x["price"] is not None], key=lambda x: -x["price"])[:12]
        if not items:
            raise FollowError("לשוק הזה אין כרגע מחירים פתוחים (אולי הוא כבר נסגר).")
        return {"label": ev.get("title") or ev_slug, "url": f"https://polymarket.com/event/{ev_slug}", "items": items}
    if stock_fetch is None:
        from .stockcheck import fetch as stock_fetch
    try:
        bars, meta = stock_fetch(ref)
    except LookupError:
        raise FollowError(f"לא מצאתי את הסימול {ref}.") from None
    price = meta.get("regularMarketPrice") or (bars[-1]["c"] if bars else None)
    if price is None:
        raise FollowError(f"אין כרגע מחיר ל-{ref}.")
    name = meta.get("longName") or meta.get("shortName") or ref
    url = f"https://finance.yahoo.com/quote/{ref}"
    return {"label": f"{ref.replace('-USD', '')} · {name}" if name != ref else ref.replace("-USD", ""), "url": url,
            "items": [{"key": ref, "name": ref.replace("-USD", ""), "price": float(price)}]}


def add(conn, text: str, now: datetime | None = None, **kw) -> dict:
    now = now or datetime.now(timezone.utc)
    kind, ref = parse(text)
    old = conn.execute("SELECT id FROM follows WHERE kind = %s AND ref = %s AND active", (kind, ref)).fetchone()
    if old:
        return get(conn, old[0])
    snap = snapshot(kind, ref, **kw)
    items = Jsonb(snap["items"])
    rid = conn.execute("""INSERT INTO follows (kind, ref, label, url, first, baseline, state, checked_at, created_at)
                          VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id""",
                       (kind, ref, snap["label"], snap["url"], items, items, items, now, now)).fetchone()[0]
    return get(conn, rid)


COLS = ["id", "kind", "ref", "label", "url", "created_at", "first", "baseline", "state", "checked_at", "alerted_at", "last_error"]


def get(conn, fid: int) -> dict | None:
    r = conn.execute(f"SELECT {', '.join(COLS)} FROM follows WHERE id = %s", (fid,)).fetchone()
    return dict(zip(COLS, r)) if r else None


def listing(conn) -> list[dict]:
    rows = conn.execute(f"SELECT {', '.join(COLS)} FROM follows WHERE active ORDER BY created_at DESC").fetchall()
    out = []
    for r in rows:
        d = dict(zip(COLS, r))
        first = {x["key"]: x["price"] for x in d["first"]}
        for x in d["state"]:
            x["since_start"] = (x["price"] - first[x["key"]]) if x["key"] in first and x["price"] is not None else None
        out.append(d)
    return out


def remove(conn, fid: int) -> bool:
    return conn.execute("UPDATE follows SET active = false WHERE id = %s AND active", (fid,)).rowcount == 1


def _moves(kind: str, base: list[dict], now_items: list[dict]) -> list[str]:
    b = {x["key"]: x["price"] for x in base}
    lines = []
    for x in now_items:
        p0, p1 = b.get(x["key"]), x["price"]
        if p0 is None or p1 is None:
            continue
        if kind == "polymarket":
            d = p1 - p0
            if abs(d) >= MOVE_POINTS:
                lines.append(f"{x['name']}: מ-{p0 * 100:.0f}% ל-{p1 * 100:.0f}% ({'עלה' if d > 0 else 'ירד'} {abs(d) * 100:.0f} נקודות)")
        elif p0 > 0:
            d = p1 / p0 - 1
            if abs(d) >= MOVE_PCT:
                lines.append(f"{x['name']}: מ-{p0:,.2f}$ ל-{p1:,.2f}$ ({'עלה' if d > 0 else 'ירד'} {abs(d) * 100:.1f}%)")
    return lines


def check(conn, now: datetime | None = None, **kw) -> list[str]:
    """Refresh every follow that is due; return one Telegram message per follow that moved."""
    now = now or datetime.now(timezone.utc)
    msgs = []
    for d in listing(conn):
        if d["checked_at"] and now - d["checked_at"] < EVERY:
            continue
        try:
            snap = snapshot(d["kind"], d["ref"], **kw)
        except Exception as e:  # noqa: BLE001 - one bad follow never stops the others
            conn.execute("UPDATE follows SET checked_at = %s, last_error = %s WHERE id = %s", (now, str(e)[:200], d["id"]))
            continue
        items = snap["items"]
        lines = _moves(d["kind"], d["baseline"], items)
        conn.execute("UPDATE follows SET state = %s, checked_at = %s, last_error = NULL WHERE id = %s",
                     (Jsonb(items), now, d["id"]))
        if lines:
            msgs.append("👀 " + d["label"] + "\n" + "\n".join(lines) + ("\n" + d["url"] if d["url"] else ""))
            conn.execute("UPDATE follows SET baseline = %s, alerted_at = %s WHERE id = %s", (Jsonb(items), now, d["id"]))
    return msgs
