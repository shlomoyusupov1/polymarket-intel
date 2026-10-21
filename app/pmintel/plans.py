"""Opportunity table and trade plans, built only from what the system already knows.

Rule: no number is invented. A strategy that defines no target or stop gets "not defined" and the
reason; a cost the owner hasn't entered is reported as unknown; a position is sized only from the
owner's own budget and risk profile. Nothing here places an order — paper and research only.

Sources:
  * lab forward signals (lab_signals): the strategy, its research state, entry rule, time exit;
  * recent tips (tips): the tip, its trap verdict and the measured record of its source;
  * follows (follows): what the owner asked to watch; Polymarket prices from Gamma, executable
    prices from the live CLOB order book, the taker fee from the official formula (research.py).
"""
from __future__ import annotations

from datetime import datetime, timezone

from psycopg.types.json import Jsonb

# Risk profiles. risk_pct = the most the plan may lose on one trade, as a share of the budget;
# max_position_pct = the largest share of the budget in one position; max_open = open trades at once.
PROFILES = {
    "conservative": {"label": "שמרני", "risk_pct": 0.005, "max_position_pct": 0.25, "max_open": 1},
    "moderate": {"label": "בינוני", "risk_pct": 0.01, "max_position_pct": 0.33, "max_open": 2},
    "high": {"label": "גבוה", "risk_pct": 0.02, "max_position_pct": 0.50, "max_open": 3},
}
DEFAULTS = {"budget": 100.0, "profile": "moderate", "fee_min": None, "fee_pct": 0.0, "fractional": True}
FEE_TOO_HIGH = 0.01  # a round trip that costs more than 1% of the position makes a short trade impractical


# ---------------------------------------------------------------- settings
def get_settings(conn) -> dict:
    r = conn.execute("SELECT value FROM owner_settings WHERE key = 'risk'").fetchone()
    s = {**DEFAULTS, **(r[0] if r else {})}
    s["profile"] = s["profile"] if s["profile"] in PROFILES else "moderate"
    return s


def save_settings(conn, new: dict) -> dict:
    s = get_settings(conn)
    if "budget" in new:
        b = float(new["budget"])
        if not 1 <= b <= 10_000_000:
            raise ValueError("התקציב צריך להיות בין 1 ל-10,000,000 דולר")
        s["budget"] = b
    if "profile" in new:
        if new["profile"] not in PROFILES:
            raise ValueError("פרופיל סיכון לא מוכר")
        s["profile"] = new["profile"]
    if "fee_min" in new:
        s["fee_min"] = None if new["fee_min"] in (None, "") else max(0.0, float(new["fee_min"]))
    if "fee_pct" in new:
        s["fee_pct"] = max(0.0, float(new["fee_pct"] or 0))
    if "fractional" in new:
        s["fractional"] = bool(new["fractional"])
    conn.execute("""INSERT INTO owner_settings (key, value) VALUES ('risk', %s)
                    ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()""", (Jsonb(s),))
    return s


def limits(s: dict) -> dict:
    p = PROFILES[s["profile"]]
    return {"profile": s["profile"], "profile_label": p["label"], "budget": s["budget"],
            "risk_usd": s["budget"] * p["risk_pct"], "risk_pct": p["risk_pct"],
            "max_position_usd": s["budget"] * p["max_position_pct"], "max_position_pct": p["max_position_pct"],
            "max_open": p["max_open"]}


# ---------------------------------------------------------------- sizing
def _fee(s: dict, position: float) -> float | None:
    """Round-trip broker fee for a position, or None when the owner hasn't entered his fee."""
    if s.get("fee_min") is None:
        return None
    return 2 * max(s["fee_min"], position * s.get("fee_pct", 0) / 100)


