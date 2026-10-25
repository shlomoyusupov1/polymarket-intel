"""One scoreboard for every strategy and source, across engines (stocks, crypto, Polymarket, tip sources).

Each entry has the same fields so the evidence can be compared without pretending a contract's
probability, a coin's return and a stock's return are the same thing:

  engine      stocks | crypto | polymarket | sources
  kind        historical — tested on past data, out of sample (can never prove executability)
              forward    — signals recorded before the fact, paper money, real prices
              measured   — someone else's calls, recorded when they arrived, scored afterwards
  n           sample size the verdict rests on;  clusters: independent groups (entry weeks / events)
  period      first … last observation
  success     share that beat the benchmark after costs (None when not defined)
  mean_net    average result per trade after costs, in the engine's own unit (see `unit`)
  mean_lo95   95% lower bound of the figure that decides the verdict (None when not computable)
  avg_win / avg_loss, max_drawdown (definition in `dd_note`), benchmark
  status      one of STATUSES below — the only vocabulary the site shows
  why         what decides the status;  missing: the pre-registered criteria not yet met

Statuses (strictly ordered by how much has been shown):
  confirmed     Forward Confirmed — passed pre-registered criteria on signals recorded before the fact
                (still not a promise of real-money profit)
  validating    Forward Validation — being tracked forward with paper money; no conclusion yet
  candidate     Historical Candidate — passed out-of-sample on past data only: a hypothesis
  failed        Not Supported / Failed — no edge after costs, or the forward test failed
  insufficient  Insufficient Data — too few observations for any conclusion
"""
from __future__ import annotations

STATUSES = ("confirmed", "validating", "candidate", "failed", "insufficient")
BENCH = {"stocks": "SPY", "crypto": "ביטקוין"}
UNIT = {"stocks": "מניות", "crypto": "מטבעות"}
LAB_GATE = "50+ עסקאות, יתרון מוכח על קנייה אקראית ששורד תיקון Holm, רווח נטו מוכח מול המדד אחרי עלויות, חיובית ברוב השנים"


def _e(**kw) -> dict:
    base = {"engine": None, "kind": None, "id": None, "label": None, "version": None, "updated": None,
            "data": None, "period": None, "n": None, "clusters": None, "success": None, "mean_net": None,
            "mean_lo95": None, "unit": None, "avg_win": None, "avg_loss": None, "max_drawdown": None,
            "dd_note": None, "benchmark": None, "verdict": None, "status": "insufficient", "why": None,
            "missing": [], "sensitivity": None, "assumed_cost": None}
    base.update(kw)
    assert base["status"] in STATUSES, base["status"]
    return base


def _period(a, b) -> str | None:
    return f"{a} … {b}" if a and b else None


def lab_missing(v: dict) -> list[str]:
    """The stock/crypto lab gate, criterion by criterion (stocklab.verdict)."""
    out = []
    if (v.get("n") or 0) < 50:
        out.append(f"50 עסקאות מחוץ למדגם (יש {v.get('n') or 0})")
    if not (v.get("edge_lo95") is not None and v["edge_lo95"] > 0):
        out.append("גבול תחתון חיובי מעל קנייה אקראית")
    if not (v.get("p_holm") is not None and v["p_holm"] < 0.05):
        out.append("שרידות בתיקון לבדיקת הרבה שיטות (Holm)")
    c = v.get("gate_checks") or {}
    if not c.get("net_profit_vs_bench", (v.get("mean_excess_lo95") or 0) > 0):
        out.append("רווח נטו מוכח מול המדד אחרי עלויות (גבול תחתון חיובי)")
    if c and not c.get("most_years_positive"):
        out.append("חיובית ב-60% מהשנים לפחות")
    if c and not c.get("survives_2x_cost", True):
        out.append("נשארת ברווח גם בעלות כפולה")
    return out


