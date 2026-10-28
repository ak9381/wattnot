// Round 7 shared UI: zoomable popups, the right-to-left info panel, tooltips,
// small SVG charts and the worked energy -> cost -> water example.
// No libraries and no network calls. Both dashboards import this file.
import { init as initPan } from "./pancanvas.js";

const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const fmt = (v, d = 2) => (v == null || Number.isNaN(+v) ? "–" : (+v).toLocaleString("en-US", { maximumFractionDigits: d, minimumFractionDigits: 0 }));

// ---------- popup (optionally pan/zoom, reusing pancanvas.js) ----------
let popN = 0;
export function popup(title, html, opts = {}) {
  const id = `r7pop${++popN}`;
  const wrap = document.createElement("div");
  wrap.className = "r7pop-scrim"; wrap.id = id;
  wrap.innerHTML = `<div class="r7pop glass" role="dialog" aria-modal="true" aria-labelledby="${id}t">
    <header><h2 id="${id}t">${esc(title)}</h2>${opts.sub ? `<p>${opts.sub}</p>` : ""}<span class="sp"></span>
    <button class="btn r7x" aria-label="Close">Close</button></header>
    <div class="r7pop-body${opts.pan ? " pan" : ""}"><div class="r7pop-inner">${html}</div></div></div>`;
  document.body.appendChild(wrap);
  const prev = document.activeElement;
  let pan = null;
  const close = () => { try { pan?.destroy(); } catch {} wrap.remove(); document.removeEventListener("keydown", onKey); prev?.focus?.(); opts.onClose?.(); };
  const onKey = (e) => { if (e.key === "Escape") close(); };
  document.addEventListener("keydown", onKey);
  wrap.addEventListener("click", (e) => { if (e.target === wrap) close(); });
  wrap.querySelector(".r7x").onclick = close;
  const body = wrap.querySelector(".r7pop-body"), inner = wrap.querySelector(".r7pop-inner");
  if (opts.pan) {
    pan = initPan(body, inner, { follow: false, minimap: false, miniSelector: opts.miniSelector || "svg", goto: () => pan.fit() });
    requestAnimationFrame(() => pan.fit());
  }
  wrap.querySelector(".r7x").focus();
  opts.onMount?.(inner, { close, pan });
  return { close, el: wrap, pan };
}

// ---------- info panel (slides in from the right) ----------
export const INFO = {};
export function registerInfo(map) { Object.assign(INFO, map); }
export const infoBtn = (key, label) => `<button class="ibtn" data-info="${esc(key)}" aria-label="More about ${esc(label || INFO[key]?.title || key)}" title="More about ${esc(label || INFO[key]?.title || key)}">i</button>`;
function ensurePanel() {
  let p = document.getElementById("r7info");
  if (p) return p;
  p = document.createElement("aside");
  p.id = "r7info"; p.className = "r7info glass"; p.setAttribute("role", "dialog"); p.setAttribute("aria-label", "Explanation"); p.setAttribute("aria-hidden", "true");
  p.innerHTML = `<header><span class="lvl">Explained</span><span class="sp"></span><button class="btn r7ix" aria-label="Close explanation">Close</button></header><div class="r7info-body"></div>`;
  document.body.appendChild(p);
  p.querySelector(".r7ix").onclick = closeInfo;
  return p;
}
export function openInfo(key) {
  const it = INFO[key]; if (!it) return;
  const p = ensurePanel();
  p.querySelector(".r7info-body").innerHTML = `<h2>${esc(it.title)}</h2>${it.html}`;
  p.classList.add("on"); p.setAttribute("aria-hidden", "false");
  p.querySelector(".r7ix").focus();
}
export function closeInfo() { const p = document.getElementById("r7info"); if (p) { p.classList.remove("on"); p.setAttribute("aria-hidden", "true"); } }