def size(s: dict, price: float, stop: float | None = None, typical_loss: float | None = None,
         stress_loss: float | None = None) -> dict:
    """Position for one stock/coin trade under the owner's limits, with four risk figures kept apart:

      exposure      the most of the budget in one position (profile cap)
      planned loss  only with a stop: units x (entry - stop) + fees; sized so it stays within risk_usd
      stress loss   without a stop: the strategy's own measured bad case — the 5th percentile of its
                    hold-period return after cost in the out-of-sample backtest (stocklab raw_p5);
                    sized so that this loss stays within risk_usd
      max loss      without a stop the whole position can be lost (gaps, halts); always shown, never hidden
    A missing stress figure is said plainly and the position falls back to the exposure cap.
    Plain-language notes explain each number."""
    L = limits(s)
    notes, ok = [], True
    if not price or price <= 0:
        return {"ok": False, "notes": ["אין מחיר עדכני, אז אי אפשר לחשב גודל עסקה."]}
    position, by = L["max_position_usd"], "תקרה"
    fee0 = _fee(s, position) or 0.0
    budget_for_loss = L["risk_usd"] - fee0
    if stop is not None:
        if stop >= price:
            return {"ok": False, "notes": ["מחיר העצירה חייב להיות מתחת למחיר הכניסה."]}
        if budget_for_loss <= 0:
            ok = False
            notes.append(f"העמלה לבדה ({fee0:,.2f}$) גדולה מההפסד המותר לעסקה ({L['risk_usd']:,.2f}$). אין גודל עסקה שעומד במגבלה.")
        else:
            units_risk = budget_for_loss / (price - stop)
            if units_risk * price < position:
                position, by = units_risk * price, "סיכון"
    elif stress_loss is not None and stress_loss < 0:
        if budget_for_loss <= 0:
            ok = False
            notes.append(f"העמלה לבדה ({fee0:,.2f}$) גדולה מההפסד המותר לעסקה ({L['risk_usd']:,.2f}$). אין גודל עסקה שעומד במגבלה.")
        elif budget_for_loss / abs(stress_loss) < position:
            position, by = budget_for_loss / abs(stress_loss), "תרחיש קיצון"
    units = position / price
    if not s.get("fractional", True):
        units = int(units)
        position = units * price
        if units == 0:
            ok = False
            notes.append(f"במחיר {price:,.2f}$ אי אפשר לקנות אפילו יחידה אחת בסכום הזה. צריך חשבון שמאפשר שברי מניות, או תקציב גדול יותר.")
    fee_rt = _fee(s, position) if position else None
    planned_loss = (units * (price - stop) + (fee_rt or 0)) if stop is not None else None
    stress_usd = position * abs(stress_loss) + (fee_rt or 0) if stress_loss is not None and stop is None else None
    bad_case = position * abs(typical_loss) + (fee_rt or 0) if typical_loss is not None and stop is None else None
    max_loss = position + (fee_rt or 0)
    notes.append(f"הסכום לעסקה: {position:,.2f}$ מתוך {s['budget']:,.0f}$ — " + {
        "סיכון": "מוגבל כך שאם העצירה תופעל, ההפסד כולל העמלה לא יעבור את המגבלה שלך.",
        "תרחיש קיצון": "מוגבל כך שגם בתרחיש הרע של השיטה (5% העסקאות הגרועות) ההפסד לא יעבור את המגבלה שלך.",
        "תקרה": f"התקרה לעסקה אחת בפרופיל {L['profile_label']} ({L['max_position_pct']:.0%} מהתקציב)."}[by])
    if stop is not None:
        notes.append(f"אם יוצאים בעצירה ({stop:,.2f}$) ההפסד המתוכנן הוא בערך {planned_loss:,.2f}$, "
                     f"והמגבלה שלך היא {L['risk_usd']:,.2f}$ לעסקה. בפער פתיחה או בתנועה חדה היציאה יכולה להיות במחיר גרוע יותר.")
    else:
        notes.append("לא הוגדרה עצירת הפסד. היציאה היא לפי זמן.")
        if stress_usd is not None:
            notes.append(f"תרחיש קיצון: ב-5% מהעסקאות הגרועות של השיטה (בבדיקה על העבר) ההפסד היה {abs(stress_loss):.1%} או יותר — "
                         f"על הסכום הזה בערך {stress_usd:,.2f}$ כולל עמלה.")
        else:
            notes.append("אין לשיטה הזו נתון תרחיש קיצון מהמעבדה, לכן הגודל הוא רק לפי תקרת החשיפה ולא לפי סיכון.")
        if bad_case is not None:
            notes.append(f"בעסקה מפסידה ממוצעת של השיטה ההפסד היה בערך {bad_case:,.2f}$ על הסכום הזה.")
    notes.append(f"הפסד מקסימלי אפשרי: כל הסכום ({max_loss:,.2f}$ כולל עמלה) — במקרה של קריסה, פער פתיחה חד או הפסקת מסחר."
                 if stop is None else "בלי עצירה מבוצעת בפועל, גם כאן ההפסד המקסימלי הוא כל הסכום.")
    if fee_rt is None:
        notes.append("לא הזנת את עמלת הברוקר, אז העלות לא כלולה. בחשבון קטן היא יכולה לאכול את כל הרווח.")
    else:
        frac = fee_rt / position if position else None
        notes.append(f"עמלה הלוך-חזור: {fee_rt:,.2f}$" + (f", כלומר {frac:.1%} מהסכום." if frac is not None else "."))
        if frac is not None and frac > FEE_TOO_HIGH:
            ok = False
            notes.append("העמלה גבוהה מ-1% מהסכום. עסקה קצרה כזו לא מעשית בתקציב הזה.")
    return {"ok": ok, "position_usd": position, "units": units, "sized_by": by, "fee_round_trip": fee_rt,
            "planned_loss": planned_loss, "stress_loss_usd": stress_usd, "bad_case_loss": bad_case,
            "max_loss_usd": max_loss, "cash_left": s["budget"] - position, "limits": L, "notes": notes,
            "risk_basis": "stop" if stop is not None else ("stress" if stress_usd is not None else "exposure_only")}


