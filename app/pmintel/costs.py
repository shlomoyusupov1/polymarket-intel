"""Trading-cost estimates from daily bars only (no paid quote data).

The bid-ask spread is estimated with the Corwin & Schultz (2012) high-low estimator: over two days
the high/low range grows with volatility proportionally to time but the spread does not, so the two
can be separated. Two-day estimates below zero are set to zero, then averaged over the window
(the authors' recommendation). A round trip pays the whole spread (half in, half out) plus a fee.
"""
from __future__ import annotations

import math

FEE_RT = 0.001        # broker fee + regulatory fees, both sides, as a share of the position
MIN_SPREAD = 0.0005   # even SPY costs something
MAX_COST = 0.25       # beyond this the estimate is meaningless; the stock is effectively untradable
K = 3 - 2 * math.sqrt(2)


def cs_spread(bars: list[dict]) -> float | None:
    """Average Corwin-Schultz spread over consecutive bar pairs. bars: dicts with 'h' and 'l'."""
    est = []
    for a, b in zip(bars, bars[1:]):
        h1, l1, h2, l2 = float(a["h"]), float(a["l"]), float(b["h"]), float(b["l"])
        if min(h1, l1, h2, l2) <= 0 or h1 < l1 or h2 < l2:
            continue
        beta = math.log(h1 / l1) ** 2 + math.log(h2 / l2) ** 2
        gamma = math.log(max(h1, h2) / min(l1, l2)) ** 2
        alpha = (math.sqrt(2 * beta) - math.sqrt(beta)) / K - math.sqrt(gamma / K)
        s = 2 * (math.exp(alpha) - 1) / (1 + math.exp(alpha))
        est.append(max(0.0, s))
    return sum(est) / len(est) if est else None


def round_trip(bars_before: list[dict], window: int = 20) -> tuple[float, str]:
    """Estimated round-trip cost using the `window` sessions before entry. Returns (cost, source)."""
    w = bars_before[-window:]
    s = cs_spread(w) if len(w) >= 5 else None
    if s is not None:
        return min(MAX_COST, max(MIN_SPREAD, s) + FEE_RT), "corwin_schultz"
    price = float(bars_before[-1]["c"]) if bars_before else None
    return fallback(price), "price_tier"


def fallback(price: float | None) -> float:
    """Conservative cost when the range estimator has no data: cheap stocks trade with wide spreads."""
    if price is None or price < 1:
        return 0.06
    if price < 5:
        return 0.02
    return 0.002
