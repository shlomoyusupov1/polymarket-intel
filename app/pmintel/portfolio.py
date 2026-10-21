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

from datetime import date, datetime, time, timezone
from zoneinfo import ZoneInfo

from .stocklab import STRATEGIES, side


def _event_at(day, strategy: str, is_entry: bool) -> datetime:
    """Sort fills by when cash actually becomes available, not just by calendar day.

    Stocks enter at 09:30 and close at 16:00 America/New_York; crypto uses UTC
    daily bars (00:00 entry; end-of-day exit). Events from both engines share
    one cash account, so sorting must use a common timezone.
    """
    d = date.fromisoformat(str(day))
    if strategy.startswith("crypto:"):
        clock = time(0, 0) if is_entry else time(23, 59, 59)
        return datetime.combine(d, clock, timezone.utc)
    clock = time(9, 30) if is_entry else time(16, 0)
    return datetime.combine(d, clock, ZoneInfo("America/New_York")).astimezone(timezone.utc)


def marked_curve(legs: list[dict], budget: float, marks: dict) -> list[dict]:
    """Daily equity, marked to market: budget + realized P&L of trades closed by that day + the open
    positions valued at that day's close (latest mark on or before the day; the entry price before any).
    legs: taken positions {id, entry_day, exit_day|None, size, units, entry_price, pnl|None}."""
    days = sorted({str(d) for leg in legs for d in (marks.get(leg["id"]) or {})}
                  | {str(leg["entry_day"]) for leg in legs} | {str(leg["exit_day"]) for leg in legs if leg["exit_day"]})
    out = []
    for d in days:
        eq = budget
        for leg in legs:
            ent, ex = str(leg["entry_day"]), (str(leg["exit_day"]) if leg["exit_day"] else None)
            if ex and ex <= d:
                eq += leg["pnl"]
            elif ent <= d:
                m = marks.get(leg["id"]) or {}
                past = [k for k in m if str(k) <= d]
                px = m[max(past)] if past else leg["entry_price"]
                eq += leg["units"] * px - leg["size"]
        out.append({"day": d, "equity": eq})
    return out


def drawdown(curve: list[dict]) -> float | None:
    if not curve:
        return None
    peak, dd = curve[0]["equity"], 0.0
    for c in curve:
        peak = max(peak, c["equity"])
        dd = min(dd, c["equity"] / peak - 1)
    return dd


def replay(rows: list[dict], budget: float, max_position: float, max_open: int,
           allowed=None, eligible_at_signal=None, marks: dict | None = None) -> dict:
    """rows: lab_signals as dicts (see api), any order. allowed(strategy)->bool filters strategies."""
    rows = sorted(rows, key=lambda r: (str(r["recorded_at"]), r["id"]))
    events, decisions = [], {}
    for r in rows:
        if allowed and not allowed(r["strategy"]):
            continue
        if eligible_at_signal and not eligible_at_signal(r):
            decisions[r["id"]] = ("skipped", "השיטה לא הייתה במצב הנדרש ברגע האיתות")
            continue
        rid, strategy = r["id"], r["strategy"]
        base = strategy.split(":")[-1]
        if base in STRATEGIES and side(base) == -1:
            decisions[rid] = ("skipped", "איתות אזהרה בלבד — אינו הוראת קנייה")
            continue
        if r.get("sealed_ok") is False:
            decisions[rid] = ("skipped", "האיתות נרשם מאוחר מדי — אינו זכאי לעסקה")
            continue
        if r["status"] in ("open", "closed") and r["entry_day"]:
            if not r.get("entry_price") or r["entry_price"] <= 0:
                decisions[rid] = ("skipped", "חסר מחיר כניסה תקף")
                continue
            events.append((_event_at(r["entry_day"], strategy, True), 1, r))
            if r["status"] == "closed" and r["exit_day"]:
                events.append((_event_at(r["exit_day"], strategy, False), 2, r))
    # Respect actual market sessions. A 16:00 exit cannot fund a 09:30 entry.
    events.sort(key=lambda e: (e[0], e[1], str(e[2]["recorded_at"])))
    cash, open_, trades, legs = budget, {}, [], {}
    realized = 0.0
    curve, peak, dd = [], budget, 0.0
    for at, kind, r in events:
        day = str(r["entry_day"] if kind == 1 else r["exit_day"])
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
            legs[rid] = {"id": rid, "entry_day": r["entry_day"], "exit_day": None, "size": size, "units": units,
                         "entry_price": r["entry_price"], "pnl": None}
            decisions[rid] = ("taken", "נכנס לתיק המדומה")
        elif rid in open_:
            o = open_.pop(rid)
            gross = o["units"] * r["entry_price"] * (1 + (r["ret"] or 0))
            fee = gross * (r["cost_rt"] or 0) / 2
            proceeds = gross - fee
            pnl = proceeds - o["size"]
            cash += proceeds
            realized += pnl
            legs[rid].update(exit_day=r["exit_day"], pnl=pnl)
            trades.append({"id": rid, "pnl": pnl, "pnl_pct": pnl / o["size"], "size": o["size"],
                           "bench": o["size"] * (r["spy_ret"] or 0), "exit_day": day})
            # Diagnostic only: closed-trade curve omits open-position losses.
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
            "wins": sum(1 for t in trades if t["pnl"] > 0),
            # true drawdown needs every open position marked daily (lab_marks); without marks: unknown
            "max_drawdown": drawdown(marked_curve(list(legs.values()), budget, marks)) if marks is not None and legs else None,
            "marked_curve": marked_curve(list(legs.values()), budget, marks) if marks is not None and legs else [],
            "closed_trade_drawdown": dd if trades else None,
            "drawdown_note": ("לפי שווי יומי של כל הפוזיציות (מחיר סגירה בכל יום מסחר), לא רק עסקאות שנסגרו."
                              if marks is not None and legs else
                              "טרם ניתן למדוד ירידה מרבית אמיתית: חסרים סימוני שווי במהלך חיי הפוזיציות."),
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
        if dec is not None:
            what, why = {"taken": "נכנס לתיק", "skipped": "דילוג"}[dec[0]], dec[1]
        elif r["status"] == "pending":
            what, why = "ממתין לכניסה", "נרשם לפני הפתיחה; ייכנס במחיר הפתיחה הבאה"
        else:
            what, why = "לא בתיק", "השיטה לא בתחום התיק שבחרת"
        out.append({"id": r["id"], "strategy": labels.get(r["strategy"], r["strategy"]), "symbol": r["symbol"],
                    "signal_day": r["signal_day"], "recorded_at": r["recorded_at"], "sha256": r["sha256"],
                    "lab_verdict": r.get("lab_verdict"), "strategy_state": r.get("strategy_state"),
                    "status": r["status"], "entry_day": r["entry_day"],
                    "entry_price": r["entry_price"], "exit_day": r["exit_day"], "hold": r["hold"],
                    "ret_after_cost": (r["ret"] - r["cost_rt"]) if r["ret"] is not None else None,
                    "excess": r["excess"], "decision": what, "why": why,
                    "sealed_before_entry": r.get("sealed_ok")})
    return out


def as_date(v):
    return v if isinstance(v, date) else (date.fromisoformat(str(v)) if v else None)