def polymarket_buy(stake: float, levels_asks: list[tuple[float, float]] | None, mid: float | None,
                   category: str | None) -> dict:
    """What a YES buy of `stake` dollars returns, using the live asks when there are any."""
    from .orderbook import vwap_for_notional
    from .research import taker_fee_frac
    price = vwap_for_notional(levels_asks, stake) if levels_asks else None
    executable = price is not None
    price = price or mid
    if not price or price <= 0 or price >= 1:
        return {"ok": False, "notes": ["אין כרגע מחיר שאפשר לקנות בו."]}
    fee = taker_fee_frac(price, category)
    shares = stake / price * (1 - fee)          # the fee is taken in shares
    return {"ok": True, "executable": executable, "price": price, "stake": stake, "shares": shares,
            "payout_if_yes": shares, "profit_if_yes": shares - stake, "loss_if_no": stake, "fee_frac": fee,
            "notes": [("מחיר ממוצע לפי ספר הפקודות עכשיו" if executable else "אין ספר פקודות זמין: לפי מחיר התצוגה, ייתכן שתשלם יותר")
                      + f": {price * 100:.1f}¢",
                      f"שמים {stake:,.2f}$ ← אם התשובה \"כן\": מקבלים {shares:,.2f}$ (רווח {shares - stake:,.2f}$). אם \"לא\": מפסידים {stake:,.2f}$.",
                      f"עמלת קנייה של Polymarket: בערך {fee:.1%} מהסכום (נלקחת ממספר המניות)."]}


# ---------------------------------------------------------------- opportunities
ACTION = {"confirmed": "מועמדת לפי אסטרטגיה", "validating": "לבדיקה", "candidate": "לבדיקה",
          "failed": "להמתין", "insufficient": "להמתין"}


def _d(v) -> str:
    """Date as the owner reads it: 19.10.2026 (and the time when there is one)."""
    if v is None:
        return "לא ידוע"
    if isinstance(v, str):
        try:
            v = datetime.fromisoformat(v.replace("Z", "+00:00"))
        except ValueError:
            return v
    if isinstance(v, datetime):
        from zoneinfo import ZoneInfo
        return v.astimezone(ZoneInfo("Asia/Jerusalem")).strftime("%d.%m.%Y %H:%M")
    return v.strftime("%d.%m.%Y")


def _risk_level(atr):
    if atr is None:
        return None
    return "נמוך" if atr < 0.02 else ("בינוני" if atr < 0.045 else "גבוה")


