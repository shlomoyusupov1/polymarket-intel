"use strict";
// All market text is untrusted: everything is rendered via textContent, never innerHTML.

const $ = (id) => document.getElementById(id);
function h(tag, attrs = {}, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v == null) continue;
    if (k === "class") el.className = v;
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else el.setAttribute(k, v);
  }
  for (const c of kids.flat()) if (c != null) el.append(c instanceof Node ? c : String(c));
  return el;
}
async function api(path) {
  const r = await fetch(path, { headers: { Accept: "application/json" } });
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return r.json();
}

const usd = (v) => v == null ? "—" : "$" + Intl.NumberFormat("en", { notation: "compact", maximumFractionDigits: 1 }).format(v);
const pct = (v) => v == null ? "—" : (v * 100).toFixed(1) + "%";
const num = (v, d = 3) => v == null ? "—" : Number(v).toFixed(d);
const ago = (iso) => {
  if (!iso) return "אף פעם";
  const m = Math.round((Date.now() - new Date(iso)) / 60000);
  if (m < 1) return "עכשיו";
  if (m < 60) return `לפני ${m} דק׳`;
  if (m < 1440) return `לפני ${Math.round(m / 60)} שע׳`;
  return `לפני ${Math.round(m / 1440)} ימים`;
};
const date = (iso) => iso ? new Date(iso).toLocaleDateString("he-IL") : "—";
const empty = (t) => h("div", { class: "empty" }, t);
const fail = (box, e) => box.replaceChildren(h("div", { class: "empty err" }, "שגיאה בטעינה: " + e.message));

// ---------- tabs ----------
const loaders = {};
let current = "overview";
document.querySelectorAll(".tab").forEach((b) => b.addEventListener("click", () => show(b.dataset.tab)));
function show(tab) {
  current = tab;
  document.querySelectorAll(".tab").forEach((b) => b.classList.toggle("active", b.dataset.tab === tab));
  document.querySelectorAll(".panel").forEach((p) => p.classList.toggle("active", p.id === tab));
  loaders[tab]?.();
}

// ---------- health / freshness ----------
async function refreshHealth() {
  try {
    const hl = await api("/api/health");
    $("health-dot").className = "dot " + (hl.status === "ok" ? "ok" : "stale");
    $("freshness").textContent = hl.counts.last_snapshot
      ? `עדכון אחרון ${ago(hl.counts.last_snapshot)}` + (hl.status !== "ok" ? " · הנתונים לא טריים" : "")
      : "עדיין לא נאספו נתונים";
    return hl;
  } catch (e) {
    $("health-dot").className = "dot err";
    $("freshness").textContent = "השרת לא זמין";
    throw e;
  }
}

// ---------- market row ----------
function marketRow(m, extra) {
  return h("div", { class: "row", onclick: () => openMarket(m.id) },
    h("div", {},
      h("div", { class: "q", dir: "auto" }, m.question),
      h("div", { class: "meta" },
        h("span", {}, "נזילות " + usd(m.liquidity)),
        h("span", {}, "24ש׳ " + usd(m.volume_24h)),
        m.end_date ? h("span", {}, "נסגר " + date(m.end_date)) : null,
        extra)),
    h("div", { class: "price" },
      extra?.price ?? pct(m.ref_price ?? m.price_now),
      h("small", {}, m.ref_outcome ?? ""),
      h("span", { class: "badge wait" }, m.decision ?? "WAIT")));
}

