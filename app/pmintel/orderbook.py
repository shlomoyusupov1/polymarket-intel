"""Executable-price metrics from a CLOB order book.

Historical CLOB data has prices but no depth, so executability can't be backtested.
We therefore record live books going forward; every row is something we observed.
All metrics are for the market's reference outcome token (YES when it exists).
"""


def _levels(side) -> list[tuple[float, float]]:
    out = []
    for lv in side or []:
        try:
            p, s = float(lv["price"]), float(lv["size"])
        except (KeyError, TypeError, ValueError):
            continue
        if 0 < p < 1 and s > 0:
            out.append((p, s))
    return out


def vwap_for_notional(levels: list[tuple[float, float]], usd: float) -> float | None:
    """Average price paid (or received) to trade `usd` of notional walking the book.
    None if the book is too thin to fill the whole amount."""
    remaining, shares = usd, 0.0
    for price, size in levels:
        take = min(remaining, price * size)
        shares += take / price
        remaining -= take
        if remaining <= 1e-9:
            return usd / shares
    return None


def depth_within(levels: list[tuple[float, float]], best: float, cents: float) -> float:
    return sum(p * s for p, s in levels if abs(p - best) <= cents + 1e-9)


def summarize(book: dict) -> dict | None:
    bids = sorted(_levels(book.get("bids")), key=lambda x: -x[0])  # best (highest) first
    asks = sorted(_levels(book.get("asks")), key=lambda x: x[0])   # best (lowest) first
    if not bids and not asks:
        return None
    bb = bids[0][0] if bids else None
    ba = asks[0][0] if asks else None
    return {
        # crossed/locked or one-sided books are recorded but flagged invalid
        "valid": bb is not None and ba is not None and bb < ba,
        "n_bids": len(bids),
        "n_asks": len(asks),
        "best_bid": bb,
        "best_ask": ba,
        "spread": (ba - bb) if bb is not None and ba is not None else None,
        "bid_depth_5c": depth_within(bids, bb, 0.05) if bids else 0.0,
        "ask_depth_5c": depth_within(asks, ba, 0.05) if asks else 0.0,
        "buy_vwap_100": vwap_for_notional(asks, 100),
        "buy_vwap_1000": vwap_for_notional(asks, 1000),
        "sell_vwap_100": vwap_for_notional(bids, 100),
        "sell_vwap_1000": vwap_for_notional(bids, 1000),
        "levels": {"bids": bids[:10], "asks": asks[:10]},
    }