def opportunities(conn, entries: list[dict], labs: dict) -> list[dict]:
    """labs: {'stocks': stocklab report, 'crypto': cryptolab report}; entries: performance scoreboard."""
    by_id = {e["id"]: e for e in entries}
    out = []
    try:
        sig = conn.execute("""SELECT id, strategy, symbol, signal_day, recorded_at, hold, status, entry_day, entry_price,
                                     last_price, last_day, atr_pct FROM lab_signals
                              WHERE status IN ('pending', 'open') ORDER BY signal_day DESC LIMIT 200""").fetchall()
    except Exception:  # noqa: BLE001 - tracker table not deployed yet
        sig = []
    for rid, strat, sym, sday, rec, hold, st, eday, eprice, lp, lday, atr in sig:
        market = "crypto" if strat.startswith("crypto:") else "stocks"
        k = strat.split(":", 1)[-1]
        pre = "crypto:" if market == "crypto" else ""
        e = by_id.get(f"fwd:{pre}{k}") if by_id.get(f"fwd:{pre}{k}", {}).get("status") == "confirmed" else by_id.get(f"lab:{pre}{k}")
        status = e["status"] if e else "insufficient"
        spec = ((labs.get(market) or {}).get("strategies") or {}).get(k, {})
        warn = spec.get("side") == -1
        out.append({
            "kind": "signal", "id": rid, "domain": market, "asset": sym.replace("-USD", ""), "symbol": sym,
            "strategy": spec.get("label", k), "price": lp or eprice, "price_at": (lday or eday or sday),
            "action": "להמתין" if warn else ACTION[status], "warning": warn,
            "entry": (f"נכנס בכסף מדומה ב-{_d(eday)}, במחיר {eprice:,.2f}$" if st == "open" and eprice
                      else ("בפתיחה הבאה (פקודת שוק)" if market == "stocks" else "יום אחרי האיתות, במחיר הפתיחה")),
            "target": None, "target_why": "האסטרטגיה לא מגדירה יעד רווח. היציאה לפי זמן.",
            "stop": None, "stop_why": "האסטרטגיה נבדקה בלי עצירת הפסד. אפשר להגדיר עצירה משלך במחשבון.",
            "horizon": f"{hold} {'ימי מסחר' if market == 'stocks' else 'ימים'}", "risk": _risk_level(atr), "atr_pct": atr,
            "research": status, "research_why": e["why"] if e else "השיטה עוד לא נבדקה", "updated": rec,
            "typical_loss": spec.get("avg_loss"), "typical_win": spec.get("avg_win"), "stress_loss": spec.get("raw_p5"),
        })
    try:
        from . import tips as T
        src = {b["source"]: b["gate"] for b in T.leaderboard(conn)}
        tips = conn.execute("""SELECT id, source, symbol, ts, verdict, mentioned_price, entry_price FROM tips
                               WHERE ts > now() - interval '72 hours' ORDER BY ts DESC LIMIT 40""").fetchall()
    except Exception:  # noqa: BLE001
        src, tips = {}, []
    seen = set()
    for tid, source, sym, ts, verdict, mp, ep in tips:
        if (source, sym) in seen:
            continue
        seen.add((source, sym))
        g = src.get(source) or {}
        status = {"reliable": "confirmed", "promising": "validating", "not_reliable": "failed"}.get(g.get("status"), "insufficient")
        red = verdict == "red"
        out.append({
            "kind": "tip", "id": tid, "domain": "stocks", "asset": sym, "symbol": sym, "strategy": f"טיפ מ{source}",
            "price": ep or mp, "price_at": ts, "action": "להמתין" if red or status in ("failed", "insufficient") else "לבדיקה",
            "warning": red, "entry": "טיפ אינו כלל כניסה", "target": None, "target_why": "לטיפ אין יעד מוגדר שנבדק.",
            "stop": None, "stop_why": "לטיפ אין עצירה מוגדרת.", "horizon": "נמדד ל-5 ימי מסחר",
            "risk": "גבוה" if red else None, "research": status,
            "research_why": ("סומן באדום: סימני מלכודת. " if red else "") + (
                f"המקור נמדד על {g.get('n') or 0} טיפים" + (" והפסיד למדד אחרי עמלות." if status == "failed" else ".")),
            "updated": ts, "typical_loss": None,
        })
    try:
        from . import follows as F
        fl = F.listing(conn)
    except Exception:  # noqa: BLE001
        fl = []
    for f in fl:
        top = f["state"][0] if f["state"] else None
        dom = {"polymarket": "polymarket", "stock": "stocks", "crypto": "crypto"}[f["kind"]]
        out.append({
            "kind": "follow", "id": f["id"], "domain": dom, "asset": f["label"], "symbol": f["ref"],
            "strategy": "אתה עוקב", "price": top["price"] if top else None, "price_at": f["checked_at"],
            "price_unit": "prob" if f["kind"] == "polymarket" else "usd", "top_name": top["name"] if top else None,
            "action": "במעקב", "warning": False,
            "entry": "אין אסטרטגיה שקובעת כניסה", "target": None, "target_why": "טרם נקבע — אין מספיק ראיות.",
            "stop": None, "stop_why": "טרם נקבע — אין מספיק ראיות.",
            "horizon": "עד הכרעת החוזה" if f["kind"] == "polymarket" else "—", "risk": None,
            "research": "insufficient", "research_why": "אין שיטה שנבדקה על הנכס הזה. רק מעקב מחירים.",
            "updated": f["checked_at"], "typical_loss": None,
        })
    rank = {"מועמדת לפי אסטרטגיה": 0, "לבדיקה": 1, "במעקב": 2, "להמתין": 3}
    out.sort(key=lambda r: (rank[r["action"]], -(r["updated"].timestamp() if isinstance(r["updated"], datetime) else 0)))
    return out


