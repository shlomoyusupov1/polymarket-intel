"""Plain-text research evidence from the production database: what each engine actually found.

  docker compose exec -T api python -m pmintel.research_summary

Prints, per domain: how much data exists and how fresh it is; every research run (including failed
or unfinished ones); the latest stock and crypto lab results out of sample, after costs, with their
limits; the crypto cost-sensitivity table; the Polymarket walk-forward verdict and what the order books
say about executable prices; forward paper tracking; tip sources. Nothing is estimated: a missing
table or report prints INSUFFICIENT DATA.
"""
from __future__ import annotations

from datetime import datetime, timezone

from . import db

V = {"candidate_for_paper": "HISTORICAL CANDIDATE", "edge_below_target": "EDGE, NOT NET PROFIT",
     "not_supported": "FAILED", "insufficient_data": "INSUFFICIENT DATA"}


def _one(conn, sql, params=()):
    try:
        return conn.execute(sql, params).fetchone()
    except Exception as e:  # noqa: BLE001 - table may not exist yet (autocommit: nothing to roll back)
        return ("n/a: " + type(e).__name__,)


def _rows(conn, sql, params=()):
    try:
        return conn.execute(sql, params).fetchall()
    except Exception:  # noqa: BLE001
        return []


def pc(v, d=1):
    return "—" if v is None else f"{v * 100:+.{d}f}%"


def pr(v):
    return "—" if v is None else f"{v * 100:.0f}%"


def age(ts, now):
    if ts is None:
        return "never"
    if isinstance(ts, str):
        return ts
    m = (now - ts).total_seconds() / 60
    return f"{m:.0f} min ago" if m < 120 else (f"{m / 60:.1f} h ago" if m < 2880 else f"{m / 1440:.1f} days ago")


def data_section(conn, now) -> list[str]:
    out = ["## Data by domain (count · newest)"]
    q = [("Polymarket markets", "SELECT count(*), max(updated_at) FROM markets"),
         ("Polymarket snapshots", "SELECT count(*), max(ts) FROM snapshots"),
         ("Polymarket order books", "SELECT count(*), max(ts) FROM orderbooks"),
         ("Polymarket price history", "SELECT count(*), max(ts) FROM price_history"),
         ("Tips (Telegram/manual)", "SELECT count(*), max(ts) FROM tips"),
         ("Tips with 5-day outcome", "SELECT count(*), max(outcome_at) FROM tips WHERE r5 IS NOT NULL"),
         ("SEC insider buys", "SELECT count(*), max(accepted) FROM sec_buys"),
         ("SEC 13D / earnings", "SELECT count(*), max(accepted) FROM sec_events"),
         ("ETF bars (paper room)", "SELECT count(*), max(day)::text FROM etf_bars"),
         ("Lab forward signals: stocks", "SELECT count(*), max(recorded_at) FROM lab_signals WHERE strategy NOT LIKE 'crypto:%%'"),
         ("Polymarket execution check rows", "SELECT count(*), max(recorded_at) FROM pm_paper WHERE rule <> 'none'"),
         ("Lab forward signals: crypto", "SELECT count(*), max(recorded_at) FROM lab_signals WHERE strategy LIKE 'crypto:%%'"),
         ("Host metrics", "SELECT count(*), max(ts) FROM host_metrics")]
    for name, sql in q:
        r = _one(conn, sql)
        out.append(f"- {name}: {r[0]} · {age(r[1], now) if len(r) > 1 else ''}")
    return out


def runs_section(conn, now) -> list[str]:
    out = ["## Research runs (all, newest first)"]
    rows = _rows(conn, """SELECT job, status, started_at, finished_at, items, left(coalesce(error, ''), 140)
                          FROM job_runs WHERE job IN ('stocklab', 'cryptolab', 'research', 'backfill', 'lab_paper')
                          ORDER BY started_at DESC LIMIT 25""")
    if not rows:
        out.append("INSUFFICIENT DATA: no research job has ever run")
    for job, st, s, f, items, err in rows:
        stuck = " STUCK (running > 2 h)" if st == "running" and (now - s).total_seconds() > 7200 else ""
        dur = f"{(f - s).total_seconds() / 60:.1f} min" if f else "unfinished"
        out.append(f"- {s:%Y-%m-%d %H:%M} {job}: {st}{stuck} · {dur} · items={items}" + (f" · {err}" if err else ""))
    for kind in ("stocklab_v1", "cryptolab_v1", "historical_v1", "etf_paper_v2"):
        r = _one(conn, "SELECT max(created_at), count(*) FROM research_reports WHERE kind = %s", (kind,))
        out.append(f"- report {kind}: {r[1] if len(r) > 1 else 0} saved · latest {age(r[0], now)}")
    return out


