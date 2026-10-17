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
async function api(path) {
  const r = await fetch(path, { headers: { Accept: "application/json" } });
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return r.json();
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
  { id: "home", path: "#/", label: "מרכז הפיקוד", short: "מרכז", icon: "home", nav: "both" },
  { id: "markets", path: "#/markets", label: "סייר השווקים", short: "שווקים", icon: "list", nav: "both" },
  { id: "scanner", path: "#/scanner", label: "סורק הזדמנויות", short: "הזדמנויות", icon: "radar", nav: "both" },
  { id: "watchlist", path: "#/watchlist", label: "רשימת מעקב", short: "מעקב", icon: "star", nav: "both" },
  { id: "research", path: "#/research", label: "מעבדת מחקר", icon: "lab", nav: "side" },
  { id: "check", path: "#/check", label: "בודק מניות חשודות", icon: "shield", nav: "side" },
  { id: "tips", path: "#/tips", label: "מעקב טיפים", icon: "radar", nav: "side" },
  { id: "etf", path: "#/etf", label: "חדר מסחר ETF (נייר)", icon: "wallet", nav: "side" },
  { id: "portfolio", path: "#/portfolio", label: "תיק נייר", icon: "wallet", nav: "side", soon: true },
  { id: "alerts", path: "#/alerts", label: "התראות", icon: "bell", nav: "side", soon: true },
  { id: "system", path: "#/system", label: "בריאות המערכת", icon: "pulse", nav: "side" },
  { id: "more", path: "#/more", label: "עוד", short: "עוד", icon: "more", nav: "bottom" },
];
function buildNav() {
  const side = $("#side-nav"), bottom = $("#bottom-nav");
  ROUTES.filter((r) => r.nav !== "bottom").forEach((r, i) => {
    if (i === 4) side.append(h("div", { class: "sep" }));
    side.append(h("a", { href: r.path, "data-route": r.id }, icon(r.icon), r.label, r.soon ? h("span", { class: "soon" }, "בקרוב") : null));
  });
  ROUTES.filter((r) => r.nav === "both" || r.nav === "bottom").forEach((r) =>
    bottom.append(h("a", { href: r.path, "data-route": r.id }, icon(r.icon, 22), r.short)));
}
let current = null;
function route() {
  const hash = location.hash || "#/";
  let id = "home", arg = null;
  const m = hash.match(/^#\/m\/(.+)$/);
  const mc = hash.match(/^#\/check\/(.+)$/);
  if (m) { id = "detail"; arg = decodeURIComponent(m[1]); }
  else if (mc) { id = "check"; arg = decodeURIComponent(mc[1]); }
  else { const r = ROUTES.find((x) => x.path === hash); if (r) id = r.id; }
  current = id;
  const navId = id === "detail" ? "markets" : (["research", "check", "tips", "etf", "portfolio", "alerts", "system"].includes(id) && innerWidth < 960 ? "more" : id);
  document.querySelectorAll("[data-route]").forEach((a) => a.toggleAttribute("aria-current", false));
  document.querySelectorAll(`[data-route="${navId}"], #side-nav [data-route="${id === "detail" ? "markets" : id}"]`)
    .forEach((a) => a.setAttribute("aria-current", "page"));
  const view = $("#view");
  view.replaceChildren(skel(5));
  const page = PAGES[id] || PAGES.home;
  $("#page-title").textContent = id === "detail" ? "פרטי שוק" : (ROUTES.find((r) => r.id === id)?.label || "");
  document.title = $("#page-title").textContent + " · Polymarket Intel";
  scrollTo(0, 0);
  Promise.resolve(page(view, arg)).catch((e) => view.replaceChildren(fail(e)));
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
    $("#side-health").replaceChildren(h("div", null, "שווקים פתוחים: ", ltr((hl.counts.open_markets || 0).toLocaleString())),
      h("div", null, "ספר פקודות אחרון: ", ago(hl.counts.last_orderbook)));
    return hl;
  } catch {
    $("#top-health").replaceChildren(h("span", { class: "dot bad" }), "השרת לא זמין");
    return null;
  }
}

// ================= PAGES =================
const PAGES = {};

// ---- Command Center ----
PAGES.home = async (view) => {
  const [o, macro] = await Promise.all([api("/api/overview"), api("/api/macro").catch(() => [])]);
  if (current !== "home") return;
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
      h("a", { href: "#/markets", class: "faint" }, "→ חזרה לסייר"),
      h("h1", { dir: "auto" }, m.question),
      h("div", { class: "detail-actions" }, status, until(m.end_date) ? h("span", { class: "badge" }, until(m.end_date)) : null,
        m.neg_risk ? h("span", { class: "badge info" }, "חלק מקבוצת תוצאות") : null,
        starBtn(m.id), url ? h("a", { class: "btn", href: url, target: "_blank", rel: "noopener noreferrer" }, "פתח ב-Polymarket", icon("ext", 16)) : null)),
    h("div", { class: "cols-main" },
      h("div", { class: "stack" },
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
          h("div", { class: "detail-actions" }, h("span", { class: "badge wait", style: "font-size:15px;padding:6px 14px" }, "WAIT · המתן")),
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
          h("td", null, h("span", { class: "badge " + (x.edge_supported ? "ok" : "") }, x.edge_supported ? "נתמך" : "לא הוכח"))))))))))));
};
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
  const board = d.leaderboard.map((r) => h("tr", null, h("td", null, r.source), h("td", null, ltr(String(r.tips))),
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
    card("דירוג מקורות", d.rule, table(["מקור", "טיפים", "נמדדו (5 ימים)", "חציון 5 ימים", "הרוויחו", "מול SPY (5 ימים)", "ניצחו את SPY", "ממוצע 20 יום", "הירידה הגרועה", "אדומים"], board,
      "עוד אין טיפים. רשום ידנית, או חבר את טלגרם (הוראות ב-README).")),
    card("טיפים אחרונים", "לחיצה על סימול פותחת את בודק המניות", table(["זמן", "מקור", "סימול", "סיכון", "כניסה", "יום", "5 ימים", "20 יום", "ירידה מקס׳"], recent, "—")),
    h("p", { class: "faint" }, "נמדד בלי עמלות ובלי החלקה — בפועל התוצאה הייתה גרועה יותר, במיוחד במניות OTC. אין כאן המלצה לקנות דבר.")));
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