def from_stocklab(report: dict | None, updated=None, engine: str = "stocks") -> list[dict]:
    if not report:
        return []
    out, b = [], BENCH[engine]
    pre = "" if engine == "stocks" else engine + ":"
    for k, v in (report.get("strategies") or {}).items():
        verdict = v.get("verdict")
        status = {"candidate_for_paper": "candidate", "insufficient_data": "insufficient"}.get(verdict, "failed")
        out.append(_e(engine=engine, kind="historical", id=f"lab:{pre}{k}", label=v.get("label", k),
                      version=f"{'stock' if engine == 'stocks' else engine}lab_v1", updated=updated,
                      data=f"Yahoo daily, {len(report.get('universe') or [])} {UNIT[engine]}",
                      period=_period(v.get("first_entry"), v.get("last_entry")),
                      n=v.get("n"), clusters=v.get("clusters"), success=v.get("success_rate"),
                      mean_net=v.get("edge_vs_baseline"), mean_lo95=v.get("edge_lo95"),
                      unit="לעסקה, מעל קנייה אקראית אחרי עלות", avg_win=v.get("avg_win"), avg_loss=v.get("avg_loss"),
                      max_drawdown=v.get("max_dd_sum"), dd_note="סכום עסקאות מצטבר, לא תיק",
                      benchmark=f"{b} + קנייה אקראית", verdict=verdict, status=status,
                      why=_lab_why(v, b), missing=lab_missing(v) if status != "candidate" else [],
                      sensitivity=v.get("cost_sensitivity"), assumed_cost=report.get("assumed_cost")))
    return out


def _lab_why(v: dict, bench: str = "SPY") -> str:
    side = " (נר אזהרה: נמדד אם הנכס פיגר אחרי האיתות)" if v.get("side") == -1 else ""
    if v.get("verdict") == "insufficient_data":
        return f"רק {v.get('n') or 0} עסקאות מחוץ למדגם (צריך 50){side}"
    if v.get("verdict") == "candidate_for_paper":
        return f"עברה על העבר מול {bench} וקנייה אקראית, ושרדה את התיקון. השערה בלבד עד שתוכח קדימה{side}"
    if v.get("verdict") == "edge_below_target":
        return f"יש יתרון על קנייה אקראית, אבל עוד לא רווח נטו מוכח מול המדד{side}"
    if (v.get("edge_lo95") or 0) > 0:
        return f"גבול תחתון חיובי, אבל לא שורד את התיקון לבדיקת הרבה שיטות{side}"
    return f"אין יתרון על קנייה אקראית אחרי עלויות{side}"


def from_lab_forward(summary: dict, lab_report: dict | None, engine: str = "stocks") -> list[dict]:
    st = (lab_report or {}).get("strategies") or {}
    out, b = [], BENCH[engine]
    pre = "" if engine == "stocks" else engine + ":"
    for k, v in summary.items():
        hist = st.get(k, {}).get("verdict")
        missing = []
        if v["n"] < 50:
            missing.append(f"50 עסקאות קדימה (יש {v['n']})")
        if not (v.get("mean_lo95") is not None and v["mean_lo95"] > 0):
            missing.append(f"רווח נטו מוכח מול {b} אחרי עלות (גבול תחתון חיובי)")
        if hist != "candidate_for_paper":
            missing.append("מעבר בבדיקה על העבר")
        if v["status"] == "confirmed" and hist == "candidate_for_paper":
            status, why = "confirmed", f"עברה על העבר וגם קדימה: 50+ עסקאות ורווח נטו מוכח מול {b} אחרי עלות. עדיין לא הבטחה לרווח"
        elif v["status"] == "not_confirmed":
            status, why = "failed", "50+ עסקאות קדימה והשער לא הושג"
        else:
            status = "validating"
            why = (f"{v['n']} עסקאות קדימה עד עכשיו (צריך 50)" if v["n"] < 50
                   else "חיובית קדימה, אבל לא עברה את הבדיקה על העבר — לא נחשבת")
        out.append(_e(engine=engine, kind="forward", id=f"fwd:{pre}{k}", label=st.get(k, {}).get("label", k),
                      version="lab_paper", data="איתותים נחתמים לפני הכניסה; Yahoo daily",
                      period=_period(v.get("first_entry"), v.get("last_entry")),
                      n=v["n"], clusters=v.get("clusters"), success=v["success_rate"], mean_net=v["mean_excess"],
                      mean_lo95=v["mean_lo95"], unit=f"לעסקה, מול {b} אחרי עלות", avg_win=v.get("avg_win"),
                      avg_loss=v.get("avg_loss"), benchmark=f"{b} באותם ימים", verdict=v["status"], status=status,
                      why=why, missing=missing if status != "confirmed" else []))
    return out