def lab_section(conn, kind: str, title: str, bench: str) -> list[str]:
    out = [f"## {title}"]
    r = _one(conn, "SELECT result, created_at FROM research_reports WHERE kind = %s ORDER BY id DESC LIMIT 1", (kind,))
    if not r or r[0] is None or isinstance(r[0], str):
        return out + [f"INSUFFICIENT DATA: no {kind} report yet"]
    rep, created = r
    st = rep.get("strategies") or {}
    out.append(f"run {created:%Y-%m-%d %H:%M} UTC · {len(rep.get('universe') or [])} symbols · data {rep.get('first_day')} … {rep.get('last_day')}"
               f" · missing: {', '.join(rep.get('missing') or []) or 'none'}")
    m = rep.get("method") or {}
    out.append(f"method: entry {m.get('entry')}, exit {m.get('exit')}, cost {m.get('cost')}, {m.get('walk_forward')}")
    b = rep.get("benchmark_stats")
    if b:
        out.append(f"{bench} buy-and-hold over the out-of-sample years {b['from']} … {b['to']}: total {pc(b['total_return'], 0)}, "
                   f"CAGR {pc(b['cagr'])}, volatility {b['volatility'] * 100:.0f}%, max drawdown {pc(b['max_drawdown'], 0)}")
    out.append("")
    out.append(f"{'strategy':24s} {'n':>5s} {'weeks':>5s} {'beat ' + bench:>9s} {'vs random':>9s} {'lo95':>7s} {'p_holm':>7s} {'avg win/loss':>15s} {'dd(sum)':>8s}  verdict")
    order = sorted(st.items(), key=lambda kv: (kv[1].get("verdict") != "candidate_for_paper", -(kv[1].get("edge_vs_baseline") or -9)))
    for k, v in order:
        ph = v.get("p_holm")
        out.append(f"{k:24s} {v.get('n', 0):5d} {v.get('clusters') or 0:5d} {pr(v.get('success_rate')):>9s} "
                   f"{pc(v.get('edge_vs_baseline'), 2):>9s} {pc(v.get('edge_lo95'), 2):>7s} "
                   f"{'—' if ph is None else f'{ph:.3f}':>7s} {pc(v.get('avg_win')) + '/' + pc(v.get('avg_loss')):>15s} "
                   f"{pc(v.get('max_dd_sum'), 0):>8s}  {V.get(v.get('verdict'), v.get('verdict'))}"
                   + ("  [warning pattern: scored as lag vs benchmark]" if v.get("side") == -1 else ""))
    sens = [(k, v["cost_sensitivity"]) for k, v in order if v.get("cost_sensitivity")]
    if sens:
        out += ["", f"cost sensitivity (assumed {rep.get('assumed_cost', 0) * 100:.1f}% round trip; edge vs random does not move with a fixed cost):",
                f"{'strategy':24s} " + " ".join(f"{c['cost'] * 100:>4.1f}%: win/mean      " for c in sens[0][1])]
        for k, cs in sens:
            out.append(f"{k:24s} " + " ".join(f"{pr(c['success_rate']):>5s}/{pc(c['mean_excess'], 2):>7s} {V.get(c['verdict'], '')[:6]:6s}" for c in cs))
    out += ["", "dd(sum) = deepest fall of the running sum of per-trade results (losing streaks), not an account drawdown.",
            "limits: universe = today's survivors (controlled by the random-entry baseline, not removed); "
                "overlapping trades in the same week are dependent (CI clustered by entry week); "
                "Holm across all strategies of this lab; daily bars only (no intraday fills)."]
    return out


def forward_section(conn) -> list[str]:
    from . import lab_paper as P
    from .cryptolab import CRYPTO
    out = ["## Forward paper tracking (signals sealed before entry)"]
    closed = _rows(conn, "SELECT strategy, excess, entry_day FROM lab_signals WHERE status = 'closed'")
    opened = _one(conn, "SELECT count(*) FILTER (WHERE status IN ('pending', 'open')), count(*) FROM lab_signals")
    out.append(f"open/pending {opened[0]} · total recorded {opened[1] if len(opened) > 1 else '—'} · closed {len(closed)}")
    for name, m in (("stocks", None), ("crypto", CRYPTO)):
        s = P.summary(P.split(closed, m) if m else P.split(closed))
        if not s:
            out.append(f"- {name}: INSUFFICIENT DATA (no closed forward trades yet)")
        for k, v in s.items():
            out.append(f"- {name} {k}: n={v['n']} win={pr(v['success_rate'])} mean={pc(v['mean_excess'], 2)} -> {v['status'].upper()}")
    return out


