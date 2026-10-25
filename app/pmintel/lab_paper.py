"""Forward paper tracking of the strategy lab: the only test a backtest can't fake.

Every trading day after the close, for each lab strategy with the parameters the lab chose last:
  1. record the symbols whose signal fired on today's completed bar. The record is timestamped and
     hashed; it counts only if it was written before the next session's 09:30 New York open;
  2. each recorded signal enters at the next open and exits at the close `hold` sessions later,
     exactly like the backtest, paying the same per-stock cost, against SPY over the same sessions;
  3. one open position per strategy and symbol (no stacking the same bet).

Returns are computed from one consistent adjusted price series at exit time, so a dividend paid
mid-trade is counted and doesn't distort the price. Nothing here places orders.

Crypto (cryptolab.py) is tracked the same way in the same table, with strategy names prefixed
"crypto:". Its daily bar closes at 00:00 UTC; the signal is sealed after that and entry is the open
one day later (the lag the backtest uses too), so the record always precedes the entry.

  docker compose run --rm labpaper --once
  (service `labpaper` loops every 30 min: stocks after 17:15 New York time on weekdays, crypto after
   00:15 UTC every day)
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import math
import time as _time
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from psycopg.types.json import Jsonb

from . import db, stocklab as L

log = logging.getLogger("pmintel.lab_paper")
NY = ZoneInfo("America/New_York")
READY_AFTER = time(17, 15)
GATE = {"min_n": 50, "win_rate": 0.60}


def complete(bars: list[dict], now_ny: datetime, m: L.Market = L.STOCKS) -> list[dict]:
    """Only bars that are final: stocks after 17:15 New York; a crypto day once the UTC day is over."""
    if m.key == "crypto":
        today = now_ny.astimezone(timezone.utc).date().isoformat()
        return [b for b in bars if b["day"] < today]
    today = now_ny.date().isoformat()
    return [b for b in bars if b["day"] < today or (b["day"] == today and now_ny.time() >= READY_AFTER)]


def next_open(day: str) -> datetime:
    """09:30 New York on the next weekday after `day` (holidays only make the deadline later)."""
    d = date.fromisoformat(day) + timedelta(days=1)
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return datetime.combine(d, time(9, 30), NY)


def deadline(day: str, m: L.Market = L.STOCKS) -> datetime:
    """The moment a signal from bar `day` must already be sealed: the entry bar's open."""
    if m.key == "crypto":
        return datetime.combine(date.fromisoformat(day) + timedelta(days=1 + m.lag), time(0, 0), timezone.utc)
    return next_open(day)


def plan(lab: dict) -> dict[str, dict]:
    """Strategy -> {'params', 'hold'} from the latest lab report (frozen until the next lab run)."""
    out = {}
    for name, v in (lab.get("strategies") or {}).items():
        cp = v.get("current_params")
        if cp and name in L.STRATEGIES:
            out[name] = {"params": cp["params"], "hold": int(cp["hold"]), "verdict": v.get("verdict")}
    return out


def state_now(hist_verdict: str | None, fwd: dict | None) -> str:
    """The strategy's research state at this moment, as it will be sealed with the signal."""
    if hist_verdict != "candidate_for_paper":
        return "not_eligible"
    return "confirmed" if fwd and fwd.get("status") == "confirmed" else "validating"


def record_signals(conn, feats: dict, strategies: dict, now: datetime, m: L.Market = L.STOCKS) -> int:
    """Write today's signals. Returns how many new rows were sealed."""
    n = 0
    closed = conn.execute("SELECT strategy, excess, entry_day FROM lab_signals WHERE status = 'closed'").fetchall()
    fwd = summary(split(closed, m))
    for base, p in strategies.items():
        name = m.prefix + base
        hits = L.live_signals(base, feats, p["params"])
        last = max(f["day"][-1] for f in feats.values())
        if now >= deadline(last, m):
            log.warning("too late to record %s signals for %s", name, last)
            continue
        state = state_now(p.get("verdict"), fwd.get(base))
        for sym in hits:
            open_ = conn.execute("SELECT 1 FROM lab_signals WHERE strategy = %s AND symbol = %s AND status IN ('pending', 'open')",
                                 (name, sym)).fetchone()
            if open_:
                continue
            body = {"strategy": name, "params": p["params"], "hold": p["hold"], "symbol": sym, "signal_day": last,
                    "recorded_at": now.isoformat(), "strategy_state": state}
            sha = hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()
            cur = conn.execute("""INSERT INTO lab_signals (strategy, params, hold, symbol, signal_day, recorded_at, sha256,
                                                           lab_verdict, strategy_state, cost_rt, status)
                                  VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'pending') ON CONFLICT DO NOTHING""",
                               (name, Jsonb(p["params"]), p["hold"], sym, last, now, sha, p.get("verdict"), state,
                                feats[sym]["cost"][-1]))
            n += cur.rowcount
    return n