// ---------- overview ----------
loaders.overview = async () => {
  const k = $("kpis"), mv = $("movers");
  try {
    const o = await api("/api/overview");
    const s = o.stats;
    k.replaceChildren(
      ...[["שווקים פתוחים", (s.open_markets ?? 0).toLocaleString()],
          ["במעקב (נזילים)", (s.tracked_markets ?? 0).toLocaleString()],
          ["נפח 24ש׳", usd(s.volume_24h)],
          ["נזילות כוללת", usd(s.liquidity)]]
        .map(([l, v]) => h("div", { class: "kpi" }, h("div", { class: "v" }, v), h("div", { class: "l" }, l))));
    mv.replaceChildren(...(o.movers.length ? o.movers.map((m) => {
      const c = m.change;
      return marketRow(m, h("span", { class: "chg " + (c >= 0 ? "up" : "down") },
        `${c >= 0 ? "▲" : "▼"} ${(Math.abs(c) * 100).toFixed(1)} נק׳ `,
        h("bdi", { dir: "ltr" }, `${pct(m.price_then)} → ${pct(m.price_now)}`)));
    }) : [empty("אין עדיין מספיק היסטוריה לחישוב תנועות")]));
  } catch (e) { fail(mv, e); }
};

// ---------- scanner ----------
let offset = 0, searchTimer;
loaders.scanner = () => loadMarkets(true);
async function loadMarkets(reset) {
  const box = $("markets");
  if (reset) { offset = 0; box.replaceChildren(empty("טוען…")); }
  const p = new URLSearchParams({ sort: $("sort").value, min_liquidity: $("minliq").value, limit: 50, offset });
  if ($("search").value.trim()) p.set("search", $("search").value.trim());
  try {
    const rows = await api("/api/markets?" + p);
    if (reset) box.replaceChildren();
    rows.forEach((m) => box.append(marketRow(m)));
    if (reset && !rows.length) box.append(empty("לא נמצאו שווקים"));
    offset += rows.length;
    $("more").hidden = rows.length < 50;
  } catch (e) { fail(box, e); }
}
$("search").addEventListener("input", () => { clearTimeout(searchTimer); searchTimer = setTimeout(() => loadMarkets(true), 300); });
$("sort").addEventListener("change", () => loadMarkets(true));
$("minliq").addEventListener("change", () => loadMarkets(true));
$("more").addEventListener("click", () => loadMarkets(false));

