import json
import math
from pathlib import Path

from pmintel.stockcheck import assess


def test_real_psig_history_is_red():
    bars = json.loads((Path(__file__).parent / "psig_bars.json").read_text())["bars"]
    r = assess(bars, isin="KYG7308J1132")
    texts = " | ".join(f["text"] for f in r["flags"])
    assert r["verdict"] == "red", texts
    assert r["metrics"]["worst_day"] < -0.85  # 2026-06-26: 11.78 -> 1.09 intraday
    assert r["metrics"]["worst_day_date"] == "2026-06-26"
    assert "קיימן" in texts


def test_calm_liquid_stock_is_green():
    bars, p = [], 500.0
    for i in range(250):
        p *= 1 + 0.0004 * math.sin(i / 7)
        bars.append({"day": f"2026-{1 + i // 28:02d}-{1 + i % 28:02d}", "o": p, "h": p * 1.006, "l": p * 0.994,
                     "c": p, "v": 50_000_000})
    r = assess(bars)
    assert r["verdict"] == "green" and r["flags"] == []


def test_short_history_is_unknown():
    assert assess([{"day": "2026-01-01", "o": 1, "h": 1, "l": 1, "c": 1, "v": 1}])["verdict"] == "unknown"
