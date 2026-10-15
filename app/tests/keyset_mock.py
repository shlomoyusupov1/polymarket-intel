"""Mock of Gamma /markets/keyset: serves `rows` in pages using an opaque cursor."""
import httpx


def keyset_response(request: httpx.Request, rows: list, page: int = 2) -> httpx.Response:
    start = int(request.url.params.get("after_cursor", "0") or 0)
    chunk = rows[start:start + page]
    nxt = str(start + page) if start + page < len(rows) else ""
    return httpx.Response(200, json={"markets": chunk, "next_cursor": nxt})
