"""Live market monitor service: fast feeds for what matters, detection every few seconds, alerts.

Watched assets (refreshed every minute from the database):
  * everything the owner follows (follows) — Polymarket outcomes, stocks, coins;
  * every open or pending paper position (lab_signals);
  * benchmarks for broad moves: SPY QQQ IWM XLK XLF XLE · BTC ETH SOL XRP.

Feeds (all free, no keys):
  * Polymarket — official CLOB market WebSocket (book / price_change / best_bid_ask / last_trade_price,
    each with the exchange's own millisecond timestamp);
  * crypto — Coinbase Advanced Trade public WebSocket, ticker + heartbeats (message timestamp);
  * stocks/ETFs — Yahoo chart quote polled every 60 s in US regular hours. It is not a licensed live
    feed: the quote's own regularMarketTime is used as the source time, so the real delay is measured
    and shown, and a stale quote is never shown as current.

Every pass: live_detect detectors -> alerts (cooldown per asset+kind, daily cap) written to
live_alerts; the Telegram service sends them within seconds. Market / Risk / Strategy / Data levels.
Detecting a fall is never a sell recommendation. Nothing here trades.

  docker compose up -d live
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import deque
from datetime import datetime, time as dtime, timezone
from zoneinfo import ZoneInfo

import httpx
from psycopg.types.json import Jsonb

from . import db
from . import live_detect as D

log = logging.getLogger("pmintel.live")
NY = ZoneInfo("America/New_York")
IL = ZoneInfo("Asia/Jerusalem")
PM_WS = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
CB_WS = "wss://advanced-trade-ws.coinbase.com"
YAHOO = "https://query1.finance.yahoo.com/v8/finance/chart/"
BENCH_STOCKS = ["SPY", "QQQ", "IWM", "XLK", "XLF", "XLE"]
BENCH_CRYPTO = ["BTC-USD", "ETH-USD", "SOL-USD", "XRP-USD"]
COINBASE_OK = {"BTC-USD", "ETH-USD", "SOL-USD", "XRP-USD", "DOGE-USD", "ADA-USD", "AVAX-USD", "LINK-USD", "DOT-USD",
               "LTC-USD", "BCH-USD", "XLM-USD", "ATOM-USD", "ETC-USD", "HBAR-USD", "NEAR-USD", "FIL-USD"}
COOLDOWN = 30 * 60
DAILY_CAP = 30
LEVEL_HE = {"market": "🟡 התראת שוק", "risk": "🔴 התראת סיכון", "strategy": "🟢 איתות אסטרטגיה", "data": "⚠️ בעיית נתונים"}
SOURCE_HE = {"polymarket": "Polymarket WebSocket", "coinbase": "Coinbase WebSocket", "yahoo": "Yahoo (ציטוט כל דקה, לא פיד מורשה)"}


def market_open(now: datetime | None = None) -> bool:
    n = (now or datetime.now(timezone.utc)).astimezone(NY)
    return n.weekday() < 5 and dtime(9, 30) <= n.time() <= dtime(16, 0)


class Feed:
    def __init__(self, name: str):
        self.name, self.connected, self.msgs, self.reconnects = name, False, 0, 0
        self.last_msg, self.error, self.note = None, None, None
        self.lags: deque = deque(maxlen=2000)        # (recv_time, lag seconds)
        self.subscribed = 0

    def lag(self, recv: float, src: float):
        self.msgs += 1
        self.last_msg = recv
        if src:
            self.lags.append((recv, max(0.0, recv - src)))

    def stats(self) -> dict:
        now = time.time()
        return D.latency_stats([l for r, l in self.lags if now - r < 300])


class Monitor:
    def __init__(self, conn, sender=None):
        self.conn = conn
        self.series: dict[str, D.Series] = {}
        self.roles: dict[str, set] = {}
        self.feeds = {k: Feed(k) for k in ("polymarket", "coinbase", "yahoo")}
        self.pm_tokens: dict[str, str] = {}          # token -> display name
        self.crypto: set[str] = set(BENCH_CRYPTO)
        self.stocks: set[str] = set(BENCH_STOCKS)
        self.last_alert: dict[tuple, float] = {}
        self.sent_today: tuple[str, int] = ("", 0)
        self.positions: dict[str, dict] = {}         # symbol -> paper position (size, units, entry)
        self.risk_usd = None
        self.pm_changed = asyncio.Event()
        self.cb_changed = asyncio.Event()

    # ------------------------------------------------------------ universe
    def series_for(self, asset: str, cls: str, name: str, source: str) -> D.Series:
        s = self.series.get(asset)
        if s is None:
            s = self.series[asset] = D.Series(asset, cls, name, source)
        return s

    def refresh_universe(self, snapshot=None):
        """Read follows and paper positions; resolve Polymarket tokens (Gamma) for followed events."""
        from . import follows as F, plans, portfolio as PF
        roles: dict[str, set] = {}
        for sym in BENCH_STOCKS:
            roles.setdefault(f"st:{sym}", set()).add("benchmark")
        for sym in BENCH_CRYPTO:
            roles.setdefault(f"cx:{sym}", set()).add("benchmark")
        stocks, crypto, tokens = set(BENCH_STOCKS), set(BENCH_CRYPTO), {}
        try:
            fl = F.listing(self.conn)
        except Exception:  # noqa: BLE001
            fl = []
        for f in fl:
            if f["kind"] == "polymarket":
                try:
                    snap = (snapshot or F.snapshot)("polymarket", f["ref"])
                except Exception as e:  # noqa: BLE001
                    log.info("follow %s: %s", f["ref"], e)
                    continue
                for it in snap["items"][:8]:
                    if it.get("token"):
                        tokens[it["token"]] = f"{it['name']} ({snap['label'][:40]})"
                        roles.setdefault(f"pm:{it['token']}", set()).add("follow")
            elif f["kind"] == "crypto":
                crypto.add(f["ref"])
                roles.setdefault(f"cx:{f['ref']}", set()).add("follow")
            else:
                stocks.add(f["ref"])
                roles.setdefault(f"st:{f['ref']}", set()).add("follow")
        try:
            cols = ["id", "strategy", "symbol", "signal_day", "recorded_at", "sha256", "lab_verdict", "hold", "cost_rt",
                    "status", "entry_day", "entry_price", "exit_day", "ret", "spy_ret", "excess", "last_price", "last_day"]
            rows = [dict(zip(cols, r)) for r in self.conn.execute(f"SELECT {', '.join(cols)} FROM lab_signals").fetchall()]
            from . import lab_paper as LP
            from .cryptolab import CRYPTO
            from .stocklab import STOCKS
            for row in rows:
                m = CRYPTO if row["strategy"].startswith("crypto:") else STOCKS
                row["sealed_ok"] = row["recorded_at"] < LP.deadline(str(row["signal_day"]), m)
            L = plans.limits(plans.get_settings(self.conn))
            cap = min(L["max_position_usd"], L["risk_usd"])
            pf = PF.replay(rows, L["budget"], cap, L["max_open"])
            self.risk_usd = L["risk_usd"]
            self.positions = {p["symbol"]: p for p in pf["positions"]}
        except Exception as e:  # noqa: BLE001
            log.info("positions unavailable: %s", e)
            self.positions = {}
        for sym in self.positions:
            if sym.endswith("-USD"):
                crypto.add(sym)
                roles.setdefault(f"cx:{sym}", set()).add("position")
            else:
                stocks.add(sym)
                roles.setdefault(f"st:{sym}", set()).add("position")
        unsupported = sorted(c for c in crypto if c not in COINBASE_OK)
        self.feeds["coinbase"].note = ("אין ב-Coinbase: " + ", ".join(unsupported)) if unsupported else None
        crypto = {c for c in crypto if c in COINBASE_OK}
        if tokens.keys() != self.pm_tokens.keys():
            self.pm_changed.set()
        if crypto != self.crypto:
            self.cb_changed.set()
        self.pm_tokens, self.crypto, self.stocks, self.roles = tokens, crypto, stocks, roles
        for t, name in tokens.items():
            self.series_for(f"pm:{t}", "polymarket", name, "polymarket")
        for c in crypto:
            self.series_for(f"cx:{c}", "crypto", c.replace("-USD", ""), "coinbase")
        for sym in stocks:
            self.series_for(f"st:{sym}", "stocks", sym, "yahoo")
        for k in [k for k in self.series if k not in roles]:
            del self.series[k]

    # ------------------------------------------------------------ ingestion (parsers are pure: tested)
    def on_polymarket(self, msg, recv: float):
        items = msg if isinstance(msg, list) else [msg]
        for m in items:
            if not isinstance(m, dict):
                continue
            ts = _ms(m.get("timestamp"))
            et = m.get("event_type")
            if et == "book":
                bids = [float(x["price"]) for x in m.get("bids") or []]
                asks = [float(x["price"]) for x in m.get("asks") or []]
                self._pm(m.get("asset_id"), ts, recv, max(bids) if bids else None, min(asks) if asks else None)
            elif et == "price_change":
                for c in m.get("price_changes") or []:
                    self._pm(c.get("asset_id"), ts, recv, _f(c.get("best_bid")), _f(c.get("best_ask")))
            elif et == "best_bid_ask":
                self._pm(m.get("asset_id"), ts, recv, _f(m.get("best_bid")), _f(m.get("best_ask")))
            elif et == "last_trade_price":
                s = self.series.get(f"pm:{m.get('asset_id')}")
                p = _f(m.get("price"))
                if s and p:
                    last = s.last()
                    if last and last.bid:   # keep the quoted mid as the price; the trade counts as activity
                        s.add(D.Obs(ts or recv, recv, last.price, last.bid, last.ask, trade=True))
                    else:
                        s.add(D.Obs(ts or recv, recv, p, trade=True))
            self.feeds["polymarket"].lag(recv, ts)

    def _pm(self, token, ts, recv, bid, ask):
        s = self.series.get(f"pm:{token}")
        if not s or bid is None or ask is None or ask <= bid:
            return
        s.add(D.Obs(ts or recv, recv, (bid + ask) / 2, bid, ask))

    def on_coinbase(self, msg: dict, recv: float):
        if msg.get("channel") != "ticker":
            if msg.get("channel") == "heartbeats":
                self.feeds["coinbase"].last_msg = recv
            return
        ts = _iso(msg.get("timestamp"))
        for ev in msg.get("events") or []:
            for t in ev.get("tickers") or []:
                s = self.series.get(f"cx:{t.get('product_id')}")
                if s:
                    s.add(D.Obs(ts or recv, recv, _f(t.get("price")), _f(t.get("best_bid")), _f(t.get("best_ask"))))
        self.feeds["coinbase"].lag(recv, ts)

    def on_yahoo(self, sym: str, meta: dict, recv: float):
        s = self.series.get(f"st:{sym}")
        p, ts = _f(meta.get("regularMarketPrice")), meta.get("regularMarketTime")
        if s and p and ts:
            last = s.last()
            if not last or ts > last.t:            # an unchanged quote time is not a new observation
                s.add(D.Obs(float(ts), recv, p))
        self.feeds["yahoo"].lag(recv, float(ts) if ts else 0)

    # ------------------------------------------------------------ alerts
    def level(self, ev: D.Event) -> str:
        if ev.kind == "stale":
            return "data"
        sym = ev.asset.split(":", 1)[-1]
        return "risk" if sym in self.positions else "market"

    def impact(self, ev: D.Event) -> str:
        sym = ev.asset.split(":", 1)[-1]
        p = self.positions.get(sym)
        if ev.kind == "breadth":
            return ("בתיק המדומה יש " + str(len(self.positions)) + " פוזיציות פתוחות.") if self.positions else "אין פוזיציות פתוחות בתיק המדומה."
        if not p or not ev.price:
            return "אין פוזיציה בנכס הזה בתיק המדומה."
        value = p["units"] * ev.price
        pnl = value - p["size"]
        line = f"בתיק המדומה: {p['units']:.4f} יחידות, שווי {value:,.2f}$, רווח/הפסד לא ממומש {pnl:+,.2f}$."
        if self.risk_usd is not None and pnl <= -self.risk_usd:
            line += f" ההפסד חורג ממגבלת הסיכון שלך ({self.risk_usd:,.2f}$ לעסקה)."
        return line

    def compose(self, ev: D.Event, lvl: str, now: float) -> str:
        when = datetime.fromtimestamp(ev.src_t, IL).strftime("%H:%M:%S") if ev.src_t else "לא ידוע"
        lag = (ev.recv_t - ev.src_t) if ev.src_t and ev.recv_t else None
        lines = [f"{LEVEL_HE[lvl]}", ev.text,
                 f"זמן הנתון: {when} · מקור: {SOURCE_HE.get(ev.source, ev.source)}" + (f" · עיכוב {lag:.1f} שנ׳" if lag is not None else "")]
        if ev.kind == "move" and ev.rarity is not None:
            lines.append(f"חריג? תנועה כזו או גדולה ממנה הופיעה ב-{ev.rarity:.1%} מהחלונות באותו אורך ב-24 השעות האחרונות."
                         if ev.rarity >= 0.001 else "חריג? לא הופיעה תנועה כזו ב-24 השעות האחרונות.")
        if ev.kind != "stale":
            lines.append(self.impact(ev))
            fresh = ev.src_t and now - ev.src_t < D.STALE[ev.cls]
            lines.append("הנתונים עדכניים." if fresh else "שים לב: הנתון לא עדכני — אל תחליט לפיו.")
            if ev.kind in ("breadth", "vol_spike") or lvl == "risk":
                lines.append("כלל זהירות: לא לשקול כניסה חדשה עד שהתנודה נרגעת.")
            lines.append("זו התראה על עובדה בשוק, לא המלצה למכור.")
        return "\n".join(lines)

    def raise_alert(self, ev: D.Event, now: float, lvl: str | None = None) -> bool:
        lvl = lvl or self.level(ev)
        key = (ev.asset, ev.kind)
        if now - self.last_alert.get(key, 0) < COOLDOWN:
            return False
        day = datetime.fromtimestamp(now, IL).date().isoformat()
        d, n = self.sent_today
        n = n if d == day else 0
        status = "pending" if n < DAILY_CAP or lvl in ("risk", "strategy") else "suppressed"
        self.sent_today = (day, n + 1)
        self.last_alert[key] = now
        text = self.compose(ev, lvl, now)
        self.conn.execute("""INSERT INTO live_alerts (level, kind, asset, text, payload, src_ts, recv_ts, detected_at, status)
                             VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                          (lvl, ev.kind, ev.asset, text,
                           Jsonb({"window": ev.window, "size": ev.size, "price": ev.price, "from": ev.price_from,
                                  "rarity": ev.rarity, "name": ev.name, "cls": ev.cls, "source": ev.source}),
                           _dt(ev.src_t), _dt(ev.recv_t), _dt(now), status))
        return True

    def evaluate(self, now: float | None = None) -> int:
        now = now or time.time()
        n, mo = 0, market_open(datetime.fromtimestamp(now, timezone.utc))
        for s in self.series.values():
            for ev in D.detect(s, now):
                n += self.raise_alert(ev, now)
            st = D.stale(s, now, market_open=mo)
            if st and ({"follow", "position"} & self.roles.get(s.asset, set())) and s.cls != "polymarket":
                n += self.raise_alert(st, now)
        for cls, syms in (("stocks", BENCH_STOCKS), ("crypto", BENCH_CRYPTO)):
            grp = [self.series[f"{'st' if cls == 'stocks' else 'cx'}:{x}"] for x in syms
                   if f"{'st' if cls == 'stocks' else 'cx'}:{x}" in self.series]
            ev = D.breadth(grp, now)
            if ev:
                n += self.raise_alert(ev, now, "market")
        for name, f in self.feeds.items():
            want = {"polymarket": bool(self.pm_tokens), "coinbase": bool(self.crypto), "yahoo": mo}[name]
            silent = now - (f.last_msg or 0) > {"polymarket": 600, "coinbase": 120, "yahoo": 300}[name]
            if want and (not f.connected or silent):
                ev = D.Event("stale", f"feed:{name}", "crypto", name,
                             f"הזרם {SOURCE_HE[name]} " + ("מנותק" if not f.connected else "שקט מדי") +
                             (f" ({f.error})" if f.error else "") + ". המחירים ממנו מסומנים כלא עדכניים.",
                             None, None, None, None, f.last_msg, f.last_msg, name)
                n += self.raise_alert(ev, now, "data")
        return n

    # ------------------------------------------------------------ persistence
    def persist(self, now: float, ticks: bool = False):
        for a, s in self.series.items():
            o = s.last()
            if not o:
                continue
            self.conn.execute("""INSERT INTO live_prices (asset, cls, name, source, price, bid, ask, src_ts, recv_ts, fresh, roles, bad_ticks, updated_at)
                                 VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, now())
                                 ON CONFLICT (asset) DO UPDATE SET name = EXCLUDED.name, price = EXCLUDED.price, bid = EXCLUDED.bid,
                                   ask = EXCLUDED.ask, src_ts = EXCLUDED.src_ts, recv_ts = EXCLUDED.recv_ts, fresh = EXCLUDED.fresh,
                                   roles = EXCLUDED.roles, bad_ticks = EXCLUDED.bad_ticks, updated_at = now()""",
                              (a, s.cls, s.name, s.source, o.price, o.bid, o.ask, _dt(o.t), _dt(o.recv), D.is_fresh(s, now),
                               sorted(self.roles.get(a, [])), s.bad))
            if ticks:
                self.conn.execute("INSERT INTO live_ticks (asset, src_ts, recv_ts, price) VALUES (%s, %s, %s, %s)",
                                  (a, _dt(o.t), _dt(o.recv), o.price))
        self.conn.execute("UPDATE live_prices SET fresh = false WHERE updated_at < now() - interval '2 minutes'")
        for name, f in self.feeds.items():
            st = f.stats()
            self.conn.execute("""INSERT INTO live_feeds (feed, connected, subscribed, msgs, reconnects, last_msg_at, lag_mean, lag_p95, error, note, updated_at)
                                 VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, now())
                                 ON CONFLICT (feed) DO UPDATE SET connected = EXCLUDED.connected, subscribed = EXCLUDED.subscribed,
                                   msgs = EXCLUDED.msgs, reconnects = EXCLUDED.reconnects, last_msg_at = EXCLUDED.last_msg_at,
                                   lag_mean = EXCLUDED.lag_mean, lag_p95 = EXCLUDED.lag_p95, error = EXCLUDED.error,
                                   note = EXCLUDED.note, updated_at = now()""",
                              (name, f.connected, f.subscribed, f.msgs, f.reconnects, _dt(f.last_msg), st["mean"], st["p95"],
                               f.error, f.note))

    # ------------------------------------------------------------ feeds
    async def polymarket_loop(self):
        from websockets.asyncio.client import connect
        f, backoff = self.feeds["polymarket"], 1
        while True:
            tokens = list(self.pm_tokens)
            if not tokens:
                f.connected, f.subscribed = False, 0
                self.pm_changed.clear()
                await self._wait_change(self.pm_changed, 60)
                continue
            try:
                async with connect(PM_WS, open_timeout=15, ping_interval=None, max_size=2**23) as ws:
                    await ws.send(json.dumps({"assets_ids": tokens, "type": "market", "custom_feature_enabled": True}))
                    f.connected, f.error, f.subscribed, backoff = True, None, len(tokens), 1
                    self.pm_changed.clear()
                    last_ping = time.time()
                    while not self.pm_changed.is_set():
                        try:
                            raw = await asyncio.wait_for(ws.recv(), timeout=10)
                        except asyncio.TimeoutError:
                            raw = None
                        if time.time() - last_ping >= 10:
                            await ws.send("PING")
                            last_ping = time.time()
                        if raw and raw != "PONG":
                            try:
                                self.on_polymarket(json.loads(raw), time.time())
                            except ValueError:
                                pass
            except Exception as e:  # noqa: BLE001
                f.error = type(e).__name__ + ": " + str(e)[:120]
                log.info("polymarket ws: %s", f.error)
            f.connected = False
            f.reconnects += 1
            await asyncio.sleep(min(backoff, 60))
            backoff *= 2

    async def coinbase_loop(self):
        from websockets.asyncio.client import connect
        f, backoff = self.feeds["coinbase"], 1
        while True:
            products = sorted(self.crypto)
            try:
                async with connect(CB_WS, open_timeout=15, max_size=2**23) as ws:
                    await ws.send(json.dumps({"type": "subscribe", "product_ids": products, "channel": "ticker"}))
                    await ws.send(json.dumps({"type": "subscribe", "channel": "heartbeats"}))
                    f.connected, f.error, f.subscribed, backoff = True, None, len(products), 1
                    self.cb_changed.clear()
                    while not self.cb_changed.is_set():
                        try:
                            raw = await asyncio.wait_for(ws.recv(), timeout=30)
                        except asyncio.TimeoutError:
                            continue
                        try:
                            self.on_coinbase(json.loads(raw), time.time())
                        except ValueError:
                            pass
            except Exception as e:  # noqa: BLE001
                f.error = type(e).__name__ + ": " + str(e)[:120]
                log.info("coinbase ws: %s", f.error)
            f.connected = False
            f.reconnects += 1
            await asyncio.sleep(min(backoff, 60))
            backoff *= 2

    async def yahoo_loop(self):
        f = self.feeds["yahoo"]
        async with httpx.AsyncClient(timeout=15, headers={"User-Agent": "Mozilla/5.0 (pmintel live)"}) as c:
            while True:
                if not market_open():
                    f.connected, f.note = True, "השוק האמריקאי סגור — אין ציטוטים חדשים עד הפתיחה"
                    await asyncio.sleep(60)
                    continue
                f.note = "ציטוט כל 60 שניות; העיכוב נמדד מזמן הציטוט עצמו"
                ok = 0
                for sym in sorted(self.stocks):
                    try:
                        r = await c.get(YAHOO + sym, params={"range": "1d", "interval": "1m"})
                        r.raise_for_status()
                        meta = ((r.json().get("chart") or {}).get("result") or [{}])[0].get("meta") or {}
                        self.on_yahoo(sym, meta, time.time())
                        ok += 1
                    except Exception as e:  # noqa: BLE001
                        f.error = f"{sym}: {type(e).__name__}"
                    await asyncio.sleep(0.5)
                f.connected = ok > 0
                f.subscribed = len(self.stocks)
                if ok == len(self.stocks):
                    f.error = None
                await asyncio.sleep(max(5, 60 - 0.5 * len(self.stocks)))

    async def _wait_change(self, event, timeout):
        try:
            await asyncio.wait_for(event.wait(), timeout)
        except asyncio.TimeoutError:
            pass

    async def engine_loop(self):
        n = 0
        while True:
            now = time.time()
            try:
                if n % 20 == 0:               # every minute
                    await asyncio.to_thread(self.refresh_universe)
                self.evaluate(now)
                self.persist(now, ticks=(n % 20 == 0))
                if n % 1200 == 0:             # hourly: keep 7 days of sampled ticks
                    self.conn.execute("DELETE FROM live_ticks WHERE src_ts < now() - interval '7 days'")
            except Exception:
                log.exception("engine pass failed")
            n += 1
            await asyncio.sleep(3)

    async def run(self):
        await asyncio.to_thread(self.refresh_universe)
        await asyncio.gather(self.engine_loop(), self.polymarket_loop(), self.coinbase_loop(), self.yahoo_loop())


def _f(v):
    try:
        x = float(v)
        return x if x == x else None
    except (TypeError, ValueError):
        return None


def _ms(v):
    try:
        x = float(v)
        return x / 1000 if x > 1e11 else x
    except (TypeError, ValueError):
        return None


def _iso(v):
    """RFC 3339 with any number of fraction digits (Coinbase sends nanoseconds)."""
    import re
    try:
        t = re.sub(r"(\.\d{6})\d+", r"\1", str(v)).replace("Z", "+00:00")
        return datetime.fromisoformat(t).timestamp()
    except ValueError:
        return None


def _dt(t):
    return datetime.fromtimestamp(t, timezone.utc) if t else None


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    conn = db.connect()
    db.init_schema(conn)
    asyncio.run(Monitor(conn).run())


if __name__ == "__main__":
    main()
