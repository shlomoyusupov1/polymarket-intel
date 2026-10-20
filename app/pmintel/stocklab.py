"""Strategy lab: test simple, pre-declared stock strategies on years of daily data, out of sample.

Every strategy and its small parameter grid are fixed here, before looking at results. For each
strategy:
  * a signal is computed from data up to day i's close; entry is day i+1's open; exit is the close
    H sessions after entry (H is part of the grid);
  * each trade pays that stock's estimated round-trip cost (costs.py, from the 20 sessions before);
  * a symbol can't re-enter while a trade is open (no overlapping copies of the same bet);
  * walk-forward by calendar year: the parameters are chosen on all earlier years only, then scored
    on the next year; the out-of-sample (OOS) trades of every test year are pooled;
  * success = beat SPY over the same sessions after cost (the owner's 60% target);
  * because the universe is today's large companies (survivors), any long trade in it looks good
    against SPY. So every trade is also compared with the BASELINE: the average result of buying any
    stock of the universe on any day with the same hold, in the same year. A strategy is worth
    something only if it beats that baseline;
  * the CI clusters trades by entry week (signals in the same week share the market), and the
    p-values are Holm-corrected across strategies.

A backtest can only promote a strategy to 'candidate_for_paper'. It earns trust only after forward
paper tracking, which starts from the signals listed under signals_today.
"""
from __future__ import annotations

import json
import logging
import math
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from statistics import median
from zoneinfo import ZoneInfo

import httpx

from . import costs

log = logging.getLogger("pmintel.stocklab")
YAHOO = "https://query1.finance.yahoo.com/v8/finance/chart/"

# Fixed in advance: liquid US large caps across sectors + the owner's watchlist. Known bias: these are
# today's survivors; the baseline comparison below is what controls for it.
UNIVERSE = ("AAPL MSFT NVDA AMZN GOOGL META TSLA AVGO JPM V MA UNH HD PG JNJ XOM CVX KO PEP COST WMT MRK "
            "ABBV LLY BAC WFC GS MS CAT DE BA GE HON UPS NKE MCD SBUX DIS NFLX ADBE CRM ORCL INTC AMD QCOM "
            "TXN CSCO IBM PYPL MU AMAT LRCX F GM T VZ PFE TMO PLTR ANET SHOP SOFI AFRM UBER ABNB").split()
BENCH = "SPY"
HOLDS = (5, 10, 20)
FIRST_TEST_YEARS = 3          # years of history before the first out-of-sample year
MIN_TRAIN_TRADES = 30
GATE = {"min_n": 50, "win_rate": 0.60, "alpha": 0.05}

