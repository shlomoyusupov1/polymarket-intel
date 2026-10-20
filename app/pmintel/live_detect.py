"""Market Risk Monitor: pure detection logic over live price series (no I/O; see live.py for the feeds).

Each asset keeps a short in-memory series of observations (source time, receive time, price, bid,
ask). Detectors run every few seconds and return *facts* — never buy/sell advice:

  move        price moved more than the pre-registered threshold within 1, 5 or 15 minutes
  vol_spike   1-minute volatility of the last 15 minutes >= VOL_RATIO x its trailing baseline
  spread      the quoted spread widened to >= SPREAD_RATIO x its 30-minute median (and above a floor)
  activity    trades in the last 5 minutes >= ACTIVITY_RATIO x the trailing rate (Polymarket)
  breadth     several benchmark assets fell together within 15 minutes
  stale       no fresh observation for longer than the asset class allows (data problem, not a market event)

Bad ticks (non-positive / NaN prices, probabilities outside 0..1, or a one-tick jump above JUMP_MAX that
the next tick does not confirm) are quarantined and counted, never alerted on.
Thresholds are fixed here, in advance; changing them is a code change with a test.
"""
from __future__ import annotations

import math
from bisect import bisect_left
from collections import deque
from dataclasses import dataclass, field
from statistics import median, pstdev

# per asset class: window seconds -> threshold (fraction of price; for Polymarket: probability points)
MOVE = {"crypto": {60: 0.02, 300: 0.035, 900: 0.05},
        "stocks": {60: 0.01, 300: 0.02, 900: 0.03},
        "polymarket": {60: 0.05, 300: 0.08, 900: 0.10}}
VOL_RATIO, VOL_MIN_BASE = 3.0, 120          # baseline needs >= 120 one-minute returns
SPREAD_RATIO, SPREAD_FLOOR = 3.0, {"crypto": 0.002, "stocks": 0.005, "polymarket": 0.04}
ACTIVITY_RATIO, ACTIVITY_MIN = 5.0, 10
JUMP_MAX = {"crypto": 0.30, "stocks": 0.30, "polymarket": 0.60}
STALE = {"crypto": 120, "stocks": 300, "polymarket": 1800}   # seconds without a fresh observation
BREADTH_MIN, BREADTH_SHARE = 3, 0.5
KEEP = 24 * 3600


@dataclass
class Obs:
    t: float          # source timestamp (epoch seconds) — what the market time was
    recv: float       # when we received it
    price: float
    bid: float | None = None
    ask: float | None = None
    trade: bool = False