def polymarket_section(conn) -> list[str]:
    out = ["## Polymarket research"]
    r = _one(conn, "SELECT result, created_at FROM research_reports WHERE kind = 'historical_v1' ORDER BY id DESC LIMIT 1")
    if not r or r[0] is None or isinstance(r[0], str):
        out.append("INSUFFICIENT DATA: no historical research report yet")
    else:
        out.append(f"run {r[1]:%Y-%m-%d %H:%M} UTC")
        for h, x in (r[0] or {}).items():
            wf = (x or {}).get("walk_forward") or {}
            ot = wf.get("oos_trading") or {}
            label = ("24 h before SCHEDULED end (honest: no look-ahead on close time)" if h == "24_scheduled"
                     else f"{h} h before actual close")
            ci = ot.get("ci")
            out.append(f"- {label}: verdict {wf.get('verdict', 'n/a')} · OOS events {ot.get('events')} · "
                       f"mean/$ after taker fee {pc(ot.get('mean_return'), 2)} · CI "
                       f"{'—' if not ci else pc(ci[0], 2) + ' … ' + pc(ci[1], 2)} · rules {', '.join(ot.get('rules_chosen') or []) or '—'}")
        out.append("  note: history is mid/last-trade prices, not executable asks — a positive result is a hypothesis only")
    b = _one(conn, """SELECT count(*), round(100.0 * avg(valid::int), 1), count(DISTINCT market_id)
                      FROM orderbooks WHERE ts > now() - interval '24 hours'""")
    out.append(f"order books last 24 h: {b[0]} books, {b[2] if len(b) > 2 else '—'} markets, valid {b[1] if len(b) > 1 else '—'}% "
               "(valid = both sides quoted); no opportunity is marked executable without a live ask")
    return out


def pm_paper_section(conn) -> list[str]:
    from . import pm_paper as PP
    out = ["## Polymarket execution check (paper at real asks, 24 h before scheduled end, sealed)"]
    try:
        s = PP.summary(conn)
    except Exception as e:  # noqa: BLE001
        return out + [f"INSUFFICIENT DATA: {type(e).__name__} (table not created yet?)"]
    out.append(f"markets looked at {s['markets_seen']} · last {s['last_recorded']} · stake ${s['stake_usd']:.0f} · "
               f"validated needs {s['min_events']}+ settled events with the 95% lower bound > 0")
    if not s["rules"]:
        out.append("INSUFFICIENT DATA: no paper entry yet")
    for k, v in s["rules"].items():
        ci = v["ci_real"]
        out.append(f"- {k}{' [chosen by the study]' if v['chosen'] else ''}: recorded {v['recorded']} · fillable {pr(v['fill_rate'])} · "
                   f"ask above study price {pc(v['avg_ask_above_mid'], 1)} · settled events {v['events']} · "
                   f"real {pc(v['mean_real'], 2)} (CI {'—' if not ci else pc(ci[0], 2) + ' … ' + pc(ci[1], 2)}) · "
                   f"study-style on same trades {pc(v['mean_hist_same_trades'], 2)} -> {v['status'].upper()}")
    return out


def tips_section(conn) -> list[str]:
    from . import tips as T
    out = ["## Tip sources (5 days vs SPY after cost)"]
    try:
        board = T.leaderboard(conn)
    except Exception as e:  # noqa: BLE001
        return out + [f"n/a: {type(e).__name__}"]
    if not board:
        out.append("INSUFFICIENT DATA: no tips yet")
    for b in board:
        g = b["gate"]
        out.append(f"- {b['source']}: n={g['n']} win={pr(g.get('success_rate'))} edge={pc(g.get('edge_after_cost'), 2)} "
                   f"lo95={pc(g.get('edge_lo95'), 2)} -> {g['status'].upper()}")
    return out


def build(conn) -> str:
    now = datetime.now(timezone.utc)
    parts = [f"# Research evidence — {now:%Y-%m-%d %H:%M} UTC", ""]
    for sec in (data_section(conn, now), runs_section(conn, now),
                lab_section(conn, "stocklab_v1", "Stocks lab (out of sample, after cost)", "SPY"),
                lab_section(conn, "cryptolab_v1", "Crypto lab (out of sample, after cost)", "BTC"),
                forward_section(conn), polymarket_section(conn), pm_paper_section(conn), tips_section(conn)):
        parts += sec + [""]
    return "\n".join(parts)


if __name__ == "__main__":
    c = db.connect()
    c.autocommit = True
    print(build(c))