STRATEGIES = {
    "breakout_volume": {
        "label": "פריצה עם מחזור גבוה",
        "rule": "סגירה מעל השיא של L הימים הקודמים, מחזור דולרי פי m מהחציון של 50 יום, ומעל ממוצע 50",
        "grid": [{"L": L, "m": m} for L in (20, 55) for m in (1.5, 2.5)]},
    "dip_exhaustion": {
        "label": "קנייה בירידה כשהמוכרים נחלשים",
        "rule": "במגמת עלייה (מעל ממוצע 200), ירידה של d% בשלושה ימים, היום המחזור נמוך מימי הירידה והסגירה מעל הפתיחה",
        "grid": [{"d": d} for d in (0.05, 0.08)]},
    "ma_stack": {
        "label": "ממוצעים נעים מסודרים",
        "rule": "היום הראשון שבו סגירה > ממוצע 10 > ממוצע 20 > ממוצע 50 (ובגרסה השנייה גם ממוצע 50 > 200)",
        "grid": [{"long": f} for f in (False, True)]},
    "near_52w_high": {
        "label": "קרוב לשיא השנתי",
        "rule": "היום הראשון שהסגירה בטווח g% מהשיא של 52 השבועות",
        "grid": [{"g": g} for g in (0.02, 0.05)]},
    "momentum_12_1": {
        "label": "המניות שעלו הכי הרבה בשנה",
        "rule": "ביום המסחר הראשון בחודש: המניות ב-q% העליונים של הרשימה לפי התשואה של 12 החודשים האחרונים, בלי החודש האחרון",
        "grid": [{"q": q} for q in (0.1, 0.2)]},
    "low_volatility": {
        "label": "המניות הכי רגועות",
        "rule": "ביום המסחר הראשון בחודש: המניות ב-q% הכי רגועות של הרשימה לפי התנודתיות של 60 יום",
        "grid": [{"q": q} for q in (0.1, 0.2)]},
    "turn_of_month": {
        "label": "קנייה בסוף החודש",
        "rule": "קונים שלושה ימי מסחר לפני סוף החודש (ובגרסה השנייה רק מעל ממוצע 200)",
        "grid": [{"trend": t} for t in (False, True)]},
    "rsi2_pullback": {
        "label": "ירידה קצרה בתוך מגמת עלייה",
        "rule": "RSI של יומיים מתחת ל-t ומעל ממוצע 200",
        "grid": [{"t": t} for t in (5, 10)]},
    # ---- candlestick patterns (one bar = one day; "down move" = close below the 20-day average and below 5 days ago)
    "candle_hammer": {
        "label": "נר פטיש",
        "rule": "אחרי ירידה: גוף קטן למעלה, פתיל תחתון ארוך פי k מהגוף, כמעט בלי פתיל עליון",
        "grid": [{"k": k} for k in (2, 3)]},
    "candle_bull_engulf": {
        "label": "נר בולע עולה",
        "rule": "אחרי ירידה: נר אדום ואחריו נר ירוק שהגוף שלו מכסה את כל הגוף של האדום (ובגרסה השנייה רק מעל ממוצע 200)",
        "grid": [{"trend": t} for t in (False, True)]},
    "candle_doji": {
        "label": "נר דוג'י אחרי ירידה",
        "rule": "אחרי ירידה: פתיחה וסגירה כמעט באותו מחיר (גוף קטן מ-d מטווח היום)",
        "grid": [{"d": d} for d in (0.05, 0.1)]},
    "candle_morning_star": {
        "label": "כוכב הבוקר (שלושה נרות)",
        "rule": "אחרי ירידה: נר אדום גדול, נר קטן, ואז נר ירוק שנסגר מעל m מהגוף של האדום",
        "grid": [{"m": m} for m in (0.5, 0.75)]},
    "candle_three_soldiers": {
        "label": "שלושה חיילים לבנים",
        "rule": "שלושה נרות ירוקים ברצף, כל אחד נסגר גבוה מהקודם וקרוב לשיא היום (פתיל עליון עד u מהטווח)",
        "grid": [{"u": u} for u in (0.2, 0.35)]},
    "candle_shooting_star": {
        "label": "נר כוכב נופל (אזהרה)", "side": -1,
        "rule": "אחרי עלייה: גוף קטן למטה ופתיל עליון ארוך פי k מהגוף. נבדק כאזהרה: האם אחריו המניה מפגרת אחרי המדד",
        "grid": [{"k": k} for k in (2, 3)]},
    "candle_bear_engulf": {
        "label": "נר בולע יורד (אזהרה)", "side": -1,
        "rule": "אחרי עלייה: נר ירוק ואחריו נר אדום שמכסה את כל הגוף שלו. נבדק כאזהרה: האם אחריו המניה מפגרת אחרי המדד",
        "grid": [{"trend": t} for t in (False, True)]},
}
# Warning patterns (side -1) are scored as "the asset lagged the benchmark after the signal, after cost":
# what you'd gain by not buying / selling what you hold. No short selling is assumed or placed.


def side(name: str) -> int:
    return STRATEGIES[name].get("side", 1)



@dataclass(frozen=True)
class Market:
    """Everything that differs between asset classes; the method itself is shared."""
    key: str                      # 'stocks' | 'crypto'
    report_kind: str              # research_reports.kind
    job: str                      # job_runs.job for the backtest
    paper_job: str                # job_runs.job for forward tracking
    universe: tuple
    bench: str
    bench_label: str
    holds: tuple                  # in bars (trading sessions for stocks, days for crypto)
    strategies: tuple
    year: int = 252               # bars in a year
    month: int = 21               # bars in a month
    lag: int = 0                  # extra bars between the signal bar and the entry bar
    fixed_cost: float | None = None   # round trip; None = per-stock estimate (costs.py)
    min_bars: int = 300
    prefix: str = ""              # strategy-name prefix in lab_signals