@dataclass
class Series:
    asset: str
    cls: str                       # crypto | stocks | polymarket
    name: str = ""
    source: str = ""
    obs: list = field(default_factory=list)
    ts: list = field(default_factory=list)          # source times of obs, for bisect
    trades: deque = field(default_factory=deque)     # source times of trades (Polymarket activity)
    pending: Obs | None = None     # a suspicious jump waiting for confirmation
    bad: int = 0

    # ------------------------------------------------------------ ingestion
    def valid(self, p) -> bool:
        if p is None or not isinstance(p, (int, float)) or math.isnan(p) or math.isinf(p) or p <= 0:
            return False
        return not (self.cls == "polymarket" and p >= 1.0001)

    def add(self, o: Obs) -> bool:
        """Append one observation. Returns False when it was quarantined or rejected."""
        if not self.valid(o.price):
            self.bad += 1
            return False
        if self.obs and o.t < self.obs[-1].t - 5:      # out of order by more than 5 s: ignore
            self.bad += 1
            return False
        if o.trade:
            self.trades.append(o.t)
            while self.trades and self.trades[0] < o.t - KEEP:
                self.trades.popleft()
        last = self.obs[-1].price if self.obs else None
        if last is not None:
            jump = abs(o.price - last) if self.cls == "polymarket" else abs(o.price / last - 1)
            if jump > JUMP_MAX[self.cls]:
                if self.pending is not None and _close(self.pending.price, o.price, self.cls):
                    self._push(self.pending)            # two ticks agree: a real move
                    self.pending = None
                else:
                    self.pending = o
                    self.bad += 1
                    return False
            else:
                self.pending = None
        self._push(o)
        return True

    def _push(self, o: Obs) -> None:
        # one observation per 2 seconds is plenty for 1-minute windows; keep the latest in each slot
        if self.obs and o.t - self.obs[-1].t < 2:
            self.obs[-1], self.ts[-1] = o, o.t
        else:
            self.obs.append(o)
            self.ts.append(o.t)
        cut = bisect_left(self.ts, o.t - KEEP)
        if cut > 500:
            del self.obs[:cut], self.ts[:cut]

    # ------------------------------------------------------------ queries
    def last(self) -> Obs | None:
        return self.obs[-1] if self.obs else None

    def at(self, t: float) -> Obs | None:
        """Last observation at or before time t."""
        if not self.obs or self.ts[0] > t:
            return None
        i = bisect_left(self.ts, t + 1e-9) - 1
        return self.obs[i] if i >= 0 else None

    def change(self, window: float) -> tuple[float, Obs, Obs] | None:
        last = self.last()
        if not last:
            return None
        then = self.at(last.t - window)
        if not then or last.t - then.t < window * 0.8:
            return None
        d = (last.price - then.price) if self.cls == "polymarket" else (last.price / then.price - 1)
        return d, then, last

    def minute_returns(self, start: float, end: float) -> list[float]:
        out, t = [], start
        prev = self.at(t)
        while t + 60 <= end:
            t += 60
            cur = self.at(t)
            if prev and cur and cur is not prev:
                out.append((cur.price - prev.price) if self.cls == "polymarket" else (cur.price / prev.price - 1))
            prev = cur
        return out

    def history_share(self, window: float, size: float, now: float) -> float | None:
        """Share of past windows (sampled each minute over the kept history) with |move| >= size."""
        if not self.obs or now - self.obs[0].t < 4 * 3600:
            return None
        moves, t = [], self.obs[0].t + window
        while t <= now - window:
            a, b = self.at(t - window), self.at(t)
            if a and b:
                moves.append(abs((b.price - a.price) if self.cls == "polymarket" else (b.price / a.price - 1)))
            t += 60
        if len(moves) < 60:
            return None
        return sum(1 for m in moves if m >= abs(size)) / len(moves)


def _close(a: float, b: float, cls: str) -> bool:
    return abs(a - b) <= (0.02 if cls == "polymarket" else 0.02 * a)


@dataclass
class Event:
    kind: str           # move | vol_spike | spread | activity | breadth | stale
    asset: str
    cls: str
    name: str
    text: str           # plain Hebrew, facts only
    window: int | None
    size: float | None
    price: float | None
    price_from: float | None
    src_t: float | None
    recv_t: float | None
    source: str
    rarity: float | None = None   # share of past windows with a move this large


def _fmt(cls, p):
    if p is None:
        return "—"
    return f"{p * 100:.0f}%" if cls == "polymarket" else (f"{p:,.2f}$" if p >= 1 else f"{p:.4f}$")


WIN_HE = {60: "דקה", 300: "5 דקות", 900: "15 דקות"}