// ---------- tooltips: any element with data-tip ----------
let tipEl = null;
function showTip(t) {
  tipEl ||= Object.assign(document.createElement("div"), { className: "r7tip", role: "tooltip", id: "r7tip" });
  if (!tipEl.isConnected) document.body.appendChild(tipEl);
  tipEl.textContent = t.dataset.tip; tipEl.hidden = false;
  const r = t.getBoundingClientRect(), w = Math.min(280, tipEl.offsetWidth);
  tipEl.style.left = `${Math.max(8, Math.min(innerWidth - w - 8, r.left + r.width / 2 - w / 2))}px`;
  tipEl.style.top = `${r.top - tipEl.offsetHeight - 8 < 0 ? r.bottom + 8 : r.top - tipEl.offsetHeight - 8}px`;
  t.setAttribute("aria-describedby", "r7tip");
}
const hideTip = () => { if (tipEl) tipEl.hidden = true; };
let wired = false;
export function wireR7() {
  if (wired) return; wired = true;
  document.addEventListener("click", (e) => {
    const b = e.target.closest("[data-info]");
    if (b && INFO[b.dataset.info]) { e.preventDefault(); e.stopPropagation(); openInfo(b.dataset.info); }
  }, true);
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") { closeInfo(); hideTip(); } });
  document.addEventListener("mouseover", (e) => { const t = e.target.closest("[data-tip]"); if (t) showTip(t); });
  document.addEventListener("mouseout", (e) => { if (e.target.closest("[data-tip]")) hideTip(); });
  document.addEventListener("focusin", (e) => { const t = e.target.closest("[data-tip]"); if (t) showTip(t); else hideTip(); });
}
export const tip = (text) => ` data-tip="${esc(text)}" tabindex="0"`;

// ---------- charts ----------
// Line chart for "with vs without WattNot". series: [{name, cls, pts:[{x,y}]}].
// Built for a real watts-over-time series; per-repeat points work the same.
export function lineChart(series, o = {}) {
  const W = o.w || 640, H = o.h || 260, L = 56, R = 16, T = 18, B = 40;
  const all = series.flatMap((s) => s.pts);
  if (!all.length) return `<p class="r7miss">${esc(o.empty || "Measured in the long run")}</p>`;
  const xs = all.map((p) => p.x), ys = all.map((p) => p.y);
  const x0 = Math.min(...xs), x1 = Math.max(...xs), lo = o.y0 ?? Math.min(0, ...ys), hi = Math.max(...ys) * 1.08;
  const X = (x) => L + ((x - x0) / ((x1 - x0) || 1)) * (W - L - R), Y = (y) => H - B - ((y - lo) / ((hi - lo) || 1)) * (H - T - B);
  const ticks = 4, grid = Array.from({ length: ticks + 1 }, (_, i) => lo + ((hi - lo) * i) / ticks);
  const path = (s) => s.pts.map((p, i) => `${i ? "L" : "M"}${X(p.x).toFixed(1)},${Y(p.y).toFixed(1)}`).join("");
  const area = (s) => `${path(s)}L${X(s.pts.at(-1).x).toFixed(1)},${Y(lo)}L${X(s.pts[0].x).toFixed(1)},${Y(lo)}Z`;
  return `<svg class="r7chart" viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc(o.aria || "line chart")}">
    ${grid.map((g) => `<line class="g" x1="${L}" x2="${W - R}" y1="${Y(g)}" y2="${Y(g)}"/><text class="ax" x="${L - 8}" y="${Y(g) + 4}" text-anchor="end">${fmt(g, 1)}</text>`).join("")}
    ${(o.xticks || []).map((t) => `<text class="ax" x="${X(t.x)}" y="${H - B + 18}" text-anchor="middle">${esc(t.label)}</text>`).join("")}
    ${o.yLabel ? `<text class="ax lab" x="12" y="${T + (H - T - B) / 2}" transform="rotate(-90 12 ${T + (H - T - B) / 2})" text-anchor="middle">${esc(o.yLabel)}</text>` : ""}
    ${o.xLabel ? `<text class="ax lab" x="${L + (W - L - R) / 2}" y="${H - 4}" text-anchor="middle">${esc(o.xLabel)}</text>` : ""}
    ${series.map((s) => `<path class="area ${s.cls}" d="${area(s)}"/><path class="ln ${s.cls}" d="${path(s)}"/>${s.pts.map((p) => `<circle class="pt ${s.cls}" cx="${X(p.x)}" cy="${Y(p.y)}" r="3.5"><title>${esc(s.name)}: ${fmt(p.y, 2)} ${esc(o.unit || "")}${p.label ? ` · ${esc(p.label)}` : ""}</title></circle>`).join("")}`).join("")}
    </svg><div class="r7keys">${series.map((s) => `<span><i class="k ${s.cls}"></i>${esc(s.name)}</span>`).join("")}</div>`;
}
export function donut(parts, o = {}) {
  const tot = parts.reduce((a, p) => a + (p.v || 0), 0) || 1, R = 54, C = 2 * Math.PI * R;
  let off = 0;
  const segs = parts.map((p) => { const len = ((p.v || 0) / tot) * C; const s = `<circle r="${R}" cx="70" cy="70" fill="none" stroke="${p.color}" stroke-width="16" stroke-dasharray="${len} ${C - len}" stroke-dashoffset="${-off}" transform="rotate(-90 70 70)"><title>${esc(p.name)}: ${fmt(((p.v || 0) / tot) * 100, 1)}%</title></circle>`; off += len; return s; }).join("");
  return `<div class="r7donut"><svg viewBox="0 0 140 140" role="img" aria-label="${esc(o.aria || "share")}"><circle r="${R}" cx="70" cy="70" fill="none" stroke="rgba(255,255,255,.06)" stroke-width="16"/>${segs}
    <text x="70" y="68" text-anchor="middle" class="dv">${esc(o.center || "")}</text><text x="70" y="86" text-anchor="middle" class="dl">${esc(o.centerSub || "")}</text></svg>
    <ul>${parts.map((p) => `<li><i style="background:${p.color}"></i>${esc(p.name)}<b>${fmt(((p.v || 0) / tot) * 100, 1)}%</b></li>`).join("")}</ul></div>`;
}
export function gauge(v, max, o = {}) {
  const f = Math.max(0, Math.min(1, (v || 0) / (max || 1))), R = 60, a = Math.PI * f;
  const x = 80 - R * Math.cos(a), y = 80 - R * Math.sin(a);
  const lim = o.limit != null ? Math.max(0, Math.min(1, o.limit / max)) : null;
  const lx = lim == null ? 0 : 80 - (R + 8) * Math.cos(Math.PI * lim), ly = lim == null ? 0 : 80 - (R + 8) * Math.sin(Math.PI * lim);
  return `<svg class="r7gauge" viewBox="0 0 160 100" role="img" aria-label="${esc(o.aria || "gauge")}">
    <path d="M20,80 A60,60 0 0 1 140,80" class="tr"/><path d="M20,80 A60,60 0 0 1 ${x.toFixed(1)},${y.toFixed(1)}" class="fl ${o.cls || ""}"/>
    ${lim == null ? "" : `<line x1="${80 - (R - 10) * Math.cos(Math.PI * lim)}" y1="${80 - (R - 10) * Math.sin(Math.PI * lim)}" x2="${lx}" y2="${ly}" class="lim"><title>limit ${esc(o.limitLabel || "")}</title></line>`}
    <text x="80" y="74" text-anchor="middle" class="dv">${esc(o.text ?? fmt(v, 1))}</text><text x="80" y="94" text-anchor="middle" class="dl">${esc(o.sub || "")}</text></svg>`;
}