def settle(conn, feats: dict, spy: list[dict], m: L.Market = L.STOCKS) -> int:
    """Fill entries and close trades whose hold has elapsed. Returns rows changed."""
    sidx = {b["day"]: k for k, b in enumerate(spy)}
    changed = 0
    like = "crypto:%"
    rows = conn.execute("""SELECT id, symbol, signal_day, hold, cost_rt, status, strategy FROM lab_signals
                           WHERE status IN ('pending', 'open') AND (strategy LIKE %s) = %s""",
                        (like, m.key == "crypto")).fetchall()
    for rid, sym, sday, hold, cost, status, name in rows:
        f = feats.get(sym)
        if not f:
            continue
        sday = sday.isoformat() if hasattr(sday, "isoformat") else str(sday)
        try:
            i = f["day"].index(sday)
        except ValueError:
            continue
        # latest observed price and typical daily range (14-day average high-low / close), for the plan card
        k = len(f["c"]) - 1
        rng = [(f["h"][j] - f["l"][j]) / f["c"][j] for j in range(max(0, k - 13), k + 1) if f["c"][j]]
        conn.execute("UPDATE lab_signals SET last_price = %s, last_day = %s, atr_pct = %s WHERE id = %s",
                     (f["c"][k], f["day"][k], sum(rng) / len(rng) if rng else None, rid))
        # daily closes while the paper position is held (entry .. exit), for marked-to-market equity
        e0 = i + 1 + m.lag
        for j in range(e0, min(len(f["day"]), i + hold + m.lag + 1)):
            conn.execute("INSERT INTO lab_marks (signal_id, day, close) VALUES (%s, %s, %s) ON CONFLICT DO NOTHING",
                         (rid, f["day"][j], f["c"][j]))
        e, x = i + 1 + m.lag, i + hold + m.lag
        if e >= len(f["day"]):
            continue
        if status == "pending":
            conn.execute("UPDATE lab_signals SET status = 'open', entry_day = %s, entry_price = %s WHERE id = %s",
                         (f["day"][e], f["o"][e], rid))
            changed += 1
        if x < len(f["day"]):
            k = sidx.get(f["day"][e])
            if k is None or k + hold - 1 >= len(spy) or spy[k + hold - 1]["day"] != f["day"][x]:
                continue
            r = f["c"][x] / f["o"][e] - 1
            rs = spy[k + hold - 1]["c"] / spy[k]["o"] - 1
            sd = L.side(name[len(m.prefix):]) if name[len(m.prefix):] in L.STRATEGIES else 1
            conn.execute("""UPDATE lab_signals SET status = 'closed', exit_day = %s, ret = %s, spy_ret = %s,
                            excess = %s, closed_at = now() WHERE id = %s""",
                         (f["day"][x], r, rs, sd * (r - rs) - cost, rid))
            changed += 1
    return changed


def _wilson_lo(k: int, n: int, z: float = 1.96) -> float:
    if not n:
        return 0.0
    p = k / n
    return max(0.0, (p + z * z / (2 * n) - z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))) / (1 + z * z / n))


def split(rows: list[tuple], m: L.Market = L.STOCKS) -> list[tuple]:
    """This market's rows, with the prefix removed from the strategy name."""
    if m.prefix:
        return [(r[0][len(m.prefix):], *r[1:]) for r in rows if r[0].startswith(m.prefix)]
    return [r for r in rows if ":" not in r[0]]