// ---------- market details ----------
function lineChart(points) {
  const NS = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(NS, "svg");
  svg.setAttribute("viewBox", "0 0 300 120");
  svg.setAttribute("preserveAspectRatio", "none");
  svg.classList.add("chart");
  if (points.length < 2) {
    const t = document.createElementNS(NS, "text");
    t.setAttribute("x", 150); t.setAttribute("y", 64); t.setAttribute("text-anchor", "middle");
    t.textContent = "אין עדיין היסטוריה";
    svg.append(t); return svg;
  }
  const xs = points.map((p) => +new Date(p.t)), x0 = Math.min(...xs), x1 = Math.max(...xs) || x0 + 1;
  const d = points.map((p, i) =>
    `${i ? "L" : "M"}${((xs[i] - x0) / (x1 - x0 || 1) * 296 + 2).toFixed(1)},${(116 - p.v * 112).toFixed(1)}`).join(" ");
  const path = document.createElementNS(NS, "path");
  path.setAttribute("d", d); path.classList.add("l");
  svg.append(path);
  return svg;
}
function qualityBlock(dq) {
  if (!dq) return null;
  const cls = dq.level === "good" ? "ok" : dq.level === "limited" ? "wait" : "err";
  const label = { good: "איכות נתונים: טובה", limited: "איכות נתונים: חלקית", bad: "איכות נתונים: לא מספקת" }[dq.level];
  return h("div", { class: "decision" }, h("span", { class: "badge " + cls }, label),
    dq.issues.length ? h("div", { class: "note" }, dq.issues.join(" · ")) : null);
}
function execBlock(m) {
  const o = m.orderbook;
  if (!o) return h("p", { class: "note" }, "אין עדיין ספר פקודות למעקב בשוק הזה (נאסף רק לשווקים המובילים).");
  const row = (l, a, b) => h("tr", {}, h("td", {}, l), h("td", {}, h("bdi", { dir: "ltr" }, a)), h("td", {}, h("bdi", { dir: "ltr" }, b)));
  return h("div", {},
    h("h2", {}, "מחיר ביצוע אמיתי (" + (m.ref_outcome ?? "") + ")"),
    h("p", { class: "note" }, "מתוך ספר הפקודות, " + ago(o.ts) + ". אם ריק — אין מספיק עומק."),
    h("div", { class: "scroll" }, h("table", {},
      h("thead", {}, h("tr", {}, h("th", {}, ""), h("th", {}, "$100"), h("th", {}, "$1,000"))),
      h("tbody", {},
        row("קנייה", pct(o.buy_vwap_100), pct(o.buy_vwap_1000)),
        row("מכירה", pct(o.sell_vwap_100), pct(o.sell_vwap_1000)),
        row("עומק ±5¢", usd(o.bid_depth_5c) + " / " + usd(o.ask_depth_5c), "")))));
}
async function openMarket(id) {
  const body = $("sheet-body");
  body.replaceChildren(empty("טוען…"));
  $("sheet").hidden = false;
  try {
    const m = await api("/api/markets/" + encodeURIComponent(id));
    const pts = (m.snapshots.length ? m.snapshots.map((s) => ({ t: s.ts, v: s.ref_price }))
                                    : m.history.map((s) => ({ t: s.ts, v: s.price }))).filter((p) => p.v != null);
    body.replaceChildren(
      h("h2", { dir: "auto" }, m.question),
      h("div", { class: "meta" }, h("span", {}, m.category || "ללא קטגוריה"), h("span", {}, "נסגר " + date(m.end_date))),
      lineChart(pts),
      h("div", { class: "note" }, `מחיר ${m.ref_outcome ?? ""} · ${m.snapshots.length ? "snapshots שנאספו" : "היסטוריית CLOB"} · ${pts.length} נקודות`),
      h("div", { class: "grid2" },
        ...[["מחיר שוק (" + (m.ref_outcome ?? "?") + ")", pct(m.ref_price)], ["Bid / Ask", `${num(m.best_bid, 2)} / ${num(m.best_ask, 2)}`],
            ["נזילות", usd(m.liquidity)], ["נפח 24ש׳", usd(m.volume_24h)]]
          .map(([l, v]) => h("div", { class: "kpi" }, h("div", { class: "v" }, v), h("div", { class: "l" }, l)))),
      qualityBlock(m.data_quality),
      execBlock(m),
      h("div", { class: "decision" },
        h("span", { class: "badge wait" }, m.decision), " ", m.reason,
        h("div", { class: "note" }, "הסתברות עצמאית: אין · יתרון משוער: אין · ביטחון: INSUFFICIENT EVIDENCE")),
      m.url ? h("p", {}, h("a", { href: m.url, target: "_blank", rel: "noopener noreferrer" }, "פתח ב-Polymarket ↗")) : null);
  } catch (e) { fail(body, e); }
}
$("sheet-close").addEventListener("click", () => ($("sheet").hidden = true));
$("sheet").addEventListener("click", (e) => { if (e.target.id === "sheet") $("sheet").hidden = true; });