STOCKS = Market("stocks", "stocklab_v1", "stocklab", "lab_paper", tuple(UNIVERSE), BENCH, "SPY", HOLDS, tuple(STRATEGIES))


# ---------------------------------------------------------------- data
def fetch(symbol: str, client: httpx.Client | None = None, range_: str = "10y") -> list[dict]:
    """Daily bars, split- and dividend-adjusted (o/h/l/c scaled by adjclose/close); 'dv' is the raw
    dollar volume (close x volume), which splits don't distort."""
    client = client or httpx.Client(timeout=30, headers={"User-Agent": "Mozilla/5.0 (pmintel research)"})
    r = client.get(YAHOO + symbol, params={"range": range_, "interval": "1d", "events": "div,split"})
    r.raise_for_status()
    res = ((r.json().get("chart") or {}).get("result") or [None])[0]
    if not res:
        raise LookupError(symbol)
    tz = ZoneInfo((res.get("meta") or {}).get("exchangeTimezoneName") or "America/New_York")
    q = res["indicators"]["quote"][0]
    adj = ((res["indicators"].get("adjclose") or [{}])[0]).get("adjclose") or q["close"]
    out = []
    for i, ts in enumerate(res.get("timestamp") or []):
        o, h, l, c, v, a = q["open"][i], q["high"][i], q["low"][i], q["close"][i], q["volume"][i], adj[i]
        if None in (o, h, l, c, v, a) or c <= 0 or o <= 0:
            continue
        f = a / c
        out.append({"day": datetime.fromtimestamp(ts, tz).date().isoformat(),
                    "o": o * f, "h": h * f, "l": l * f, "c": a, "dv": c * v})
    return out


# ---------------------------------------------------------------- indicators
def _sma(x: list[float], n: int) -> list[float | None]:
    out, s = [None] * len(x), 0.0
    for i, v in enumerate(x):
        s += v
        if i >= n:
            s -= x[i - n]
        if i >= n - 1:
            out[i] = s / n
    return out


def _rsi2(c: list[float]) -> list[float | None]:
    """Wilder RSI with period 2."""
    out = [None] * len(c)
    up = dn = None
    for i in range(1, len(c)):
        ch = c[i] - c[i - 1]
        u, d = max(ch, 0.0), max(-ch, 0.0)
        if up is None:
            up, dn = u, d
        else:
            up, dn = (up + u) / 2, (dn + d) / 2
        if i >= 2:
            out[i] = 100.0 if dn == 0 else 100 - 100 / (1 + up / dn)
    return out


def _spread_series(bars: list[dict], window: int = 20) -> list[float]:
    """Round-trip cost for an entry the day after i, using the `window` sessions up to i."""
    two = [None]
    for a, b in zip(bars, bars[1:]):
        two.append(costs.cs_spread([a, b]))
    out, s, k = [], 0.0, []
    for i, v in enumerate(two):
        k.append(v)
        if len(k) > window - 1:
            k.pop(0)
        vals = [x for x in k if x is not None]
        out.append(min(costs.MAX_COST, max(costs.MIN_SPREAD, sum(vals) / len(vals)) + costs.FEE_RT)
                   if len(vals) >= 4 else costs.fallback(bars[i]["c"]))
    return out


def features(bars: list[dict], m: Market = STOCKS) -> dict:
    c = [b["c"] for b in bars]
    hi = [b["h"] for b in bars]
    dv = [b["dv"] for b in bars]
    n = len(bars)
    f = {"c": c, "o": [b["o"] for b in bars], "h": hi, "l": [b["l"] for b in bars], "dv": dv, "day": [b["day"] for b in bars],
         "ma10": _sma(c, 10), "ma20": _sma(c, 20), "ma50": _sma(c, 50), "ma200": _sma(c, 200),
         "rsi2": _rsi2(c),
         "cost": [m.fixed_cost] * len(bars) if m.fixed_cost is not None else _spread_series(bars)}
    Y, M = m.year, m.month
    f["max20"] = [max(hi[i - 20:i]) if i >= 20 else None for i in range(n)]
    f["max55"] = [max(hi[i - 55:i]) if i >= 55 else None for i in range(n)]
    f["max252"] = [max(hi[i - Y + 1:i + 1]) if i >= Y - 1 else None for i in range(n)]  # one-year high
    f["dvmed50"] = [median(dv[i - 50:i]) if i >= 50 else None for i in range(n)]
    f["mom"] = [c[i - M] / c[i - Y] - 1 if i >= Y else None for i in range(n)]
    r = [None] + [c[i] / c[i - 1] - 1 for i in range(1, n)]
    vol = [None] * n
    for i in range(60, n):
        w = r[i - 59:i + 1]
        m = sum(w) / 60
        vol[i] = math.sqrt(sum((x - m) ** 2 for x in w) / 59)
    f["vol60"] = vol
    f["mom_rank"] = [None] * n
    f["vol_rank"] = [None] * n
    return f