def from_tips(board: list[dict]) -> list[dict]:
    out = []
    for b in board:
        g = b.get("gate") or {}
        status = {"reliable": "confirmed", "promising": "validating", "not_reliable": "failed"}.get(g.get("status"), "insufficient")
        why = ("ירד בגלל התיקון לריבוי מקורות" if g.get("downgraded") else
               {"confirmed": "50+ טיפים, רווח נטו מוכח מול SPY אחרי עלות, שרד את התיקון. עדיין לא הבטחה לרווח",
                "validating": "חיובי עד עכשיו, עוד לא מוכח",
                "failed": "מפסיד ל-SPY אחרי עלויות",
                "insufficient": f"{g.get('n') or 0} טיפים נמדדו (צריך 30 כדי לשפוט)"}[status])
        if g.get("downgraded"):
            status = "failed"
        missing = []
        if (g.get("n") or 0) < 50:
            missing.append(f"50 טיפים (יש {g.get('n') or 0})")
        if not ((g.get("edge_lo95") or 0) > 0):
            missing.append("רווח נטו מוכח מול SPY (גבול תחתון חיובי)")
        out.append(_e(engine="sources", kind="measured", id=f"src:{b['source']}", label=b["source"],
                      version="tips gate", updated=b.get("last_tip"), data="Telegram / SEC / manual, Yahoo daily",
                      n=g.get("n"), clusters=g.get("symbols"), success=g.get("success_rate"),
                      mean_net=g.get("edge_after_cost"), mean_lo95=g.get("edge_lo95"),
                      unit="ל-5 ימים, מול SPY אחרי עלות", avg_win=g.get("avg_win"), avg_loss=g.get("avg_loss"),
                      benchmark="SPY באותם 5 ימים", verdict=g.get("status"), status=status, why=why,
                      missing=missing if status != "confirmed" else []))
    return out


def from_etf(report: dict | None, updated=None) -> list[dict]:
    if not report:
        return []
    spy = report.get("benchmark_spy") or {}
    out = []
    for name, a in (report.get("accounts") or {}).items():
        trades = a.get("closed_trades") or 0
        out.append(_e(engine="stocks", kind="forward", id=f"etf:{name}", label=f"חדר מסחר: {name}",
                      version="etf_paper_v2 (frozen)", updated=updated, data="Yahoo daily ETF bars, intents sealed pre-open",
                      n=trades, success=a.get("win_rate"),
                      mean_net=(a.get("return") - spy.get("return")) if a.get("return") is not None and spy.get("return") is not None else None,
                      unit="תשואה כוללת מול SPY", max_drawdown=a.get("max_drawdown"), dd_note="חשבון מדומה",
                      benchmark=f"SPY בהחזקה ({(spy.get('return') or 0) * 100:+.1f}%)", verdict="collecting",
                      status="validating" if trades else "insufficient",
                      why="ניסוי של חצי שנה עד 16.4.2027; נשפט בסוף", missing=["סוף הניסוי (16.4.2027)"]))
    return out


def from_polymarket(report: dict | None, updated=None) -> list[dict]:
    if not report:
        return []
    out = []
    for h, r in (report or {}).items():
        wf = (r or {}).get("walk_forward") or {}
        if not wf:
            continue
        ot = wf.get("oos_trading") or {}
        ci = ot.get("ci") or [None, None]
        verdict = wf.get("verdict")
        status = {"supported": "candidate", "oos_positive_needs_execution_check": "candidate",
                  "insufficient_data": "insufficient"}.get(verdict, "failed")
        missing = ["מעקב קדימה במחיר הקנייה האמיתי מספר הפקודות"]
        if verdict == "oos_positive_needs_execution_check":
            missing.insert(0, "אימות ביצוע: מחיר שאפשר באמת לקנות בו, לא מחיר אמצע")
        out.append(_e(engine="polymarket", kind="historical", id=f"pm:wf:{h}", label=f"כללי טווח מחיר, {h} שעות לפני הסגירה",
                      version="walk-forward v1 + taker fees", updated=updated,
                      data="Polymarket price history (mid/last trade, not executable)",
                      n=ot.get("events"), clusters=ot.get("events"), mean_net=ot.get("mean_return"), mean_lo95=ci[0],
                      unit="לכל דולר, אחרי עמלת taker", benchmark="מחיר השוק כתחזית", verdict=verdict, status=status,
                      why={"insufficient_data": "אין מספיק שווקים מוכרעים עם היסטוריה",
                           "not_supported": "אף כלל לא הרוויח מחוץ למדגם אחרי עלויות",
                           "oos_positive_needs_execution_check": "חיובי מחוץ למדגם על מחירי אמצע — לא הוכח שאפשר לבצע",
                           "supported": "עבר את השערים ההיסטוריים; השערה עד מעקב קדימה"}.get(verdict, ""),
                      missing=missing))
    return out


def summarize(entries: list[dict]) -> dict:
    out: dict = {}
    for e in entries:
        s = out.setdefault(e["engine"], {k: 0 for k in STATUSES})
        s[e["status"]] += 1
    return out
