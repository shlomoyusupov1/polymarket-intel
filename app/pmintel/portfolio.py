"""Virtual portfolio and decision history, replayed from the forward paper signals (lab_signals).

Every signal in lab_signals was sealed before its entry (lab_paper.py), so this is a paper account
built from decisions recorded in advance — not a backtest. The replay applies the owner's settings:
  * start with `budget` in cash;
  * signals are taken in the order they were recorded; a signal opens a position of
    max_position_usd (capped by the cash left) at its paper entry price, paying half the round-trip
    cost on entry and half on exit;
  * at most max_open positions at once, and never the same symbol twice at once; a signal that
    doesn't fit is recorded in the history as skipped, with the reason;
  * a position closes when its paper trade closes (time exit), at its paper exit price;
  * open positions are valued at the last observed price.
`scope="proven"` uses only strategies that are at least historical candidates; `scope="all"` uses
every tracked strategy, including ones that failed (for learning only — clearly labelled).
"""
from __future__ import annotations

from datetime import date


def replay(rows: list[dict], budget: float, max_position: float, max_open: int, allowed=None) -> dict:
    """rows: lab_signals as dicts (see api), any order. allowed(strategy)->bool filters strategies."""
    rows = sorted(rows, key=lambda r: (str(r["recorded_at"]), r["id"]))
    events = []
    for r in rows:
        if allowed and not allowed(r["strategy"]):
            continue
        if r["status"] in ("open", "closed") and r["entry_day"]:
            events.append((str(r["entry_day"]), 1, r))
        if r["status"] == "closed" and r["exit_day"]:
            events.append((str(r["exit_day"]), 2, r))
    # exits before entries on the same day free the slot for that day's entries
    events.sort(key=lambda e: (e[0], -e[1], str(e[2]["recorded_at"])))
    cash, open_, trades, decisions = budget, {}, [], {}
    realized = 0.0
    curve, peak, dd = [], budget, 0.0
    for day, kind, r in events:
        rid = r["id"]
        if kind == 1:
            if len(open_) >= max_open:
                decisions[rid] = ("skipped", f"כבר היו {max_open} עסקאות פתוחות (המגבלה בפרופיל שלך)")
                continue
            if any(o["symbol"] == r["symbol"] for o in open_.values()):
                decisions[rid] = ("skipped", "כבר הייתה פוזיציה פתוחה באותו נכס")
                continue
            size = min(max_position, cash)
            if size < 1:
                decisions[rid] = ("skipped", "לא נשאר מזומן פנוי")
                continue
            fee = size * (r["cost_rt"] or 0) / 2
            units = (size - fee) / r["entry_price"]
            cash -= size
            open_[rid] = {"row": r, "symbol": r["symbol"], "size": size, "units": units, "entry_fee": fee}
            decisions[rid] = ("taken", "נכנס לתיק המדומה")
        elif rid in open_:
            o = open_.pop(rid)
            gross = o["units"] * r["entry_price"] * (1 + (r["ret"] or 0))
            fee = gross * (r["cost_rt"] or 0) / 2
            proceeds = gross - fee
            pnl = proceeds - o["size"]
            cash += proceeds
            realized += pnl
            trades.append({"id": rid, "pnl": pnl, "pnl_pct": pnl / o["size"], "size": o["size"],
                           "bench": o["size"] * (r["spy_ret"] or 0), "exit_day": day})
            # equity on closed trades only (open ones are not marked in the curve)
            eq = budget + realized
            peak = max(peak, eq)
            dd = min(dd, eq / peak - 1)
            curve.append({"day": day, "equity": eq})
    positions, unrealized = [], 0.0
    for rid, o in open_.items():
        r = o["row"]
        px = r.get("last_price") or r["entry_price"]
        value = o["units"] * px
        u = value - o["size"]
        unrealized += u
        positions.append({"id": rid, "symbol": r["symbol"], "strategy": r["strategy"], "entry_day": r["entry_day"],
                          "entry_price": r["entry_price"], "size": o["size"], "units": o["units"], "price": px,
                          "price_day": r.get("last_day"), "value": value, "pnl": u, "pnl_pct": u / o["size"],
                          "hold": r["hold"]})
    invested = sum(p["value"] for p in positions)
    bench = sum(t["bench"] for t in trades)
    return {"budget": budget, "cash": cash, "value": cash + invested, "realized": realized, "unrealized": unrealized,
            "return": (cash + invested) / budget - 1, "closed": len(trades),
            "wins": sum(1 for t in trades if t["pnl"] > 0), "max_drawdown": dd if trades else None,
            "bench_same_trades": bench, "positions": positions, "trades": trades, "curve": curve,
            "decisions": decisions,
            "exposure": _exposure(positions)}


def _exposure(positions: list[dict]) -> dict:
    out: dict[str, float] = {}
    for p in positions:
        k = "crypto" if p["symbol"].endswith("-USD") else "stocks"
        out[k] = out.get(k, 0.0) + p["value"]
    return out


def history(rows: list[dict], decisions: dict, labels: dict) -> list[dict]:
    """Every recorded signal, newest first, with what the replay decided and how it ended."""
    out = []
    for r in sorted(rows, key=lambda r: (str(r["recorded_at"]), r["id"]), reverse=True):
        dec = decisions.get(r["id"])
        if r["status"] == "pending":
            what, why = "ממתין לכניסה", "נרשם לפני הפתיחה; ייכנס במחיר הפתיחה הבאה"
        elif dec is None:
            what, why = "לא בתיק", "השיטה לא בתחום התיק שבחרת"
        else:
            what, why = {"taken": "נכנס לתיק", "skipped": "דילוג"}[dec[0]], dec[1]
        out.append({"id": r["id"], "strategy": labels.get(r["strategy"], r["strategy"]), "symbol": r["symbol"],
                    "signal_day": r["signal_day"], "recorded_at": r["recorded_at"], "sha256": r["sha256"],
                    "lab_verdict": r.get("lab_verdict"), "status": r["status"], "entry_day": r["entry_day"],
                    "entry_price": r["entry_price"], "exit_day": r["exit_day"], "hold": r["hold"],
                    "ret_after_cost": (r["ret"] - r["cost_rt"]) if r["ret"] is not None else None,
                    "excess": r["excess"], "decision": what, "why": why,
                    "sealed_before_entry": r.get("sealed_ok")})
    return out


def as_date(v):
    return v if isinstance(v, date) else (date.fromisoformat(str(v)) if v else None)