def add_ranks(feats: dict, min_names: int = 10) -> None:
    """Cross-sectional percentile ranks per day (1 = highest), from each symbol's own value that day.
    Uses only that day's values, so a rank never depends on later data."""
    for key, out in (("mom", "mom_rank"), ("vol60", "vol_rank")):
        by_day: dict[str, list] = {}
        for sym, f in feats.items():
            for i, (d, v) in enumerate(zip(f["day"], f[key])):
                if v is not None:
                    by_day.setdefault(d, []).append((v, sym, i))
        for d, vals in by_day.items():
            if len(vals) < min_names:
                continue
            vals.sort()
            n = len(vals)
            for rank, (_v, sym, i) in enumerate(vals):
                feats[sym][out][i] = (rank + 1) / n


def _weekdays_left(day: str) -> int:
    """Weekdays after `day` until the end of its month (the trading calendar is known in advance)."""
    d = date.fromisoformat(day)
    k, x = 0, d + timedelta(days=1)
    while x.month == d.month:
        k += x.weekday() < 5
        x += timedelta(days=1)
    return k


# ---------------------------------------------------------------- signals (data up to close of i only)
def _stack(f, i, long):
    ok = lambda j: (f["ma50"][j] is not None and f["c"][j] > f["ma10"][j] > f["ma20"][j] > f["ma50"][j]
                    and (not long or (f["ma200"][j] is not None and f["ma50"][j] > f["ma200"][j])))
    return i >= 1 and ok(i) and not ok(i - 1)


def _near_high(f, i, g):
    zone = lambda j: f["max252"][j] is not None and f["c"][j] >= (1 - g) * f["max252"][j]
    return i >= 1 and zone(i) and not zone(i - 1)


def _down(f, i):
    return i >= 5 and f["ma20"][i] is not None and f["c"][i] < f["ma20"][i] and f["c"][i] < f["c"][i - 5]


def _up(f, i):
    return i >= 5 and f["ma20"][i] is not None and f["c"][i] > f["ma20"][i] and f["c"][i] > f["c"][i - 5]


def _candle(f, i):
    """(body, range, upper wick, lower wick, green) of bar i."""
    o, c, h, l = f["o"][i], f["c"][i], f["h"][i], f["l"][i]
    return abs(c - o), h - l, h - max(o, c), min(o, c) - l, c > o