// ---- Coming soon (honest) ----
PAGES.portfolio = (view) => view.replaceChildren(card("תיק נייר", "עוד לא פעיל",
  h("p", { class: "sub" }, "יופעל רק לאסטרטגיה שעברה את המחקר ההיסטורי. כל עסקת נייר תתועד במחיר ביצוע אמיתי מספר הפקודות, עם עלויות."),
  h("ul", { class: "clean" }, h("li", null, "דרוש: אסטרטגיה עם רווח סמך מעל 0 אחרי עלויות"), h("li", null, "דרוש: ספרי פקודות טריים לשווקים המעורבים"))));
PAGES.alerts = (view) => view.replaceChildren(card("התראות", "עוד לא פעיל",
  h("p", { class: "sub" }, "מתוכנן: תזוזה חריגה בשוק ממעקב, הזדמנות מבנית בת-ביצוע, ותקלה בסריקה. ערוץ: טלגרם."),
  h("p", { class: "faint" }, "אין התראות מזויפות — המסך הזה יתמלא רק כשיהיה מנגנון אמיתי מאחוריו.")));
PAGES.more = (view) => view.replaceChildren(h("div", { class: "stack" },
  ROUTES.filter((r) => r.nav === "side").map((r) => h("a", { class: "card", href: r.path, style: "display:flex;align-items:center;gap:12px;color:var(--text)" },
    icon(r.icon), h("span", { style: "font-weight:700" }, r.label), r.soon ? h("span", { class: "badge" }, "בקרוב") : null))));

// ---- System Health ----
const JOBS = { collect: "איסוף נזילים (15 דק׳)", collect_full: "סריקה מלאה", orderbooks: "ספרי פקודות", sync_closed: "סנכרון שווקים שנסגרו",
  backfill: "Backfill היסטורי", research: "דוח מחקר", etf_paper: "ניסוי ETF (נייר)" };
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
