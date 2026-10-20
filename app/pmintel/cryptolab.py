"""Crypto lab: the same pre-declared strategies and the same out-of-sample method as the stock lab
(stocklab.py), on large coins, against simply holding bitcoin.

What differs from stocks, and why:
  * coins trade 24/7: one bar per calendar day (UTC), so a "year" is 365 bars and holds are in days;
  * a daily bar closes at 00:00 UTC, the same instant the next one opens. A signal can't be acted on
    at that instant, so entry is one full day later (open of signal day + 2) — in the backtest and in
    forward tracking alike;
  * the cost is a fixed round trip (CRYPTO_COST_RT, default 1%): what an Israeli retail app or an
    exchange with a spread charges, not the exchange's posted maker fee;
  * benchmark = holding bitcoin over the identical days; baseline = buying any coin of the list on
    any day with the same hold, in the same year (survivorship control, as for stocks);
  * the turn-of-month strategy is left out (it rests on stock-market payroll and fund flows).

Nothing here touches a wallet, a key or an exchange account. Prices only.

  docker compose run --rm cryptolab
"""
from __future__ import annotations

import os

from . import stocklab as L

UNIVERSE = ("BTC-USD ETH-USD XRP-USD BNB-USD SOL-USD DOGE-USD ADA-USD TRX-USD AVAX-USD LINK-USD "
            "DOT-USD LTC-USD BCH-USD XLM-USD ATOM-USD ETC-USD HBAR-USD NEAR-USD FIL-USD XMR-USD").split()

CRYPTO = L.Market(
    key="crypto", report_kind="cryptolab_v1", job="cryptolab", paper_job="crypto_paper",
    universe=tuple(UNIVERSE), bench="BTC-USD", bench_label="ביטקוין",
    holds=(7, 14, 30),
    strategies=tuple(k for k in L.STRATEGIES if k != "turn_of_month"),
    year=365, month=30, lag=1,
    fixed_cost=float(os.environ.get("CRYPTO_COST_RT", "0.01")),
    min_bars=400, prefix="crypto:")


def run(conn=None, fetcher=L.fetch, symbols=None, pause: float = 0.4) -> dict:
    return L.run(conn, fetcher, symbols, pause, m=CRYPTO)


if __name__ == "__main__":
    L.main(CRYPTO)