def candle_signal(name: str, f: dict, i: int, p: dict) -> bool:
    if i < 6:
        return False
    body, rng, up, lo, green = _candle(f, i)
    if rng <= 0:
        return False
    o, c = f["o"], f["c"]
    if name == "candle_hammer":
        return _down(f, i) and body > 0 and lo >= p["k"] * body and up <= 0.1 * rng
    if name == "candle_shooting_star":
        return _up(f, i) and body > 0 and up >= p["k"] * body and lo <= 0.1 * rng
    if name == "candle_doji":
        return _down(f, i) and body <= p["d"] * rng
    if name in ("candle_bull_engulf", "candle_bear_engulf"):
        pb = abs(c[i - 1] - o[i - 1])
        if pb <= 0:
            return False
        if name == "candle_bull_engulf":
            ok = _down(f, i - 1) and c[i - 1] < o[i - 1] and green and o[i] <= c[i - 1] and c[i] >= o[i - 1]
            return ok and (not p["trend"] or (f["ma200"][i] is not None and c[i] > f["ma200"][i]))
        ok = _up(f, i - 1) and c[i - 1] > o[i - 1] and c[i] < o[i] and o[i] >= c[i - 1] and c[i] <= o[i - 1]
        return ok and (not p["trend"] or (f["ma200"][i] is not None and c[i] < f["ma200"][i]))
    if name == "candle_morning_star":
        b2, r2, *_ = _candle(f, i - 2)
        b1, *_ = _candle(f, i - 1)
        red2 = c[i - 2] < o[i - 2]
        return (_down(f, i - 2) and red2 and r2 > 0 and b2 >= 0.6 * r2 and b1 <= 0.3 * b2 and green
                and c[i] >= c[i - 2] + p["m"] * b2)
    if name == "candle_three_soldiers":
        for j in (i - 2, i - 1, i):
            bj, rj, uj, _, gj = _candle(f, j)
            if not gj or rj <= 0 or uj > p["u"] * rj:
                return False
        return (c[i] > c[i - 1] > c[i - 2] and o[i - 1] >= o[i - 2] and o[i - 1] <= c[i - 2]
                and o[i] >= o[i - 1] and o[i] <= c[i - 1])
    raise KeyError(name)


def signal(name: str, f: dict, i: int, p: dict) -> bool:
    if name.startswith("candle_"):
        return candle_signal(name, f, i, p)
    c = f["c"]
    if name == "breakout_volume":
        mx, med, ma = f["max%d" % p["L"]][i], f["dvmed50"][i], f["ma50"][i]
        return None not in (mx, med, ma) and c[i] > mx and f["dv"][i] > p["m"] * med and c[i] > ma
    if name == "dip_exhaustion":
        ma = f["ma200"][i]
        return (ma is not None and i >= 4 and c[i] > ma and c[i - 1] / c[i - 4] - 1 <= -p["d"]
                and f["dv"][i] < max(f["dv"][i - 3:i]) and c[i] > f["o"][i])
    if name == "ma_stack":
        return _stack(f, i, p["long"])
    if name == "near_52w_high":
        return _near_high(f, i, p["g"])
    if name in ("momentum_12_1", "low_volatility"):
        first = i >= 1 and f["day"][i][:7] != f["day"][i - 1][:7]
        if not first:
            return False
        if name == "momentum_12_1":
            r = f["mom_rank"][i]
            return r is not None and r > 1 - p["q"]
        r = f["vol_rank"][i]
        return r is not None and r <= p["q"]
    if name == "turn_of_month":
        if _weekdays_left(f["day"][i]) != 3:
            return False
        return not p["trend"] or (f["ma200"][i] is not None and c[i] > f["ma200"][i])
    if name == "rsi2_pullback":
        r, ma = f["rsi2"][i], f["ma200"][i]
        return r is not None and ma is not None and r < p["t"] and c[i] > ma
    raise KeyError(name)


# ---------------------------------------------------------------- trades
def _spy_index(spy: list[dict]) -> dict:
    return {b["day"]: (k, b) for k, b in enumerate(spy)}


def _excess(f: dict, i: int, hold: int, spy: list[dict], sidx: dict, lag: int = 0, sd: int = 1) -> tuple | None:
    """Entry at open of i+1+lag, exit at close of i+hold+lag; the benchmark over the identical bars.
    sd=-1 scores a warning signal: positive when the asset lagged the benchmark (cost still paid)."""
    e, x = i + 1 + lag, i + hold + lag
    if x >= len(f["c"]):
        return None
    s = sidx.get(f["day"][e])
    if not s or s[0] + hold - 1 >= len(spy) or spy[s[0] + hold - 1]["day"] != f["day"][x]:
        return None
    r = f["c"][x] / f["o"][e] - 1
    rs = spy[s[0] + hold - 1]["c"] / s[1]["o"] - 1
    return sd * (r - rs) - f["cost"][i], f["day"][e], f["cost"][i]


def trades(name: str, p: dict, hold: int, feats: dict, spy: list[dict], sidx: dict, lag: int = 0) -> list[dict]:
    out, sd = [], side(name)
    for sym, f in feats.items():
        nxt = 0
        for i in range(len(f["c"]) - 1):
            if i < nxt or not signal(name, f, i, p):
                continue
            ex = _excess(f, i, hold, spy, sidx, lag, sd)
            if ex is None:
                continue
            out.append({"sym": sym, "entry": ex[1], "x": ex[0], "cost": ex[2]})
            nxt = i + hold + lag  # no new copy of the same bet while this one is open
    return out


