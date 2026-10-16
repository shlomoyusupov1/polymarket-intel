"""Red-flag checker for a single US-listed stock ("is this a pump?").

Pure function `assess(bars, meta, isin)` + a Yahoo fetch. It flags the patterns seen in
ramp-and-dump schemes (tiny liquidity, sudden volume spikes, parabolic runs, prior one-day
collapses, offshore shells). It is a warning light, not a forecast and not advice.
"""
from __future__ import annotations

import re
from datetime import datetime
from statistics import median
from zoneinfo import ZoneInfo

import httpx

YAHOO = "https://query1.finance.yahoo.com/v8/finance/chart/"
SYMBOL_RE = re.compile(r"^[A-Z][A-Z0-9.\-]{0,9}$")
OFFSHORE_ISIN = {"KY": "איי קיימן", "VG": "איי הבתולה הבריטיים", "BM": "ברמודה", "MH": "איי מרשל"}


def fetch(symbol: str, client: httpx.Client | None = None) -> tuple[list[dict], dict]:
    client = client or httpx.Client(timeout=20, headers={"User-Agent": "Mozilla/5.0 (pmintel research)"})
    r = client.get(YAHOO + symbol, params={"range": "1y", "interval": "1d"})
    if r.status_code == 404:
        raise LookupError("symbol not found")
    r.raise_for_status()
    res = (r.json().get("chart") or {}).get("result")
    if not res:
        raise LookupError("symbol not found")
    res = res[0]
    meta = res.get("meta") or {}
    tz = ZoneInfo(meta.get("exchangeTimezoneName") or "America/New_York")
    q = res["indicators"]["quote"][0]
    bars = []
    for i, ts in enumerate(res.get("timestamp") or []):
        o, h, l, c, v = (q[k][i] for k in ("open", "high", "low", "close", "volume"))
        if None in (o, h, l, c, v) or c <= 0:
            continue
        bars.append({"day": datetime.fromtimestamp(ts, tz).date().isoformat(), "o": o, "h": h, "l": l, "c": c, "v": v})
    return bars, meta


def assess(bars: list[dict], meta: dict | None = None, isin: str | None = None) -> dict:
    meta = meta or {}
    if len(bars) < 30:
        return {"verdict": "unknown", "flags": [{"level": "yellow", "text": "פחות מ-30 ימי מסחר — אין מספיק היסטוריה"}],
                "metrics": {}, "bars": bars}
    c = [b["c"] for b in bars]
    v = [b["v"] for b in bars]
    dollar = [b["c"] * b["v"] for b in bars]
    last = c[-1]
    med_dollar_20 = median(dollar[-20:])
    med_vol_120 = median(v[-120:]) or 1
    max_vol_20 = max(v[-20:])
    spike = max_vol_20 / med_vol_120
    worst_day, worst_date = 0.0, None
    for i in range(1, len(bars)):
        # close-to-close and intraday open-to-low, whichever is worse
        d = min(c[i] / c[i - 1] - 1, bars[i]["l"] / bars[i]["o"] - 1)
        if d < worst_day:
            worst_day, worst_date = d, bars[i]["day"]
    ret = lambda n: c[-1] / c[-1 - n] - 1 if len(c) > n else None  # noqa: E731
    r20, r60 = ret(20), ret(60)
    low_60 = min(c[-60:])
    from_low = last / low_60 - 1
    sma50 = sum(c[-50:]) / min(50, len(c))
    avg_range = sum((b["h"] - b["l"]) / b["c"] for b in bars[-20:]) / 20

    flags = []
    add = lambda level, text: flags.append({"level": level, "text": text})  # noqa: E731
    if med_dollar_20 < 1_000_000:
        add("red", f"נזילות זעירה: מחזור יומי חציוני ${med_dollar_20:,.0f} — קל להזיז את המחיר, קשה למכור")
    elif med_dollar_20 < 20_000_000:
        add("yellow", f"נזילות נמוכה: מחזור יומי חציוני ${med_dollar_20:,.0f}")
    if worst_day <= -0.5:
        add("red", f"קריסה של {worst_day:.0%} ביום אחד ({worst_date})")
    elif worst_day <= -0.25:
        add("yellow", f"ירידה של {worst_day:.0%} ביום אחד ({worst_date})")
    if spike >= 10:
        add("red", f"קפיצת מחזור חריגה: פי {spike:.0f} מהרגיל בחודש האחרון")
    elif spike >= 4:
        add("yellow", f"מחזור גבוה מהרגיל: פי {spike:.1f}")
    if (r20 is not None and r20 >= 1.0) or from_low >= 1.5:
        add("red", f"עלייה פרבולית: {from_low:+.0%} מהשפל של 60 יום")
    elif (r20 is not None and r20 >= 0.4) or from_low >= 0.6:
        add("yellow", f"עלייה חדה: {from_low:+.0%} מהשפל של 60 יום")
    if last < 1:
        add("red", f"מניית פני: ${last:.2f}")
    elif last < 5:
        add("yellow", f"מחיר נמוך: ${last:.2f}")
    if avg_range >= 0.10:
        add("yellow", f"תנודתיות קיצונית: טווח יומי ממוצע {avg_range:.0%}")
    if isin and isin[:2].upper() in OFFSHORE_ISIN:
        add("yellow", f"רשומה ב{OFFSHORE_ISIN[isin[:2].upper()]} (ISIN {isin[:2].upper()})")
    reds = sum(f["level"] == "red" for f in flags)
    verdict = "red" if reds >= 2 else "yellow" if reds == 1 or flags else "green"
    return {"verdict": verdict, "flags": flags, "bars": [{"day": b["day"], "c": b["c"], "v": b["v"]} for b in bars],
            "metrics": {"last": last, "median_dollar_volume_20d": med_dollar_20, "volume_spike": spike,
                        "worst_day": worst_day, "worst_day_date": worst_date, "return_20d": r20, "return_60d": r60,
                        "from_60d_low": from_low, "vs_sma50": last / sma50 - 1, "avg_range_20d": avg_range},
            "name": meta.get("longName") or meta.get("shortName"), "exchange": meta.get("fullExchangeName"),
            "currency": meta.get("currency"),
            "disclaimer": "נורת אזהרה בלבד — לא תחזית ולא ייעוץ. ירוק לא אומר שהמניה טובה."}