// ---------- research ----------
const HLABEL = { "24": "יום לפני", "168": "שבוע לפני", "720": "חודש לפני" };
loaders.research = async () => {
  const box = $("research-body");
  try {
    const r = await api("/api/research/latest");
    if (!r) {
      box.replaceChildren(empty("עדיין אין דוח מחקר. הרץ backfill ואז research (ראה README)."));
      return;
    }
    const parts = [h("p", { class: "note" },
      `דוח מ-${new Date(r.created_at).toLocaleString("he-IL")} · החלקה ${r.params.slippage} · עמלה ${r.params.fee}. ` +
      "מחירי היסטוריה אינם מחירי ביצוע; תשואות מאוגדות לפי אירוע.")];
    for (const [hz, res] of Object.entries(r.result)) {
      const c = res.calibration;
      parts.push(h("h2", {}, `${HLABEL[hz] ?? hz + " שע׳"} לסגירה · ${c.n} שווקים · Brier ${num(c.brier)}`));
      parts.push(h("div", { class: "scroll" }, h("table", {},
        h("thead", {}, h("tr", {}, ...["טווח מחיר", "n", "אירועים", "מחיר ממוצע", "קרה בפועל", "CI 95%"].map((t) => h("th", {}, t)))),
        h("tbody", {}, ...c.buckets.map((b) => h("tr", {},
          h("td", {}, h("bdi", { dir: "ltr" }, `${pct(b.lo)}–${pct(b.hi)}`)), h("td", {}, b.n), h("td", {}, b.events),
          h("td", {}, pct(b.mean_price)), h("td", {}, pct(b.freq)), h("td", {}, h("bdi", { dir: "ltr" }, `${pct(b.ci_lo)}–${pct(b.ci_hi)}`))))))));
      parts.push(h("div", { class: "scroll", style: "margin-top:8px" }, h("table", {},
        h("thead", {}, h("tr", {}, ...["אסטרטגיה", "עסקאות / אירועים", "הצלחה", "תשואה (CI 95%)", "יתרון?"].map((t) => h("th", {}, t)))),
        h("tbody", {}, ...res.strategies.filter((s) => s.trades > 0).map((s) => h("tr", {},
          h("td", {}, `${s.side} `, h("bdi", { dir: "ltr" }, `${pct(s.lo)}–${pct(s.hi)}`)),
          h("td", {}, `${s.trades} / ${s.events}`), h("td", {}, pct(s.win_rate)),
          h("td", {}, h("bdi", { dir: "ltr" }, pct(s.mean_return)),
            s.ci_lo == null ? null : h("div", { class: "note" }, h("bdi", { dir: "ltr" }, `${pct(s.ci_lo)} … ${pct(s.ci_hi)}`))),
          h("td", {}, h("span", { class: "badge " + (s.edge_supported ? "ok" : "") }, s.edge_supported ? "נתמך" : "לא הוכח"))))))));
    }
    box.replaceChildren(...parts);
  } catch (e) { fail(box, e); }
};

// ---------- system ----------
const JOBS = { orderbooks: "ספרי פקודות", collect: "איסוף חי (נזילים)", collect_full: "סריקה מלאה", sync_closed: "סנכרון שווקים שנסגרו", backfill: "Backfill היסטורי", research: "דוח מחקר" };
loaders.system = async () => {
  const box = $("system-body");
  try {
    const hl = await refreshHealth();
    const c = hl.counts;
    box.replaceChildren(
      h("div", { class: "kpis" },
        ...[["שווקים פתוחים", c.open_markets], ["שווקים שנפתרו", c.resolved_markets],
            ["טוקנים עם היסטוריה", c.tokens_with_history],
            ["ספרי פקודות שנאספו", c.orderbook_rows],
            ["פיגור snapshot", hl.snapshot_lag_min == null ? "—" : Math.round(hl.snapshot_lag_min) + " דק׳"]]
          .map(([l, v]) => h("div", { class: "kpi" }, h("div", { class: "v" }, String(v ?? 0)), h("div", { class: "l" }, l)))),
      h("h2", {}, "עבודות"),
      h("div", { class: "scroll" }, h("table", {},
        h("thead", {}, h("tr", {}, ...["עבודה", "ריצה אחרונה", "סטטוס", "פריטים", "הצלחה אחרונה"].map((t) => h("th", {}, t)))),
        h("tbody", {}, ...(hl.jobs.length ? hl.jobs.map((j) => h("tr", {},
          h("td", {}, JOBS[j.job] ?? j.job), h("td", {}, ago(j.started_at)),
          h("td", {}, h("span", { class: "badge " + (j.status === "ok" ? "ok" : j.status === "error" ? "err" : "") }, j.status),
            j.error ? h("div", { class: "note err" }, j.error) : null),
          h("td", {}, j.items ?? "—"), h("td", {}, ago(hl.last_ok[j.job]))))
          : [h("tr", {}, h("td", { colspan: 5 }, "עדיין לא רצו עבודות"))])))));
  } catch (e) { fail(box, e); }
};

refreshHealth().catch(() => {});
setInterval(() => { refreshHealth().catch(() => {}); if (current === "overview") loaders.overview(); }, 5 * 60 * 1000);
show("overview");