def summary(rows: list[tuple]) -> dict:
    """rows: (strategy, excess, entry_day) for closed trades -> per-strategy forward record."""
    by: dict[str, list] = {}
    for name, x, day in rows:
        by.setdefault(name, []).append((x, day.isoformat() if hasattr(day, "isoformat") else str(day)))
    out = {}
    for name, v in by.items():
        xs = [x for x, _ in v]
        n, k = len(xs), sum(1 for x in xs if x > 0)
        m, se = L.clustered_mean(xs, [L._week(d) for _, d in v])
        lo = _wilson_lo(k, n)
        confirmed = n >= GATE["min_n"] and k / n >= GATE["win_rate"] and lo > 0.5 and se is not None and m - 1.96 * se > 0
        wins, losses = [x for x in xs if x > 0], [x for x in xs if x <= 0]
        days = sorted(d for _, d in v)
        out[name] = {"n": n, "success_rate": k / n, "success_lo95": lo, "mean_excess": m,
                     "clusters": len({L._week(d) for d in days}), "first_entry": days[0], "last_entry": days[-1],
                     "avg_win": sum(wins) / len(wins) if wins else None,
                     "avg_loss": sum(losses) / len(losses) if losses else None,
                     "mean_lo95": (m - 1.96 * se) if se else None,
                     "status": "confirmed" if confirmed else ("collecting" if n < GATE["min_n"] else "not_confirmed")}
    return out


def run_once(conn, now: datetime | None = None, fetcher=L.fetch, pause: float = 0.4, m: L.Market = L.STOCKS) -> dict:
    now = now or datetime.now(timezone.utc)
    now_ny = now.astimezone(NY)
    lab = conn.execute("SELECT result FROM research_reports WHERE kind = %s ORDER BY id DESC LIMIT 1",
                       (m.report_kind,)).fetchone()
    if not lab:
        log.info("no %s lab report yet: run `docker compose run --rm %s` first", m.key, m.job)
        return {"recorded": 0, "settled": 0, "reason": "no_lab_report"}
    strategies = plan(lab[0])
    universe = L.UNIVERSE if m is L.STOCKS else m.universe  # L.UNIVERSE: tests narrow it
    bench = L.BENCH if m is L.STOCKS else m.bench
    with db.job_run(conn, m.paper_job) as run:
        spy = complete(fetcher(bench, range_="2y"), now_ny, m)
        feats, missing = {}, []
        for s in universe:
            try:
                b = complete(fetcher(s, range_="2y"), now_ny, m)
                if len(b) >= m.year + 10:
                    feats[s] = L.features(b, m)
            except Exception as e:  # noqa: BLE001
                missing.append(s)
                log.info("no bars for %s: %s", s, e)
            _time.sleep(pause)
        L.add_ranks(feats)
        settled = settle(conn, feats, spy, m)
        recorded = record_signals(conn, feats, strategies, now, m) if feats else 0
        run["items"] = recorded + settled
        run["detail"] = {"recorded": recorded, "settled": settled, "missing": missing,
                         "latest_bar": spy[-1]["day"] if spy else None}
    log.info("%s paper: recorded %d, settled %d, missing %s", m.key, recorded, settled, missing)
    return {"recorded": recorded, "settled": settled, "latest_bar": spy[-1]["day"] if spy else None}


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true")
    args = ap.parse_args()
    from .cryptolab import CRYPTO
    conn = db.connect()
    db.init_schema(conn)
    if args.once:
        print(run_once(conn), run_once(conn, m=CRYPTO))
        return
    done_for, crypto_done = None, None
    while True:
        now_ny = datetime.now(NY)
        if now_ny.weekday() < 5 and now_ny.time() >= READY_AFTER and done_for != now_ny.date():
            try:
                rep = run_once(conn)
                if rep.get("latest_bar") == now_ny.date().isoformat() or now_ny.hour >= 21 or rep.get("reason"):
                    done_for = now_ny.date()
            except Exception:
                log.exception("lab paper run failed; retrying in 30 min")
        utc = datetime.now(timezone.utc)
        if utc.time() >= time(0, 15) and crypto_done != utc.date():
            try:
                rep = run_once(conn, m=CRYPTO)
                yesterday = (utc.date() - timedelta(days=1)).isoformat()
                if rep.get("latest_bar") == yesterday or utc.hour >= 12 or rep.get("reason"):
                    crypto_done = utc.date()
            except Exception:
                log.exception("crypto paper run failed; retrying in 30 min")
        _time.sleep(1800)


if __name__ == "__main__":
    main()