def baseline(feats: dict, hold: int, spy: list[dict], sidx: dict, step: int = 5, lag: int = 0, sd: int = 1) -> dict[int, float]:
    """Mean net excess of buying any universe stock on any (every `step`-th) day, per entry year."""
    acc: dict[int, list[float]] = {}
    for f in feats.values():
        for i in range(0, len(f["c"]) - 1, step):
            ex = _excess(f, i, hold, spy, sidx, lag, sd)
            if ex:
                acc.setdefault(int(ex[1][:4]), []).append(ex[0])
    return {y: sum(v) / len(v) for y, v in acc.items()}


# ---------------------------------------------------------------- statistics
def _wilson_lo(k: int, n: int, z: float = 1.96) -> float:
    if n == 0:
        return 0.0
    p = k / n
    d = 1 + z * z / n
    return max(0.0, (p + z * z / (2 * n) - z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))) / d)


def _week(day: str) -> str:
    y, w, _ = date.fromisoformat(day).isocalendar()
    return f"{y}-{w}"


def clustered_mean(xs: list[float], keys: list[str]) -> tuple[float | None, float | None]:
    """Mean and cluster-robust standard error (clusters = keys)."""
    n = len(xs)
    if n == 0:
        return None, None
    m = sum(xs) / n
    g: dict[str, float] = {}
    for x, k in zip(xs, keys):
        g[k] = g.get(k, 0.0) + (x - m)
    if len(g) < 2:
        return m, None
    G = len(g)
    se = math.sqrt(G / (G - 1) * sum(v * v for v in g.values())) / n
    return m, se


def holm(ps: dict[str, float | None]) -> dict[str, float | None]:
    items = sorted((p, k) for k, p in ps.items() if p is not None)
    m, run, out = len(items), 0.0, {k: None for k in ps}
    for i, (p, k) in enumerate(items):
        run = max(run, min(1.0, (m - i) * p))
        out[k] = run
    return out


def summarize(oos: list[dict]) -> dict:
    n = len(oos)
    xs = [t["x"] for t in oos]
    adj = [t["x"] - t["base"] for t in oos]
    keys = [_week(t["entry"]) for t in oos]
    k = sum(1 for x in xs if x > 0)
    m, se = clustered_mean(xs, keys)
    ma, sea = clustered_mean(adj, keys)
    p = None
    if sea:
        p = 0.5 * math.erfc((ma / sea) / math.sqrt(2))
    wins, losses = [x for x in xs if x > 0], [x for x in xs if x <= 0]
    years: dict[str, list[float]] = {}
    for t in oos:
        years.setdefault(t["entry"][:4], []).append(t["x"] - t["base"])
    # cumulative excess per trade in entry order: the deepest fall from a running peak (sum of trades, not
    # an account curve — overlapping trades aren't sized; it shows losing streaks, not a portfolio drawdown)
    cum = peak = dd = 0.0
    for t in sorted(oos, key=lambda t: t["entry"]):
        cum += t["x"]
        peak = max(peak, cum)
        dd = min(dd, cum - peak)
    return {"n": n, "symbols": len({t["sym"] for t in oos}), "success_rate": k / n if n else None,
            "clusters": len(set(keys)), "first_entry": min(t["entry"] for t in oos) if n else None,
            "last_entry": max(t["entry"] for t in oos) if n else None, "max_dd_sum": dd if n else None,
            "success_lo95": _wilson_lo(k, n) if n else None,
            "mean_excess": m, "mean_excess_lo95": (m - 1.96 * se) if se else None,
            "edge_vs_baseline": ma, "edge_lo95": (ma - 1.96 * sea) if sea else None, "p_edge": p,
            "avg_win": sum(wins) / len(wins) if wins else None, "avg_loss": sum(losses) / len(losses) if losses else None,
            "profit_factor": sum(wins) / -sum(losses) if losses and sum(losses) < 0 else None,
            "avg_cost": sum(t["cost"] for t in oos) / n if n else None,
            "by_year": {y: {"n": len(v), "edge_vs_baseline": sum(v) / len(v)} for y, v in sorted(years.items())}}


