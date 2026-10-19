"""One scoreboard for every strategy and source, across engines (stocks, Polymarket, tip sources).

Each entry has the same fields so the evidence can be compared without pretending a contract's
probability and a stock's return are the same thing:

  engine     stocks | polymarket | sources
  kind       historical  — tested on past data, out of sample (can never prove executability)
             forward     — signals recorded before the fact, paper money, real prices
             measured    — someone else's calls, measured after the fact
  n          sample size the verdict rests on
  success    share of trades that beat the benchmark after costs (None when not defined)
  mean_net   average result per trade after costs, in the engine's own unit (see `unit`)
  mean_lo95  95% lower bound of mean_net (None when not computable)
  status     passed | promising | failed | collecting  (normalised from each engine's own verdict)
  why        one line: what decides the status
"""
from __future__ import annotations

STATUS = {
    # stock lab / forward tracker
    "candidate_for_paper": "passed", "confirmed": "passed", "edge_below_target": "promising",
    "not_supported": "failed", "not_confirmed": "failed", "insufficient_data": "collecting", "collecting": "collecting",
    # tip-source gate
    "reliable": "passed", "promising": "promising", "not_reliable": "failed",
    # Polymarket walk-forward
    "supported": "passed", "oos_positive_needs_execution_check": "promising",
}


def _e(**kw) -> dict:
    base = {"engine": None, "kind": None, "id": None, "label": None, "version": None, "updated": None,
            "data": None, "n": None, "success": None, "mean_net": None, "mean_lo95": None, "unit": None,
            "max_drawdown": None, "benchmark": None, "verdict": None, "status": "collecting", "why": None}
    base.update(kw)
    base["status"] = STATUS.get(base["verdict"], base["status"])
    return base


def from_stocklab(report: dict | None, updated=None) -> list[dict]:
    if not report:
        return []
    out = []
    for k, v in (report.get("strategies") or {}).items():
        out.append(_e(engine="stocks", kind="historical", id=f"lab:{k}", label=v.get("label", k), version="stocklab_v1",
                      updated=updated, data=f"Yahoo daily, {report.get('first_day')}…{report.get('last_day')}, "
                      f"{len(report.get('universe') or [])} stocks",
                      n=v.get("n"), success=v.get("success_rate"), mean_net=v.get("edge_vs_baseline"),
                      mean_lo95=v.get("edge_lo95"), unit="לעסקה, מעל קנייה אקראית",
                      benchmark="SPY + קנייה אקראית", verdict=v.get("verdict"),
                      why=_lab_why(v)))
    return out


def _lab_why(v: dict) -> str:
    if v.get("verdict") == "insufficient_data":
        return f"רק {v.get('n') or 0} עסקאות מחוץ למדגם (צריך 50)"
    if v.get("verdict") == "candidate_for_paper":
        return "ניצחה את SPY ואת הקנייה האקראית מחוץ למדגם, ושרדה את התיקון לריבוי בדיקות"
    if (v.get("edge_lo95") or 0) > 0:
        return "גבול תחתון חיובי, אבל " + ("מתחת ל-60% הצלחה" if v.get("verdict") == "edge_below_target"
                                               else "לא אחרי התיקון לבדיקת כמה אסטרטגיות")
    return "אין יתרון על קנייה אקראית אחרי עלויות"


def from_lab_forward(summary: dict, lab_report: dict | None) -> list[dict]:
    st = (lab_report or {}).get("strategies") or {}
    out = []
    for k, v in summary.items():
        verdict = v["status"]
        why = {"confirmed": "50+ עסקאות קדימה, 60%+ ניצחו את SPY אחרי עלות, גבול תחתון חיובי",
               "not_confirmed": "50+ עסקאות קדימה, אבל השער לא הושג",
               "collecting": f"{v['n']} עסקאות קדימה עד עכשיו (צריך 50)"}[verdict]
        if verdict == "confirmed" and st.get(k, {}).get("verdict") != "candidate_for_paper":
            verdict, why = "edge_below_target", "חיובית קדימה, אבל נכשלה בבדיקה על העבר"
        out.append(_e(engine="stocks", kind="forward", id=f"fwd:{k}", label=st.get(k, {}).get("label", k),
                      version="lab_paper", data="signals sealed before the next open; Yahoo daily",
                      n=v["n"], success=v["success_rate"], mean_net=v["mean_excess"], mean_lo95=v["mean_lo95"],
                      unit="לעסקה, מול SPY", benchmark="SPY באותם ימים", verdict=verdict, why=why))
    return out


def from_tips(board: list[dict]) -> list[dict]:
    out = []
    for b in board:
        g = b.get("gate") or {}
        why = ("ירד בגלל התיקון לריבוי מקורות" if g.get("downgraded") else
               {"reliable": "50+ טיפים, 60%+ ניצחו את SPY אחרי עלות, שרד את התיקון",
                "promising": "55%+ וחיובי, עוד לא מוכח",
                "not_reliable": "מפסיד ל-SPY אחרי עלויות",
                "collecting": f"{g.get('n') or 0} טיפים נמדדו (צריך 30 כדי לשפוט)"}.get(g.get("status"), ""))
        out.append(_e(engine="sources", kind="measured", id=f"src:{b['source']}", label=b["source"],
                      version="tips gate", updated=b.get("last_tip"), data="Telegram / SEC / manual, Yahoo daily",
                      n=g.get("n"), success=g.get("success_rate"), mean_net=g.get("edge_after_cost"),
                      mean_lo95=g.get("edge_lo95"), unit="ל-5 ימים, מול SPY אחרי עלות",
                      benchmark="SPY באותם 5 ימים", verdict=g.get("status"), why=why))
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
                      unit="תשואה כוללת מול SPY", max_drawdown=a.get("max_drawdown"),
                      benchmark=f"SPY בהחזקה ({(spy.get('return') or 0) * 100:+.1f}%)", verdict="collecting",
                      why="ניסוי של חצי שנה עד 16.4.2027; נשפט בסוף"))
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
        out.append(_e(engine="polymarket", kind="historical", id=f"pm:wf:{h}", label=f"כללי טווח מחיר, {h} שעות לפני הסגירה",
                      version="walk-forward v1 + taker fees", updated=updated,
                      data="Polymarket price history (mid/last trade, not executable)",
                      n=ot.get("events"), mean_net=ot.get("mean_return"), mean_lo95=ci[0], unit="לכל דולר",
                      benchmark="מחיר השוק כתחזית", verdict=wf.get("verdict"),
                      why={"insufficient_data": "אין מספיק שווקים מוכרעים עם היסטוריה",
                           "not_supported": "אף כלל לא הרוויח מחוץ למדגם אחרי עלויות",
                           "oos_positive_needs_execution_check": "חיובי מחוץ למדגם; צריך אימות בספרי פקודות חיים",
                           "supported": "עבר את כל השערים כולל ביצוע"}.get(wf.get("verdict"), "")))
    return out


def summarize(entries: list[dict]) -> dict:
    out: dict = {}
    for e in entries:
        s = out.setdefault(e["engine"], {"passed": 0, "promising": 0, "failed": 0, "collecting": 0})
        s[e["status"]] += 1
    return out
