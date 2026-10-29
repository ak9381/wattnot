// Bento layer: cards show a big number + short label; detail moves into a click popup.
// Per-number label chips become a small coloured dot (tooltip + aria-label); one legend per page.
// Data and components are unchanged: the popup shows the card's original full content.
export const LABELS = {
  LIVE: "Recorded from a real API call made once for this run.",
  MEASURED: "Read from a meter or a real run.",
  ESTIMATE: "Calculated from measured numbers plus a stated assumption.",
  SIMULATED: "Produced by a model or simulator, not measured.",
  REPLAY: "Replayed from saved, signed records; nothing new was run.",
  ADVICE: "A suggestion only; never applied automatically.",
  MOCK: "Sample data that shows the layout only.",
};
const RX = new RegExp(`^\\W{0,3}(${Object.keys(LABELS).join("|")})\\W{0,3}$`, "i");
// Cards that become bento boxes, and the parts that stay visible on the box.
export const BENTO = [
  { sel: ".ov-kpi", hide: "small" },
  { sel: ".hero-item", hide: ".hero-sub" },
  { sel: ".bd-card", hide: ".bd-card-sub", own: true },
  { sel: "#kpis .kpi", hide: ".foot", own: true },
  { sel: ".hotspot-stat-box", hide: "" },
  { sel: ".eulink-metric-box", hide: "" },
  { sel: ".metatools-tile", hide: "p, small, .metatools-trace-id" },
  { sel: ".lv-row", hide: "p, details, .lv-sig, audio" },
  { sel: ".mtag", hide: "span", own: true },
  { sel: ".hotspot-visual", hide: ".hotspot-photo-credit", full: ".hotspot-card" },
];

export function labelOf(text) { const m = (text || "").trim().match(RX); return m ? m[1].toUpperCase() : null; }

function quiet(root) {
  root.querySelectorAll("span, b, i, small, em").forEach((el) => {
    if (el.closest(".wn-pop, .wn-legend, .wn-dot, .r7legend, .legend, .vg-slot-container") || el.dataset.wnq) return;
    if (el.children.length > 2) return;
    const l = labelOf(el.textContent); if (!l) return;
    const host = el.parentElement?.closest(".v-chip, .b-chip, .lv-chip") || el;
    if (host.dataset.wnq) return;
    host.dataset.wnq = l; host.classList.add("wn-dot", `wn-dot-${l.toLowerCase()}`);
    host.setAttribute("title", `${l}: ${LABELS[l]}`); host.setAttribute("aria-label", `${l}: ${LABELS[l]}`); host.setAttribute("tabindex", "0");
  });
}

// Inline label words inside badges and short lines (e.g. "segmentation MEASURED") become dots too.
const WORD = new RegExp(`\\b(${Object.keys(LABELS).join("|")})\\b`, "g");
const dot = (l) => `<span class="wn-dot wn-dot-${l.toLowerCase()}" data-wnq="${l}" tabindex="0" title="${l}: ${LABELS[l]}" aria-label="${l}: ${LABELS[l]}"></span>`;
function quietInline(root) {
  const w = document.createTreeWalker(root.body || root, NodeFilter.SHOW_TEXT);
  const hits = [];
  while (w.nextNode()) {
    const t = w.currentNode, p = t.parentElement;
    if (!p || p.closest(".wn-pop, .wn-legend, .r7legend, script, style, code, pre, textarea, .vg-slot-container, svg, [data-wn-full]")) continue;
    WORD.lastIndex = 0; if (WORD.test(t.nodeValue)) hits.push(t);
  }
  for (const t of hits) {
    const span = document.createElement("span");
    span.innerHTML = t.nodeValue.replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c])).replace(/\s*\(?\b(LIVE|MEASURED|ESTIMATE|SIMULATED|REPLAY|ADVICE|MOCK)\b\)?/g, (_, l) => dot(l));
    t.replaceWith(...span.childNodes);
  }
}

function legend() {
  if (document.querySelector(".wn-legend") || document.body.classList.contains("is-landing")) return;
  if ([...document.querySelectorAll(".r7legend")].some((e) => e.textContent.trim())) return; // v2 already shows a dot legend
  const own = document.querySelector("#legend, .legend");
  const used = new Set([...document.querySelectorAll("[data-wnq]")].map((e) => e.dataset.wnq));
  if (!used.size) return;
  const el = document.createElement("div"); el.className = "wn-legend"; el.setAttribute("aria-label", "Number labels");
  el.innerHTML = [...Object.keys(LABELS)].filter((l) => used.has(l)).map((l) => `<span title="${LABELS[l]}"><i class="wn-dot wn-dot-${l.toLowerCase()}"></i>${l.toLowerCase()}</span>`).join("");
  if (own) { own.replaceWith(el); el.id = own.id || ""; return; }
  const main = document.querySelector("main") || document.body;
  main.prepend(el);
}

