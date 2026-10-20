"use strict";
/* Polymarket Intelligence — dashboard (no dependencies).
   Security: every market-supplied string is rendered with textContent via h(); never innerHTML.
   Honesty: every number comes from the API; missing data renders as "—" or an explicit empty state. */

// ---------------- helpers ----------------
const $ = (s, el = document) => el.querySelector(s);
function h(tag, attrs, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v == null || v === false) continue;
    if (k === "class") el.className = v;
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const c of kids.flat(Infinity)) if (c != null && c !== false) el.append(c instanceof Node ? c : String(c));
  return el;
}
const NS = "http://www.w3.org/2000/svg";
function s(tag, attrs, ...kids) {
  const el = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(attrs || {})) if (v != null) el.setAttribute(k, v);
  for (const c of kids.flat(Infinity)) if (c != null) el.append(c instanceof Node ? c : document.createTextNode(String(c)));
  return el;
}
async function api(path, ms = 20000) {
  // never leave a page spinning forever: a server that doesn't answer becomes a visible error
  const ctl = new AbortController();
  const t = setTimeout(() => ctl.abort(), ms);
  try {
    const r = await fetch(path, { headers: { Accept: "application/json" }, signal: ctl.signal });
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    return await r.json();
  } catch (e) {
    throw e.name === "AbortError" ? new Error("השרת לא ענה תוך " + ms / 1000 + " שניות") : e;
  } finally { clearTimeout(t); }
}
const num = (v) => v == null || isNaN(v) ? null : Number(v);
const usd = (v) => num(v) == null ? "—" : "$" + Intl.NumberFormat("en", { notation: "compact", maximumFractionDigits: 1 }).format(v);
const pct = (v, d = 1) => num(v) == null ? "—" : (v * 100).toFixed(d) + "%";
const cents = (v) => num(v) == null ? "—" : (v * 100).toFixed(1) + "¢";
const ltr = (t, cls) => h("bdi", { dir: "ltr", class: "num " + (cls || "") }, t);
// change in percentage points: number isolated LTR, Hebrew unit outside so it reads correctly in RTL
const ptsEl = (v) => num(v) == null ? h("span", { class: "faint" }, "—")
  : h("span", { class: v >= 0 ? "up" : "down" }, ltr((v >= 0 ? "+" : "−") + Math.abs(v * 100).toFixed(1)), " נק׳");
const dateHe = (iso) => iso ? new Date(iso).toLocaleDateString("he-IL", { day: "numeric", month: "short", year: "numeric" }) : "—";
function ago(iso) {
  if (!iso) return "אף פעם";
  const m = Math.round((Date.now() - new Date(iso)) / 60000);
  if (m < 1) return "עכשיו";
  if (m < 60) return `לפני ${m} דק׳`;
  if (m < 1440) return `לפני ${Math.round(m / 60)} שע׳`;
  return `לפני ${Math.round(m / 1440)} ימים`;
}
function until(iso) {
  if (!iso) return null;
  const d = (new Date(iso) - Date.now()) / 86400000;
  if (d < 0) return "המועד עבר";
  if (d < 1) return "נסגר היום";
  if (d < 2) return "נסגר מחר";
  return `נסגר בעוד ${Math.round(d)} ימים`;
}
const safeUrl = (u) => { try { const x = new URL(u); return ["http:", "https:"].includes(x.protocol) ? x.href : null; } catch { return null; } };
const empty = (t) => h("div", { class: "empty" }, t);
const fail = (e) => h("div", { class: "empty err" }, "לא ניתן לטעון: " + (e.message || e));
const skel = (n = 3) => h("div", { class: "stack" }, Array.from({ length: n }, () => h("div", { class: "skel" })));

// ---------------- icons (inline stroke svg) ----------------
const ICON = {
  home: "M3 11 12 4l9 7v9a1 1 0 0 1-1 1h-5v-6H9v6H4a1 1 0 0 1-1-1z",
  list: "M8 6h13M8 12h13M8 18h13M3.5 6h.01M3.5 12h.01M3.5 18h.01",
  radar: "M12 12 19 5M21 12a9 9 0 1 1-9-9M16.5 12A4.5 4.5 0 1 1 12 7.5",
  star: "m12 3 2.8 5.7 6.2.9-4.5 4.4 1 6.2L12 17.3 6.5 20.2l1-6.2L3 9.6l6.2-.9z",
  lab: "M9 3h6M10 3v6L4.5 19a1.5 1.5 0 0 0 1.3 2h12.4a1.5 1.5 0 0 0 1.3-2L14 9V3",
  wallet: "M3 7h15a3 3 0 0 1 3 3v7a3 3 0 0 1-3 3H6a3 3 0 0 1-3-3zM3 7l12-4v4M16 14h.01",
  bell: "M6 16V11a6 6 0 1 1 12 0v5l2 2H4zM10 20a2 2 0 0 0 4 0",
  pulse: "M3 12h4l3-8 4 16 3-8h4",
  more: "M5 12h.01M12 12h.01M19 12h.01",
  search: "m21 21-4.3-4.3M11 18a7 7 0 1 1 0-14 7 7 0 0 1 0 14z",
  ext: "M14 4h6v6M20 4l-9 9M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5",
  back: "M9 6l6 6-6 6",
  shield: "M12 3 4 6v6c0 5 3.5 8 8 9 4.5-1 8-4 8-9V6zM12 8v5M12 16h.01",
  coin: "M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18zM9.5 8h3.5a2 2 0 0 1 0 4H9.5zM9.5 12h4a2 2 0 0 1 0 4h-4zM9.5 8v8M11 6.5V8M11 16v1.5",
};
const icon = (name, size = 20) => s("svg", { width: size, height: size, viewBox: "0 0 24 24", fill: "none", stroke: "currentColor",
  "stroke-width": 1.9, "stroke-linecap": "round", "stroke-linejoin": "round", "aria-hidden": "true" }, s("path", { d: ICON[name] }));

// ---------------- watchlist (this browser only) ----------------
const WL_KEY = "pmintel.watchlist";
function wlGet() { try { return JSON.parse(localStorage.getItem(WL_KEY) || "[]"); } catch { return []; } }
function wlSet(ids) { try { localStorage.setItem(WL_KEY, JSON.stringify(ids)); } catch { /* private mode */ } }
function starBtn(id) {
  const on = () => wlGet().includes(id);
  const b = h("button", { class: "star", type: "button", "aria-pressed": String(on()), "aria-label": "הוסף לרשימת המעקב" }, icon("star", 20));
  b.addEventListener("click", (e) => {
    e.preventDefault(); e.stopPropagation();
    const ids = wlGet(); const i = ids.indexOf(id);
    if (i >= 0) ids.splice(i, 1); else ids.unshift(id);
    wlSet(ids); b.setAttribute("aria-pressed", String(on()));
  });
  return b;
}