// ---------- the worked example used by every cost / water number ----------
export const KWH_PER_J = 1 / 3.6e6;
export function workedExample(jStock, jWatt, usdPerKwh = 0.1387, lPerKwh = 0.36) {
  if (jStock == null || jWatt == null) return `<p class="r7miss">Measured in the long run</p>`;
  const k = (j) => j * 1e6 * KWH_PER_J;
  return `<ol class="r7ex">
    <li><b>Energy per answer.</b> Without WattNot ${fmt(jStock, 2)} J, with WattNot ${fmt(jWatt, 2)} J (measured on the GPU).</li>
    <li><b>Scale to 1 million answers.</b> ${fmt(jStock, 2)} J × 1,000,000 ÷ 3,600,000 J per kWh = <b>${fmt(k(jStock), 2)} kWh</b>; with WattNot <b>${fmt(k(jWatt), 2)} kWh</b>.</li>
    <li><b>Cost.</b> kWh × $${usdPerKwh} per kWh (EIA average): $${fmt(k(jStock) * usdPerKwh, 2)} → $${fmt(k(jWatt) * usdPerKwh, 2)}, saving <b>$${fmt((k(jStock) - k(jWatt)) * usdPerKwh, 2)}</b>.</li>
    <li><b>Water.</b> kWh × ${lPerKwh} L per kWh (LBNL 2024, on-site cooling): ${fmt(k(jStock) * lPerKwh, 2)} L → ${fmt(k(jWatt) * lPerKwh, 2)} L, saving <b>${fmt((k(jStock) - k(jWatt)) * lPerKwh, 2)} L</b>.</li></ol>
    <p class="r7note">Step 1 is measured. Steps 2 to 4 are arithmetic on it, so cost and water are estimates.</p>`;
}