def verdict(s: dict) -> str:
    if s["n"] < GATE["min_n"]:
        return "insufficient_data"
    edge = s["edge_lo95"] is not None and s["edge_lo95"] > 0 and s.get("p_holm") is not None and s["p_holm"] < GATE["alpha"]
    if edge and s["success_rate"] >= GATE["win_rate"] and s["success_lo95"] > 0.5:
        return "candidate_for_paper"
    if edge:
        return "edge_below_target"
    return "not_supported"


# ---------------------------------------------------------------- walk-forward
def walk_forward(name: str, feats: dict, spy: list[dict], m: Market = STOCKS) -> dict:
    sidx = _spy_index(spy)
    spec = STRATEGIES[name]
    combos = [(p, hold) for p in spec["grid"] for hold in m.holds]
    base = {hold: baseline(feats, hold, spy, sidx, lag=m.lag, sd=side(name)) for hold in m.holds}
    all_tr = {}
    for p, hold in combos:
        tr = trades(name, p, hold, feats, spy, sidx, m.lag)
        for t in tr:
            t["base"] = base[hold].get(int(t["entry"][:4]), 0.0)
        all_tr[(json.dumps(p, sort_keys=True), hold)] = tr
    years = sorted({int(d[:4]) for f in feats.values() for d in f["day"]})
    if len(years) <= FIRST_TEST_YEARS:
        return {"oos": [], "choices": {}}
    oos, choices = [], {}
    for ty in years[FIRST_TEST_YEARS:]:
        best, best_score = None, None
        for key, tr in all_tr.items():
            hold = key[1]
            # train = earlier years only, with an embargo so no training trade is still open when the
            # test year starts (H sessions <= 1.5*H calendar days; cutoff leaves 2*H days)
            cutoff = f"{ty - 1}-12-{31 - min(30, 2 * hold + m.lag):02d}"
            train = [t["x"] - t["base"] for t in tr if t["entry"] < cutoff]
            if len(train) < MIN_TRAIN_TRADES:
                continue
            score = sum(train) / len(train)
            if best_score is None or score > best_score:
                best, best_score = key, score
        if best is None:
            continue
        choices[str(ty)] = {"params": json.loads(best[0]), "hold": best[1], "train_edge": best_score}
        oos += [t for t in all_tr[best] if t["entry"].startswith(str(ty))]
    return {"oos": oos, "choices": choices}


def bench_stats(bench: list[dict], start: str | None, year: int) -> dict | None:
    """Buy-and-hold of the benchmark from `start` (first out-of-sample day): return, CAGR, volatility, max drawdown."""
    b = [x for x in bench if start and x["day"] >= start]
    if len(b) < 20:
        return None
    c = [x["c"] for x in b]
    r = [c[i] / c[i - 1] - 1 for i in range(1, len(c))]
    m = sum(r) / len(r)
    vol = math.sqrt(sum((x - m) ** 2 for x in r) / (len(r) - 1) * year)
    peak, dd = c[0], 0.0
    for x in c:
        peak = max(peak, x)
        dd = min(dd, x / peak - 1)
    yrs = len(c) / year
    return {"from": b[0]["day"], "to": b[-1]["day"], "total_return": c[-1] / c[0] - 1,
            "cagr": (c[-1] / c[0]) ** (1 / yrs) - 1 if yrs > 0 else None, "volatility": vol, "max_drawdown": dd}


COST_GRID = (0.002, 0.005, 0.01, 0.02)


def cost_sensitivity(oos: list[dict], s: dict, assumed: float) -> list[dict]:
    """Same trades re-scored at other fixed round-trip costs. The edge over random entry doesn't move
    (the random baseline pays the same cost); what moves is the result against the benchmark."""
    out = []
    for c in COST_GRID:
        xs = [t["x"] + assumed - c for t in oos]
        n, k = len(xs), sum(1 for x in xs if x > 0)
        v = {**s, "success_rate": k / n if n else None, "success_lo95": _wilson_lo(k, n) if n else None}
        out.append({"cost": c, "success_rate": v["success_rate"], "mean_excess": sum(xs) / n if n else None,
                    "verdict": verdict(v) if n else "insufficient_data"})
    return out