def now_utc():
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------- the trade plan card
def plan(row: dict, s: dict, extra: dict | None = None) -> dict:
    """Plain-language plan for one opportunity row (see opportunities()). extra: live data for follows."""
    extra = extra or {}
    L = limits(s)
    st = row["research"]
    proven = st == "confirmed"
    out = {"row": row, "limits": L, "settings": s, "sections": []}

    def sec(title, *lines):
        out["sections"].append({"title": title, "lines": [x for x in lines if x]})

    STATE_HE = {"confirmed": "אומתה קדימה", "validating": "בבדיקה קדימה", "candidate": "מועמדת מהעבר",
                "failed": "נכשלה", "insufficient": "אין מספיק נתונים"}
    if row["kind"] == "follow" and row["domain"] == "polymarket":
        sec("מה אנחנו בוחנים",
            f"השאלה: {row['asset']}",
            "זה שוק שאתה עוקב אחריו. אין למערכת שיטה שנבדקה על שווקים כאלה, ואין לה הערכה עצמאית של הסיכוי — המחיר הוא רק מה שהמשתתפים חושבים.",
            "לכן ההמלצה: להמתין. החשבון למטה הוא רק כדי להבין מה בדיוק היית שם ומה היית יכול לקבל.")
        stake = L["risk_usd"]
        rows = []
        for it in extra.get("outcomes", []):
            b = polymarket_buy(stake, it.get("asks"), it.get("price"), extra.get("category"))
            rows.append({"name": it["name"], "price": it.get("price"), **b})
        out["polymarket"] = {"stake": stake, "outcomes": rows, "end": extra.get("end"), "rules": extra.get("rules"),
                             "resolution_source": extra.get("resolution_source")}
        sec("כמה כסף",
            f"בהימור של כן/לא אפשר להפסיד את כל הסכום. לכן הסכום לכל חוזה הוא בדיוק המגבלה שלך להפסד בעסקה: {stake:,.2f}$ "
            f"({L['risk_pct']:.1%} מ-{L['budget']:,.0f}$, פרופיל {L['profile_label']}).")
        sec("מתי זה נגמר",
            f"מועד ההכרעה הצפוי: {_d(extra.get('end'))}.",
            "הכסף נעול עד ההכרעה. אפשר למכור לפני כן, אבל רק במחיר שמישהו מוכן לשלם באותו רגע.",
            "ההכרעה יכולה להתעכב (ערעורים, הרכבת ממשלה ארוכה, שינוי תנאים).")
        sec("מה עדיין לא ידוע",
            "אין למערכת הערכת סיכוי עצמאית ומכוילת לשוק הזה. בלי זה אין דרך לדעת אם המחיר זול או יקר.",
            "המחקר על שווקים שנגמרו עוד לא מצא דרך להרוויח מטעויות במחיר אחרי עמלות.")
        return out

    price = row.get("price")
    sz = (size(s, price, typical_loss=row.get("typical_loss"), stress_loss=row.get("stress_loss"))
          if price else {"ok": False, "notes": ["אין מחיר עדכני."]})
    out["sizing"] = sz
    why_here = {"signal": f"השיטה \"{row['strategy']}\" נתנה איתות על {row['asset']}.",
                "tip": f"{row['asset']} הגיע כטיפ ({row['strategy']}).",
                "follow": f"אתה עוקב אחרי {row['asset']}."}[row["kind"]]
    verdict = ("השיטה עברה גם בדיקה על העבר וגם בדיקה קדימה. זה עדיין לא הבטחה לרווח." if proven else
               {"candidate": "השיטה עברה רק בדיקה על נתוני העבר. זו השערה, לא איתות מסחר מאומת.",
                "validating": "השיטה נבדקת עכשיו קדימה בכסף מדומה. אין עדיין מסקנה.",
                "failed": "השיטה לא הוכיחה יתרון בבדיקה. המערכת עוקבת רק כדי למדוד.",
                "insufficient": "אין מספיק נתונים כדי לשפוט את השיטה."}[st])
    sec("מה אנחנו בוחנים", why_here, f"מצב המחקר: {STATE_HE[st]}. {verdict}", row.get("research_why"),
        "זו נורת אזהרה: השיטה בודקת אם אחרי האיתות הנכס מפגר אחרי המדד. לא קונים לפיה." if row.get("warning") else None)
    sec("מתי להיכנס",
        f"כלל הכניסה: {row['entry']}." if row["kind"] == "signal" else row["entry"] + ".",
        f"המחיר האחרון שנבדק: {price:,.2f}$ ({_d(row.get('price_at'))})." if price else "אין מחיר עדכני.",
        "המחיר בפועל ייקבע בפתיחה ויכול להיות שונה מהמחיר האחרון." if row["kind"] == "signal" else None,
        "ההמלצה: להמתין. אין כאן אסטרטגיה מאומתת, אז אין מחיר כניסה מומלץ." if not proven else None)
    sec("כמה כסף", *sz.get("notes", []))
    sec("מתי לצאת",
        f"יציאה לפי זמן: {row['horizon']} אחרי הכניסה, במחיר הסגירה." if row["kind"] == "signal" else None,
        f"יעד רווח: {row['target_why']}", f"עצירת הפסד: {row['stop_why']}",
        "עצירה לא מבטיחה ביצוע במחיר שבחרת: בפער פתיחה או בתנועה חדה המחיר יכול לדלג מעליה.")
    atr = row.get("atr_pct")
    sec("מה התרחישים",
        f"תנודה יומית טיפוסית של הנכס: בערך {atr:.1%} ביום (ממוצע 14 ימים). על {sz['position_usd']:,.2f}$ זה בערך {sz['position_usd'] * atr:,.2f}$ ביום."
        if atr and sz.get("position_usd") else None,
        f"בעסקאות המנצחות של השיטה (על העבר) הרווח הממוצע מעל המדד היה {row['typical_win']:.1%}." if row.get("typical_win") else None,
        "תוחלת: אין הערכה מבוססת נתונים לעסקה הזו, אז לא מוצגת." if not proven else None)
    sec("מה עדיין לא ידוע",
        "אם השיטה תעבוד קדימה. רק מעקב קדימה של 50 עסקאות יענה על זה." if not proven else None,
        "העמלה האמיתית שלך, אם לא הזנת אותה בהגדרות." if s.get("fee_min") is None else None,
        "חדשות או דוחות שיכולים להזיז את המחיר ביום הכניסה.")
    return out
