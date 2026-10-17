"""Polymarket Data API v2 (https://data-api.polymarket.com/v2, public, no key).

Used as an independent second source:
  - /v2/prices-history  fallback history when the CLOB rejects a token, and a coverage cross-check
  - /v2/resolutions     cross-check of our resolved outcome (derived from Gamma outcomePrices)
  - /v2/status          freshness of the Data API itself

Response field names below were taken from the published OpenAPI spec where available; where the
spec was not readable, parsing is deliberately tolerant and every check also stores the raw keys it
saw, so a schema mismatch shows up in the integration report instead of being silently guessed.
"""
from __future__ import annotations

from datetime import datetime, timezone

from .http import ApiClient

BASE = "https://data-api.polymarket.com"


def _points(obj) -> list[tuple[datetime, float]]:
    """Extract (time, price) pairs from a history payload of unknown exact shape."""
    seq = obj
    if isinstance(obj, dict):
        for k in ("data", "history", "points", "prices"):
            if isinstance(obj.get(k), list):
                seq = obj[k]
                break
            if isinstance(obj.get(k), dict):
                return _points(obj[k])
    out = []
    for p in seq if isinstance(seq, list) else []:
        if not isinstance(p, dict):
            continue
        t = next((p[k] for k in ("t", "ts", "timestamp", "time", "bucket_start") if k in p), None)
        v = next((p[k] for k in ("p", "price", "value", "close", "c") if k in p), None)
        try:
            t = float(t)
            out.append((datetime.fromtimestamp(t / 1000 if t > 1e11 else t, tz=timezone.utc), float(v)))
        except (TypeError, ValueError):
            continue
    return out


class DataApi:
    def __init__(self, api: ApiClient | None = None):
        self.api = api or ApiClient(BASE, min_interval=0.2)

    def status(self) -> dict:
        return self.api.get("/v2/status") or {}

    def price_history(self, token_id: str, start: datetime | None = None, end: datetime | None = None,
                      bucket_seconds: int = 3600, max_pages: int = 20) -> list[tuple[datetime, float]]:
        params: dict = {"token_id": token_id, "bucket_seconds": bucket_seconds, "limit": 10000}
        if start and end:
            params.update(start=int(start.timestamp()), end=int(end.timestamp()))
        else:
            params["interval"] = "max"
        out: list = []
        for _ in range(max_pages):
            page = self.api.get("/v2/prices-history", params) or {}
            out += _points(page)
            cur = (page.get("pagination") or {}).get("next_cursor") or page.get("next_cursor") if isinstance(page, dict) else None
            if not cur:
                break
            params = {"token_id": token_id, "cursor": cur}
        return sorted(set(out))

    def resolutions(self, conditions: list[str]) -> list[dict]:
        out = []
        for i in range(0, len(conditions), 20):
            page = self.api.get("/v2/resolutions", {"condition": ",".join(conditions[i:i + 20])}) or {}
            out += page.get("data", []) if isinstance(page, dict) else page
        return out


WINNER_KEYS = ("winning_outcome", "winner", "resolved_outcome", "outcome", "winning_outcome_name")
COND_KEYS = ("condition_id", "conditionId", "condition")


def compare_resolutions(ours: dict[str, str], theirs: list[dict]) -> dict:
    """ours: condition_id -> our resolved outcome. Returns agreement counts + keys seen."""
    agree = disagree = unknown = 0
    keys: set[str] = set()
    examples = []
    for r in theirs:
        if not isinstance(r, dict):
            continue
        keys.update(r)
        cid = next((str(r[k]) for k in COND_KEYS if r.get(k)), None)
        win = next((str(r[k]) for k in WINNER_KEYS if r.get(k) not in (None, "")), None)
        if cid not in ours or win is None:
            unknown += 1
            continue
        if win.strip().lower() == ours[cid].strip().lower():
            agree += 1
        else:
            disagree += 1
            examples.append({"condition": cid, "ours": ours[cid], "data_api": win})
    return {"agree": agree, "disagree": disagree, "unrecognised": unknown, "keys_seen": sorted(keys),
            "disagreements": examples[:5]}