def live_signals(name: str, feats: dict, params: dict) -> list[str]:
    """Symbols whose signal fired on the latest bar (for forward paper tracking)."""
    out = []
    last = max(f["day"][-1] for f in feats.values())
    for sym, f in feats.items():
        i = len(f["c"]) - 1
        if f["day"][i] == last and signal(name, f, i, params):
            out.append(sym)
    return out


def run(conn=None, fetcher=fetch, symbols=None, pause: float = 0.4, m: Market = STOCKS) -> dict:
    t0 = time.monotonic()
    symbols = m.universe if symbols is None else symbols
    spy = fetcher(m.bench)
    feats, missing = {}, []
    for s in symbols:
        try:
            b = fetcher(s)
            if len(b) >= m.min_bars:
                feats[s] = features(b, m)
            else:
                missing.append(s)
        except Exception as e:  # noqa: BLE001
            log.info("no data for %s: %s", s, e)
            missing.append(s)
        time.sleep(pause)
    add_ranks(feats)
    res, oos_by = {}, {}
    for name in m.strategies:
        spec = STRATEGIES[name]
        wf = walk_forward(name, feats, spy, m)
        s = summarize(wf["oos"])
        oos_by[name] = wf["oos"]
        last = list(wf["choices"].values())[-1] if wf["choices"] else None
        res[name] = {"label": spec["label"], "rule": spec["rule"], "grid": spec["grid"], "holds": m.holds, "side": side(name),
                     "choices": wf["choices"], **s,
                     "signals_today": live_signals(name, feats, last["params"]) if last else [],
                     "current_params": last}
    ph = holm({k: v["p_edge"] for k, v in res.items()})
    for k, v in res.items():
        v["p_holm"] = ph[k]
        v["verdict"] = verdict(v)
        if m.fixed_cost is not None:
            v["cost_sensitivity"] = cost_sensitivity(oos_by[k], v, m.fixed_cost)
    years = sorted({int(d[:4]) for f in feats.values() for d in f["day"]})
    oos_start = f"{years[FIRST_TEST_YEARS]}-01-01" if len(years) > FIRST_TEST_YEARS else None
    out = {"market": m.key, "strategies": res, "benchmark_stats": bench_stats(spy, oos_start, m.year),
           "assumed_cost": m.fixed_cost, "universe": list(feats), "missing": missing, "benchmark": m.bench,
           "first_day": min(f["day"][0] for f in feats.values()) if feats else None,
           "last_day": max(f["day"][-1] for f in feats.values()) if feats else None,
           "seconds": round(time.monotonic() - t0, 1),
           "method": {"entry": "next open" if not m.lag else f"open {m.lag + 1} bars after the signal",
                      "exit": "close after H bars",
                      "cost": "Corwin-Schultz spread + 0.1% fee" if m.fixed_cost is None else f"fixed {m.fixed_cost:.2%} round trip",
                      "walk_forward": f"by calendar year, first {FIRST_TEST_YEARS} years train only",
                      "baseline": "any universe stock, any day, same hold, same year", "gate": GATE,
                      "bias": "universe = today's survivors; controlled by the baseline, not removed"}}
    if conn is not None:
        conn.execute("INSERT INTO research_reports (kind, params, result) VALUES (%s, %s, %s)",
                     (m.report_kind, json.dumps({"symbols": len(symbols)}), json.dumps(out, default=str)))
    return out


def main(m: Market = STOCKS) -> None:
    from . import db
    logging.basicConfig(level=logging.INFO)
    c = db.connect()
    db.init_schema(c)
    with db.job_run(c, m.job) as st:
        r = run(c, m=m)
        st["items"] = sum(v["n"] for v in r["strategies"].values())
    for k, v in r["strategies"].items():
        print(f"{k:18s} n={v['n']:5d} win={v['success_rate'] or 0:.0%} edge_vs_base={v['edge_vs_baseline'] or 0:+.2%} "
              f"lo95={v['edge_lo95'] if v['edge_lo95'] is None else round(v['edge_lo95'] * 100, 2)} -> {v['verdict']}")


if __name__ == "__main__":
    main()
