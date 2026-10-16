"""ETF paper study v2: pure, deterministic rules (no I/O).

Pre-registered in docs/ETF_PAPER_V2_PROTOCOL.md. The v1 study in the ai-trading-room repo
stays FAILED (three missed sessions) and is not modified; this is a new, separate study that
fixes v1's two structural blockers: whole-share sizing (a QQQ share > the 20% cap) and a
health gate that required evidence the infrastructure could never provide.

Signal rules, risk limits, costs and universe are carried over from v1 unchanged. Changes:
capital $3,000 per account, fractional quantities (0.001 share, min order $10), and a health
gate made of checks this server can actually perform (fresh complete bars for every symbol,
intent sealed before the open, server clock within 60 s of an external HTTP Date header).

Paper only: nothing here can place an order.
"""
from __future__ import annotations

import hashlib
import json
import math
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

NY = ZoneInfo("America/New_York")
UNIVERSE = ("SPY", "QQQ", "IWM", "EFA", "EEM", "IEF", "TLT")
ACCOUNTS = ("trend", "breakout")
PROTOCOL = {
    "version": "etf_paper_v2",
    "window_start": "2026-10-19",
    "window_end": "2027-04-16",
    "initial_capital": 3000.0,
    "cost_bps_per_side": 5,
    "max_positions": 5,
    "max_gross": 0.60,
    "max_single": 0.20,
    "max_risk": 0.0025,
    "max_hold_sessions": 126,
    "min_dollar_volume": 20_000_000.0,
    "min_order_usd": 10.0,
    "quantity_step": 0.001,
    "drawdown_halt": 0.92,
    "daily_loss_limit": 0.99,
    "max_clock_skew_s": 60,
    "warmup_bars": 254,
}
COST = PROTOCOL["cost_bps_per_side"] / 10000.0


def market_open(day: date) -> datetime:
    return datetime.combine(day, time(9, 30), NY)


def canonical_hash(payload: dict) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def valid_bar(b: dict) -> bool:
    vals = (b.get("o"), b.get("h"), b.get("l"), b.get("c"), b.get("v"))
    if any(v is None or not math.isfinite(float(v)) for v in vals):
        return False
    o, h, l, c, v = map(float, vals)
    return min(o, h, l, c) > 0 and v >= 0 and l <= min(o, c) and h >= max(o, c)


