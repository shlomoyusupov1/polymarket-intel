"""Turn raw Gamma market objects into normalized records.

Key correctness points (fixes from the V1 prototype):
- outcomes / outcomePrices / clobTokenIds arrive as JSON-encoded strings.
- The "reference" outcome is the one labelled "Yes" when present; otherwise the first
  outcome, and its label is kept so the UI never mislabels it as YES.
- Links use the *event* slug (polymarket.com/event/<event-slug>), not the market slug.
- A closed market counts as resolved only if exactly one outcome settled at ~1.
"""
import json
from datetime import datetime, timezone


def _list(v) -> list:
    if isinstance(v, list):
        return v
    if isinstance(v, str) and v.strip():
        try:
            out = json.loads(v)
            return out if isinstance(out, list) else []
        except json.JSONDecodeError:
            return []
    return []


def _float(v):
    try:
        if v is None or v == "":
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def _bool(v):
    if v is None:
        return None
    if isinstance(v, bool):
        return v
    return str(v).strip().lower() in ("true", "1", "yes")


def _ts(v):
    if not v:
        return None
    s = str(v).strip().replace(" ", "T", 1)
    if s.endswith("+00"):
        s += ":00"
    s = s.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def resolved_outcome(outcomes: list, prices: list) -> str | None:
    if not outcomes or len(outcomes) != len(prices) or any(p is None for p in prices):
        return None
    winners = [i for i, p in enumerate(prices) if p >= 0.99]
    losers = [i for i, p in enumerate(prices) if p <= 0.01]
    if len(winners) == 1 and len(losers) == len(prices) - 1:
        return str(outcomes[winners[0]])
    return None  # unresolved, 50/50 split, or ambiguous


def rules_hash(m: dict) -> str:
    """Version of the resolution terms: any change to description, resolution source, end date or
    outcomes yields a new hash (history kept in market_rules)."""
    import hashlib
    import json
    key = json.dumps([m.get("description") or "", m.get("resolutionSource") or "", m.get("endDate") or "",
                      [str(o) for o in _list(m.get("outcomes"))]], ensure_ascii=False)
    return hashlib.sha256(key.encode()).hexdigest()[:16]


def normalize_market(m: dict) -> dict:
    outcomes = [str(o) for o in _list(m.get("outcomes"))]
    prices = [_float(p) for p in _list(m.get("outcomePrices"))]
    tokens = [str(t) for t in _list(m.get("clobTokenIds"))]

    yes_idx = next((i for i, o in enumerate(outcomes) if o.strip().lower() == "yes"), None)
    ref_idx = yes_idx if yes_idx is not None else (0 if outcomes else None)
    ref_price = prices[ref_idx] if ref_idx is not None and ref_idx < len(prices) else None

    events = m.get("events") or []
    ev = events[0] if events and isinstance(events[0], dict) else {}
    event_slug = ev.get("slug")
    slug = m.get("slug")

    # Gamma's bestBid/bestAsk/spread refer to the first outcome token.
    first = ref_idx == 0
    closed = bool(m.get("closed"))

    return {
        "id": str(m["id"]),
        "condition_id": m.get("conditionId"),
        "event_id": str(ev["id"]) if ev.get("id") is not None else None,
        "event_slug": event_slug,
        "slug": slug,
        "question": m.get("question") or "",
        "category": m.get("category") or ev.get("category"),
        "outcomes": outcomes,
        "token_ids": tokens,
        "ref_index": ref_idx,
        "ref_outcome": outcomes[ref_idx] if ref_idx is not None else None,
        "ref_price": ref_price,
        "best_bid": _float(m.get("bestBid")) if first else None,
        "best_ask": _float(m.get("bestAsk")) if first else None,
        "spread": _float(m.get("spread")) if first else None,
        "last_trade": _float(m.get("lastTradePrice")) if first else None,
        "volume": _float(m.get("volumeNum")) if m.get("volumeNum") is not None else _float(m.get("volume")),
        "volume_24h": _float(m.get("volume24hr")),
        "liquidity": _float(m.get("liquidityNum")) if m.get("liquidityNum") is not None else _float(m.get("liquidity")),
        "start_date": _ts(m.get("startDate")),
        "end_date": _ts(m.get("endDate")),
        "closed_time": _ts(m.get("closedTime")),
        "active": bool(m.get("active")),
        "closed": closed,
        "neg_risk": bool(m.get("negRisk")),
        "resolution_status": m.get("umaResolutionStatus"),
        "resolved_outcome": resolved_outcome(outcomes, prices) if closed else None,
        "resolution_source": m.get("resolutionSource"),
        "description": m.get("description"),
        "url": f"https://polymarket.com/event/{event_slug or slug}" if (event_slug or slug) else None,
        "outcome_prices": prices if len(prices) == len(outcomes) else None,
        "accepting_orders": _bool(m.get("acceptingOrders")),
        "enable_order_book": _bool(m.get("enableOrderBook")),
        "archived": _bool(m.get("archived")),
        "neg_risk_market_id": m.get("negRiskMarketID") or None,
        "fees_enabled": _bool(m.get("feesEnabled")),
        # Polymarket's own reported changes of the first outcome's price (source: Gamma)
        "one_day_change": _float(m.get("oneDayPriceChange")) if first else None,
        "one_week_change": _float(m.get("oneWeekPriceChange")) if first else None,
        "volume_1wk": _float(m.get("volume1wk")),
        "group_item_title": m.get("groupItemTitle") or None,
        # the catch-all "Other" leg of an augmented negRisk event: its presence means new outcomes can appear
        "neg_risk_other": _bool(m.get("negRiskOther")),
        # provenance: Gamma's own last-update time (source time) and the resolution-terms version
        "source_updated_at": _ts(m.get("updatedAt")),
        "rules_hash": rules_hash(m),
    }
