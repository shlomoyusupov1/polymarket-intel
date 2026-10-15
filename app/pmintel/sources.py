"""Polymarket public data sources: Gamma (market metadata) and CLOB (price history)."""
from datetime import datetime, timezone

from . import config
from .http import ApiClient


class Gamma:
    def __init__(self, api: ApiClient | None = None, page_size: int = 500, max_pages: int = 2000):
        self.api = api or ApiClient(config.GAMMA_BASE)
        self.page_size = page_size
        self.max_pages = max_pages

    def iter_markets(self, **filters):
        """Paginate /markets until an empty page. Does not assume the server honours page_size."""
        offset = 0
        for _ in range(self.max_pages):
            page = self.api.get("/markets", {**filters, "limit": self.page_size, "offset": offset})
            if not page:
                return
            yield from page
            offset += len(page)
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