def features(bars: list[dict], strategy: str) -> dict:
    """bars: ordered daily bars ending at the decision bar (inclusive). Same formulas as v1."""
    if len(bars) < PROTOCOL["warmup_bars"]:
        raise ValueError("warmup_missing")
    i = len(bars) - 1
    c = [float(b["c"]) for b in bars]
    hi = [float(b["h"]) for b in bars]
    lo = [float(b["l"]) for b in bars]
    vol = [float(b["v"]) for b in bars]
    close = c[i]
    sma200 = sum(c[i - 199:i + 1]) / 200.0
    momentum = close / c[i - 126] - 1.0
    atr = sum(max(hi[j] - lo[j], abs(hi[j] - c[j - 1]), abs(lo[j] - c[j - 1]))
              for j in range(i - 19, i + 1)) / 20.0
    dollar = sorted(c[j] * vol[j] for j in range(i - 19, i + 1))
    median_dollar = dollar[len(dollar) // 2]
    regime = close > sma200
    out = {"close": close, "sma200": sma200, "momentum": momentum, "atr": atr,
           "median_dollar_volume": median_dollar, "regime": regime,
           "liquidity_ok": close >= 5 and median_dollar >= PROTOCOL["min_dollar_volume"]}
    if strategy == "trend":
        out.update(score=momentum, entry_signal=regime and momentum > 0, exit_signal=not regime)
    elif strategy == "breakout":
        prior_high = max(hi[i - 55:i])
        prior_low = min(lo[i - 20:i])
        out.update(prior_55_high=prior_high, prior_20_low=prior_low,
                   score=(close - prior_high) / atr if atr > 0 else -1e9,
                   entry_signal=regime and close > prior_high, exit_signal=close < prior_low)
    else:
        raise ValueError("unknown_strategy")
    return out


def build_intent(history: dict[str, list[dict]], based_on: date, strategy: str, health: dict) -> dict:
    """history[symbol]: bars up to and including `based_on`. health: data/clock evidence."""
    feats, problems = {}, []
    for sym in UNIVERSE:
        bars = history.get(sym) or []
        if not bars or bars[-1]["day"] != based_on.isoformat() or not all(valid_bar(b) for b in bars[-254:]):
            problems.append(f"{sym}:bar_missing_or_invalid")
            continue
        try:
            f = features(bars, strategy)
        except ValueError as e:
            problems.append(f"{sym}:{e}")
            continue
        f["symbol"] = sym
        feats[sym] = f
    ranked = sorted((f for f in feats.values() if f["entry_signal"]),
                    key=lambda f: (-f["score"], f["symbol"]))
    return {"protocol": PROTOCOL["version"], "strategy": strategy, "based_on": based_on.isoformat(),
            "features": feats, "ranked_candidates": [f["symbol"] for f in ranked],
            "health": {**health, "data_ok": not problems, "data_problems": problems}}


def _step(q: float) -> float:
    s = PROTOCOL["quantity_step"]
    return math.floor(q / s + 1e-9) * s


def replay(bars: dict[str, dict[str, dict]], intents: dict[tuple[str, str], dict],
           dividends: list[dict], sessions: list[str], prev_session: dict[str, str]) -> dict:
    """bars[symbol][day] (first-seen, complete bars); intents[(account, based_on)] =
    {"created_at": datetime, "payload": {...}}; sessions: window sessions with a SPY bar."""
    initial = PROTOCOL["initial_capital"]
    accounts = {a: _run_account(a, bars, intents, dividends, sessions, prev_session) for a in ACCOUNTS}
    bench = _benchmark(bars, dividends, sessions, initial)
    return {"protocol": PROTOCOL, "sessions": len(sessions),
            "first_session": sessions[0] if sessions else None,
            "last_session": sessions[-1] if sessions else None,
            "accounts": accounts, "benchmark_spy": bench, "orders_submitted": False}


def _history_until(bars_sym: dict[str, dict], day: str, n: int) -> list[dict]:
    days = sorted(d for d in bars_sym if d <= day)[-n:]
    return [bars_sym[d] | {"day": d} for d in days]


def _run_account(account, bars, intents, dividends, sessions, prev_session) -> dict:
    p = PROTOCOL
    cash, peak, halted = p["initial_capital"], p["initial_capital"], False
    prev_equity = p["initial_capital"]
    lots: dict[str, dict] = {}
    trades, decisions, curve, missed = [], [], [], []
    divs_income = 0.0
    for s in sessions:
        prev = prev_session[s]
        rec = intents.get((account, prev))
        sealed = rec is not None and rec["created_at"] < market_open(date.fromisoformat(s))
        if sealed:
            intent = rec["payload"]
            feats = intent["features"]
        else:
            missed.append({"session": s, "reason": "intent_missing" if rec is None else "intent_late"})
            intent, feats = None, {}
            for sym in lots:  # exits stay causal: computed from bars through `prev` only
                try:
                    feats[sym] = features(_history_until(bars[sym], prev, 300), account)
                except (ValueError, KeyError):
                    pass
        ex = {sym: bars[sym].get(s) for sym in UNIVERSE}
        # dividends: credited on the ex-date to lots held at the prior close (approximation)
        for d in dividends:
            if d["ex_date"] == s and d["symbol"] in lots:
                amt = lots[d["symbol"]]["quantity"] * d["amount"]
                cash += amt
                divs_income += amt
        # exits at the open
        for sym, lot in list(lots.items()):
            bar = ex.get(sym)
            if bar is None:
                continue
            o = float(bar["o"])
            f = feats.get(sym, {})
            reason = ("gap_stop" if o <= lot["stop"] else "rule_exit" if f.get("exit_signal")
                      else "time_exit" if lot["held"] >= p["max_hold_sessions"] else None)
            if reason:
                trades.append(_close(lot, sym, o, s, reason))
                cash += lot["quantity"] * o * (1 - COST)
                del lots[sym]
        open_equity = cash + sum(l["quantity"] * float(ex[sym]["o"]) for sym, l in lots.items() if ex.get(sym))
        gate = []
        if open_equity <= peak * p["drawdown_halt"]:
            halted = True
        if halted:
            gate.append("drawdown_halt")
        if open_equity <= prev_equity * p["daily_loss_limit"]:
            gate.append("daily_loss")
        if intent is not None:
            h = intent["health"]
            if not h.get("data_ok"):
                gate.append("health_data")
            if not h.get("clock_ok"):
                gate.append("health_clock")
        for sym in (intent["ranked_candidates"] if intent else []):
            f = feats[sym]
            d = {"session": s, "symbol": sym, "status": "rejected", "reasons": []}
            bar = ex.get(sym)
            if sym in lots:
                d["reasons"] = ["already_held"]
            elif not f["liquidity_ok"]:
                d["reasons"] = ["liquidity_or_price"]
            elif gate:
                d["reasons"] = list(gate)
            elif len(lots) >= p["max_positions"]:
                d["reasons"] = ["position_count"]
            elif bar is None:
                d["reasons"] = ["execution_bar_missing"]
            if d["reasons"]:
                decisions.append(d)
                continue
            o = float(bar["o"])
            entry = o * (1 + COST)
            stop = entry - 2 * f["atr"]
            equity = cash + sum(l["quantity"] * float(ex[x]["o"]) for x, l in lots.items() if ex.get(x))
            gross = sum(l["quantity"] * float(ex[x]["o"]) for x, l in lots.items() if ex.get(x))
            if stop <= 0:
                d["reasons"] = ["invalid_stop"]
                decisions.append(d)
                continue
            cap = min(equity * p["max_risk"] / (entry - stop), equity * p["max_single"] / entry,
                      cash / entry, max(0.0, equity * p["max_gross"] - gross) / entry)
            q = _step(max(0.0, cap))
            if q * entry < p["min_order_usd"]:
                d["reasons"] = ["below_min_order"]
                decisions.append(d)
                continue
            cash -= q * entry
            lots[sym] = {"quantity": q, "entry": entry, "basis": q * entry, "stop": stop,
                         "entry_session": s, "held": 0}
            d.update(status="filled", quantity=q, entry=entry, stop=stop, notional=q * entry,
                     initial_risk=q * (entry - stop))
            decisions.append(d)
        # intraday stops; then mark at the close
        for sym, lot in list(lots.items()):
            bar = ex.get(sym)
            if bar is None:
                continue
            lot["held"] += 1
            if float(bar["l"]) <= lot["stop"]:
                px = min(float(bar["o"]), lot["stop"])
                trades.append(_close(lot, sym, px, s, "stop"))
                cash += lot["quantity"] * px * (1 - COST)
                del lots[sym]
        market = sum(l["quantity"] * float((ex.get(sym) or {}).get("c") or l["entry"]) for sym, l in lots.items())
        equity = cash + market
        peak = max(peak, equity)
        prev_equity = equity
        curve.append({"session": s, "equity": equity, "cash": cash, "market_value": market,
                      "positions": sorted(lots)})
    return _summary(account, curve, trades, decisions, missed, lots, bars, divs_income, halted)


def _close(lot, sym, raw_px, session, reason):
    fill = raw_px * (1 - COST)
    return {"symbol": sym, "entry_session": lot["entry_session"], "exit_session": session,
            "quantity": lot["quantity"], "entry": lot["entry"], "exit": fill,
            "pnl": lot["quantity"] * fill - lot["basis"], "reason": reason}


def _summary(account, curve, trades, decisions, missed, lots, bars, divs, halted):
    initial = PROTOCOL["initial_capital"]
    last = curve[-1] if curve else {"equity": initial, "cash": initial, "session": None}
    peak, mdd = initial, 0.0
    for pt in curve:
        peak = max(peak, pt["equity"])
        mdd = min(mdd, pt["equity"] / peak - 1)
    open_pos = []
    liquidation = last["cash"]
    for sym, l in sorted(lots.items()):
        px = float((bars[sym].get(last["session"]) or {}).get("c") or l["entry"])
        liquidation += l["quantity"] * px * (1 - COST)
        open_pos.append({"symbol": sym, "quantity": l["quantity"], "entry": l["entry"], "stop": l["stop"],
                         "entry_session": l["entry_session"], "mark": px,
                         "unrealized_pnl": l["quantity"] * px - l["basis"]})
    rejected: dict[str, int] = {}
    for d in decisions:
        for r in d["reasons"]:
            rejected[r] = rejected.get(r, 0) + 1
    wins = [t for t in trades if t["pnl"] > 0]
    return {"account": account, "initial": initial, "marked_equity": last["equity"],
            "liquidated_equity": liquidation, "return": liquidation / initial - 1,
            "max_drawdown": mdd, "realized_pnl": sum(t["pnl"] for t in trades),
            "dividend_income": divs, "closed_trades": len(trades),
            "win_rate": len(wins) / len(trades) if trades else None,
            "filled_entries": sum(d["status"] == "filled" for d in decisions),
            "rejected_reasons": rejected, "missed_sessions": missed, "halted": halted,
            "open_positions": open_pos, "trades": trades, "decisions": decisions, "curve": curve}


def _benchmark(bars, dividends, sessions, initial):
    spy = bars.get("SPY", {})
    if not sessions or sessions[0] not in spy:
        return None
    shares = initial * (1 - COST) / float(spy[sessions[0]]["o"])
    cash, curve = 0.0, []
    for s in sessions:
        for d in dividends:
            if d["symbol"] == "SPY" and d["ex_date"] == s:
                cash += shares * d["amount"]
        curve.append({"session": s, "equity": cash + shares * float(spy[s]["c"])})
    liq = cash + shares * float(spy[sessions[-1]]["c"]) * (1 - COST)
    peak, mdd = initial, 0.0
    for pt in curve:
        peak = max(peak, pt["equity"])
        mdd = min(mdd, pt["equity"] / peak - 1)
    return {"initial": initial, "liquidated_equity": liq, "return": liq / initial - 1,
            "dividend_income": cash, "max_drawdown": mdd, "curve": curve,
            "convention": "SPY buy-and-hold from the first session open; 5 bp each side; dividends credited on ex-date"}
