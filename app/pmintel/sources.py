"""Polymarket public data sources: Gamma (market metadata) and CLOB (price history)."""
from datetime import datetime, timezone

from . import config
from .http import ApiClient


class Gamma:
    """Market discovery.

    Verified against production (2026-10-16): /markets caps pages at 100 rows and rejects
    offsets above ~2,000 with HTTP 422, so offset paging cannot see the whole universe.
    /markets/keyset returns {"markets": [...], "next_cursor": "..."} and has no depth cap,
    so it is the primary path. The cursor goes back in `after_cursor`.
    """

    def __init__(self, api: ApiClient | None = None, page_size: int = 100, max_pages: int = 5000):
        self.api = api or ApiClient(config.GAMMA_BASE)
        self.page_size = page_size
        self.max_pages = max_pages

    def iter_markets(self, **filters):
        cursor, seen_cursors, prev_first = None, set(), None
        for _ in range(self.max_pages):
            params = {**filters, "limit": self.page_size}
            if cursor:
                params["after_cursor"] = cursor
            data = self.api.get("/markets/keyset", params) or {}
            page = data.get("markets") or []
            if not page:
                return
            first = page[0].get("id")
            if prev_first is not None and first == prev_first:
                raise RuntimeError("keyset cursor was ignored by the server (same page twice)")
            prev_first = first
            yield from page
            cursor = data.get("next_cursor")
            if not cursor:
                return
            if cursor in seen_cursors:
                raise RuntimeError("keyset cursor repeated; refusing to loop")
            seen_cursors.add(cursor)
        raise RuntimeError(f"pagination exceeded {self.max_pages} pages; refusing to loop forever")


class Clob:
    def __init__(self, api: ApiClient | None = None):
        self.api = api or ApiClient(config.CLOB_BASE)

    def price_history(self, token_id: str, fidelity_min: int, start: datetime | None = None,
                      end: datetime | None = None) -> list[tuple[datetime, float]]:
        params: dict = {"market": token_id, "fidelity": fidelity_min}
        if start and end:
            params["startTs"] = int(start.timestamp())
            params["endTs"] = int(end.timestamp())
        else:
            params["interval"] = "max"
        data = self.api.get("/prices-history", params) or {}
        out = []
        for pt in data.get("history", []):
            try:
                out.append((datetime.fromtimestamp(int(pt["t"]), tz=timezone.utc), float(pt["p"])))
            except (KeyError, TypeError, ValueError):
                continue
        return out

    def book(self, token_id: str) -> dict:
        return self.api.get("/book", {"token_id": token_id}) or {}