// ---------------- routing ----------------
const ROUTES = [
  { id: "home", path: "#/", label: "היום", short: "היום", icon: "home", primary: true, bottom: true,
    lead: "תשובה אחת: יש היום משהו שהנתונים תומכים בו? ולמה." },
  { id: "opps", path: "#/opps", label: "הזדמנויות", short: "הזדמנויות", icon: "list", primary: true, bottom: true,
    lead: "כל מה שהמערכת מצאה לבדוק — מניות, קריפטו ו-Polymarket — עם מצב המחקר של כל אחד. מספר שלא מבוסס על ראיות לא מוצג." },
  { id: "settings", path: "#/settings", label: "התקציב שלי", short: "תקציב", icon: "wallet",
    lead: "כמה כסף יש לך לניסוי, כמה מוכן להפסיד בעסקה, ומה העמלה של הברוקר. כל החישובים במערכת יוצאים מכאן." },
  { id: "plan", path: "#/plan", label: "כרטיס עסקה", lead: "" },
  { id: "live", path: "#/live", label: "שוק חי", short: "חי", icon: "pulse",
    lead: "מחירים חיים של מה שאתה עוקב אחריו, הפוזיציות המדומות ומדדי הייחוס — עם זמן המקור והעיכוב של כל מחיר, וההתראות האחרונות." },
  { id: "portfolio", path: "#/portfolio", label: "התיק המדומה", short: "תיק", icon: "wallet",
    lead: "תיק בכסף מדומה שנבנה רק מאיתותים שנרשמו מראש, לפני שהמחיר זז — לפי התקציב והסיכון שלך. לא סימולציה בדיעבד." },
  { id: "history", path: "#/history", label: "היסטוריית החלטות", icon: "list",
    lead: "כל איתות שהמערכת רשמה: מתי, מה היה ידוע, האם נכנס לתיק או דולג ולמה, ואיך זה נגמר אחרי עלויות." },
  { id: "follow", path: "#/follow", label: "המעקב שלי", short: "מעקב", icon: "star", primary: true, bottom: true,
    lead: "הדבק קישור מ-Polymarket או כתוב סימול של מניה או מטבע. תקבל הודעה בטלגרם כשהמחיר זז." },
  { id: "stocks", path: "#/stocks", label: "מניות", short: "מניות", icon: "lab",
    lead: "מה קורה בשוק עכשיו, ואיזה תחומים עולים או יורדים. זה מידע, לא המלצה." },
  { id: "evidence", path: "#/evidence", label: "המניות שלי", section: "stocks",
    lead: "כל מה שידוע על המניות שאתה עוקב אחריהן: דיווחים רשמיים, טיפים, מחיר לפני ואחרי, ומה עוד לא ידוע." },
  { id: "check", path: "#/check", label: "בדיקת מניה", short: "בדיקה", icon: "search",
    lead: "קיבלת טיפ? הכנס סימול ותראה אם יש סימנים לניפוח מחיר לפני שאתה קונה." },
  { id: "lab", path: "#/lab", label: "מעבדה", section: "stocks",
    lead: "כאן בודקים אם שיטות מסחר מוכרות היו מרוויחות ב-10 השנים האחרונות. שיטה שלא הוכחה — לא משתמשים בה." },
  { id: "tips", path: "#/tips", label: "מקורות", section: "stocks",
    lead: "כל מקור טיפים נמדד מול SPY. מקור מקבל \"אמין\" רק אחרי 50 טיפים ו-60% הצלחה שאינה מקרית." },
  { id: "etf", path: "#/etf", label: "חדר מסחר", section: "stocks",
    lead: "שתי אסטרטגיות על קרנות סל, בכסף מדומה, מול פשוט להחזיק את SPY. הכללים נקבעו מראש." },
  { id: "crypto", path: "#/crypto", label: "קריפטו", short: "קריפטו", icon: "coin",
    lead: "אותן בדיקות כמו במניות, על 20 מטבעות גדולים. כל שיטה צריכה לנצח פשוט להחזיק ביטקוין, אחרי עמלות." },
  { id: "pm", path: "#/pm", label: "Polymarket", short: "Polymarket", icon: "list",
    lead: "מה זה Polymarket, איך מרוויחים ומפסידים בו, ומה המערכת שלנו בודקת לפני שהיא אומרת משהו." },
  { id: "pmoverview", path: "#/pm/overview", label: "סקירה", section: "pm", lead: "מה זז, מה מושך כסף, והאם יש הזדמנות שנתמכת בנתונים." },
  { id: "markets", path: "#/markets", label: "כל השווקים", section: "pm", lead: "חיפוש וסינון של השווקים הפתוחים. לחיצה על שוק פותחת הסבר מלא." },
  { id: "scanner", path: "#/scanner", label: "ניסוי פערים", section: "pm", lead: "מקרים ששני שווקים קשורים לא מסתדרים במחיר. עדיין ניסוי — לא הוכח שאפשר להרוויח מזה." },
  { id: "research", path: "#/research", label: "מחקר", section: "pm", lead: "בודקים על שווקים שכבר נגמרו: האם המחיר ניבא נכון, והאם הייתה דרך להרוויח אחרי עמלות." },
  { id: "watchlist", path: "#/watchlist", label: "מעקב", section: "pm", lead: "השווקים שסימנת בכוכב." },
  { id: "performance", path: "#/performance", label: "תוצאות", short: "תוצאות", icon: "radar", primary: true, bottom: true,
    lead: "כל שיטה שהמערכת בודקת, במקום אחד: מה עבר, מה נכשל, ומה עוד מחכה לנתונים." },
  { id: "agents", path: "#/agents", label: "חדר הסוכנים", short: "סוכנים", icon: "pulse",
    lead: "כל חלק במערכת הוא סוכן עם תפקיד. כאן רואים מי עובד עכשיו, מה הוא מצא, ומי נתקע — הכל מהשרת, בזמן אמת." },
  { id: "system", path: "#/system", label: "מערכת", short: "מערכת", icon: "pulse", lead: "האם האיסוף רץ, מה נכשל, ומתי עודכן כל דבר." },
  { id: "more", path: "#/more", label: "עוד", short: "עוד", icon: "more", primary: true, bottom: true, lead: "שאר המסכים." },
];
const STOCK_TABS = ["stocks", "evidence", "lab", "tips", "etf"];
const PM_TABS = ["pm", "pmoverview", "markets", "scanner", "research", "watchlist"];
function buildNav() {
  const top = $("#top-nav"), bottom = $("#bottom-nav");
  ROUTES.filter((r) => r.primary).forEach((r) => top.append(h("a", { href: r.path, "data-route": r.id, title: r.label }, r.short || r.label)));
  ROUTES.filter((r) => r.bottom).forEach((r) => bottom.append(h("a", { href: r.path, "data-route": r.id }, icon(r.icon, 22), r.short)));
}
let current = null;
function route() {
  const hash = location.hash || "#/";
  let id = "home", arg = null;
  const m = hash.match(/^#\/m\/(.+)$/);
  const mc = hash.match(/^#\/check\/(.+)$/);
  const mp = hash.match(/^#\/plan\/(\w+)\/(\d+)$/);
  if (mp) { id = "plan"; arg = [mp[1], mp[2]]; }
  else if (m) { id = "detail"; arg = decodeURIComponent(m[1]); }
  else if (mc) { id = "check"; arg = decodeURIComponent(mc[1]); }
  else { const r = ROUTES.find((x) => x.path === hash); if (r) id = r.id; }
  current = id;
  const r = ROUTES.find((x) => x.id === id);
  const inPm = id === "detail" || PM_TABS.includes(id);
  const inStocks = STOCK_TABS.includes(id);
  const topId = inPm ? "pm" : inStocks ? "stocks" : id;
  const bottomId = inPm ? "pm" : inStocks ? "stocks" : (ROUTES.find((x) => x.id === id && x.bottom) ? id : "more");
  document.querySelectorAll("#top-nav [data-route]").forEach((a) => a.toggleAttribute("aria-current", a.dataset.route === topId));
  document.querySelectorAll("#bottom-nav [data-route]").forEach((a) => a.toggleAttribute("aria-current", a.dataset.route === bottomId));
  const view = $("#view");
  const body = h("div", null, skel(5));
  const parts = [];
  if (id !== "detail" && id !== "home" && id !== "plan" && r) parts.push(h("header", { class: "page-head" }, h("h1", null, r.id === "pm" ? "Polymarket — מה זה ואיך זה עובד" : r.label), r.lead ? h("p", null, r.lead) : null));
  if (inPm) parts.push(h("nav", { class: "subtabs", "aria-label": "Polymarket" },
    PM_TABS.map((t) => { const x = ROUTES.find((y) => y.id === t); return h("a", { href: x.path, "aria-current": (t === id || (id === "detail" && t === "markets")) ? "page" : null }, t === "pm" ? "מה זה" : x.label); })));
  if (inStocks) parts.push(h("nav", { class: "subtabs", "aria-label": "מניות" },
    STOCK_TABS.map((t) => { const x = ROUTES.find((y) => y.id === t); return h("a", { href: x.path, "aria-current": t === id ? "page" : null }, t === "stocks" ? "סקירה" : x.label); })));
  view.replaceChildren(...parts, body);
  document.title = (id === "detail" ? "שוק" : (r?.label || "")) + " · ראיות";
  scrollTo(0, 0);
  Promise.resolve((PAGES[id] || PAGES.home)(body, arg)).catch((e) => body.replaceChildren(fail(e)));
}

// ---------------- shared components ----------------
function marketRow(m, opts = {}) {
  const ch = num(m.one_day_change);
  const price = num(m.ref_price);
  const chEl = ptsEl(ch);
  const row = h("a", { class: "mrow" + (opts.wide ? " wide" : ""), href: "#/m/" + encodeURIComponent(m.id) },
    h("div", { class: "q", dir: "auto" }, m.question),
    h("div", { class: "px" }, h("div", { class: "p" }, ltr(pct(price))), h("div", { class: "o", dir: "auto" }, m.ref_outcome || "")),
    h("div", { class: "c" }, chEl),
    h("div", { class: "c" }, ltr(usd(m.volume_24h))),
    h("div", { class: "c" }, ltr(usd(m.liquidity))),
    h("div", { class: "meta" },
      h("span", null, "24ש׳: ", chEl.cloneNode(true)),
      h("span", null, "מחזור ", ltr(usd(m.volume_24h))),
      h("span", null, "נזילות ", ltr(usd(m.liquidity))),
      until(m.end_date) ? h("span", null, until(m.end_date)) : null));
  // the star is a sibling of the link (no interactive element nested inside <a>)
  return opts.wide ? h("div", { class: "mrow-wrap" }, row, starBtn(m.id)) : row;
}
function listHead() {
  return h("div", { class: "list-head" }, h("span", null, "שוק"), h("span", null, "מחיר"), h("span", null, "שינוי 24ש׳"),
    h("span", null, "מחזור 24ש׳"), h("span", null, "נזילות"), h("span", null, ""));
}
function card(title, sub, ...body) {
  return h("section", { class: "card" }, h("div", { class: "card-head" }, h("div", null, h("h2", null, title), sub ? h("div", { class: "sub" }, sub) : null)), ...body);
}
function kpi(label, value, note) {
  return h("div", { class: "card kpi" }, h("div", { class: "l" }, label), h("div", { class: "v" }, value), note ? h("div", { class: "faint" }, note) : null);
}

// ---------------- health ----------------
async function refreshHealth() {
  try {
    const hl = await api("/api/health");
    const ok = hl.status === "ok";
    const lag = hl.snapshot_lag_min;
    const pill = $("#top-health");
    pill.replaceChildren(h("span", { class: "dot " + (ok ? "ok" : "warn") }),
      lag == null ? "אין עדיין נתונים" : (ok ? "נתונים טריים · " : "נתונים ישנים · ") + ago(hl.counts.last_snapshot));
    return hl;
  } catch {
    $("#top-health").replaceChildren(h("span", { class: "dot bad" }), "השרת לא זמין");
    return null;
  }
}

// ================= PAGES =================
const PAGES = {};

// ---- Command Center ----
PAGES.pmoverview = async (view) => {
  const [o, macro] = await Promise.all([api("/api/overview"), api("/api/macro").catch(() => [])]);
  if (current !== "pmoverview") return;
  const st = o.stats, op = o.opportunities, fr = o.freshness;
  const supported = op.statistically_supported || [];
  const exe = op.structural_executable;
  const investigate = pickInvestigate(o.changed, o.attention);

  view.replaceChildren(
    h("div", { class: "kpis" },
      kpi("שווקים סחירים", ltr((st.tradable_markets || 0).toLocaleString()), "נזילות ≥ $1K, מקבלים פקודות"),
      kpi("מחזור 24 שעות", ltr(usd(st.volume_24h)), "כל השווקים הפתוחים"),
      kpi("נזילות כוללת", ltr(usd(st.liquidity)), null),
      kpi("עדכון אחרון", fr.snapshot_lag_min == null ? "—" : ltr(Math.round(fr.snapshot_lag_min) + " דק׳"),
        fr.status === "ok" ? "טרי" : "ישן — בדוק מערכת")),
    h("div", { style: "height:14px" }),
    h("div", { class: "cols-main aside-first" },
      h("div", { class: "stack" },
        card("מה השתנה ב-24 השעות האחרונות", "השינוי היומי כפי ש-Polymarket מדווח · שווקים עם נזילות ≥ $5K",
          o.changed.length ? h("div", { class: "mlist" }, o.changed.map((m) => marketRow(m))) : empty("אין עדיין נתוני שינוי — יופיעו אחרי סריקה מלאה")),
        card("מה מושך תשומת לב", "הכי הרבה מחזור ב-24 שעות",
          o.attention.length ? h("div", { class: "mlist" }, o.attention.map((m) => marketRow(m))) : empty("אין נתונים")),
        card("מה Polymarket אומר על הכלכלה", "ריבית, מיתון, אינפלציה — הסיכויים לפי השוק (לא תחזית שלנו). שינוי חד כאן משפיע גם על מניות ואג״ח",
          macro.length ? h("div", { class: "mlist" }, macro.map((m) => marketRow(m))) : empty("אין כרגע שווקי מאקרו נזילים"))),
      h("div", { class: "stack" },
        h("section", { class: "card" },
          h("div", { class: "sub" }, "האם יש הזדמנות נתמכת סטטיסטית?"),
          h("div", { class: "answer", style: "margin:6px 0 10px" }, supported.length ? `כן — ${supported.length}` : "לא"),
          h("ul", { class: "clean" },
            h("li", null, "מחקר היסטורי: ", op.historical_report_at ? `${supported.length} אסטרטגיות עם רווח סמך מעל 0 (${ago(op.historical_report_at)})` : "עוד לא רץ על נתוני עבר"),
            h("li", null, "סורק מבני: ", op.structural_report_at
              ? h("span", null, `${op.structural_theoretical ?? 0} תאורטיות, `, h("strong", null, `${exe ?? 0} בנות-ביצוע`), ` (${ago(op.structural_report_at)})`)
              : "עוד לא רץ")),
          h("div", { style: "margin-top:12px" }, h("a", { class: "btn", href: "#/scanner" }, "לסורק ההזדמנויות"))),
        card("מה לבדוק עכשיו", "סינון אוטומטי: תזוזה חדה + מחזור גבוה. לא המלצה.",
          investigate.length ? h("div", { class: "mlist" }, investigate.map((x) =>
            h("a", { class: "mrow", href: "#/m/" + encodeURIComponent(x.m.id) },
              h("div", { class: "q", dir: "auto" }, x.m.question),
              h("div", { class: "px" }, h("div", { class: "p" }, ltr(pct(x.m.ref_price)))),
              h("div", { class: "meta" }, x.why)))) : empty("אין כרגע מועמדים")),
        h("section", { class: "card" },
          h("h2", null, "טריות ואמינות הנתונים"),
          h("ul", { class: "clean", style: "margin-top:8px" },
            h("li", null, "מחירים: ", fr.snapshot_lag_min == null ? "עוד לא נאספו" : `נאספו ${ago(fr.counts.last_snapshot)}`),
            h("li", null, "ספרי פקודות: ", fr.counts.last_orderbook ? `${ago(fr.counts.last_orderbook)} · ${fr.counts.orderbook_valid_pct_24h ?? "—"}% תקינים ב-24ש׳` : "עוד לא נאספו"),
            h("li", null, "סריקה מלאה אחרונה: ", ago(fr.last_ok?.collect_full)),
            h("li", null, "נתוני עבר (שווקים שנפתרו): ", (fr.counts.resolved_markets || 0).toLocaleString())),
          h("div", { style: "margin-top:12px" }, h("a", { class: "btn", href: "#/system" }, "לבריאות המערכת"))))));
};
function pickInvestigate(changed, attention) {
  const vols = attention.map((m) => num(m.volume_24h) || 0).sort((a, b) => a - b);
  const floor = vols.length ? vols[Math.floor(vols.length / 2)] / 10 : 0;
  return changed.filter((m) => (num(m.volume_24h) || 0) >= floor).slice(0, 4).map((m) => ({
    m, why: h("span", null, "זז ", ptsEl(m.one_day_change), " ביום · מחזור ", ltr(usd(m.volume_24h))),
  }));
}

// ---- Market Explorer ----
const explorer = { search: "", sort: "volume_24h", minLiq: "1000", tradable: true, openOnly: true, offset: 0 };
PAGES.markets = async (view) => {
  const list = h("div", { class: "mlist" });
  const more = h("button", { class: "btn", type: "button", hidden: true }, "טען עוד");
  const input = h("input", { type: "search", placeholder: "חיפוש שוק…", value: explorer.search, "aria-label": "חיפוש שוק", autocomplete: "off" });
  const sorts = [["volume_24h", "מחזור 24ש׳"], ["change", "שינוי יומי"], ["liquidity", "נזילות"], ["end_date", "סגירה קרובה"]];
  const seg = h("div", { class: "seg", role: "group", "aria-label": "מיון" }, sorts.map(([v, l]) =>
    h("button", { type: "button", "aria-pressed": String(explorer.sort === v), onclick: (e) => {
      explorer.sort = v; seg.querySelectorAll("button").forEach((b) => b.setAttribute("aria-pressed", String(b === e.currentTarget))); load(true);
    } }, l)));
  const liq = h("select", { "aria-label": "נזילות מינימלית", onchange: (e) => { explorer.minLiq = e.target.value; load(true); } },
    [["0", "כל נזילות"], ["1000", "נזילות ≥ $1K"], ["10000", "≥ $10K"], ["100000", "≥ $100K"]].map(([v, l]) =>
      h("option", { value: v, selected: explorer.minLiq === v }, l)));
  const trad = h("label", { class: "check" }, h("input", { type: "checkbox", checked: explorer.tradable,
    onchange: (e) => { explorer.tradable = e.target.checked; load(true); } }), "רק סחירים");
  const openOnly = h("label", { class: "check", title: "מסתיר שווקים שכבר כמעט הוכרעו (97%+ או 3%-) ושווקים שנסגרים תוך 24 שעות — משחקים חיים" },
    h("input", { type: "checkbox", checked: explorer.openOnly, onchange: (e) => { explorer.openOnly = e.target.checked; load(true); } }), "בלי שווקים שכבר הוכרעו");
  let t;
  input.addEventListener("input", () => { clearTimeout(t); t = setTimeout(() => { explorer.search = input.value.trim(); load(true); }, 300); });
  more.addEventListener("click", () => load(false));

  async function load(reset) {
    if (reset) { explorer.offset = 0; list.replaceChildren(skel(6)); }
    const p = new URLSearchParams({ sort: explorer.sort, min_liquidity: explorer.minLiq, tradable: explorer.tradable, open_only: explorer.openOnly, limit: 50, offset: explorer.offset });
    if (explorer.search) p.set("search", explorer.search);
    try {
      const rows = await api("/api/markets?" + p);
      if (reset) list.replaceChildren();
      rows.forEach((m) => list.append(marketRow(m, { wide: true })));
      if (reset && !rows.length) list.append(empty("לא נמצאו שווקים"));
      explorer.offset += rows.length;
      more.hidden = rows.length < 50;
    } catch (e) { list.replaceChildren(fail(e)); }
  }
  view.replaceChildren(
    h("div", { class: "toolbar" }, h("div", { class: "search" }, input, icon("search", 18)), seg, liq, trad, openOnly),
    h("section", { class: "card" }, listHead(), list, h("div", { style: "text-align:center;margin-top:10px" }, more)));
  load(true);
};

// ---- Market Detail ----
PAGES.detail = async (view, id) => {
  const m = await api("/api/markets/" + encodeURIComponent(id));
  if (current !== "detail") return;
  const outs = (m.outcomes || []).map((o, i) => ({ name: o, p: (m.outcome_prices || [])[i] }));
  const url = safeUrl(m.url);
  const src = safeUrl(m.resolution_source);
  const status = m.closed ? h("span", { class: "badge" }, "נסגר") : m.accepting_orders === false ? h("span", { class: "badge bad" }, "לא מקבל פקודות")
    : h("span", { class: "badge ok" }, "פתוח למסחר");

  const chartBox = h("div", { class: "chart-wrap" }, skel(1));
  const ranges = [["1d", "יום"], ["1w", "שבוע"], ["1m", "חודש"], ["max", "הכול"]];
  const rseg = h("div", { class: "seg", role: "group", "aria-label": "טווח זמן" });
  ranges.forEach(([r, l]) => rseg.append(h("button", { type: "button", "aria-pressed": String(r === "1w"), onclick: (e) => {
    rseg.querySelectorAll("button").forEach((b) => b.setAttribute("aria-pressed", String(b === e.currentTarget))); loadChart(r);
  } }, l)));
  async function loadChart(r) {
    chartBox.replaceChildren(h("div", { class: "skel", style: "height:240px" }));
    try {
      const d = await api(`/api/markets/${encodeURIComponent(id)}/history?range=${r}`);
      chartBox.replaceChildren(d.points.length >= 2 ? lineChart(d.points) : empty("אין היסטוריית מחיר לטווח הזה"));
    } catch (e) { chartBox.replaceChildren(fail(e)); }
  }

  const bookBox = h("div", null, skel(4));
  async function loadBook() {
    try { bookBox.replaceChildren(bookView(await api(`/api/markets/${encodeURIComponent(id)}/book`))); }
    catch (e) { bookBox.replaceChildren(fail(e), m.orderbook ? h("div", { class: "faint", style: "margin-top:8px" }, `ספר שמור אחרון: ${ago(m.orderbook.ts)}`) : null); }
  }

  const rules = h("div", { class: "rules", dir: "auto" }, m.description || "אין תיאור כללי הכרעה במקור.");
  const rulesBtn = h("button", { class: "btn", type: "button", onclick: () => { rules.classList.toggle("open"); rulesBtn.textContent = rules.classList.contains("open") ? "הצג פחות" : "הצג הכול"; } }, "הצג הכול");
  const dq = m.data_quality;

  view.replaceChildren(
    h("div", { class: "detail-head" },
      h("a", { href: "#/markets", class: "faint" }, "→ חזרה לשווקים"),
      h("h1", { dir: "auto" }, m.question),
      h("div", { class: "detail-actions" }, status, until(m.end_date) ? h("span", { class: "badge" }, until(m.end_date)) : null,
        m.neg_risk ? h("span", { class: "badge info" }, "חלק מקבוצת תוצאות") : null,
        url ? h("button", { class: "btn primary", type: "button", onclick: async (ev) => { const b = ev.currentTarget; b.disabled = true;
          try { await apiSend("POST", "/api/follows", { text: url }); b.textContent = "במעקב ✓"; } catch (e) { b.disabled = false; alert(e.message); } } }, "עקוב בטלגרם") : null,
        url ? h("a", { class: "btn", href: url, target: "_blank", rel: "noopener noreferrer" }, "פתח ב-Polymarket", icon("ext", 16)) : null)),
    h("div", { class: "cols-main" },
      h("div", { class: "stack" },
        plainMarket(m),
        card("תוצאות ומחירים", "מחיר = ההסתברות שהשוק מייחס · לא תחזית עצמאית",
          outs.length ? h("div", { class: "outcomes" }, outs.map((o) => h("div", { class: "outcome" },
            h("div", { class: "n", dir: "auto" }, o.name), h("div", { class: "p" }, ltr(pct(o.p))),
            h("div", { class: "bar" }, h("i", { style: `width:${Math.max(0, Math.min(100, (num(o.p) || 0) * 100))}%` }))))) : empty("אין מחירי תוצאות")),
        h("section", { class: "card" },
          h("div", { class: "card-head" }, h("div", null, h("h2", null, `היסטוריית מחיר · ${m.ref_outcome || ""}`), h("div", { class: "sub" }, "מקור: CLOB של Polymarket")), rseg),
          chartBox),
        h("section", { class: "card" }, h("h2", { style: "margin-bottom:12px" }, "מחזור ונזילות"),
          h("div", { class: "stats" },
            stat("מחזור כולל", usd(m.volume)), stat("מחזור 24ש׳", usd(m.volume_24h)), stat("נזילות", usd(m.liquidity)),
            stat("Bid / Ask", `${cents(m.best_bid)} / ${cents(m.best_ask)}`), stat("מרווח", cents(m.spread)),
            h("div", null, h("div", { class: "l" }, "שינוי יומי"), h("div", { class: "v" }, ptsEl(m.one_day_change))))),
        h("section", { class: "card" }, h("h2", null, "כללי הכרעה"), h("div", { class: "sub", style: "margin-bottom:8px" }, "הטקסט המקורי מ-Polymarket"),
          rules, h("div", { class: "detail-actions", style: "margin-top:10px" }, rulesBtn,
            src ? h("a", { class: "btn", href: src, target: "_blank", rel: "noopener noreferrer" }, "מקור ההכרעה", icon("ext", 16)) : null))),
      h("div", { class: "stack" },
        h("section", { class: "card reco" },
          h("div", { class: "sub" }, "המלצה"),
          h("div", { class: "detail-actions" }, h("span", { class: "badge wait", style: "font-size:15px;padding:6px 14px" }, "לא לקנות כרגע")),
          h("div", { style: "font-size:15px" }, m.reason),
          h("div", { class: "section-title" }, "מגבלות"),
          h("ul", { class: "clean" },
            h("li", null, "אין מודל הסתברות עצמאי שעבר אימות היסטורי."),
            h("li", null, "מחיר שוק הוא קונצנזוס, לא יתרון."),
            h("li", null, "YES/NO יוצג רק עם הצדקה סטטיסטית ואיכות נתונים טובה."))),
        h("section", { class: "card" }, h("h2", null, "איכות נתונים"),
          h("div", { class: "detail-actions", style: "margin:8px 0" }, h("span", { class: "badge " + ({ good: "ok", limited: "wait", bad: "bad" }[dq.level]) },
            { good: "טובה", limited: "חלקית", bad: "לא מספקת" }[dq.level])),
          dq.issues.length ? h("ul", { class: "clean" }, dq.issues.map((i) => h("li", null, i))) : h("div", { class: "sub" }, "אין בעיות ידועות")),
        priceTypesCard(m.prices, m.accepting_orders),
        h("section", { class: "card" }, h("h2", null, "ספר פקודות ומחיר ביצוע"), h("div", { class: "sub", style: "margin-bottom:10px" }, "חי מ-CLOB · מתעדכן כל 30 שניות"), bookBox))));
  loadChart("1w");
  loadBook();
};
const stat = (l, v) => h("div", null, h("div", { class: "l" }, l), h("div", { class: "v" }, ltr(v)));
// Four different "prices" that are easy to confuse: show each with what it is, its source and time.
function priceTypesCard(p, accepting) {
  if (!p) return null;
  const when = (t) => t ? ago(t) : "לא ידוע";
  const row = (name, value, what, src, t) => h("tr", null, h("td", null, h("strong", null, name), h("div", { class: "faint" }, what)),
    h("td", null, value), h("td", null, h("div", null, src), h("div", { class: "faint" }, "זמן מקור: " + when(t))));
  const ex = p.executable;
  return card("ארבעה סוגי מחיר", accepting === true ? "השוק מקבל פקודות (אומת ב-Gamma)" : accepting === false ? "השוק לא מקבל פקודות" : "לא ידוע אם השוק מקבל פקודות — לא נחשב סחיר",
    h("div", { class: "scroll-x" }, h("table", { class: "t" },
      h("thead", null, h("tr", null, ["סוג", "ערך", "מקור"].map((x) => h("th", null, x)))),
      h("tbody", null,
        row("מחיר תצוגה", ltr(pct(p.display.value)), "מה שמוצג באתר Polymarket. לא בהכרח אפשר לקנות בו", "Gamma", p.display.source_time),
        row("Bid / Ask מדווח", ltr(`${cents(p.quoted.bid)} / ${cents(p.quoted.ask)}`), "הצעה הכי טובה לפי Gamma, בלי כמות", "Gamma", p.quoted.source_time),
        p.book ? row("ספר פקודות", p.book.valid ? h("span", { class: "badge ok" }, "תקין") : h("span", { class: "badge bad" }, "לא תקין"),
          "שני הצדדים קיימים ו-Bid קטן מ-Ask", "CLOB", p.book.source_time || p.book.ingested_at)
          : row("ספר פקודות", "—", "לא נאסף לשוק הזה", "CLOB", null),
        ex ? row("מחיר בר-ביצוע", h("div", null, ltr(`$100: ${cents(ex.buy_100)}`), h("div", null, ltr(`$1,000: ${ex.buy_1000 == null ? "אין עומק" : cents(ex.buy_1000)}`))),
          "מחיר ממוצע לקנייה בפועל לפי עומק הספר", "CLOB", ex.source_time || ex.ingested_at)
          : row("מחיר בר-ביצוע", "—", "אין ספר תקין, אי אפשר לחשב", "CLOB", null)))));
}

function bookView(b) {
  if (b.empty) return empty("ספר הפקודות ריק");
  const levels = (side) => (b.levels?.[side] || []).slice(0, 8);
  const maxSize = Math.max(1, ...["bids", "asks"].flatMap((sd) => levels(sd).map(([p, sz]) => p * sz)));
  const lvl = (cls) => ([p, sz]) => h("div", { class: "lvl " + cls }, h("i", { style: `width:${Math.round(p * sz / maxSize * 100)}%` }),
    h("span", null, ltr(cents(p))), h("span", null, ltr(usd(p * sz))));
  return h("div", { class: "stack" },
    h("div", { class: "detail-actions" },
      h("span", { class: "badge " + (b.valid ? "ok" : "bad") }, b.valid ? "ספר תקין" : "ספר לא תקין"),
      h("span", { class: "faint" }, `${b.n_bids} רמות קנייה · ${b.n_asks} רמות מכירה · ${ago(b.fetched_at)}`)),
    h("div", { class: "scroll-x" }, h("table", { class: "t" },
      h("thead", null, h("tr", null, h("th", null, ""), h("th", null, "$100"), h("th", null, "$1,000"))),
      h("tbody", null,
        h("tr", null, h("td", null, "מחיר קנייה ממוצע"), h("td", null, ltr(cents(b.buy_vwap_100))), h("td", null, ltr(cents(b.buy_vwap_1000)))),
        h("tr", null, h("td", null, "מחיר מכירה ממוצע"), h("td", null, ltr(cents(b.sell_vwap_100))), h("td", null, ltr(cents(b.sell_vwap_1000))))))),
    h("div", { class: "faint" }, "\"—\" = אין מספיק עומק למלא את הסכום."),
    h("div", { class: "depth" },
      h("div", { class: "side" }, h("h4", null, "קונים (Bid)"), levels("bids").map(lvl("bid"))),
      h("div", { class: "side" }, h("h4", null, "מוכרים (Ask)"), levels("asks").map(lvl("ask")))));
}

function lineChart(points) {
  const W = 800, H = 300, P = { t: 14, r: 10, b: 26, l: 44 };
  const xs = points.map((p) => p.t), ys = points.map((p) => p.p);
  const x0 = Math.min(...xs), x1 = Math.max(...xs);
  let y0 = Math.min(...ys), y1 = Math.max(...ys);
  const pad = Math.max(0.02, (y1 - y0) * 0.15); y0 = Math.max(0, y0 - pad); y1 = Math.min(1, y1 + pad);
  const X = (t) => P.l + (t - x0) / ((x1 - x0) || 1) * (W - P.l - P.r);
  const Y = (v) => P.t + (1 - (v - y0) / ((y1 - y0) || 1)) * (H - P.t - P.b);
  const d = points.map((p, i) => `${i ? "L" : "M"}${X(p.t).toFixed(1)},${Y(p.p).toFixed(1)}`).join("");
  const area = `${d}L${X(x1).toFixed(1)},${H - P.b}L${X(x0).toFixed(1)},${H - P.b}Z`;
  const last = points[points.length - 1], first = points[0];
  const color = last.p >= first.p ? "var(--up)" : "var(--down)";
  const ticks = [y0, (y0 + y1) / 2, y1];
  const fmtT = (t) => new Date(t * 1000).toLocaleDateString("he-IL", (x1 - x0) < 2 * 86400 ? { hour: "2-digit", minute: "2-digit" } : { day: "numeric", month: "short" });
  const svg = s("svg", { class: "chart", direction: "ltr", viewBox: `0 0 ${W} ${H}`, preserveAspectRatio: "none", role: "img",
    "aria-label": `גרף מחיר: מ-${pct(first.p)} ל-${pct(last.p)}` },
    s("defs", null, s("linearGradient", { id: "g", x1: 0, y1: 0, x2: 0, y2: 1 },
      s("stop", { offset: "0%", "stop-color": color, "stop-opacity": 0.28 }), s("stop", { offset: "100%", "stop-color": color, "stop-opacity": 0 }))),
    ticks.map((v) => [s("line", { x1: P.l, x2: W - P.r, y1: Y(v), y2: Y(v), stroke: "#243041", "stroke-width": 1 }),
      s("text", { x: P.l - 6, y: Y(v) + 4, "text-anchor": "end", "font-size": 11, fill: "#6F8094" }, (v * 100).toFixed(0) + "%")]),
    s("text", { x: P.l, y: H - 6, "font-size": 11, fill: "#6F8094" }, fmtT(x0)),
    s("text", { x: W - P.r, y: H - 6, "text-anchor": "end", "font-size": 11, fill: "#6F8094" }, fmtT(x1)),
    s("path", { d: area, fill: "url(#g)" }),
    s("path", { d, fill: "none", stroke: color, "stroke-width": 2, "vector-effect": "non-scaling-stroke" }));
  const cursor = s("line", { y1: P.t, y2: H - P.b, stroke: "#9AA9BB", "stroke-width": 1, "stroke-dasharray": "3 3", visibility: "hidden", "vector-effect": "non-scaling-stroke" });
  svg.append(cursor);
  const tip = h("div", { class: "chart-tip" }, ltr(pct(last.p)), " · ", fmtT(last.t));
  svg.addEventListener("pointermove", (e) => {
    const r = svg.getBoundingClientRect();
    const tx = x0 + ((e.clientX - r.left) / r.width * W - P.l) / (W - P.l - P.r) * (x1 - x0);
    let best = points[0];
    for (const p of points) if (Math.abs(p.t - tx) < Math.abs(best.t - tx)) best = p;
    cursor.setAttribute("x1", X(best.t)); cursor.setAttribute("x2", X(best.t)); cursor.setAttribute("visibility", "visible");
    tip.replaceChildren(ltr(pct(best.p)), " · ", new Date(best.t * 1000).toLocaleString("he-IL", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" }));
  });
  svg.addEventListener("pointerleave", () => { cursor.setAttribute("visibility", "hidden"); tip.replaceChildren(ltr(pct(last.p)), " · ", fmtT(last.t)); });
  return h("div", { class: "chart-wrap" }, svg, tip);
}

// ---- Opportunity Scanner ----
PAGES.scanner = async (view) => {
  const [rep, ov] = await Promise.all([api("/api/report/latest"), api("/api/overview")]);
  if (current !== "scanner") return;
  const st = rep?.result?.structural;
  const supported = ov.opportunities.statistically_supported || [];
  const theo = st?.theoretical || [], exe = st?.executable || [], rej = st?.rejected_after_live_check || [];
  view.replaceChildren(h("div", { class: "stack" },
    h("section", { class: "card" }, h("div", { class: "sub" }, "השורה התחתונה"),
      h("div", { class: "answer", style: "margin:6px 0" }, exe.length ? `${exe.length} הזדמנויות בנות-ביצוע` : "אין הזדמנות בת-ביצוע"),
      h("div", { class: "sub" }, rep ? `דוח מבני ${ago(rep.created_at)} · ${st.groups_checked} קבוצות נבדקו` : "הדוח המבני עוד לא רץ על השרת")),
    h("div", { class: "kpis" },
      kpi("חוסר עקביות מתמטי", ltr(String(st?.mathematical?.length ?? "—")), "Σ מחירים ≠ 1 (±2%)"),
      kpi("הזדמנות תאורטית", ltr(String(theo.length || (st ? 0 : "—"))), "Σ ask < $1 לפני עלויות"),
      kpi("נבדקו בספר חי", ltr(String(st?.live_checked ?? "—")), "כל הרגליים, 10 ו-100 מניות"),
      kpi("בנות-ביצוע", ltr(String(st ? exe.length : "—")), "אחרי עומק, עמלות, רגל Other")),
    card("למה תאורטי ≠ בר-ביצוע", null, h("ul", { class: "clean" },
      h("li", null, "כל רגל היא פקודה נפרדת: מילוי חלקי משאיר אותך חשוף."),
      h("li", null, "רגל \"Other\" אומרת שאפשר להוסיף תוצאות — הסט לא שלם."),
      h("li", null, "עמלות, ומחיר שמטפס כשקונים כמות."),
      h("li", null, "הכסף נעול עד ההכרעה; מחלוקות הכרעה אפשריות."))),
    card("מועמדים שנבדקו בספר החי", "מה קרה כשניסינו לקנות סט מלא",
      (exe.length || rej.length) ? h("div", { class: "scroll-x" }, h("table", { class: "t" },
        h("thead", null, h("tr", null, ["אירוע", "רגליים", "Σ ask", "100 מניות", "חסמים"].map((x) => h("th", null, x)))),
        h("tbody", null, [...exe, ...rej].map((g) => {
          const s100 = g.sizes?.[100] || g.sizes?.["100"];
          return h("tr", null, h("td", { dir: "auto" }, g.event || g.group), h("td", null, ltr(String(g.legs))), h("td", null, ltr(String(g.sum_ask))),
            h("td", null, s100?.fillable ? ltr(`נטו ${s100.net >= 0 ? "+" : ""}$${s100.net}`, s100.net > 0 ? "up" : "down") : "לא ניתן למלא"),
            h("td", null, g.blockers?.length ? g.blockers.join(" · ") : h("span", { class: "badge ok" }, "אין"))); })))) : empty(st ? "אין מועמדים תאורטיים לבדיקה" : "ממתין לדוח הראשון")),
    card("אסטרטגיות עם תמיכה סטטיסטית", "מהמחקר ההיסטורי",
      supported.length ? h("ul", { class: "clean" }, supported.map((x) => h("li", null, `${x.side} ${pct(x.lo, 0)}–${pct(x.hi, 0)} · ${x.horizon_h}ש׳`))) :
        empty("אין. YES/NO לא יוצגו עד שתהיה הצדקה סטטיסטית וביצועית."))));
};

// ---- Watchlist ----
PAGES.watchlist = async (view) => {
  const ids = wlGet();
  if (!ids.length) { view.replaceChildren(empty("הרשימה ריקה. סמן ★ בשוק כדי לעקוב אחריו (נשמר בדפדפן הזה בלבד).")); return; }
  const rows = await api("/api/markets?tradable=false&limit=200&ids=" + encodeURIComponent(ids.join(",")));
  if (current !== "watchlist") return;
  const byId = Object.fromEntries(rows.map((r) => [r.id, r]));
  view.replaceChildren(h("section", { class: "card" }, listHead(),
    h("div", { class: "mlist" }, ids.map((id) => byId[id] ? marketRow(byId[id], { wide: true }) : null)),
    h("div", { class: "faint", style: "margin-top:10px" }, "נשמר בדפדפן הזה בלבד.")));
};

// ---- Research Lab ----
PAGES.research = async (view) => {
  const r = await api("/api/research/latest");
  if (current !== "research") return;
  if (!r) { view.replaceChildren(empty("עדיין אין דוח מחקר. צריך להריץ backfill של שווקים שנפתרו ואז research.")); return; }
  const LBL = { "24": "יום לפני הסגירה", "168": "שבוע לפני", "720": "חודש לפני" };
  view.replaceChildren(h("div", { class: "stack" },
    h("div", { class: "sub" }, `דוח מ-${new Date(r.created_at).toLocaleString("he-IL")} · החלקה ${r.params.slippage} · עמלה ${r.params.fee} · תשואות לפי אירוע`),
    ...Object.entries(r.result).map(([hz, res]) => h("div", { class: "cols-2" },
      card(`כיול · ${LBL[hz] || hz + " שע׳"}`, `${res.calibration.n} שווקים · Brier ${res.calibration.brier == null ? "—" : res.calibration.brier.toFixed(3)}`,
        reliability(res.calibration.buckets)),
      card("אסטרטגיות", "אחרי החלקה; ממוצע לפי אירוע", h("div", { class: "scroll-x" }, h("table", { class: "t" },
        h("thead", null, h("tr", null, ["כלל", "עסקאות/אירועים", "תשואה", "CI 95%", ""].map((x) => h("th", null, x)))),
        h("tbody", null, res.strategies.filter((x) => x.trades > 0).map((x) => h("tr", null,
          h("td", null, `${x.side} `, ltr(`${pct(x.lo, 0)}–${pct(x.hi, 0)}`)), h("td", null, ltr(`${x.trades}/${x.events}`)),
          h("td", null, ltr(pct(x.mean_return), x.mean_return >= 0 ? "up" : "down")),
          h("td", null, x.ci_lo == null ? "—" : ltr(`${pct(x.ci_lo)} … ${pct(x.ci_hi)}`)),
          h("td", null, h("span", { class: "badge" }, x.insample_ci_positive ? "חיובי בתוך המדגם בלבד" : "לא הוכח")))))))),
      wfCard(res.walk_forward)))));
};
const WF_VERDICT = { supported: ["ok", "נתמך — עבר את כל השערים"], oos_positive_needs_execution_check: ["wait", "חיובי מחוץ למדגם — עדיין לא אומת ביצוע בספר פקודות"],
  not_supported: ["", "לא נתמך"], insufficient_data: ["", "אין מספיק נתונים"] };
function wfCard(w) {
  if (!w) return null;
  const [cls, label] = WF_VERDICT[w.verdict] || ["", w.verdict];
  const f = w.forecast, o = w.oos_trading, g = w.gates || {};
  const gate = (ok, text) => h("li", null, ok ? "✓ " : "✗ ", text);
  return card("בדיקה מחוץ למדגם (Walk-Forward)", "מאמנים על העבר, בודקים על התקופה שאחריו. בלי מידע עתידי, אירועים קשורים מוצאים מהאימון",
    h("div", { class: "detail-actions", style: "margin-bottom:8px" }, h("span", { class: "badge " + cls }, label)),
    w.verdict === "insufficient_data" ? h("p", { class: "sub" }, w.reason) : h("div", { class: "stack" },
      f ? h("div", { class: "stats" },
        stat("Brier — מחיר השוק", f.market_brier.toFixed(4)), stat("Brier — כיול מחדש", f.model_brier.toFixed(4)),
        stat("Log loss — שוק / מודל", `${f.market_log_loss.toFixed(3)} / ${f.model_log_loss.toFixed(3)}`)) : null,
      f ? h("p", { class: "sub" }, f.model_beats_market ? "הכיול מחדש ניבא טוב יותר מהשוק מחוץ למדגם." : "שום מודל לא ניבא טוב יותר ממחיר השוק מחוץ למדגם.") : null,
      o ? h("p", { class: "sub" }, `כלל מסחר שנבחר באימון ונבדק אחריו: ${o.events} אירועים, תשואה ממוצעת `, ltr(pct(o.mean_return)),
        o.ci ? h("span", null, " (טווח 95%: ", ltr(`${pct(o.ci[0])} … ${pct(o.ci[1])}`), ")") : null) : null,
      h("ul", { class: "clean" },
        gate(g.oos_ci_above_zero_30_events, "רווח מחוץ למדגם, לפחות 30 אירועים, כל הטווח מעל 0"),
        gate(g.survives_holm, "שורד תיקון לבדיקה של הרבה כללים (Holm)"),
        gate(g.positive_at_2x_slippage, "נשאר חיובי גם עם החלקה כפולה"),
        gate(g.execution_validated, "אומת שאפשר לבצע בספר פקודות חי"))));
}
function reliability(buckets) {
  const W = 300, P = 30;
  const X = (v) => P + v * (W - 2 * P), Y = (v) => W - P - v * (W - 2 * P);
  const maxN = Math.max(1, ...buckets.map((b) => b.n));
  return s("svg", { class: "reliab", viewBox: `0 0 ${W} ${W}`, role: "img", "aria-label": "גרף כיול: מחיר מול שכיחות בפועל" },
    s("line", { x1: X(0), y1: Y(0), x2: X(1), y2: Y(1), stroke: "#3A4A5E", "stroke-dasharray": "4 4" }),
    s("line", { x1: X(0), y1: Y(0), x2: X(1), y2: Y(0), stroke: "#243041" }), s("line", { x1: X(0), y1: Y(0), x2: X(0), y2: Y(1), stroke: "#243041" }),
    s("text", { x: W / 2, y: W - 6, "text-anchor": "middle", "font-size": 11, fill: "#6F8094" }, "מחיר השוק"),
    s("text", { x: 10, y: W / 2, "font-size": 11, fill: "#6F8094", transform: `rotate(-90 10 ${W / 2})`, "text-anchor": "middle" }, "קרה בפועל"),
    buckets.map((b) => [s("line", { x1: X(b.mean_price), x2: X(b.mean_price), y1: Y(b.ci_lo), y2: Y(b.ci_hi), stroke: "#5B9BFF", "stroke-width": 1.5 }),
      s("circle", { cx: X(b.mean_price), cy: Y(b.freq), r: 3 + 6 * Math.sqrt(b.n / maxN), fill: "#5B9BFF", "fill-opacity": 0.85 })]));
}

// ---- Evidence board (watchlist) ----
const KIND = { sec: "דיווח רשמי ל-SEC", telegram: "טלגרם", manual: "רישום ידני" };
PAGES.evidence = async (view) => {
  const d = await api("/api/evidence");
  if (current !== "evidence") return;
  const pctOrDash = (v) => v == null ? h("span", { class: "faint" }, "—") : ltr(pct(v, 1), v >= 0 ? "up" : "down");
  const finding = (f) => h("div", { class: "card", style: "padding:12px" },
    h("div", { class: "detail-actions" },
      h("span", { class: "badge " + (f.official ? "ok" : "") }, KIND[f.kind] || f.kind),
      h("strong", null, f.source),
      f.risk_verdict ? h("span", { class: "badge " + ({ red: "bad", yellow: "wait", green: "ok" }[f.risk_verdict] || "") }, "סיכון: " + ({ red: "אדום", yellow: "צהוב", green: "ירוק" }[f.risk_verdict] || f.risk_verdict)) : null),
    h("div", { class: "stats", style: "margin-top:10px" },
      stat("פורסם", new Date(f.published_at).toLocaleString("he-IL", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" })),
      stat("מחיר לפני הפרסום", f.prior_close == null ? "—" : "$" + f.prior_close.toFixed(2)),
      stat("מחיר עכשיו", f.current == null ? "—" : "$" + f.current.toFixed(2)),
      h("div", null, h("div", { class: "l" }, "שינוי מאז"), h("div", { class: "v" }, pctOrDash(f.change_since))),
      h("div", null, h("div", { class: "l" }, "אחרי 5 ימים"), h("div", { class: "v" }, pctOrDash(f.r5))),
      h("div", null, h("div", { class: "l" }, "מול SPY (5 ימים)"), h("div", { class: "v" }, pctOrDash(f.excess_r5)))),
    h("div", { class: "detail-actions", style: "margin-top:8px" },
      safeUrl(f.source_url) ? h("a", { class: "btn", href: safeUrl(f.source_url), target: "_blank", rel: "noopener noreferrer" }, "למקור", icon("ext", 16)) : h("span", { class: "faint" }, "אין קישור למקור"),
      h("a", { class: "btn", href: "#/check/" + encodeURIComponent(f.symbol || "") }, "בדיקת סיכון")),
    h("div", { class: "sub", style: "margin-top:8px" }, "איכות הנתונים: " + (f.data_quality.join(" · ") || "תקינה")),
    h("div", { class: "faint", style: "margin-top:4px" }, "מה עוד לא ידוע: " + f.unknown.join(" · ")));
  const pm = (m) => h("div", { class: "card", style: "padding:12px" },
    h("div", { class: "detail-actions" }, h("span", { class: "badge info" }, "Polymarket · התאמה לפי שם החברה"),
      h("a", { href: "#/m/" + encodeURIComponent(m.id), dir: "auto" }, m.question)),
    h("div", { class: "sub", style: "margin-top:6px" }, "מחיר: ", ltr(pct(m.price)), " · שינוי יומי: ", ptsEl(m.one_day_change)),
    m.resolution_risk.length ? h("ul", { class: "clean" }, m.resolution_risk.map((r) => h("li", null, r))) : h("div", { class: "faint" }, "לא זוהו סיכוני הכרעה בולטים"),
    h("div", { class: "faint" }, m.unknown.join(" · ")));
  view.replaceChildren(h("div", { class: "stack" },
    h("p", { class: "sub" }, d.note),
    ...d.watchlist.map((w) => card(w.symbol,
      w.price == null ? "אין נתוני מחיר" : h("span", null, ltr("$" + w.price.toFixed(2)), " · ", pctOrDash(w.day_change), " ביום האחרון · ", w.price_day),
      w.findings.length ? h("div", { class: "stack" }, w.findings.map((f) => finding({ ...f, symbol: w.symbol }))) : empty("אין ממצאים ב-60 הימים האחרונים"),
      w.polymarket.length ? h("div", { class: "stack", style: "margin-top:10px" }, w.polymarket.map(pm)) : null))));
};

// ---- Suspicious-stock checker ----
const VERDICT = { red: ["bad", "אדום — דגלים של ניפוח מחיר (pump). לא להיכנס."], yellow: ["wait", "צהוב — יש סימני סיכון, לבדוק לעומק."],
  green: ["ok", "ירוק — לא נמצאו דגלים אדומים. זה לא אומר שהמניה טובה."], unknown: ["info", "אין מספיק נתונים"] };
function priceChart(bars) {
  const W = 800, H = 220, P = { t: 10, r: 10, b: 22, l: 50 };
  const c = bars.map((b) => b.c), y0 = Math.min(...c) * 0.95, y1 = Math.max(...c) * 1.05;
  const X = (i) => P.l + i / Math.max(1, bars.length - 1) * (W - P.l - P.r), Y = (v) => P.t + (1 - (v - y0) / (y1 - y0)) * (H - P.t - P.b);
  const up = c[c.length - 1] >= c[0];
  return h("div", { class: "chart-wrap" }, s("svg", { class: "chart", direction: "ltr", viewBox: `0 0 ${W} ${H}`, preserveAspectRatio: "none", role: "img", "aria-label": "מחיר סגירה בשנה האחרונה" },
    [y0, (y0 + y1) / 2, y1].map((v) => [s("line", { x1: P.l, x2: W - P.r, y1: Y(v), y2: Y(v), stroke: "#243041" }),
      s("text", { x: P.l - 6, y: Y(v) + 4, "text-anchor": "end", "font-size": 11, fill: "#6F8094" }, "$" + v.toFixed(v < 10 ? 2 : 0))]),
    s("text", { x: P.l, y: H - 5, "font-size": 11, fill: "#6F8094" }, bars[0].day),
    s("text", { x: W - P.r, y: H - 5, "text-anchor": "end", "font-size": 11, fill: "#6F8094" }, bars[bars.length - 1].day),
    s("path", { d: bars.map((b, i) => `${i ? "L" : "M"}${X(i).toFixed(1)},${Y(b.c).toFixed(1)}`).join(""), fill: "none",
      stroke: up ? "var(--up)" : "var(--down)", "stroke-width": 2, "vector-effect": "non-scaling-stroke" })));
}
PAGES.check = async (view, sym) => {
  const input = h("input", { type: "search", placeholder: "סימול, למשל PSIG", value: sym || "", "aria-label": "סימול מניה", autocomplete: "off", dir: "ltr" });
  const isin = h("input", { type: "search", placeholder: "ISIN (לא חובה)", "aria-label": "ISIN", autocomplete: "off", dir: "ltr" });
  const out = h("div", { class: "stack" });
  const go = (e) => { e.preventDefault(); const v = input.value.trim().toUpperCase(); if (v) location.hash = "#/check/" + encodeURIComponent(v) + (isin.value.trim() ? "?isin=" + encodeURIComponent(isin.value.trim()) : ""); };
  view.replaceChildren(h("div", { class: "stack" },
    card("בודק מניות חשודות", "קיבלת \"המלצה חמה\"? תבדוק כאן לפני שאתה קונה",
      h("form", { class: "toolbar", onsubmit: go }, h("div", { class: "search" }, input, icon("search", 18)), h("div", { class: "search" }, isin),
        h("button", { type: "submit", class: "btn primary" }, "בדוק"))),
    out));
  if (!sym) { out.append(empty("הכנס סימול כדי לבדוק נזילות, קפיצות מחזור, קריסות קודמות ועלייה פרבולית.")); return; }
  const [symbol, qs] = sym.split("?");
  const isinQ = new URLSearchParams(qs || "").get("isin");
  if (isinQ) isin.value = isinQ;
  input.value = symbol;
  out.append(skel(3));
  let r;
  try { r = await api(`/api/check/${encodeURIComponent(symbol)}` + (isinQ ? `?isin=${encodeURIComponent(isinQ)}` : "")); }
  catch (e) { if (current === "check") out.replaceChildren(fail(e)); return; }
  if (current !== "check") return;
  const [cls, label] = VERDICT[r.verdict] || VERDICT.unknown;
  const m = r.metrics || {};
  out.replaceChildren(
    h("div", { class: "card" }, h("div", { class: "sub" }, ltr(r.symbol), r.name ? " · " + r.name : "", r.exchange ? " · " + r.exchange : ""),
      h("div", { style: "margin:10px 0" }, h("div", { class: "badge " + cls, style: "font-size:16px;padding:8px 14px;white-space:normal;display:block;line-height:1.4" }, label)),
      r.flags.length ? h("ul", { class: "clean" }, r.flags.map((f) => h("li", null,
        h("span", { class: "dot " + (f.level === "red" ? "bad" : "warn") }), " ", f.text))) : null,
      h("p", { class: "faint" }, r.disclaimer)),
    r.bars && r.bars.length > 1 ? card("מחיר בשנה האחרונה", r.source, priceChart(r.bars)) : null,
    m.last != null ? h("div", { class: "kpis" },
      kpi("מחיר אחרון", ltr("$" + m.last.toFixed(2))),
      kpi("מחזור יומי חציוני", ltr(usd(m.median_dollar_volume_20d)), "20 ימים"),
      kpi("הירידה הגרועה ביום אחד", ltr(pct(m.worst_day, 0), "down"), m.worst_day_date || ""),
      kpi("מהשפל של 60 יום", ltr(pct(m.from_60d_low, 0), m.from_60d_low >= 0 ? "up" : "down"))) : null,
    card("איך מזהים תרגיל pump", null, h("ul", { class: "clean" },
      h("li", null, "מבטיחים תשואה מדויקת בזמן קצר (\"10–20% בשבוע\")."),
      h("li", null, "כולם מקבלים אותו מחיר כניסה באותו רגע."),
      h("li", null, "מבקשים צילום מסך של ההוראה או של הביצוע."),
      h("li", null, "מניה קטנה, לרוב רשומה באיים (קיימן, BVI), עם מחזור זעיר."),
      h("li", null, "\"עמלה מהרווחים\" שתשלם להם בהמשך."))));
};

// ---- Tip tracker ----
const pctCell = (v) => v == null ? h("span", { class: "faint" }, "—") : ltr(pct(v, 1), v >= 0 ? "up" : "down");
const VBADGE = { red: ["bad", "אדום"], yellow: ["wait", "צהוב"], green: ["ok", "ירוק"], unknown: ["info", "?"] };
PAGES.tips = async (view) => {
  const d = await api("/api/tips");
  if (current !== "tips") return;
  const sym = h("input", { type: "search", placeholder: "סימול", "aria-label": "סימול", dir: "ltr", autocomplete: "off", required: true });
  const src = h("input", { type: "search", placeholder: "מקור (למשל: וואטסאפ הילה)", "aria-label": "מקור", autocomplete: "off", required: true });
  const price = h("input", { type: "number", step: "any", min: "0", placeholder: "מחיר שנאמר (לא חובה)", "aria-label": "מחיר", dir: "ltr" });
  const msg = h("div", { class: "faint", role: "status" });
  const form = h("form", { class: "toolbar", onsubmit: async (e) => {
    e.preventDefault();
    const body = { symbol: sym.value.trim(), source: src.value.trim() };
    if (price.value) body.price = Number(price.value);
    const r = await fetch("/api/tips", { method: "POST", headers: { "Content-Type": "application/json", Accept: "application/json" }, body: JSON.stringify(body) });
    if (r.status === 201) { msg.textContent = "נרשם. התוצאה תימדד אוטומטית."; setTimeout(route, 600); }
    else msg.textContent = r.status === 409 ? "הטיפ הזה כבר נרשם ב-24 השעות האחרונות." : `שגיאה (${r.status})`;
  } }, h("div", { class: "search" }, sym), h("div", { class: "search" }, src), h("div", { class: "search" }, price),
    h("button", { type: "submit", class: "btn primary" }, "רשום טיפ"));
  const table = (head, rows, none) => rows.length ? h("div", { class: "scroll-x" }, h("table", { class: "t" },
    h("thead", null, h("tr", null, head.map((x) => h("th", null, x)))), h("tbody", null, rows))) : empty(none);
  const GATE = { reliable: ["ok", "אמין"], promising: ["wait", "מבטיח — עוד לא מוכח"], not_reliable: ["bad", "לא אמין"], collecting: ["", "צובר נתונים"] };
  const gateCell = (g) => { if (!g) return "—"; const [c, l] = GATE[g.status] || ["", g.status];
    return h("div", null, h("span", { class: "badge " + c }, l),
      g.n ? h("div", { class: "faint" }, ltr(`${pct(g.success_rate, 0)} · n=${g.n}`)) : null,
      g.n ? h("div", { class: "faint" }, "רווח ממוצע אחרי עלות ", ltr(pct(g.edge_after_cost, 1), g.edge_after_cost >= 0 ? "up" : "down"),
        g.avg_cost != null ? h("span", null, " · עלות ", ltr(pct(g.avg_cost, 1))) : null) : null,
      g.downgraded ? h("div", { class: "faint" }, `ירד ל"מבטיח": נבדקו ${g.sources_tested} מקורות, והתוצאה יכולה להיות מזל`) : null); };
  const board = d.leaderboard.map((r) => h("tr", null, h("td", null, r.source), h("td", null, gateCell(r.gate)), h("td", null, ltr(String(r.tips))),
    h("td", null, ltr(String(r.measured_5d))), h("td", null, pctCell(r.median_r5)), h("td", null, r.win_rate_5d == null ? "—" : ltr(pct(r.win_rate_5d, 0))),
    h("td", null, pctCell(r.excess_r5)), h("td", null, r.beat_spy_5d == null ? "—" : ltr(pct(r.beat_spy_5d, 0))),
    h("td", null, pctCell(r.avg_r20)), h("td", null, pctCell(r.worst_drawdown_20)), h("td", null, r.red_share == null ? "—" : ltr(pct(r.red_share, 0)))));
  const recent = d.recent.map((t) => { const [c, l] = VBADGE[t.verdict] || ["info", "—"]; return h("tr", null,
    h("td", null, new Date(t.ts).toLocaleString("he-IL", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" })),
    h("td", null, t.source), h("td", null, h("a", { href: "#/check/" + encodeURIComponent(t.symbol) }, ltr(t.symbol))),
    h("td", null, h("span", { class: "badge " + c }, l)), h("td", null, t.entry_price == null ? "—" : ltr("$" + t.entry_price.toFixed(2))),
    h("td", null, pctCell(t.r1)), h("td", null, pctCell(t.r5)), h("td", null, pctCell(t.r20)), h("td", null, pctCell(t.max_drawdown_20))); });
  view.replaceChildren(h("div", { class: "stack" },
    card("רישום טיפ ידני", "קיבלת המלצה בוואטסאפ? תרשום אותה כאן — המערכת תמדוד מה קרה אחרי", form, msg),
    card("דירוג מקורות", d.rule + ". \"אמין\" = לפחות 50 טיפים, 60% ניצחו את SPY, רווח ממוצע חיובי אחרי עלויות, והתוצאה לא מקרית גם אחרי תיקון למספר המקורות שנבדקו", table(["מקור", "אמינות (ניצח SPY אחרי עלויות)", "טיפים", "נמדדו (5 ימים)", "חציון 5 ימים", "הרוויחו", "מול SPY (5 ימים)", "ניצחו את SPY", "ממוצע 20 יום", "הירידה הגרועה", "אדומים"], board,
      "עוד אין טיפים. רשום ידנית, או חבר את טלגרם (הוראות ב-README).")),
    card("טיפים אחרונים", "לחיצה על סימול פותחת את בודק המניות", table(["זמן", "מקור", "סימול", "סיכון", "כניסה", "יום", "5 ימים", "20 יום", "ירידה מקס׳"], recent, "—")),
    h("p", { class: "faint" }, "עמודות התשואה בטבלאות הן לפני עלויות. עמודת האמינות כבר מורידה לכל מניה את עלות המסחר המשוערת שלה — במניות זולות זה יכול להגיע לכמה אחוזים לעסקה. אין כאן המלצה לקנות דבר.")));
};

// ---- ETF paper study v2 ----
const REASON = { already_held: "כבר מוחזק", liquidity_or_price: "נזילות/מחיר", drawdown_halt: "עצירה: ירידה 8% מהשיא",
  daily_loss: "הפסד יומי 1%", health_data: "נתונים חסרים", health_clock: "שעון השרת לא מסונכרן", position_count: "כבר 5 פוזיציות",
  execution_bar_missing: "אין מחיר ביצוע", invalid_stop: "סטופ לא תקין", below_min_order: "מתחת ל-$10",
  gap_stop: "פתיחה מתחת לסטופ", stop: "סטופ", rule_exit: "אות יציאה", time_exit: "זמן החזקה מקסימלי",
  intent_missing: "לא נרשמה החלטה לפני הפתיחה", intent_late: "ההחלטה נרשמה מאוחר" };
const ACCT = { trend: "מגמה (Trend)", breakout: "פריצה (Breakout)" };
const money = (v) => num(v) == null ? "—" : "$" + Number(v).toLocaleString("en", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
function equityChart(series) {
  const W = 800, H = 260, P = { t: 12, r: 10, b: 24, l: 56 };
  const all = series.flatMap((x) => x.curve.map((p) => p.equity));
  if (!all.length) return empty("עוד אין ימי מסחר בחלון הניסוי.");
  const n = Math.max(...series.map((x) => x.curve.length));
  let y0 = Math.min(...all, 3000), y1 = Math.max(...all, 3000); const pad = Math.max(15, (y1 - y0) * 0.1); y0 -= pad; y1 += pad;
  const X = (i) => P.l + (n > 1 ? i / (n - 1) : 0.5) * (W - P.l - P.r), Y = (v) => P.t + (1 - (v - y0) / (y1 - y0)) * (H - P.t - P.b);
  return h("div", { class: "chart-wrap" }, s("svg", { class: "chart", direction: "ltr", viewBox: `0 0 ${W} ${H}`, preserveAspectRatio: "none", role: "img", "aria-label": "שווי החשבונות לאורך הניסוי" },
    [y0 + pad, 3000, y1 - pad].map((v) => [s("line", { x1: P.l, x2: W - P.r, y1: Y(v), y2: Y(v), stroke: "#243041" }),
      s("text", { x: P.l - 6, y: Y(v) + 4, "text-anchor": "end", "font-size": 11, fill: "#6F8094" }, "$" + Math.round(v).toLocaleString("en"))]),
    series.map((x) => s("path", { d: x.curve.map((p, i) => `${i ? "L" : "M"}${X(i).toFixed(1)},${Y(p.equity).toFixed(1)}`).join(""),
      fill: "none", stroke: x.color, "stroke-width": 2, "stroke-dasharray": x.dash || "", "vector-effect": "non-scaling-stroke" }))),
    h("div", { class: "faint", style: "display:flex;gap:16px;flex-wrap:wrap;margin-top:6px" },
      series.map((x) => h("span", null, h("span", { style: `display:inline-block;width:14px;height:3px;background:${x.color};vertical-align:middle;margin-inline-end:6px` }), x.name))));
}
PAGES.etf = async (view) => {
  const r = await api("/api/etf/latest");
  if (current !== "etf") return;
  if (!r) { view.replaceChildren(empty("הניסוי עוד לא רץ. הריצה הראשונה מתבצעת אחרי סגירת המסחר בארה״ב (00:15 שעון ישראל).")); return; }
  const R = r.result, P = R.protocol, b = R.benchmark_spy;
  const accts = Object.values(R.accounts);
  const kpis = h("div", { class: "kpis" },
    accts.map((a) => kpi(ACCT[a.account] || a.account, h("span", null, ltr(money(a.liquidated_equity)), " ", ltr(pct(a.return, 2), a.return >= 0 ? "up" : "down")),
      `${a.filled_entries} כניסות · ${a.closed_trades} עסקאות סגורות · ירידה מקס׳ ${pct(a.max_drawdown, 1)}`)),
    kpi("SPY (פשוט להחזיק)", b ? h("span", null, ltr(money(b.liquidated_equity)), " ", ltr(pct(b.return, 2), b.return >= 0 ? "up" : "down")) : "—", "הרף שצריך לנצח"),
    kpi("ימי מסחר בניסוי", ltr(String(R.sessions)), `${dateHe(P.window_start)} → ${dateHe(P.window_end)}`));
  const series = [
    ...accts.map((a, i) => ({ name: ACCT[a.account] || a.account, curve: a.curve, color: ["#5B9BFF", "#F0B14A"][i % 2] })),
    ...(b ? [{ name: "SPY", curve: b.curve, color: "#9AA9BB", dash: "5 4" }] : [])];
  const posRows = accts.flatMap((a) => a.open_positions.map((p) => h("tr", null, h("td", null, ACCT[a.account]), h("td", null, ltr(p.symbol)),
    h("td", null, ltr(p.quantity.toFixed(3))), h("td", null, ltr(money(p.entry))), h("td", null, ltr(money(p.stop))),
    h("td", null, ltr(money(p.unrealized_pnl), p.unrealized_pnl >= 0 ? "up" : "down")))));
  const decRows = accts.flatMap((a) => a.decisions.filter((d) => !(d.reasons || []).includes("already_held")).map((d) => ({ ...d, account: a.account })))
    .sort((x, y) => y.session.localeCompare(x.session)).slice(0, 30).map((d) => h("tr", null,
      h("td", null, dateHe(d.session)), h("td", null, ACCT[d.account]), h("td", null, ltr(d.symbol)),
      h("td", null, d.status === "filled" ? h("span", { class: "badge ok" }, "נקנה") : h("span", { class: "badge" }, "נדחה")),
      h("td", null, d.status === "filled" ? h("span", null, ltr(`${d.quantity.toFixed(3)} × ${money(d.entry)}`), " · סיכון ", ltr(money(d.initial_risk)))
        : d.reasons.map((x) => REASON[x] || x).join(", "))));
  const trRows = accts.flatMap((a) => a.trades.map((t) => ({ ...t, account: a.account }))).sort((x, y) => y.exit_session.localeCompare(x.exit_session))
    .map((t) => h("tr", null, h("td", null, dateHe(t.exit_session)), h("td", null, ACCT[t.account]), h("td", null, ltr(t.symbol)),
      h("td", null, REASON[t.reason] || t.reason), h("td", null, ltr(money(t.pnl), t.pnl >= 0 ? "up" : "down"))));
  const missed = accts.flatMap((a) => a.missed_sessions.map((m) => `${dateHe(m.session)} · ${ACCT[a.account]}: ${REASON[m.reason] || m.reason}`));
  const table = (head, rows, none) => rows.length ? h("div", { class: "scroll-x" }, h("table", { class: "t" },
    h("thead", null, h("tr", null, head.map((x) => h("th", null, x)))), h("tbody", null, rows))) : empty(none);
  const hl = R.health || {};
  view.replaceChildren(h("div", { class: "stack" },
    h("div", { class: "card", style: "border-color:#3A2E14" }, h("strong", null, "כסף מדומה בלבד. "),
      `כל חשבון מתחיל ב-${money(P.initial_capital)}. אין חיבור לברוקר ואין פקודות אמיתיות. הכללים נקבעו מראש ולא משתנים לפי התוצאות. ניסוי v1 הקודם נשאר רשום כנכשל.`),
    kpis,
    card("שווי החשבונות", "אחרי עמלות של 0.05% לכל צד; SPY כולל דיבידנדים", equityChart(series)),
    card("פוזיציות פתוחות", null, table(["חשבון", "ETF", "כמות", "כניסה", "סטופ", "רווח/הפסד"], posRows, "אין פוזיציות פתוחות.")),
    card("החלטות אחרונות", "כולל מה נדחה ולמה (בלי \"כבר מוחזק\")", table(["תאריך", "חשבון", "ETF", "", "פרטים"], decRows, "עוד אין החלטות.")),
    card("עסקאות שנסגרו", null, table(["תאריך", "חשבון", "ETF", "סיבה", "רווח/הפסד"], trRows, "עוד לא נסגרו עסקאות.")),
    card("בריאות הניסוי", `עודכן ${ago(r.created_at)}`,
      h("ul", { class: "clean" },
        h("li", null, "נר אחרון: ", dateHe(R.latest_bar)),
        h("li", null, "שעון השרת: ", hl.clock_ok ? h("span", { class: "badge ok" }, "תקין") : h("span", { class: "badge bad" }, "לא תקין"),
          hl.clock_skew_s == null ? "" : h("span", { class: "faint" }, " סטייה ", ltr(hl.clock_skew_s.toFixed(1) + "s"))),
        h("li", null, "החלטות שנחתמו: ", ltr(String(R.intents_total))),
        (hl.fetch_problems || []).length ? h("li", { class: "err" }, "בעיות הורדה: " + hl.fetch_problems.join(", ")) : null),
      missed.length ? h("div", null, h("div", { class: "sub" }, "ימים שהוחמצו (לא נכנסו עסקאות חדשות):"), h("ul", { class: "clean" }, missed.map((m) => h("li", null, m)))) : null),
    card("כללי הניסוי", P.version,
      h("ul", { class: "clean" },
        h("li", null, "7 קרנות: SPY, QQQ, IWM, EFA, EEM, IEF, TLT. החלטה אחרי הסגירה, ביצוע בפתיחה הבאה."),
        h("li", null, "מגמה: מעל ממוצע 200 יום ותשואת חצי שנה חיובית. פריצה: מעל ממוצע 200 ומעל שיא 55 יום."),
        h("li", null, "עד 5 פוזיציות, עד 20% לקרן, עד 60% מושקע, סיכון 0.25% מהחשבון לעסקה, סטופ 2×ATR."),
        h("li", null, "חלקי מניות (0.001) — כך שגם QQQ יקרה אפשרית. עצירת כניסות בירידה של 8% מהשיא.")))));
};

// ---- הזדמנויות (Opportunity Center) ----
const DOMAIN = { stocks: "מניות", crypto: "קריפטו", polymarket: "Polymarket" };
const ACT_CLS = { "מועמדת לפי אסטרטגיה": "ok", "לבדיקה": "info", "במעקב": "", "להמתין": "wait" };
const usd2 = (v) => v == null ? "—" : "$" + Number(v).toLocaleString("en", { maximumFractionDigits: v < 10 ? 2 : 2, minimumFractionDigits: 2 });
const when = (v) => { if (!v) return "—"; const d = new Date(v); return isNaN(d) ? String(v) : d.toLocaleString("he-IL", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" }); };
function budgetBar(st) {
  if (!st) return null; const L = st.limits;
  return h("div", { class: "budget-bar" },
    h("span", null, "תקציב ", h("strong", null, ltr("$" + L.budget.toLocaleString())), ` · פרופיל ${L.profile_label} · עד `, ltr(usd2(L.max_position_usd)), " לעסקה · עד ",
      ltr(usd2(L.risk_usd)), " הפסד מתוכנן לעסקה"),
    h("a", { href: "#/settings" }, "שנה"));
}
const oppPrice = (r) => r.price == null ? "—" : r.price_unit === "prob" ? `${r.top_name ? r.top_name + ": " : ""}${Math.round(r.price * 100)}%` : usd2(r.price);
PAGES.opps = async (view) => {
  const [d, st] = await Promise.all([api("/api/opportunities"), api("/api/settings").catch(() => null)]);
  if (current !== "opps") return;
  const L = st ? st.limits : null;
  const F = { domain: "all", action: "all", risk: "all", sort: "action" };
  const list = h("div", { class: "opps" });
  const perTrade = (r) => !L ? "—" : r.domain === "polymarket" ? usd2(L.risk_usd) : usd2(L.max_position_usd);
  const cell = (k, v, cls) => h("div", { class: "c " + (cls || "") }, h("div", { class: "k" }, k), h("div", { class: "v" }, v));
  const draw = () => {
    let rows = d.rows.filter((r) => (F.domain === "all" || r.domain === F.domain) && (F.action === "all" || r.action === F.action)
      && (F.risk === "all" || r.risk === F.risk));
    if (F.sort === "updated") rows = [...rows].sort((a, b) => new Date(b.updated) - new Date(a.updated));
    if (F.sort === "risk") { const R = { "נמוך": 0, "בינוני": 1, "גבוה": 2 }; rows = [...rows].sort((a, b) => (R[a.risk] ?? 3) - (R[b.risk] ?? 3)); }
    list.replaceChildren(...(rows.length ? rows.map((r) => h("a", { class: "card opp", href: `#/plan/${r.kind}/${r.id}` },
      h("div", { class: "row-between" }, h("div", null, h("strong", { class: "asset", dir: "auto" }, r.asset), " ", h("span", { class: "badge" }, DOMAIN[r.domain])),
        h("span", { class: "badge " + (ACT_CLS[r.action] || "") }, r.action)),
      h("div", { class: "faint", dir: "auto" }, r.strategy, r.warning ? " · אזהרה" : ""),
      h("div", { class: "grid" },
        cell("מחיר אחרון", h("span", null, ltr(oppPrice(r)), h("div", { class: "faint" }, when(r.price_at)))),
        cell("כניסה", r.entry),
        cell("סכום לעסקה", perTrade(r)),
        cell("יעד", r.target == null ? "טרם נקבע" : usd2(r.target), r.target == null ? "muted" : ""),
        cell("יציאה בהפסד", r.stop == null ? "טרם נקבע" : usd2(r.stop), r.stop == null ? "muted" : ""),
        cell("יחס סיכוי-סיכון", "אין חישוב אמין", "muted"),
        cell("אופק זמן", r.horizon),
        cell("מצב מחקר", stateBadge(r.research)),
        cell("סיכון (תנודה)", r.risk || "—")),
      h("div", { class: "more-link" }, "לכרטיס העסקה המלא"))) : [empty("אין כאן כלום לפי הסינון הזה.")]));
  };
  const chips = (key, opts) => h("div", { class: "seg", role: "group" }, opts.map(([v, l]) => h("button", { type: "button", "aria-pressed": String(F[key] === v),
    onclick: (e) => { F[key] = v; e.currentTarget.parentNode.querySelectorAll("button").forEach((b) => b.setAttribute("aria-pressed", String(b === e.currentTarget))); draw(); } }, l)));
  const sel = (key, opts, label) => h("select", { "aria-label": label, onchange: (e) => { F[key] = e.target.value; draw(); } }, opts.map(([v, l]) => h("option", { value: v }, l)));
  const proven = d.rows.filter((r) => r.action === "מועמדת לפי אסטרטגיה").length;
  draw();
  view.replaceChildren(h("div", { class: "stack" },
    h("section", { class: "verdict" + (proven ? " go" : "") }, h("h2", null, proven ? `${proven} מבוססות על שיטה שהוכחה.` : "אין כאן שום דבר שהוכח."),
      h("p", null, `${d.rows.length} דברים שהמערכת עוקבת אחריהם. ${proven ? "" : "כולם במצב בדיקה או המתנה — לכן אין מחיר כניסה, יעד או עצירה מומלצים."}`)),
    budgetBar(st),
    h("div", { class: "toolbar" }, chips("domain", [["all", "הכל"], ["stocks", "מניות"], ["crypto", "קריפטו"], ["polymarket", "Polymarket"]]),
      sel("action", [["all", "כל הפעולות"], ["מועמדת לפי אסטרטגיה", "מועמדת לפי אסטרטגיה"], ["לבדיקה", "לבדיקה"], ["במעקב", "במעקב"], ["להמתין", "להמתין"]], "פעולה"),
      sel("risk", [["all", "כל רמות הסיכון"], ["נמוך", "סיכון נמוך"], ["בינוני", "סיכון בינוני"], ["גבוה", "סיכון גבוה"]], "סיכון"),
      sel("sort", [["action", "מיון: הכי רלוונטי"], ["updated", "מיון: עדכון אחרון"], ["risk", "מיון: סיכון נמוך קודם"]], "מיון")),
    list,
    h("p", { class: "faint" }, "\"טרם נקבע\" אומר שאין שיטה מאומתת שמגדירה את המספר הזה. המערכת לא ממציאה מחיר כניסה, יעד או עצירה.")));
};

PAGES.plan = async (view, arg) => {
  const [kind, id] = arg || [];
  const p = await api(`/api/plan/${kind}/${id}`);
  if (current !== "plan") return;
  const r = p.row;
  const parts = [h("header", { class: "page-head" }, h("h1", { dir: "auto" }, r.asset),
      h("p", null, h("span", { class: "badge" }, DOMAIN[r.domain]), " ", h("span", { class: "badge " + (ACT_CLS[r.action] || "") }, r.action), " ", stateBadge(r.research))),
    budgetBar({ limits: p.limits })];
  p.sections.forEach((x) => parts.push(card(x.title, null, h("ul", { class: "clean plain" }, x.lines.map((l) => h("li", { dir: "auto" }, l))))));
  if (p.polymarket) {
    const pm = p.polymarket;
    parts.splice(3, 0, card("החשבון הפשוט", `לכל אחת מהתשובות: שמים ${usd2(pm.stake)} על "כן"`,
      h("div", { class: "scroll-x" }, h("table", { class: "t" },
        h("thead", null, h("tr", null, ["תשובה", "מחיר", "מקבלים אם כן", "רווח אם כן", "הפסד אם לא", "מקור המחיר"].map((x) => h("th", null, x)))),
        h("tbody", null, pm.outcomes.map((o) => h("tr", null, h("td", { dir: "auto" }, o.name),
          h("td", null, o.ok ? ltr((o.price * 100).toFixed(1) + "¢") : "—"),
          h("td", null, o.ok ? ltr(usd2(o.payout_if_yes)) : "—"), h("td", null, o.ok ? h("span", { class: "up" }, ltr(usd2(o.profit_if_yes))) : "—"),
          h("td", null, o.ok ? h("span", { class: "down" }, ltr(usd2(o.loss_if_no))) : "—"),
          h("td", { class: "faint" }, !o.ok ? "אין מחיר" : o.executable ? "ספר פקודות עכשיו" : "מחיר תצוגה")))))),
      pm.rules ? h("details", { class: "more" }, h("summary", null, "כללי ההכרעה (מ-Polymarket)"), h("p", { dir: "auto", class: "rules" }, pm.rules),
        pm.resolution_source ? h("p", { class: "faint", dir: "auto" }, "מקור ההכרעה: ", pm.resolution_source) : null) : null));
  }
  if (p.sizing && r.price) {
    const entry = h("input", { type: "number", step: "any", value: r.price, "aria-label": "מחיר כניסה" });
    const stop = h("input", { type: "number", step: "any", placeholder: "למשל " + (r.price * 0.95).toFixed(2), "aria-label": "מחיר עצירה" });
    const outBox = h("ul", { class: "clean plain" });
    const calc = async (ev) => { ev.preventDefault();
      const q = new URLSearchParams({ price: entry.value }); if (stop.value) q.set("stop", stop.value);
      try { const z = await api("/api/size?" + q); outBox.replaceChildren(...z.notes.map((n) => h("li", null, n)),
        z.units != null ? h("li", null, h("strong", null, `יחידות: ${Number(z.units).toLocaleString("en", { maximumFractionDigits: 4 })} · נשאר בצד: ${usd2(z.cash_left)}`)) : null); }
      catch (e) { outBox.replaceChildren(h("li", { class: "down" }, e.message)); } };
    parts.push(card("מחשבון: אם הייתי קובע עצירה משלי", "השיטה עצמה לא מגדירה עצירה. כאן אפשר לבדוק כמה יחידות מתאימות לתקציב שלך עם עצירה שאתה בוחר.",
      h("form", { class: "calc", onsubmit: calc }, h("label", null, "מחיר כניסה ($)", entry), h("label", null, "מחיר עצירה ($)", stop),
        h("button", { class: "btn primary", type: "submit" }, "חשב")), outBox));
  }
  parts.push(h("p", { class: "faint" }, "מחקר וסימולציה בלבד. אין כאן ייעוץ השקעות ואין ביצוע עסקאות."));
  view.replaceChildren(h("div", { class: "stack" }, ...parts.filter(Boolean)));
};

PAGES.settings = async (view) => {
  const st = await api("/api/settings");
  if (current !== "settings") return;
  const S = st.settings, P = st.profiles;
  const budget = h("input", { type: "number", min: 1, step: "any", value: S.budget, "aria-label": "תקציב בדולרים" });
  const feeMin = h("input", { type: "number", min: 0, step: "any", value: S.fee_min ?? "", placeholder: "לא ידוע", "aria-label": "עמלת מינימום לפקודה" });
  const feePct = h("input", { type: "number", min: 0, step: "any", value: S.fee_pct || 0, "aria-label": "עמלה באחוזים" });
  const frac = h("input", { type: "checkbox", checked: S.fractional });
  let prof = S.profile;
  const profBox = h("div", { class: "profiles" });
  const drawProf = () => { const b = Number(budget.value) || S.budget;
    profBox.replaceChildren(...Object.entries(P).map(([k, x]) => h("label", { class: "prof" + (k === prof ? " on" : "") },
      h("input", { type: "radio", name: "prof", checked: k === prof, onchange: () => { prof = k; drawProf(); } }),
      h("div", null, h("strong", null, x.label),
        h("div", null, `מפסיד לכל היותר ${(x.risk_pct * 100).toFixed(1)}% מהתקציב בעסקה (${usd2(b * x.risk_pct)}).`),
        h("div", null, `עד ${Math.round(x.max_position_pct * 100)}% מהתקציב בעסקה אחת (${usd2(b * x.max_position_pct)}). עד ${x.max_open} עסקאות פתוחות.`))))); };
  budget.addEventListener("input", drawProf);
  drawProf();
  const msg = h("p", { class: "form-msg", role: "status" });
  const save = async (ev) => { ev.preventDefault();
    try { await apiSend("PUT", "/api/settings", { budget: Number(budget.value), profile: prof, fee_min: feeMin.value === "" ? null : Number(feeMin.value),
      fee_pct: Number(feePct.value || 0), fractional: frac.checked }); msg.className = "form-msg"; msg.textContent = "נשמר. כל החישובים במערכת מתעדכנים לפי זה."; }
    catch (e) { msg.className = "form-msg err"; msg.textContent = e.message; } };
  view.replaceChildren(h("form", { class: "stack settings", onsubmit: save },
    card("כמה כסף יש לניסוי", "רק כסף שאתה מוכן להפסיד. זה משמש לחישוב בלבד — שום דבר לא נקנה.", h("label", null, "תקציב ($)", budget)),
    card("כמה סיכון", "הפסד מתוכנן = כמה תפסיד אם תצא בעצירה שקבעת. בהימור כן/לא זה כל הסכום.", profBox),
    card("עמלת הברוקר", "בחשבון קטן העמלה קובעת אם עסקה בכלל משתלמת. את הנתון תמצא בתעריפון של הברוקר (למשל ONE ZERO).",
      h("label", null, "עמלת מינימום לפקודה ($)", feeMin), h("label", null, "או עמלה באחוזים מהסכום (%)", feePct),
      h("label", { class: "check" }, frac, "הברוקר מאפשר לקנות שברי מניות")),
    h("button", { class: "btn primary", type: "submit" }, "שמור"), msg));
};

// ---- שוק חי (Live monitor) ----
let liveTimer = null;
const secs = (v) => v == null ? "—" : v < 1 ? `${(v * 1000).toFixed(0)} מ״ש` : v < 120 ? `${v.toFixed(1)} שנ׳` : v < 7200 ? `${Math.round(v / 60)} דק׳` : `${Math.round(v / 3600)} שע׳`;
const FEED_HE = { polymarket: "Polymarket (WebSocket)", coinbase: "קריפטו — Coinbase (WebSocket)", yahoo: "מניות — Yahoo (ציטוט כל דקה)" };
const LVL = { market: ["wait", "שוק"], risk: ["bad", "סיכון"], strategy: ["ok", "אסטרטגיה"], data: ["", "נתונים"] };
PAGES.live = async (view) => {
  if (liveTimer) { clearInterval(liveTimer); liveTimer = null; }
  const body = h("div", { class: "stack" }, skel(4));
  view.replaceChildren(body);
  const draw = async () => {
    const [st, al] = await Promise.all([api("/api/live/status"), api("/api/live/alerts?limit=30").catch(() => [])]);
    if (current !== "live") return;
    if (!st.running) { body.replaceChildren(card("המעקב החי לא רץ", "השירות live עוד לא הופעל בשרת, או שנפל.",
      h("pre", { class: "code", dir: "ltr" }, "sudo docker compose up -d live"))); return; }
    const L = st.latency || {};
    const feeds = Object.entries(st.feeds).map(([k, f]) => h("div", { class: "reason" },
      h("div", { class: "k" }, h("span", { class: "dot " + (f.connected ? "ok" : "bad") }), " ", FEED_HE[k] || k),
      h("div", { class: "v" }, f.connected ? "מחובר" : "מנותק"),
      h("div", { class: "d" }, `${f.subscribed} נכסים · הודעה אחרונה ${f.last_msg_at ? ago(f.last_msg_at) : "אף פעם"}`),
      h("div", { class: "d" }, `עיכוב מהמקור: ממוצע ${secs(f.lag_mean)} · P95 ${secs(f.lag_p95)}`),
      f.note ? h("div", { class: "d" }, f.note) : null, f.error ? h("div", { class: "d down" }, f.error) : null));
    const prices = h("div", { class: "scroll-x" }, h("table", { class: "t" },
      h("thead", null, h("tr", null, ["נכס", "מחיר", "זמן במקור", "גיל", "מצב", "למה במעקב"].map((x) => h("th", null, x)))),
      h("tbody", null, st.prices.map((r) => h("tr", null, h("td", { dir: "auto" }, r.name),
        h("td", null, ltr(r.cls === "polymarket" ? Math.round(r.price * 100) + "%" : usd2(r.price))),
        h("td", null, r.src_ts ? ltr(new Date(r.src_ts).toLocaleTimeString("he-IL")) : "—"),
        h("td", null, secs(r.age_s)),
        h("td", null, h("span", { class: "badge " + (r.fresh ? "ok" : "wait") }, r.fresh ? "חי" : "לא עדכני")),
        h("td", { class: "faint" }, (r.roles || []).map((x) => ({ follow: "אתה עוקב", position: "פוזיציה מדומה", benchmark: "מדד ייחוס" }[x] || x)).join(", ")))))));
    const alerts = al.length ? h("ul", { class: "hist" }, al.map((a) => { const [c, l] = LVL[a.level] || ["", a.level];
      return h("li", { class: "card" }, h("div", { class: "row-between" }, h("span", { class: "badge " + c }, l), h("span", { class: "faint" }, when(a.detected_at))),
        h("pre", { class: "alert-text", dir: "auto" }, a.text),
        h("div", { class: "faint" }, a.status === "sent" ? `נשלח לטלגרם · ${secs((new Date(a.sent_at) - new Date(a.src_ts)) / 1000)} מזמן הנתון` :
          a.status === "suppressed" ? "לא נשלח: הגעת לתקרת ההתראות היומית" : a.status === "failed" ? "השליחה לטלגרם נכשלה" : "ממתין לשליחה")); }))
      : empty("עוד לא היו התראות. זה טוב: לא זוהה אירוע חריג.");
    body.replaceChildren(
      h("div", { class: "reasons" }, feeds),
      card("כמה מהר", "מזמן הנתון אצל המקור, דרך הזיהוי, ועד ההודעה בטלגרם (7 ימים אחרונים)",
        h("ul", { class: "clean plain" },
          h("li", null, `זיהוי: ממוצע ${secs(L.detect?.mean)}, P95 ${secs(L.detect?.p95)} (${L.detect?.n || 0} התראות)`),
          h("li", null, `עד הטלגרם: ממוצע ${secs(L.alerts?.mean)}, P95 ${secs(L.alerts?.p95)} · נכשלו ${L.failed || 0} · ממתינות ${L.pending || 0}`),
          h("li", { class: "faint" }, "יעד: עד 10 שניות בקריפטו וב-Polymarket. במניות עד כ-2 דקות, כי הציטוט מגיע פעם בדקה ולא מפיד מורשה."))),
      card("מחירים", "\"חי\" רק כשהמחיר טרי. מחיר ישן מסומן \"לא עדכני\" ולא משמש להחלטה.", prices),
      card("התראות אחרונות", "שוק = אירוע בשוק · סיכון = נוגע לפוזיציה מדומה · נתונים = זרם תקוע או מנותק", alerts),
      h("p", { class: "faint" }, "התראה על ירידה היא עובדה בשוק, לא המלצה למכור. מתעדכן כל 10 שניות."));
  };
  try { await draw(); } catch (e) { body.replaceChildren(fail(e)); }
  liveTimer = setInterval(() => { if (current !== "live") { clearInterval(liveTimer); liveTimer = null; return; } draw().catch(() => {}); }, 10000);
};

// ---- המעקב שלי (Follow) ----
async function apiSend(method, path, body) {
  const r = await fetch(path, { method, headers: { "Content-Type": "application/json", Accept: "application/json" }, body: body ? JSON.stringify(body) : undefined });
  const d = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(d.detail || `שגיאה ${r.status}`);
  return d;
}
const followPrice = (f, x) => f.kind === "polymarket" ? Math.round(x.price * 100) + "%" : "$" + Number(x.price).toLocaleString("en", { maximumFractionDigits: x.price < 10 ? 4 : 2 });
function followChange(f, x) {
  if (x.since_start == null || Math.abs(x.since_start) < 1e-9) return h("span", { class: "faint" }, "לא זז מאז שהתחלת");
  if (f.kind === "polymarket") { const d = Math.round(x.since_start * 100);
    return d === 0 ? h("span", { class: "faint" }, "כמעט לא זז") : h("span", { class: d > 0 ? "up" : "down" }, `${d > 0 ? "עלה" : "ירד"} ${Math.abs(d)} נקודות מאז שהתחלת`); }
  const first = x.price - x.since_start, pc = first ? x.since_start / first * 100 : 0;
  return h("span", { class: pc > 0 ? "up" : "down" }, `${pc > 0 ? "עלה" : "ירד"} ${Math.abs(pc).toFixed(1)}% מאז שהתחלת`);
}
function followBox(onChange) {
  const q = h("input", { type: "text", placeholder: "קישור או סימול, למשל AMD", "aria-label": "מה לעקוב", dir: "auto", autocomplete: "off" });
  const msg = h("p", { class: "form-msg", role: "status" });
  const btn = h("button", { class: "btn primary", type: "submit" }, "עקוב");
  const go = async (ev) => {
    ev.preventDefault();
    const text = q.value.trim(); if (!text) return;
    btn.disabled = true; msg.className = "form-msg"; msg.textContent = "בודק…";
    try { const f = await apiSend("POST", "/api/follows", { text }); q.value = ""; msg.textContent = `נוסף: ${f.label}. תקבל הודעה בטלגרם כשהמחיר זז.`; onChange && onChange(); }
    catch (e) { msg.className = "form-msg err"; msg.textContent = e.message; }
    finally { btn.disabled = false; }
  };
  return h("div", null, h("form", { class: "quick", onsubmit: go }, q, btn), msg);
}
function followList(list, onChange, compact) {
  if (!list.length) return empty("עוד לא הוספת כלום. הדבק למעלה קישור לשוק ב-Polymarket, או כתוב סימול של מניה או מטבע.");
  return h("ul", { class: "follows" }, list.map((f) => {
    const items = compact ? f.state.slice(0, 3) : f.state;
    const stop = h("button", { class: "btn small", type: "button", onclick: async () => { stop.disabled = true;
      try { await apiSend("DELETE", "/api/follows/" + f.id); onChange && onChange(); } catch (e) { stop.disabled = false; alert(e.message); } } }, "הפסק לעקוב");
    return h("li", null,
      h("div", { class: "row-between" }, h("a", { class: "flabel", href: f.url || "#/follow", target: "_blank", rel: "noopener noreferrer", dir: "auto" }, f.label), compact ? null : stop),
      h("ul", { class: "fitems" }, items.map((x) => h("li", null, h("span", { class: "n", dir: "auto" }, x.name),
        h("strong", null, ltr(followPrice(f, x))), followChange(f, x)))),
      compact && f.state.length > 3 ? h("div", { class: "faint" }, `ועוד ${f.state.length - 3}`) : null,
      f.last_error ? h("div", { class: "faint" }, "הבדיקה האחרונה לא הצליחה, ננסה שוב בעוד רבע שעה.") : null,
      compact ? null : h("div", { class: "faint" }, `נוסף ${dateHe(f.created_at)}` + (f.alerted_at ? ` · הודעה אחרונה ${ago(f.alerted_at)}` : " · עוד לא נשלחה הודעה")));
  }));
}
PAGES.follow = async (view) => {
  const box = h("div", null, skel(2));
  const load = async () => { const l = await api("/api/follows").catch(() => null);
    box.replaceChildren(l ? followList(l, load) : empty("לא הצלחתי לקרוא את רשימת המעקב מהשרת.")); };
  view.replaceChildren(h("div", { class: "stack" },
    card("עקוב אחרי משהו", "עובד על המחשב ועל הטלפון. ההודעה מגיעה ל\"הודעות שמורות\" שלך בטלגרם.", followBox(load)),
    box,
    h("p", { class: "faint" }, "שוק ב-Polymarket: הודעה כשתוצאה זזה 5 נקודות או יותר. מניה או מטבע: כשהמחיר זז 5% או יותר. המעקב רק מדווח על מחירים. הוא לא קונה ולא ממליץ.")));
  load();
};

// ---- היום (Today) ----
const ENGINE = { stocks: "מניות", crypto: "קריפטו", polymarket: "Polymarket", sources: "קבוצות טיפים" };
// the five research states, shared by every screen (see app/pmintel/performance.py)
const STATE = {
  confirmed: { cls: "ok", he: "אומתה קדימה", en: "Forward Confirmed", d: "עברה קריטריונים שנקבעו מראש על איתותים שנרשמו לפני שהמחיר זז. עדיין לא הבטחה לרווח." },
  validating: { cls: "info", he: "בבדיקה קדימה", en: "Forward Validation", d: "נבדקת עכשיו בכסף מדומה. אין עדיין מסקנה." },
  candidate: { cls: "wait", he: "מועמדת מהעבר", en: "Historical Candidate", d: "עברה בדיקה על נתוני עבר בלבד. זו השערה, לא הוכחה." },
  insufficient: { cls: "", he: "אין מספיק נתונים", en: "Insufficient Data", d: "מעט מדי תצפיות כדי להגיד משהו." },
  failed: { cls: "bad", he: "נכשלה", en: "Not Supported / Failed", d: "אין יתרון אחרי עלויות, או שהבדיקה קדימה נכשלה." },
};
const STATUS_RANK = { confirmed: 0, validating: 1, candidate: 2, insufficient: 3, failed: 4 };
const stateBadge = (st) => { const x = STATE[st] || STATE.insufficient; return h("span", { class: "badge " + x.cls, title: x.en }, x.he); };
// one plain sentence per engine, from the scoreboard only (no guessing)
function engineThought(engine, entries) {
  const es = entries.filter((e) => e.engine === engine);
  const by = (st) => es.filter((e) => e.status === st);
  const BM = { stocks: "המדד", crypto: "החזקת ביטקוין", polymarket: "מחיר השוק" }[engine];
  if (by("confirmed").length) return ["ok", `${by("confirmed").map((e) => e.label).join(", ")}: אומתה קדימה. שווה לדבר על סכום קטן, אחרי בירור עמלות ומס.`];
  if (engine === "sources") {
    if (!es.length) return ["", "עוד לא הגיעו טיפים למדידה."];
    const meas = es.filter((e) => e.status !== "insufficient");
    if (!meas.length) return ["", `${es.length} קבוצות במעקב. אף אחת עוד לא צברה 30 טיפים, אז אין עדיין מה לשפוט.`];
    return [by("failed").length === meas.length ? "bad" : "wait", `${meas.length} קבוצות נמדדו: ${by("failed").length} מפסידות למדד אחרי עמלות. אל תקנה לפי טיפים.`];
  }
  const hist = es.filter((e) => e.kind === "historical");
  if (!hist.length) return ["", engine === "polymarket" ? "המחקר על שווקים שנגמרו עוד לא רץ." : "עוד לא נבדק על נתונים אמיתיים. ממתין להרצה הראשונה בשרת."];
  const cand = by("candidate").filter((e) => e.kind === "historical");
  if (cand.length) {
    const fw = es.filter((e) => e.kind === "forward" && cand.some((c) => c.label === e.label));
    return ["wait", `${cand.map((e) => e.label).join(", ")}: מועמדת מהעבר, כלומר השערה. בבדיקה קדימה: ${Math.max(0, ...fw.map((e) => e.n || 0))} מתוך 50 עסקאות.`];
  }
  const failed = hist.filter((e) => e.status === "failed").length, ins = hist.filter((e) => e.status === "insufficient").length;
  return ["bad", `${hist.length} שיטות נבדקו על העבר: ${failed} נכשלו${ins ? `, ל-${ins} אין מספיק נתונים` : ""}. אף אחת לא מנצחת את ${BM} אחרי עמלות.`];
}
// how far the most advanced strategy got: 0 = nothing passed, 1 = historical candidate, 2 = forward confirmed
function ladderStage(entries) {
  if (entries.some((e) => e.status === "confirmed" && e.engine !== "sources")) return 2;
  if (entries.some((e) => e.status === "candidate")) return 1;
  return 0;
}
let homeTimer = null;
function agentsMini(d) {
  if (!d) return empty("לא הצלחתי לקרוא את מצב הסוכנים.");
  const ORDER = { working: 0, error: 1, late: 2, ok: 3, quiet: 4, idle: 5 };
  const DOT = { working: "info", ok: "ok", late: "warn", error: "bad", idle: "", quiet: "" };
  const NAME = Object.fromEntries(d.agents.map((a) => [a.id, a.name]));
  const list = h("ul", { class: "agents-mini" }, [...d.agents].sort((a, b) => ORDER[a.state] - ORDER[b.state]).map((a) =>
    h("li", { class: a.state }, h("span", { class: "dot " + (DOT[a.state] || ""), "aria-hidden": "true" }),
      h("a", { href: a.href || "#/agents", class: "who" }, a.name),
      h("span", { class: "what" }, a.state === "working" ? "עובד עכשיו · " + a.line : a.line),
      h("span", { class: "when" }, a.state === "idle" ? "עוד לא התחיל" : a.ago || ""))));
  const ST = { ok: "סיים", partial: "סיים חלקית", error: "נכשל", interrupted: "נקטע", running: "התחיל" };
  const feed = d.feed.slice(0, 6).map((r) => h("li", null, h("span", { class: "t" }, ltr(new Date(r.started_at).toLocaleTimeString("he-IL", { hour: "2-digit", minute: "2-digit" }))),
    ` ${NAME[r.agent] || "המערכת"} ${ST[r.status] || r.status}: ${JOBS[r.job] || r.job}`,
    r.items != null && r.status !== "running" ? h("span", { class: "faint" }, ` · ${Number(r.items).toLocaleString()} פריטים`) : null));
  const cnt = (f) => d.agents.filter(f).length;
  const sum = h("p", { class: "agents-sum" }, `${cnt((a) => a.state === "working")} עובדים עכשיו · ${cnt((a) => a.state === "ok")} הצליחו בסבב האחרון · `,
    h("span", { class: cnt((a) => a.state === "error" || a.state === "late") ? "down" : "" }, `${cnt((a) => a.state === "error" || a.state === "late")} נכשלו או מאחרים`),
    ` · ${cnt((a) => a.state === "idle" || a.state === "quiet")} מחכים למידע`);
  return h("div", null, sum, list, feed.length ? h("div", null, h("h4", { class: "mini-title" }, "מה קרה לאחרונה"), h("ul", { class: "feed-mini" }, feed)) : null);
}
function paperPicks(stk, cry, labs, entries) {
  const label = (m, k) => ((labs[m] || {}).strategies || {})[k]?.label || k;
  const sideOf = (m, k) => ((labs[m] || {}).strategies || {})[k]?.side;
  const statusOf = (m, k) => { const pre = m === "crypto" ? "crypto:" : "";
    const fw = entries.find((e) => e.id === `fwd:${pre}${k}`), hi = entries.find((e) => e.id === `lab:${pre}${k}`);
    return fw && fw.status === "confirmed" ? fw : hi || fw || null; };
  const rows = [...(stk?.recent || []).map((r) => ({ ...r, m: "stocks" })), ...(cry?.recent || []).map((r) => ({ ...r, m: "crypto" }))]
    .filter((r) => r.status !== "closed").slice(0, 8);
  if (!rows.length) return empty("היום לא נמצא כלום. איתותים נוצרים כל יום אחרי סגירת המסחר (קריפטו: אחרי חצות).");
  const DAY = { stocks: "ימי מסחר", crypto: "ימים" };
  const why = (e) => !e ? "השיטה עוד לא נבדקה על העבר."
    : e.status === "confirmed" ? "השיטה אומתה גם קדימה. שווה לעקוב; כסף אמיתי רק אחרי שיחה ובירור."
    : e.status === "candidate" ? "השיטה רק מועמדת מהעבר. עוקבים כדי לבדוק אותה, לא כדי לקנות."
    : e.status === "insufficient" ? "אין מספיק נתונים על השיטה הזו."
    : "השיטה נכשלה בבדיקה על העבר. עוקבים רק כדי למדוד.";
  return h("ul", { class: "picks" }, rows.map((r) => { const e = statusOf(r.m, r.strategy);
    return h("li", null,
      h("a", { class: "sym", href: r.m === "stocks" ? "#/check/" + r.symbol : "#/crypto" }, ltr(r.symbol.replace("-USD", ""))),
      h("div", null, h("div", null, label(r.m, r.strategy), " ", sideOf(r.m, r.strategy) === -1 ? h("span", { class: "badge wait" }, "אזהרה: לא לקנות") : null),
        h("div", { class: "faint" }, r.status === "open" ? `נכנס בכסף מדומה ב-${dateHe(r.entry_day)} · יוצא אחרי ${r.hold} ${DAY[r.m]}` : `איתות מ-${dateHe(r.signal_day)} · נכנס בפתיחה הבאה`),
        h("div", { class: "why" }, why(e))),
      e ? stateBadge(e.status) : stateBadge("insufficient")); }));
}
PAGES.home = async (view) => {
  if (homeTimer) { clearInterval(homeTimer); homeTimer = null; }
  const [perf, ag, fl] = await Promise.all(["/api/performance", "/api/agents", "/api/follows"].map((u) => api(u).catch(() => null)));
  if (current !== "home") return;
  if (!perf && !ag) throw new Error("השרת לא מחזיר נתונים כרגע. נסה לרענן בעוד דקה.");
  const entries = perf ? perf.entries : [];
  const stage = ladderStage(entries);
  const today = new Date().toLocaleDateString("he-IL", { weekday: "long", day: "numeric", month: "long" });
  const tested = entries.filter((e) => e.engine !== "sources").length;
  const VERDICT = [
    ["", "היום לא קונים כלום.", `המערכת בדקה ${tested} שיטות ועוד לא מצאה אחת שמרוויחה יותר מהמדד. כשתמצא, תקבל הודעה.`],
    ["wait", "היום לא קונים כלום.", "יש שיטה אחת שנראתה טוב על העבר. עכשיו בודקים אותה כמה שבועות בכסף מדומה לפני שסומכים עליה."],
    ["go", "יש שיטה שעברה את כל הבדיקות.", "שווה לדבר על סכום קטן. שום דבר לא נקנה לבד."]][stage];
  const flBox = h("div", null);
  const loadFl = async () => { const l = await api("/api/follows").catch(() => null); if (l) flBox.replaceChildren(followList(l, loadFl, true)); };
  flBox.replaceChildren(fl ? followList(fl, loadFl, true) : empty("לא הצלחתי לקרוא את רשימת המעקב."));
  const sysLine = (d) => { if (!d) return empty("לא הצלחתי לקרוא את מצב המערכת.");
    const bad = d.agents.filter((a) => a.state === "error" || a.state === "late");
    const work = d.agents.filter((a) => a.state === "working");
    return h("div", null,
      h("p", { class: "sys " + (bad.length ? "down" : "up") }, h("span", { class: "dot " + (bad.length ? "bad" : "ok"), "aria-hidden": "true" }),
        bad.length ? ` ${bad.length} חלקים לא עובדים: ${bad.map((a) => a.name).join(", ")}` : " הכל עובד."),
      work.length ? h("p", { class: "faint" }, "עכשיו: " + work.map((a) => `${a.name} (${a.role})`).join(", ")) : null,
      h("a", { href: "#/live" }, "שוק חי"), " · ", h("a", { href: "#/agents" }, "מה כל חלק עושה")); };
  const sysBox = h("div", null, sysLine(ag));
  view.replaceChildren(h("div", { class: "stack" },
    h("section", { class: "verdict " + VERDICT[0] }, h("div", { class: "when" }, today), h("h2", null, VERDICT[1]), h("p", null, VERDICT[2]),
      h("p", null, h("a", { href: "#/opps" }, "מה המערכת מצאה לבדוק"), " · ", h("a", { href: "#/performance" }, "מה נבדק ומה יצא"))),
    card("המעקב שלך", "הדבק קישור או כתוב סימול. תקבל הודעה בטלגרם כשהמחיר זז.", followBox(loadFl), flBox,
      h("a", { href: "#/follow" }, "לכל המעקב")),
    card("המערכת", null, sysBox),
    h("p", { class: "faint" }, "אין כאן ייעוץ השקעות ואין מסחר בכסף אמיתי.")));
  homeTimer = setInterval(async () => {
    if (current !== "home") { clearInterval(homeTimer); homeTimer = null; return; }
    const d = await api("/api/agents").catch(() => null);
    if (d && current === "home") sysBox.replaceChildren(sysLine(d));
  }, 30000);
};

// ---- Polymarket explainer ----
PAGES.pm = async (view) => {
  const [ov, res] = await Promise.all([api("/api/overview").catch(() => null), api("/api/research/latest").catch(() => null)]);
  if (current !== "pm") return;
  const st = ov?.stats || {};
  const wf = res ? Object.values(res.result || {})[0]?.walk_forward : null;
  const sec = (title, ...body) => h("section", { class: "card" }, h("h3", null, title), ...body);
  view.replaceChildren(h("div", { class: "stack" },
    h("div", { class: "explain" },
      sec("מה זה", h("p", null, "בורסה שבה סוחרים על תוצאות של אירועים אמיתיים: בחירות, החלטות ריבית, ספורט, מזג אוויר. כל שאלה היא \"שוק\", ולכל תשובה יש מחיר."),
        h("p", null, "כרגע יש ", ltr((st.tradable_markets || 0).toLocaleString()), " שווקים פתוחים שאפשר לסחור בהם, וביממה האחרונה עברו ", ltr(usd(st.volume_24h)), ".")),
      sec("מחיר = הסתברות", h("p", null, "מניית \"כן\" עולה בין 0 ל-1 דולר. כשהיא עולה 23 סנט, המשתתפים מעריכים 23% שזה יקרה. כשהשוק מוכרע, התשובה הנכונה שווה דולר והשגויה אפס."),
        h("div", { class: "example" }, h("strong", null, "דוגמה: "), "קנית 100 מניות \"כן\" ב-23 סנט = 23$. אם זה קרה — מקבלים 100$ (רווח 77$). אם לא — 0$ (הפסד 23$).")),
      sec("איך באמת מרוויחים", h("p", null, "רק כשהמחיר טועה: אתה יודע להעריך טוב יותר מהשוק, או ששני שווקים קשורים מתומחרים בסתירה. קנייה סתם, בממוצע, מחזירה את המחיר — פחות המרווח בין קנייה למכירה."),
        h("div", { class: "example" }, "אם השוק אומר 23% והסיכוי האמיתי הוא 23%, אחרי הרבה עסקאות כאלה תגיע בערך לאפס — ותפסיד את העמלות.")),
      sec("מה יכול להשתבש", h("ul", { class: "clean" },
        h("li", null, "המחיר המוצג באתר הוא לא בהכרח מה שתשלם: יש מרווח ועומק, וקנייה גדולה מזיזה את המחיר."),
        h("li", null, "תנאי ההכרעה כתובים במילים, ולפעמים מוכרעים בצורה מפתיעה — או משתנים באמצע."),
        h("li", null, "הכסף נעול עד ההכרעה, לפעמים חודשים."),
        h("li", null, "שווקים קטנים קל להזיז, בדיוק כמו מניות OTC."))),
      sec("מה המערכת שלנו עושה", h("ul", { class: "clean" },
        h("li", null, "אוספת את כל השווקים כל 6 שעות, ואת השווקים הנזילים כל 15 דקות, כולל ספרי פקודות."),
        h("li", null, "בודקת על שווקים שכבר הוכרעו: האם 23% באמת קרו בערך ב-23% מהמקרים?"),
        h("li", null, "מחפשת כלל מסחר שמרוויח גם על תקופה שהוא לא ראה (מחוץ למדגם), אחרי עלויות."),
        h("li", null, "מחפשת סתירות בין שווקים קשורים, ובודקת בספר הפקודות אם אפשר באמת לבצע אותן."))),
      sec("למה המערכת אומרת לא לקנות", h("p", null, "כי אף שיטה עוד לא הוכיחה שהיא מרוויחה: לא על נתוני העבר, לא אחרי עמלות, ולא במחיר שאפשר באמת לקנות בו."),
        h("p", null, "המצב כרגע: ", h("strong", null, wf ? ({ supported: "נמצא יתרון", oos_positive_needs_execution_check: "חיובי מחוץ למדגם, ממתין לאימות ביצוע", not_supported: "לא נמצא יתרון", insufficient_data: "אין מספיק נתוני עבר" }[wf.verdict] || wf.verdict) : "המחקר עוד לא רץ"), ". ",
          h("a", { href: "#/research" }, "לפרטי המחקר")))),
    card("איך לקרוא דף של שוק", "בכל שוק תמצא ארבעה \"מחירים\" שונים — חשוב לא לבלבל ביניהם",
      h("ul", { class: "clean" },
        h("li", null, h("strong", null, "מחיר תצוגה: "), "מה שמופיע באתר Polymarket. נקודת התחלה, לא מחיר קנייה."),
        h("li", null, h("strong", null, "Bid / Ask: "), "ההצעה הכי טובה לקנייה ולמכירה. ההפרש ביניהם הוא העלות שלך."),
        h("li", null, h("strong", null, "ספר פקודות תקין: "), "יש קונים ומוכרים משני הצדדים. בלי זה אין מחיר אמיתי."),
        h("li", null, h("strong", null, "מחיר בר-ביצוע: "), "כמה תשלם בממוצע על 100$ או 1,000$, אחרי שהקנייה \"אוכלת\" את הספר.")),
      h("div", { class: "detail-actions", style: "margin-top:12px" }, h("a", { class: "btn primary", href: "#/markets" }, "לכל השווקים"), h("a", { class: "btn", href: "#/pm/overview" }, "מה זז עכשיו")))));
};
function plainMarket(m) {
  const p = num(m.ref_price);
  if (p == null) return null;
  const c = Math.round(p * 100), o = m.ref_outcome || "כן";
  return h("div", { class: "plain" },
    "השוק מעריך ", h("strong", null, `${c}% סיכוי`), ` שהתשובה תהיה "${o}". `,
    `אם תקנה "${o}" ב-${c} סנט ותצדק, תקבל דולר — רווח של ${100 - c} סנט על כל מניה. אם תטעה, תפסיד את ה-${c} סנט. `,
    until(m.end_date) ? `ההכרעה: ${until(m.end_date)}. ` : "",
    h("span", { class: "faint" }, "זו הערכת המשתתפים, לא תחזית שבדקנו."));
}

// ---- Stocks overview ----
PAGES.stocks = async (view) => {
  const d = await api("/api/stocks/overview").catch(() => null);
  if (current !== "stocks") return;
  if (!d) { view.replaceChildren(empty("לא הצלחתי לקרוא נתוני שוק כרגע.")); return; }
  const sgn = (v, dg = 1) => { if (v == null) return "—"; const r = Number((v * 100).toFixed(dg)) || 0; return ltr((r > 0 ? "+" : "") + r.toFixed(dg) + "%", r > 0 ? "up" : r < 0 ? "down" : ""); };
  const etfs = d.etfs || [];
  const spy = etfs.find((x) => x.symbol === "SPY");
  const above = etfs.filter((x) => x.symbol.startsWith("XL") && x.above_ma200 === true).length;
  const sectors = etfs.filter((x) => x.symbol.startsWith("XL"));
  const lead = [...sectors].sort((a, b) => (b.vs_spy_3m ?? -9) - (a.vs_spy_3m ?? -9));
  const LBL = { breakout_volume: "פריצה עם מחזור", dip_exhaustion: "דיפ", ma_stack: "ממוצעים", near_52w_high: "שיא שנתי", momentum_12_1: "מומנטום", low_volatility: "תנודתיות נמוכה", turn_of_month: "מעבר חודש", rsi2_pullback: "תיקון קצר" };
  view.replaceChildren(h("div", { class: "stack" },
    h("div", { class: "reasons" },
      h("div", { class: "reason" }, h("div", { class: "k" }, "S&P 500 · 3 חודשים"), h("div", { class: "v" }, spy ? sgn(spy.r3m) : "—"),
        h("div", { class: "d" }, spy ? h("span", null, "שנה: ", sgn(spy.r1y), spy.above_ma200 != null ? (spy.above_ma200 ? " · מעל ממוצע 200" : " · מתחת לממוצע 200") : "") : "")),
      h("div", { class: "reason" }, h("div", { class: "k" }, "רוחב השוק"), h("div", { class: "v" }, `${above} מתוך ${sectors.length} סקטורים`),
        h("div", { class: "d" }, "מעל הממוצע של 200 יום. הרבה סקטורים מעל = עלייה רחבה, מעט = עלייה של מניות בודדות.")),
      h("a", { class: "reason", href: "#/lab" }, h("div", { class: "k" }, "איתותים פתוחים"), h("div", { class: "v" }, String((d.signals || []).length)),
        h("div", { class: "d" }, "עסקאות מדומות של אסטרטגיות המעבדה שעדיין פתוחות. לא המלצה — מדידה."))),
    card("סקטורים", `${d.source}. ממוין לפי ביצועים מול S&P 500 ב-3 החודשים האחרונים`,
      h("div", { class: "scroll-x" }, h("table", { class: "t" },
        h("thead", null, h("tr", null, ["קרן", "שבוע", "חודש", "3 חודשים", "מול SPY (3 ח׳)", "מתחילת השנה", "שנה", "ממוצע 200"].map((x) => h("th", null, x)))),
        h("tbody", null, [...etfs.filter((x) => !x.symbol.startsWith("XL")), ...lead].map((x) => h("tr", null,
          h("td", null, h("a", { href: "#/check/" + x.symbol }, ltr(x.symbol)), " ", h("span", { class: "faint" }, x.name)),
          h("td", null, sgn(x.r1w)), h("td", null, sgn(x.r1m)), h("td", null, sgn(x.r3m)), h("td", null, x.symbol === "SPY" ? "—" : sgn(x.vs_spy_3m)),
          h("td", null, sgn(x.ytd)), h("td", null, sgn(x.r1y)), h("td", null, x.above_ma200 == null ? "—" : x.above_ma200 ? "מעל" : "מתחת"))))))),
    card("איתותים של אסטרטגיות במחקר", "נרשמו לפני פתיחת המסחר ונמדדים עד היציאה. אסטרטגיה שלא הוכחה — האיתות שלה הוא ניסוי, לא סיבה לקנות",
      (d.signals || []).length ? h("div", { class: "scroll-x" }, h("table", { class: "t" },
        h("thead", null, h("tr", null, ["יום האיתות", "אסטרטגיה", "מניה", "מצב"].map((x) => h("th", null, x)))),
        h("tbody", null, d.signals.slice(0, 12).map((r) => h("tr", null, h("td", null, ltr(r.signal_day)), h("td", null, LBL[r.strategy] || r.strategy),
          h("td", null, h("a", { href: "#/check/" + r.symbol }, ltr(r.symbol))), h("td", null, r.status === "open" ? "פתוחה" : "ממתינה לפתיחה")))))) : empty("אין איתותים פתוחים. המעקב מתחיל אחרי ההרצה הראשונה של המעבדה.")),
    card("קניות של מנהלים (SEC Form 4)", "מנהל או דירקטור שקנה מניות של החברה שלו בכסף שלו, 25 אלף דולר ומעלה",
      (d.sec_buys || []).length ? h("div", { class: "mlist" }, d.sec_buys.map((b) => h("a", { class: "mrow", href: "#/check/" + b.symbol },
        h("div", { class: "q" }, ltr(b.symbol), h("span", { class: "faint" }, " · " + (b.title || ""))),
        h("div", { class: "px" }, ltr(usd(b.value)), h("div", { class: "faint" }, new Date(b.accepted).toLocaleDateString("he-IL")))))) : empty("עוד אין קניות במעקב.")),
    d.missing && d.missing.length ? h("p", { class: "faint" }, "חסרים נתונים: ", ltr(d.missing.join(", "))) : null));
};

// ---- Research performance (all engines) ----
PAGES.performance = async (view) => {
  const d = await api("/api/performance").catch(() => null);
  if (current !== "performance") return;
  if (!d) { view.replaceChildren(empty("לא הצלחתי לקרוא את לוח התוצאות.")); return; }
  const E = d.entries, C = d.context || {};
  const signed = (v, dg = 2) => { if (v == null) return "—"; const r = Number((v * 100).toFixed(dg)) || 0;
    return ltr((r > 0 ? "+" : "") + r.toFixed(dg) + "%", r > 0 ? "up" : r < 0 ? "down" : ""); };
  const conf = E.filter((e) => e.status === "confirmed");
  const head = conf.length
    ? h("section", { class: "verdict go" }, h("h2", null, `${conf.length} אומתו קדימה`), h("p", null, conf.map((e) => `${e.label} (${ENGINE[e.engine]})`).join(" · ") + ". אימות קדימה אינו הבטחה לרווח בכסף אמיתי."))
    : h("section", { class: "verdict" }, h("h2", null, "עוד אין שיטה שהוכיחה את עצמה."),
        h("p", null, `${E.filter((e) => e.status === "candidate").length} השערות מהעבר · ${E.filter((e) => e.status === "validating").length} בבדיקה קדימה · ${E.filter((e) => e.status === "failed").length} נכשלו · ${E.filter((e) => e.status === "insufficient").length} בלי מספיק נתונים.`));
  const legend = h("details", { class: "more legend" }, h("summary", null, "מה כל מצב אומר"),
    h("ul", { class: "clean" }, Object.entries(STATE).map(([k, x]) => h("li", null, stateBadge(k), " ", x.d))));
  const metric = (k, v) => h("div", null, h("div", { class: "k" }, k), h("div", { class: "v" }, v));
  const KINDNAME = { forward: "קדימה, בכסף מדומה", historical: "על נתוני העבר", measured: "נמדד אחרי שהגיע" };
  const cardOf = (e) => h("section", { class: "card entry" },
    h("div", { class: "row-between" }, h("h3", null, e.label), stateBadge(e.status)),
    h("div", { class: "faint" }, KINDNAME[e.kind] || e.kind, e.period ? [" · ", ltr(e.period)] : "", " · מול ", e.benchmark || "—"),
    h("p", { class: "why" }, e.why || ""),
    h("div", { class: "kv" },
      metric("עסקאות / אירועים", h("span", null, ltr(String(e.n ?? 0)), e.clusters != null ? h("span", { class: "faint" }, ` (${e.clusters} עצמאיים)`) : null)),
      metric("ניצחו את מדד הייחוס", e.success == null ? "—" : ltr(pct(e.success, 0))),
      metric("ממוצע נטו", h("span", null, signed(e.mean_net), h("div", { class: "faint" }, e.unit || ""))),
      metric("גבול תחתון 95%", signed(e.mean_lo95)),
      metric("רווח / הפסד ממוצע", h("span", null, signed(e.avg_win, 1), " / ", signed(e.avg_loss, 1))),
      metric("ירידה מרבית", e.max_drawdown == null ? "—" : h("span", null, signed(e.max_drawdown, 1), e.dd_note ? h("div", { class: "faint" }, e.dd_note) : null))),
    e.sensitivity ? h("details", { class: "more" }, h("summary", null, "רגישות לעמלות (הנחת המודל: " + pct(e.assumed_cost, 1) + ")"),
      h("table", { class: "t" }, h("thead", null, h("tr", null, ["עמלה הלוך-חזור", "ניצחו את ביטקוין", "ממוצע לעסקה", "מצב"].map((x) => h("th", null, x)))),
        h("tbody", null, e.sensitivity.map((r) => h("tr", null, h("td", null, ltr(pct(r.cost, 1))), h("td", null, r.success_rate == null ? "—" : ltr(pct(r.success_rate, 0))),
          h("td", null, signed(r.mean_excess)), h("td", null, stateBadge(r.verdict === "candidate_for_paper" ? "candidate" : r.verdict === "insufficient_data" ? "insufficient" : "failed"))))))) : null,
    e.missing && e.missing.length ? h("div", { class: "missing" }, h("strong", null, "מה עוד חסר: "), e.missing.join(" · ")) : null,
    h("div", { class: "faint src" }, e.data || "", e.updated ? " · עודכן " + new Date(e.updated).toLocaleString("he-IL") : ""));
  const ctx = (k) => { const c = C[k]; if (!c || !c.bench_stats) return null; const b = c.bench_stats;
    return h("p", { class: "ctx" }, `להשוואה: ${c.bench} בהחזקה פסיבית `, ltr(`${b.from} … ${b.to}`), ": תשואה ", ltr(pct(b.total_return, 0)), ", שנתית ", ltr(pct(b.cagr, 1)), ", תנודתיות ", ltr(pct(b.volatility, 0)), ", ירידה מרבית ", ltr(pct(b.max_drawdown, 0)), ".",
      c.assumed_cost != null ? ` עמלה מונחת: ${pct(c.assumed_cost, 1)} הלוך-חזור (הנחת מודל).` : ""); };
  const section = (k) => { const es = E.filter((e) => e.engine === k).sort((a, b) => STATUS_RANK[a.status] - STATUS_RANK[b.status] || (b.n || 0) - (a.n || 0));
    if (!es.length) return card(ENGINE[k], null, empty(k === "polymarket" ? "המחקר עוד לא רץ." : "עוד אין תוצאות מהשרת."));
    const live = es.filter((e) => !["failed", "insufficient"].includes(e.status));
    const fold = (st, title) => { const xs = es.filter((e) => e.status === st);
      return xs.length ? h("details", { class: "more" }, h("summary", null, `${title[0]} (${xs.length}) · ${title[1]}`), h("div", { class: "stack" }, xs.map(cardOf))) : null; };
    return h("section", { class: "engine" }, h("h2", { class: "section-title" }, ENGINE[k], " ", h("span", { class: "faint" }, `${es.length} נבדקו`)), ctx(k),
      live.length ? h("div", { class: "stack" }, live.map(cardOf)) : h("p", { class: "faint" }, "אין כאן אף השערה פעילה: כל מה שנבדק נכשל או שאין עליו מספיק נתונים."),
      fold("failed", ["נכשלו", "לחץ לפרטים"]), fold("insufficient", ["אין עדיין מספיק נתונים", "לחץ לפרטים"])); };
  view.replaceChildren(h("div", { class: "stack" }, head, legend, ...Object.keys(ENGINE).map(section),
    h("p", { class: "faint" }, "אל תשווה אחוזי הצלחה בין תחומים: במניות זה מול SPY, בקריפטו מול ביטקוין, ב-Polymarket מול מחיר השוק, ובטיפים ל-5 ימים. השווה את המצב, את הגבול התחתון ואת מה שחסר.")));
};

// ---- Agents room ----
const AGENT_HUE = { scout: 205, books: 175, listener: 265, sec: 30, guard: 0, judge: 145, trader: 45, researcher: 230, lab: 300, crypto: 38, keeper: 95 };
const AGENT_STATE = { working: "עובד עכשיו", ok: "תקין", late: "מאחר", error: "נתקע", idle: "עוד לא התחיל", quiet: "שקט" };
function robot(id, state) {
  const hue = AGENT_HUE[id] ?? 210;
  const body = `hsl(${hue} 55% 52%)`, dark = `hsl(${hue} 45% 32%)`;
  const eyes = state === "error"
    ? [s("path", { d: "M17 21l5 5M22 21l-5 5M34 21l5 5M39 21l-5 5", class: "r-x" })]
    : state === "idle" || state === "quiet"
      ? [s("path", { d: "M16 24h7M33 24h7", class: "r-shut" })]
      : [s("rect", { x: 16, y: 20, width: 7, height: 8, rx: 2, class: "r-eye" }), s("rect", { x: 33, y: 20, width: 7, height: 8, rx: 2, class: "r-eye" })];
  return s("svg", { viewBox: "0 0 56 64", class: "robot " + state, "aria-hidden": "true" },
    s("line", { x1: 28, y1: 4, x2: 28, y2: 11, stroke: dark, "stroke-width": 2 }),
    s("circle", { cx: 28, cy: 4, r: 3, class: "r-ant" }),
    s("rect", { x: 6, y: 10, width: 44, height: 30, rx: 9, fill: body }),
    s("rect", { x: 11, y: 15, width: 34, height: 19, rx: 6, class: "r-face" }),
    ...eyes,
    s("path", { d: "M14 44h28l-3 16H17z", fill: dark }));
}
PAGES.agents = async (view) => {
  const d = await api("/api/agents").catch(() => null);
  if (current !== "agents") return;
  if (!d) { view.replaceChildren(empty("לא הצלחתי לקרוא את מצב הסוכנים מהשרת.")); return; }
  const A = d.agents, by = Object.fromEntries(A.map((a) => [a.id, a]));
  const count = (st) => A.filter((a) => a.state === st).length;
  const bad = A.filter((a) => a.state === "error" || a.state === "late");
  const desk = (a) => h("a", { class: "desk " + a.state, href: a.href || "#/system", style: `--hue:${AGENT_HUE[a.id] ?? 210}` },
    h("div", { class: "bubble" }, a.line),
    robot(a.id, a.state),
    h("div", { class: "plate" }, h("strong", null, a.name), h("span", { class: "st" }, AGENT_STATE[a.state] || a.state, a.ago ? " · " + a.ago : "")),
    h("div", { class: "role" }, a.role));
  const zone = (title, ids) => h("section", { class: "zone" }, h("h3", null, title), h("div", { class: "desks" }, ids.map((i) => by[i]).filter(Boolean).map(desk)));
  const NAME = Object.fromEntries(A.map((a) => [a.id, a.name]));
  const ST = { ok: ["ok", "הצליח"], partial: ["wait", "חלקי"], error: ["bad", "נכשל"], interrupted: ["bad", "נקטע"], running: ["info", "רץ"] };
  const feed = d.feed.map((r) => { const [c, l] = ST[r.status] || ["", r.status];
    return h("li", null, h("span", { class: "t" }, ltr(new Date(r.started_at).toLocaleTimeString("he-IL", { hour: "2-digit", minute: "2-digit" }))),
      h("span", { class: "who" }, NAME[r.agent] || r.job), h("span", { class: "what" }, JOBS[r.job] || r.job),
      r.items != null ? h("span", { class: "faint" }, ltr(Number(r.items).toLocaleString())) : null, h("span", { class: "badge " + c }, l)); });
  view.replaceChildren(h("div", { class: "stack" },
    h("section", { class: "verdict" + (bad.length ? "" : " go") },
      h("h2", null, bad.length ? `${bad.length} סוכנים צריכים תשומת לב` : "כל הסוכנים בעבודה"),
      h("p", null, `${count("working")} עובדים ברגע זה · ${count("ok")} סיימו סבב בהצלחה · ${count("idle")} עוד לא התחילו`,
        bad.length ? " · " + bad.map((a) => a.name).join(", ") : "")),
    h("div", { class: "room" },
      zone("איסוף", ["scout", "books", "listener", "sec"]),
      zone("בדיקה ושיפוט", ["guard", "judge", "researcher", "lab", "crypto"]),
      zone("ביצוע ותחזוקה", ["trader", "keeper"])),
    card("יומן עבודה", "הסבבים האחרונים של כל הסוכנים, מהחדש לישן", feed.length ? h("ul", { class: "feed" }, feed) : empty("עוד אין סבבים.")),
    h("p", { class: "faint" }, "המצב נלקח מהשרת: מתי כל משימה רצה, אם הצליחה, ומה היא מצאה. סוכן שלא רץ מסומן ככזה — שום דבר כאן לא מדומה.")));
};

// ---- Strategy lab (stocks and crypto share it) ----
const LABS = {
  stocks: { route: "lab", q: "", bench: "SPY", day: "ימי מסחר", what: "מניות", job: "stocklab",
    empty: "הריצה הראשונה מורידה 10 שנות מחירים ל-65 מניות ובודקת 15 אסטרטגיות, כולל 7 תבניות נרות. זה לוקח כמה דקות.",
    entry: "קונים בפתיחה של היום שאחרי האיתות, ומוכרים בסגירה אחרי 5, 10 או 20 ימי מסחר.",
    cost: "לכל עסקה יורדת עלות מסחר משוערת של אותה מניה: מרווח קנייה-מכירה לפי טווח המחירים היומי, ועוד 0.1% עמלות.",
    seal: "כל יום אחרי הסגירה נרשמים האיתותים ונחתמים לפני פתיחת המסחר הבא." },
  crypto: { route: "crypto", q: "?market=crypto", bench: "ביטקוין", day: "ימים", what: "מטבעות", job: "cryptolab",
    empty: "הריצה הראשונה מורידה את כל היסטוריית המחירים של 20 מטבעות גדולים ובודקת 14 אסטרטגיות, כולל 7 תבניות נרות, מול החזקת ביטקוין. בלי ארנק ובלי מפתחות, רק מחירים.",
    entry: "קריפטו נסחר 24/7, אז יום נסגר בחצות (UTC). קונים יום שלם אחרי האיתות, כדי שאי אפשר יהיה \"לרמות\" עם מחיר שכבר עבר, ומוכרים אחרי 7, 14 או 30 ימים.",
    cost: "לכל עסקה יורדת עלות קבועה של 1% הלוך-חזור, בערך מה שאפליקציה ישראלית או בורסה עם מרווח גובה. אפשר לשנות בשרת (CRYPTO_COST_RT).",
    seal: "כל יום אחרי חצות נרשמים האיתותים ונחתמים לפני הכניסה, יום אחר כך." },
};
PAGES.lab = (view) => labPage(view, "stocks");
PAGES.crypto = (view) => labPage(view, "crypto");
async function labPage(view, market) {
  const M = LABS[market];
  const [d, fp] = await Promise.all([api("/api/lab/latest" + M.q).catch(() => null), api("/api/lab/paper" + M.q).catch(() => null)]);
  if (current !== M.route) return;
  if (!d) { view.replaceChildren(card("המעבדה עוד לא רצה", M.empty,
    h("p", null, "מריצים פעם אחת בשרת:"), h("pre", { class: "code", dir: "ltr" }, `sudo docker compose run --rm ${M.job}`))); return; }
  const r = d.result, S = r.strategies;
  const V = { candidate_for_paper: ["ok", "מועמדת למעקב בכסף מדומה"], edge_below_target: ["wait", "יש יתרון, מתחת ל-60%"],
    not_supported: ["bad", "לא עובדת"], insufficient_data: ["", "מעט מדי עסקאות"] };
  const ORDER = { candidate_for_paper: 0, edge_below_target: 1, not_supported: 2, insufficient_data: 3 };
  const list = Object.entries(S).sort((a, b) => (ORDER[a[1].verdict] - ORDER[b[1].verdict]) || ((b[1].edge_vs_baseline ?? -9) - (a[1].edge_vs_baseline ?? -9)));
  const winners = list.filter(([, v]) => v.verdict === "candidate_for_paper");
  const edge = list.filter(([, v]) => v.verdict === "edge_below_target");
  const signed = (v, d = 2) => { if (v == null) return "—"; const r = Number((v * 100).toFixed(d)) || 0;
    return ltr((r > 0 ? "+" : "") + r.toFixed(d) + "%", r > 0 ? "up" : r < 0 ? "down" : ""); };
  const head = winners.length
    ? h("section", { class: "verdict go" }, h("h2", null, winners.length === 1 ? "אסטרטגיה אחת עברה את הבדיקה על נתוני העבר" : `${winners.length} אסטרטגיות עברו את הבדיקה על נתוני העבר`),
        h("p", null, "השלב הבא: מעקב קדימה בכסף מדומה. בדיקה על העבר לבדה לא מספיקה כדי לסמוך עליהן."))
    : h("section", { class: "verdict" }, h("h2", null, edge.length ? "יש יתרון קטן, אבל אף אסטרטגיה לא מגיעה ל-60%." : "אף אסטרטגיה לא עברה את הבדיקה."),
        h("p", null, "זו תוצאה אמיתית, לא כישלון של המערכת: רוב הכללים הפשוטים לא מנצחים את השוק אחרי עלויות. ממשיכים לבדוק רעיונות חדשים באותה שיטה."));
  const cardFor = ([k, v]) => { const [c, l] = V[v.verdict] || ["", v.verdict];
    const years = Object.entries(v.by_year || {});
    return h("section", { class: "card" },
      h("div", { class: "row-between" }, h("h3", null, v.label), h("span", { class: "badge " + c }, l)),
      h("p", { class: "faint" }, v.rule),
      h("div", { class: "kv" },
        h("div", null, h("div", { class: "k" }, "עסקאות מחוץ למדגם"), h("div", { class: "v" }, ltr(String(v.n)))),
        h("div", null, h("div", { class: "k" }, v.side === -1 ? `פיגרו אחרי ${M.bench} (האזהרה צדקה)` : `ניצחו את ${M.bench} אחרי עלות`), h("div", { class: "v" }, v.success_rate == null ? "—" : ltr(pct(v.success_rate, 0)))),
        h("div", null, h("div", { class: "k" }, v.side === -1 ? "כמה חסכה האזהרה, מעל אקראי" : "יתרון לעסקה מעל קנייה אקראית"), h("div", { class: "v" }, signed(v.edge_vs_baseline))),
        h("div", null, h("div", { class: "k" }, "גבול תחתון (95%)"), h("div", { class: "v" }, signed(v.edge_lo95))),
        h("div", null, h("div", { class: "k" }, "רווח ממוצע / הפסד ממוצע"), h("div", { class: "v" }, h("span", null, signed(v.avg_win, 1), " / ", signed(v.avg_loss, 1)))),
        h("div", null, h("div", { class: "k" }, "עלות ממוצעת לעסקה"), h("div", { class: "v" }, v.avg_cost == null ? "—" : ltr(pct(v.avg_cost, 2))))),
      years.length ? h("div", { class: "years", "aria-label": "יתרון לפי שנה" }, years.map(([y, x]) => h("span", { class: "yr " + (x.edge_vs_baseline >= 0 ? "up" : "down"), title: `${x.n} עסקאות` },
        ltr(y), " ", signed(x.edge_vs_baseline, 1)))) : null,
      v.verdict === "not_supported" && v.edge_lo95 > 0 ? h("p", { class: "faint" }, "הגבול התחתון חיובי, אבל התוצאה לא שורדת את התיקון לכך שנבדקו כמה אסטרטגיות — יכול להיות מזל.") : null,
      v.current_params ? h("p", { class: "faint" }, "הגדרות שנבחרו לשנה האחרונה: ", ltr(Object.entries(v.current_params.params).map(([a, b]) => `${a}=${b}`).join(", ")), `, החזקה ${v.current_params.hold} ${M.day}`) : null,
      v.signals_today && v.signals_today.length ? h("p", null, v.side === -1 ? "אזהרה ביום האחרון: " : "איתות ביום האחרון: ", v.signals_today.map((x, i) => [i ? ", " : "", market === "stocks" ? h("a", { href: "#/check/" + x }, ltr(x)) : ltr(x.replace("-USD", ""))]),
        h("span", { class: "faint" }, v.verdict === "candidate_for_paper" ? " · נכנס למעקב" : " · לא לפעולה — האסטרטגיה לא הוכחה")) : null); };
  const FS = { confirmed: ["ok", "אומתה קדימה"], not_confirmed: ["bad", "לא אומתה"], collecting: ["", "צוברת עסקאות"] };
  const fsum = fp ? fp.summary : {};
  const STAT = { pending: "ממתין לפתיחה", open: "פתוחה", closed: "נסגרה" };
  const forward = card("מעקב קדימה בכסף מדומה",
    fp && fp.total ? `${M.seal} מאז ${new Date(fp.first_day).toLocaleDateString("he-IL")}: ${fp.total} איתותים.`
      : `מתחיל אחרי ההרצה הראשונה של המעבדה בשרת. ${M.seal}`,
    h("div", { class: "scroll-x" }, h("table", { class: "t" },
      h("thead", null, h("tr", null, ["אסטרטגיה", "עסקאות שנסגרו", `ניצחו את ${M.bench}`, "ממוצע לעסקה אחרי עלות", "מצב"].map((x) => h("th", null, x)))),
      h("tbody", null, list.map(([k, v]) => { const f = fsum[k];
        let [c, l] = f ? FS[f.status] : ["", "עוד אין עסקאות"];
        if (f && f.status === "confirmed" && v.verdict !== "candidate_for_paper") [c, l] = ["wait", "חיובית קדימה, אבל נכשלה על העבר"];
        return h("tr", null, h("td", null, v.label), h("td", null, ltr(String(f ? f.n : 0))),
          h("td", null, f ? ltr(pct(f.success_rate, 0)) : "—"), h("td", null, f ? signed(f.mean_excess) : "—"),
          h("td", null, h("span", { class: "badge " + c }, l))); })))),
    fp && fp.recent.length ? h("details", { class: "more" }, h("summary", null, "האיתותים האחרונים"),
      h("div", { class: "scroll-x" }, h("table", { class: "t" },
        h("thead", null, h("tr", null, ["יום", "אסטרטגיה", market === "stocks" ? "מניה" : "מטבע", "מצב", `מול ${M.bench} אחרי עלות`].map((x) => h("th", null, x)))),
        h("tbody", null, fp.recent.map((r) => h("tr", null, h("td", null, ltr(r.signal_day)), h("td", null, (S[r.strategy] || {}).label || r.strategy),
          h("td", null, market === "stocks" ? h("a", { href: "#/check/" + r.symbol }, ltr(r.symbol)) : ltr(r.symbol.replace("-USD", ""))), h("td", null, STAT[r.status] || r.status),
          h("td", null, r.excess == null ? "—" : signed(r.excess)))))))) : null,
    h("p", { class: "faint" }, `אסטרטגיה "אומתה קדימה" רק אחרי ${fp ? fp.gate.min_n : 50} עסקאות אמיתיות קדימה, 60% הצלחה שאינה מקרית, ורווח ממוצע חיובי אחרי עלויות — וגם עברה את הבדיקה על העבר. רק אז מדברים על כסף אמיתי.`));
  view.replaceChildren(h("div", { class: "stack" }, head, forward,
    ...list.map(cardFor),
    card("איך נבדק", `${r.universe.length} ${M.what}, ${r.first_day} עד ${r.last_day}, מול ${M.bench}`,
      h("ul", { class: "clean" },
        h("li", null, M.entry),
        h("li", null, M.cost),
        h("li", null, "ההגדרות נבחרות רק על השנים הקודמות ונבחנות על השנה הבאה. שלוש השנים הראשונות משמשות ללמידה בלבד."),
        h("li", null, `הרשימה היא ה${M.what} הגדולים של היום, כלומר כאלה ששרדו. לכן כל קנייה בהם נראית טוב בדיעבד. כדי לנטרל את זה, כל אסטרטגיה צריכה לנצח קנייה אקראית מאותה רשימה באותה שנה.`),
        h("li", null, "כשבודקים כמה אסטרטגיות, אחת יכולה להצליח במזל. התוצאות מתוקנות לזה (Holm)."),
        h("li", null, "גם אסטרטגיה שעוברת הכול היא רק מועמדת: היא חייבת להוכיח את עצמה קדימה, בכסף מדומה.")),
      r.missing && r.missing.length ? h("p", { class: "faint" }, "חסרים נתונים: ", ltr(r.missing.join(", "))) : null),
    h("p", { class: "faint" }, "עודכן ", new Date(d.created_at).toLocaleString("he-IL"), ". אין כאן המלצה לקנות דבר.")));
}

// ---- Coming soon (honest) ----
let pfScope = "all";
const pct1 = (v) => v == null ? "—" : h("span", { class: v > 0 ? "up" : v < 0 ? "down" : "" }, ltr((v > 0 ? "+" : "") + (v * 100).toFixed(1) + "%"));
const pmoney = (v) => v == null ? "—" : h("span", { class: v > 0 ? "up" : v < 0 ? "down" : "" }, ltr((v > 0 ? "+" : v < 0 ? "−" : "") + "$" + Math.abs(v).toFixed(2)));
PAGES.portfolio = async (view) => {
  const d = await api("/api/portfolio?scope=" + pfScope);
  if (current !== "portfolio") return;
  const L = d.limits;
  const seg = h("div", { class: "seg", role: "group", "aria-label": "אילו שיטות" }, [["all", "כל השיטות (ללמידה)"], ["proven", "רק שיטות שעברו"]].map(([v, l]) =>
    h("button", { type: "button", "aria-pressed": String(pfScope === v), onclick: () => { pfScope = v; route(); } }, l)));
  const tile = (k, v, note) => h("div", { class: "reason" }, h("div", { class: "k" }, k), h("div", { class: "v" }, v), note ? h("div", { class: "d" }, note) : null);
  const bench = d.closed ? d.bench_same_trades : null;
  view.replaceChildren(h("div", { class: "stack" },
    h("section", { class: "verdict" }, h("h2", null, pfScope === "all" ? "תיק תרגול: כל האיתותים" : "תיק: רק שיטות שעברו בדיקה"),
      h("p", null, pfScope === "all" ? "כולל שיטות שנכשלו בבדיקה. זה תרגול שמראה איך היה נראה לפעול לפי כל איתות — לא אסטרטגיה מומלצת."
        : (d.proven_strategies.length ? `שיטות בתיק: ${d.proven_strategies.length}.` : "אף שיטה עוד לא עברה בדיקה, לכן התיק ריק. זו התוצאה הנכונה כרגע."))),
    seg, budgetBar({ limits: L }),
    h("div", { class: "reasons two" },
      tile("שווי התיק", ltr("$" + d.value.toFixed(2)), h("span", null, "מתוך $", L.budget.toFixed(0), " · ", pct1(d["return"]))),
      tile("מזומן פנוי", ltr("$" + d.cash.toFixed(2))),
      tile("רווח/הפסד ממומש", pmoney(d.realized), `${d.closed} עסקאות נסגרו, ${d.wins} ברווח`),
      tile("רווח/הפסד לא ממומש", pmoney(d.unrealized), `${d.positions.length} פתוחות`),
      tile("ירידה מרבית", d.max_drawdown == null ? "—" : pct1(d.max_drawdown), "על עסקאות שנסגרו"),
      tile("מול המדד באותן עסקאות", bench == null ? "—" : pmoney(d.realized - bench), bench == null ? "עוד אין עסקאות סגורות" : "רווח התיק פחות מה שהמדד היה נותן על אותו סכום")),
    card("עסקאות פתוחות", `עד ${L.max_open} בבת אחת, עד $${L.max_position_usd.toFixed(2)} לכל אחת (לפי הפרופיל שלך)`,
      d.positions.length ? h("div", { class: "scroll-x" }, h("table", { class: "t" },
        h("thead", null, h("tr", null, ["נכס", "שיטה", "כניסה", "מחיר כניסה", "סכום", "יחידות", "מחיר נוכחי", "רווח/הפסד", "יציאה"].map((x) => h("th", null, x)))),
        h("tbody", null, d.positions.map((p) => h("tr", null, h("td", null, ltr(p.symbol.replace("-USD", ""))), h("td", null, p.label),
          h("td", null, ltr(String(p.entry_day))), h("td", null, ltr(usd2(p.entry_price))), h("td", null, ltr(usd2(p.size))),
          h("td", null, ltr(p.units.toFixed(4))), h("td", null, ltr(usd2(p.price)),
            h("div", { class: p.price_live ? "up" : "faint" }, p.price_live ? "חי · " + when(p.price_day) : "לא עדכני · סגירה " + (p.price_day ? String(p.price_day).slice(0, 10) : "—"))),
          h("td", null, pmoney(p.pnl), " ", pct1(p.pnl_pct)), h("td", null, `לפי זמן: אחרי ${p.hold} ימים · בלי יעד ובלי עצירה`))))))
        : empty("אין עסקאות פתוחות.")),
    h("p", null, h("a", { href: "#/history" }, "כל ההחלטות: מה נכנס, מה דולג ולמה")),
    h("p", { class: "faint" }, "כניסה במחיר הפתיחה שנרשם בפועל, יציאה לפי זמן, ועלות מסחר משוערת בכל עסקה. עסקה פתוחה מוערכת לפי המחיר האחרון שנבדק.")));
};
PAGES.history = async (view) => {
  const d = await api("/api/portfolio?scope=" + pfScope);
  if (current !== "history") return;
  const DEC = { "נכנס לתיק": "ok", "דילוג": "wait", "לא בתיק": "", "ממתין לכניסה": "info" };
  view.replaceChildren(h("div", { class: "stack" },
    h("p", null, `לפי ${pfScope === "all" ? "תיק התרגול (כל השיטות)" : "התיק של שיטות שעברו"}. ${d.history.length} איתותים.`),
    d.history.length ? h("ul", { class: "hist" }, d.history.map((x) => h("li", { class: "card" },
      h("div", { class: "row-between" }, h("strong", null, ltr(x.symbol.replace("-USD", "")), " · ", x.strategy), h("span", { class: "badge " + (DEC[x.decision] || "") }, x.decision)),
      h("div", { class: "faint" }, "זוהה ביום המסחר ", ltr(String(x.signal_day)), " · נרשם ", when(x.recorded_at),
        x.sealed_before_entry ? " · נחתם לפני הכניסה ✓" : " · נרשם מאוחר — לא נחשב"),
      h("div", null, x.why),
      x.entry_day ? h("div", null, "כניסה מדומה: ", ltr(String(x.entry_day)), " ב-", ltr(usd2(x.entry_price)),
        x.exit_day ? [" · יציאה: ", ltr(String(x.exit_day)), ` (לפי זמן, אחרי ${x.hold} ימים)`] : " · עדיין פתוחה") : null,
      x.ret_after_cost != null ? h("div", null, "תוצאה אחרי עלויות: ", pct1(x.ret_after_cost), " · מול מדד הייחוס: ", pct1(x.excess)) : null,
      h("div", { class: "faint" }, "מה היה ידוע אז: מצב השיטה בבדיקה על העבר — ", x.lab_verdict === "candidate_for_paper" ? "מועמדת" : x.lab_verdict ? "לא עברה" : "לא ידוע",
        ". חתימה: ", ltr(String(x.sha256).slice(0, 12)))))) : empty("עוד לא נרשמו איתותים. הם נוצרים כל יום אחרי סגירת המסחר."),
    h("p", { class: "faint" }, "כל איתות נחתם (SHA-256) ברגע הרישום, לפני שהמחיר של הכניסה ידוע. כך אי אפשר לשנות אחרי מעשה מה המערכת \"החליטה\".")));
};
PAGES.alerts = (view) => view.replaceChildren(card("התראות", "עוד לא פעיל",
  h("p", { class: "sub" }, "מתוכנן: תזוזה חריגה בשוק ממעקב, הזדמנות מבנית בת-ביצוע, ותקלה בסריקה. ערוץ: טלגרם."),
  h("p", { class: "faint" }, "אין התראות מזויפות — המסך הזה יתמלא רק כשיהיה מנגנון אמיתי מאחוריו.")));
PAGES.more = (view) => {
  const G = [["שלי", ["live", "portfolio", "history", "settings", "check"]], ["מניות", ["stocks", "lab", "tips", "evidence", "etf"]], ["קריפטו", ["crypto"]],
    ["Polymarket", ["pm", "markets", "research"]], ["המערכת", ["agents", "system"]]];
  view.replaceChildren(h("div", { class: "stack" }, G.map(([t, ids]) => h("section", null, h("h2", { class: "section-title" }, t),
    h("div", { class: "stack" }, ids.map((id) => { const r = ROUTES.find((x) => x.id === id);
      return h("a", { class: "reason", href: r.path }, h("div", { class: "v" }, r.label), h("div", { class: "d" }, r.lead)); }))))));
};

// ---- System Health ----
const JOBS = { collect: "איסוף נזילים (15 דק׳)", collect_full: "סריקה מלאה", orderbooks: "ספרי פקודות", sync_closed: "סנכרון שווקים שנסגרו",
  backfill: "Backfill היסטורי", research: "דוח מחקר", etf_paper: "מסחר מדומה בקרנות סל", sec_insider: "קניות מנהלים (SEC)", sec_events: "13D ודוחות (SEC)", stocklab: "מעבדת אסטרטגיות",
  lab_paper: "מעקב מניות בכסף מדומה", cryptolab: "מעבדת קריפטו", crypto_paper: "מעקב קריפטו בכסף מדומה" };
PAGES.system = async (view) => {
  const [hl, integ] = await Promise.all([api("/api/health"), api("/api/integration/latest").catch(() => null)]);
  if (current !== "system") return;
  const c = hl.counts;
  const jobRow = (j) => h("tr", null,
    h("td", null, JOBS[j.job] || j.job),
    h("td", null, ago(j.started_at)),
    h("td", null,
      h("span", { class: "badge " + (j.status === "ok" ? "ok" : j.status === "error" ? "bad" : "info") }, j.status),
      j.error ? h("div", { class: "faint err", style: "max-width:420px" }, j.error) : null),
    h("td", null, ltr(String(j.items ?? "—"))),
    h("td", null, ago(hl.last_ok[j.job])));
  const jobsTable = h("div", { class: "scroll-x" },
    h("table", { class: "t" },
      h("thead", null, h("tr", null, ["עבודה", "ריצה אחרונה", "סטטוס", "פריטים", "הצלחה אחרונה"].map((x) => h("th", null, x)))),
      h("tbody", null, hl.jobs.map(jobRow))));
  const checks = integ
    ? h("ul", { class: "clean" }, integ.result.checks.map((x) => h("li", null,
        h("span", { class: "badge " + (x.ok ? "ok" : "bad") }, x.ok ? "PASS" : "FAIL"), " ", x.check,
        x.error ? h("span", { class: "err" }, " — " + x.error) : null)))
    : empty("הרץ: python -m pmintel.validate");
  const lag = hl.snapshot_lag_min == null ? "—" : Math.round(hl.snapshot_lag_min);
  view.replaceChildren(h("div", { class: "stack" },
    h("div", { class: "kpis" },
      kpi("סטטוס", h("span", { class: hl.status === "ok" ? "up" : "down" }, hl.status === "ok" ? "תקין" : "ישן"), `פיגור ${lag} דק׳`),
      kpi("שווקים פתוחים", ltr((c.open_markets || 0).toLocaleString())),
      kpi("ספרי פקודות", ltr((c.orderbook_rows || 0).toLocaleString()), `${c.orderbook_valid_pct_24h ?? "—"}% תקינים ב-24ש׳`),
      kpi("שווקים שנפתרו", ltr((c.resolved_markets || 0).toLocaleString()), `${c.tokens_with_history || 0} עם היסטוריה`)),
    card("עבודות רקע", null, jobsTable),
    card("בדיקת אינטגרציה מול Polymarket", integ ? ago(integ.created_at) : "עוד לא רצה", checks)));
};

// ---------------- boot ----------------
buildNav();
addEventListener("hashchange", route);
addEventListener("resize", () => { /* nav highlight depends on width for the "more" group */ });
route();
refreshHealth();
setInterval(refreshHealth, 60 * 1000);