def detect(s: Series, now: float) -> list[Event]:
    out = []
    last = s.last()
    if not last:
        return out
    for w, thr in MOVE[s.cls].items():
        c = s.change(w)
        if not c:
            continue
        d, then, cur = c
        if abs(d) >= thr:
            if s.cls == "polymarket":
                what = f"{'עלה' if d > 0 else 'ירד'} {abs(d) * 100:.0f} נקודות ב-{WIN_HE[w]}"
            else:
                what = f"{'עלה' if d > 0 else 'ירד'} {abs(d) * 100:.1f}% ב-{WIN_HE[w]}"
            rare = s.history_share(w, d, now)
            out.append(Event("move", s.asset, s.cls, s.name, f"{s.name}: {what} (מ-{_fmt(s.cls, then.price)} ל-{_fmt(s.cls, cur.price)})",
                             w, d, cur.price, then.price, cur.t, cur.recv, s.source, rare))
            break  # the shortest window that crosses is enough
    recent = s.minute_returns(last.t - 900, last.t)
    base = s.minute_returns(max(s.obs[0].t, last.t - KEEP), last.t - 900)
    if len(recent) >= 10 and len(base) >= VOL_MIN_BASE:
        sb, sr = pstdev(base), pstdev(recent)
        if sb > 0 and sr / sb >= VOL_RATIO:
            out.append(Event("vol_spike", s.asset, s.cls, s.name,
                             f"{s.name}: התנודתיות ב-15 הדקות האחרונות גבוהה פי {sr / sb:.1f} מהרגיל של היממה",
                             900, sr / sb, last.price, None, last.t, last.recv, s.source))
    if last.bid and last.ask and last.ask > last.bid:
        spread = (last.ask - last.bid) if s.cls == "polymarket" else (last.ask - last.bid) / ((last.ask + last.bid) / 2)
        lo = bisect_left(s.ts, last.t - 1800)
        past = [((o.ask - o.bid) if s.cls == "polymarket" else (o.ask - o.bid) / ((o.ask + o.bid) / 2))
                for o in s.obs[lo:] if o.bid and o.ask and o.ask > o.bid and o.t < last.t - 60]
        if len(past) >= 20:
            med = median(past)
            if spread >= SPREAD_FLOOR[s.cls] and med > 0 and spread / med >= SPREAD_RATIO:
                txt = (f"{spread * 100:.0f} נקודות" if s.cls == "polymarket" else f"{spread * 100:.2f}%")
                out.append(Event("spread", s.asset, s.cls, s.name,
                                 f"{s.name}: המרווח בין קנייה למכירה נפתח ל-{txt}, פי {spread / med:.1f} מהחציון של חצי השעה האחרונה. קשה יותר לקנות ולמכור במחיר הוגן",
                                 1800, spread, last.price, None, last.t, last.recv, s.source))
    if s.cls == "polymarket":
        trades = list(s.trades)
        r5 = sum(1 for t in trades if t >= last.t - 300)
        span = last.t - (trades[0] if trades else last.t)
        prior = sum(1 for t in trades if t < last.t - 300)
        if span > 3600 and r5 >= ACTIVITY_MIN and prior:
            rate = prior / ((span - 300) / 300)
            if rate > 0 and r5 / rate >= ACTIVITY_RATIO:
                out.append(Event("activity", s.asset, s.cls, s.name,
                                 f"{s.name}: {r5} עסקאות ב-5 הדקות האחרונות, פי {r5 / rate:.0f} מהקצב הרגיל",
                                 300, r5 / rate, last.price, None, last.t, last.recv, s.source))
    return out


def stale(s: Series, now: float, market_open: bool = True) -> Event | None:
    last = s.last()
    if s.cls == "stocks" and not market_open:
        return None
    age = now - (last.recv if last else 0)
    if last is None or age > STALE[s.cls]:
        return Event("stale", s.asset, s.cls, s.name,
                     f"{s.name}: אין מחיר עדכני כבר {int(age // 60)} דקות. מחירים שלו לא יוצגו כעדכניים" if last
                     else f"{s.name}: עוד לא התקבל מחיר", None, None, last.price if last else None, None,
                     last.t if last else None, last.recv if last else None, s.source)
    return None


def is_fresh(s: Series, now: float) -> bool:
    last = s.last()
    return bool(last) and now - last.recv <= STALE[s.cls]


def breadth(group: list[Series], now: float, window: int = 900) -> Event | None:
    """Several benchmark assets of one class fell together (each by >= half its 15-minute threshold)."""
    fresh = [s for s in group if is_fresh(s, now)]
    if len(fresh) < BREADTH_MIN:
        return None
    downs = []
    for s in fresh:
        c = s.change(window)
        if c and c[0] <= -MOVE[s.cls][window] / 2:
            downs.append((s, c[0]))
    if len(downs) >= BREADTH_MIN and len(downs) >= BREADTH_SHARE * len(fresh):
        cls = fresh[0].cls
        names = ", ".join(f"{s.name} {d * 100:.1f}%" for s, d in sorted(downs, key=lambda x: x[1]))
        last_t = max(s.last().t for s, _ in downs)
        return Event("breadth", f"breadth:{cls}", cls, "ירידה רוחבית",
                     f"ירידה רוחבית ב-15 דקות: {len(downs)} מתוך {len(fresh)} נכסי ייחוס ירדו יחד ({names})",
                     window, None, None, None, last_t, now, fresh[0].source)
    return None


def latency_stats(lags: list[float]) -> dict:
    if not lags:
        return {"n": 0, "mean": None, "p95": None, "max": None}
    xs = sorted(lags)
    return {"n": len(xs), "mean": sum(xs) / len(xs), "p95": xs[min(len(xs) - 1, int(0.95 * (len(xs) - 1) + 0.5))],
            "max": xs[-1]}