let pop;
function openPop(card) {
  pop ??= Object.assign(document.createElement("div"), { className: "wn-pop-scrim", innerHTML: `<div class="wn-pop" role="dialog" aria-modal="true" aria-labelledby="wn-pop-t"><button class="wn-pop-x" aria-label="Close">×</button><div class="wn-pop-b"></div></div>` });
  if (!pop.isConnected) {
    document.body.append(pop);
    pop.addEventListener("click", (e) => { if (e.target === pop || e.target.closest(".wn-pop-x")) closePop(); });
    document.addEventListener("keydown", (e) => { if (e.key === "Escape") closePop(); });
  }
  const d = card.dataset, full = card.dataset.wnFull || card.innerHTML;
  const labels = [...new Set([...card.querySelectorAll("[data-wnq]")].map((e) => e.dataset.wnq))];
  const title = card.querySelector(".label, .lvl, .name, .hotspot-stat-label, .eulink-metric-label, .metatools-label, .lv-h b, h3, h4")?.textContent?.trim() || "Details";
  pop.querySelector(".wn-pop-b").innerHTML = `<h3 id="wn-pop-t">${title}</h3>
    <div class="wn-pop-full">${full}</div>
    ${labels.length ? `<p class="wn-pop-lab">${labels.map((l) => `<b>${l}</b>: ${LABELS[l]}`).join("<br>")}</p>` : ""}
    ${d.explainSource ? `<p class="wn-pop-src">Source: <code>${d.explainSource}</code></p>` : ""}`;
  pop.classList.add("on"); pop.querySelector(".wn-pop-x").focus();
}
function closePop() { pop?.classList.remove("on"); }

function bento(root) {
  for (const b of BENTO) root.querySelectorAll(b.sel).forEach((c) => {
    if (c.dataset.wnBento) return;
    c.dataset.wnBento = "1"; c.classList.add("wn-bento");
    if (b.hide) c.querySelectorAll(b.hide).forEach((h) => h.classList.add("wn-detail"));
    if (b.own) return; // these cards already open their own explanation popup
    c.dataset.wnFull = (b.full && c.closest(b.full)?.innerHTML) || c.innerHTML;
    c.setAttribute("role", "button"); c.setAttribute("tabindex", "0"); c.setAttribute("aria-haspopup", "dialog");
    c.addEventListener("click", (e) => { if (e.target.closest("a, button, audio, summary, .wn-dot")) return; e.stopPropagation(); openPop(c); });
    c.addEventListener("keydown", (e) => { if ((e.key === "Enter" || e.key === " ") && e.target === c) { e.preventDefault(); openPop(c); } });
  });
}

// Fresh-eyes round 1: dim stub steps, hide empty PENDING rows on Compare.
function trim(root) {
  root.querySelectorAll(".st, .bdg").forEach((e) => {
    if (e.textContent.trim() === "stub, not wired") e.closest(".node, .card, article, li, [class*=node]")?.classList.add("wn-stub");
  });
  const cmp = root.querySelector("#pReport");
  if (!cmp) return;
  let n = 0;
  cmp.querySelectorAll("tr, li, .row, div").forEach((r) => {
    if (r.dataset.wnPend || r.children.length < 2 || r.children.length > 3 || r.querySelector("div div")) return;
    if ([...r.children].some((c) => c.textContent.trim() === "PENDING")) { r.dataset.wnPend = "1"; r.hidden = true; n++; }
  });
  if (n && !cmp.querySelector(".wn-pend-note")) {
    const p = document.createElement("p"); p.className = "wn-pend-note"; p.textContent = `${n} pending checks hidden until they have data.`;
    cmp.querySelector("[data-wn-pend]")?.parentElement?.append(p);
  }
}

export function applyBento(root = document) { bento(root); quiet(root); quietInline(root); trim(root); legend(); }

if (typeof document !== "undefined" && typeof MutationObserver !== "undefined") {
  let t; const run = () => { clearTimeout(t); t = setTimeout(() => applyBento(document), 60); };
  new MutationObserver(run).observe(document.documentElement, { childList: true, subtree: true });
  run();
}
