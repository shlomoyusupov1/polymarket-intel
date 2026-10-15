import os


def _int(name: str, default: int) -> int:
    return int(os.environ.get(name, default))


def _float(name: str, default: float) -> float:
    return float(os.environ.get(name, default))


DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://pmintel:pmintel@localhost:5432/pmintel")
GAMMA_BASE = os.environ.get("GAMMA_BASE", "https://gamma-api.polymarket.com")
CLOB_BASE = os.environ.get("CLOB_BASE", "https://clob.polymarket.com")

# Collector
COLLECT_INTERVAL_MIN = _int("COLLECT_INTERVAL_MIN", 15)
# Only markets with at least this liquidity get a price snapshot (metadata is stored for all).
SNAPSHOT_MIN_LIQUIDITY = _float("SNAPSHOT_MIN_LIQUIDITY", 1000)
RESEARCH_HOUR_UTC = _int("RESEARCH_HOUR_UTC", 3)
# Live order books for the N most-traded markets each cycle (executable prices + depth)
ORDERBOOK_TOP_N = _int("ORDERBOOK_TOP_N", 50)

# Backfill
BACKFILL_SINCE = os.environ.get("BACKFILL_SINCE", "2024-01-01")
BACKFILL_MIN_VOLUME = _float("BACKFILL_MIN_VOLUME", 10000)
BACKFILL_FIDELITY_MIN = _int("BACKFILL_FIDELITY_MIN", 360)

# Research cost assumptions (conservative defaults; history prices are not executable prices)
RESEARCH_SLIPPAGE = _float("RESEARCH_SLIPPAGE", 0.01)  # price units per share
RESEARCH_FEE = _float("RESEARCH_FEE", 0.0)  # fraction of stake
