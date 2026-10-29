// Round 7 views for the new dashboard: Overview, the Power/Cost/Water
// comparison cards, the "Where should this model run?" head-to-head, and the
// info-panel texts. Numbers come only from the run record; anything the
// record does not hold reads "Measured in the long run".
import { popup, registerInfo, infoBtn, tip, lineChart, donut, gauge, workedExample, wireR7, KWH_PER_J } from "./r7.js";

const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const fmt = (v, d = 2) => (v == null || Number.isNaN(+v) ? "–" : (+v).toLocaleString("en-US", { maximumFractionDigits: d }));
const CLS = { MEASURED: "n-measured", ESTIMATE: "n-estimate", SIMULATED: "n-simulated", MOCK: "n-mock", REPLAY: "n-replay" };
const n = (text, tag) => `<span class="${CLS[tag] || ""}" title="${tag || ""}">${text}</span>`;
const PENDING = "Measured in the long run";
export const legend = () => `<div class="r7legend" role="note" aria-label="Number colours"><span><i style="background:var(--r7-blue)"></i>measured</span><span><i style="background:var(--r7-gold)"></i>estimate</span><span><i style="background:var(--r7-mauve)"></i>simulated</span><span><i style="background:#8FBBA0"></i>replay</span><span><i style="background:var(--r7-green)"></i>with WattNot</span><span><i style="background:var(--r7-red)"></i>without WattNot</span></div>`;

// ---------- the three places a model can run (always all three) ----------
export const OPTIONS = [
  { id: "own", name: "Your GPU", sub: "Muse Spark Open · adaptive top-k + tiered / k-means experts + quantization", partner: "E2E Networks · NVIDIA" },
  { id: "baseten", name: "Baseten hosted", sub: "Muse Spark Open on Baseten, paid per answer", partner: "Baseten" },
  { id: "modal", name: "Modal hosted", sub: "Muse Spark Open on Modal, paid per GPU-second", partner: "Modal" },
];
// Real data only where it exists. RUN1 measured tiered experts as a tie with
// uniform 4-bit quantization; nothing else is measured for these three yet.
const CELLS = {
  quality: { own: { text: "Tie with uniform 4-bit", tag: "MEASURED", note: "RUN1 tiered-experts test: tiered placement matched plain uniform 4-bit quantization; no gain or loss." } },
};
const ROWS = [
  { id: "quality", name: "Quality", tip: "Share of answers that agree with the full-precision model on a fixed test set." },
  { id: "cost", name: "Cost per 1k answers", tip: "Dollars for one thousand answers: electricity on your GPU, the bill on a host." },
  { id: "energy", name: "Energy per answer", tip: "Joules for one answer. Measured on your GPU; an estimate for hosts, which do not share meters." },
  { id: "p99", name: "p99 latency", tip: "The time 99 of 100 answers beat." },
  { id: "privacy", name: "Privacy / data control", tip: "Where prompts go and who can see them." },
];
const PRIVACY = { own: "Prompts stay on your machine", baseten: "Prompts go to Baseten", modal: "Prompts go to Modal" };
// Bound to the run record's placements[] and routing (backend: spark_open/h2h.py
// placements() and spark_open/router.py attach()). Ids there are local/baseten/modal.
const PID = { own: "local", baseten: "baseten", modal: "modal" };
const FIELD = { quality: ["pass_pct", (v) => `${fmt(v, 1)}%`], cost: ["usd_per_1k", (v) => `$${fmt(v, 3)}`], energy: ["wh_per_1k", (v) => `${fmt(v, 2)} Wh / 1k`], p99: ["p99_s", (v) => `${fmt(v, 2)} s`] };
const tagOf = (l) => { const u = String(l || "").toUpperCase(); return Object.keys(CLS).find((k) => u.includes(k)) || u; };
export const placement = (rec, opt) => (rec?.placements || []).find((p) => p.id === PID[opt]);
export function routing(rec) {
  const ds = rec?.routing?.decisions || [];
  return { decisions: ds, chosen: ds.find((d) => d.chosen)?.chosen || null };
}
function cell(row, opt, rec) {
  if (row === "privacy") return `<span class="wr-priv">${esc(PRIVACY[opt])}</span>`;
  const p = placement(rec, opt), f = FIELD[row];
  if (p && p.status === "not_run") return `<span class="r7miss">Not run</span><small>${esc(p.why)}</small>`;
  if (p && f && p[f[0]]?.value != null) return `${n(f[1](p[f[0]].value), tagOf(p[f[0]].label))}<small>${esc(p[f[0]].label)}</small>`;
  const c = CELLS[row]?.[opt];
  if (c) return `${n(esc(c.text), c.tag)}<small>${esc(c.note)}</small>`;
  const est = row === "energy" && opt !== "own";
  return `<span class="r7miss">${PENDING}</span>${est ? `<small>Will be an ${n("estimate", "ESTIMATE")}: hosts do not share power meters.</small>` : ""}`;
}
function pickHtml(rec) {
  const { decisions } = routing(rec);
  if (!decisions.length) return `<h3>WattNot chose: <span class="r7miss">pending the long run</span></h3>
      <p>After the long run WattNot picks the best place for each kind of work and says why, for example "WattNot chose Your GPU for batch because it was cheapest and passed all gates".</p>
      <ul class="wr-gates">${["Quality gate (power cap: pass rate within 1 pt of uncapped; top-k: 95%+ agreement)", "Speed gate (p99 within the limit)", "Budget gate"].map((g) => `<li><span class="r7miss">${esc(g)}: ${PENDING}</span></li>`).join("")}</ul>`;
  const name = (id) => OPTIONS.find((o) => PID[o.id] === id)?.name || id;
  return decisions.map((d) => `<h3>${esc(d.workload?.name || "")}: WattNot chose ${d.chosen ? esc(name(d.chosen)) : "nothing"}</h3>
      <p>because ${esc(d.reason)}${d.rule ? ` <small>(rule: ${esc(d.rule)})</small>` : ""}</p>
      <ul class="wr-gates">${(d.candidates || []).map((c) => `<li>${esc(name(c.id))}: ${c.status === "excluded" ? `failed the gate, ${esc(c.why)}` : c.status === "chosen" ? "passed, chosen" : "passed"}</li>`).join("")}${(d.flags || []).map((f) => `<li>${esc(f)}</li>`).join("")}</ul>`).join("");
}
export function whereHtml(rec) {
  return `<div class="wr">
    <p class="wr-lead">All three are compared in every run. ${infoBtn("tiered", "tiered and k-means experts")}${infoBtn("router", "the per-workload router")}</p>
    <table class="wr-t"><thead><tr><th></th>${OPTIONS.map((o) => `<th scope="col"><b>${esc(o.name)}</b><small>${esc(o.sub)}</small><span class="ptag">${esc(o.partner)}</span></th>`).join("")}</tr></thead>
    <tbody>${ROWS.map((r) => `<tr><th scope="row"><span${tip(r.tip)}>${esc(r.name)}</span></th>${OPTIONS.map((o) => `<td${routing(rec).chosen === PID[o.id] ? ' class="pick"' : ""}>${cell(r.id, o.id, rec)}</td>`).join("")}</tr>`).join("")}</tbody></table>
    <div class="wr-pick glass">${pickHtml(rec)}</div></div>`;
}
const OBF = { power: ["wh_per_1k", (v) => `${fmt(v, 2)} Wh / 1k`], cost: ["usd_per_1k", (v) => `$${fmt(v, 3)} / 1k`], water: ["wh_per_1k", (v) => `${fmt(v * 0.36 / 1000, 4)} L / 1k`] };
function optionBars(metric, rec) {
  const ch = routing(rec).chosen, f = OBF[metric];
  const val = (o) => { const p = placement(rec, o.id); if (!p) return `<span class="r7miss">${PENDING}</span>`; if (p.status === "not_run") return `<span class="r7miss" title="${esc(p.why)}">Not run</span>`; const m = p[f[0]]; return m?.value != null ? n(f[1](m.value), tagOf(m.label)) : `<span class="r7miss">not measured</span>`; };
  return `<div class="ob"><div class="lvl">By place to run ${infoBtn("router", "the router")}</div>${OPTIONS.map((o) => `<div class="ob-row${ch === PID[o.id] ? " pick" : ""}"><span>${esc(o.name)}${ch === PID[o.id] ? " · chosen" : ""}</span>${val(o)}</div>`).join("")}
    <p class="r7note">The option WattNot picks will be highlighted here. ${metric === "power" ? "" : ""}Hosted energy will be an estimate.</p></div>`;
}

// ---------- comparison cards ----------
function series(rec, f) {
  const b = (rec?.phases?.baseline?.events || []).filter((e) => e.j_per_req != null);
  const w = (rec?.phases?.wattnot?.events || []).filter((e) => e.j_per_req != null);
  return [
    { name: "Without WattNot", cls: "without", pts: b.map((e, i) => ({ x: i + 1, y: f(e.j_per_req), label: `repeat ${i + 1}` })) },
    { name: "With WattNot", cls: "with", pts: w.map((e, i) => ({ x: i + 1, y: f(e.j_per_req), label: `repeat ${i + 1}` })) },
  ];
}
function cardsData(rec, price, water) {
  const b = rec?.phases?.baseline?.metrics, w = rec?.phases?.wattnot?.metrics;
  const bj = b?.j_per_req, wj = w?.j_per_req, ok = bj != null && wj != null;
  const k1m = (j) => j * 1e6 * KWH_PER_J;
  return [
    { id: "power", title: "Power", tag: "MEASURED", unit: "J per answer",
      saved: ok ? `${fmt(((bj - wj) / bj) * 100, 1)}%` : null, sub: ok ? `${fmt(bj, 1)} → ${fmt(wj, 1)} J per answer` : null, f: (j) => j },
    { id: "cost", title: "Cost", tag: null, unit: "$ per 1M answers",
      saved: ok ? `$${fmt((k1m(bj) - k1m(wj)) * price, 2)}` : null, sub: ok ? `per million answers · $${fmt(k1m(bj) * price)} → $${fmt(k1m(wj) * price)}` : null, f: (j) => k1m(j) * price },
    { id: "water", title: "Water", tag: null, unit: "L per 1M answers",
      saved: ok ? `${fmt((k1m(bj) - k1m(wj)) * water, 2)} L` : null, sub: ok ? `per million answers · ${fmt(k1m(bj) * water)} → ${fmt(k1m(wj) * water)} L` : null, f: (j) => k1m(j) * water },
  ];
}
const METHODS = [["caps", "Power caps"], ["topk", "Adaptive top-k"], ["tiered", "Tiered / k-means experts"], ["gate", "Quality gates"], ["waste", "Agent-waste saver"], ["router", "Where-to-run router"], ["cache", "Prompt cache"], ["quant", "Quantization (tiered / k-means experts)"], ["baseten", "Baseten (hosted)"], ["modal", "Modal (hosted)"]];
export function compareHtml(rec, price = 0.1387, water = 0.36) {
  const cards = cardsData(rec, price, water);
  return `<div class="cmp-head"><div><h1>What WattNot saved</h1><p class="lead">${esc(rec?.run_name || "Pick a run")} ${infoBtn("compare", "how this is compared")}</p></div></div>${metaInRun(rec)}${legend()}
  <div class="cmp-cards">${cards.map((c) => `<section class="cmp glass" data-cmp="${c.id}">
    <header><h2 class="cmp-t">${c.title}</h2>${infoBtn(`calc-${c.id}`, `${c.title} calculation`)}</header>
    <div class="cmp-saved"><span class="lvl">Saved</span><span class="cmp-v">${c.saved ? n(esc(c.saved), c.tag) : `<span class="r7miss">${PENDING}</span>`}</span></div>
    <p class="cmp-sub">${esc(c.sub || "")}</p>
    <button class="btn cmp-dd" aria-expanded="false" aria-controls="cmpx-${c.id}">Show over the run ▾</button>
    <div class="cmp-x" id="cmpx-${c.id}" hidden>
      <button class="cmp-chart" data-pop="chart-${c.id}" aria-label="Open the ${c.title} graph in a window">${lineChart(series(rec, c.f), { unit: c.unit, yLabel: c.unit, xLabel: "Repeat (per-repeat RUN2 data; the long run adds a time series)", w: 520, h: 220, y0: 0, xticks: [1, 2, 3].map((x) => ({ x, label: `#${x}` })), aria: `${c.title} with and without WattNot` })}</button>
      <div class="lvl">By method</div>
      <ul class="cmp-m">${METHODS.map(([id, name]) => `<li><button class="linkish" data-pop="m-${c.id}-${id}">${esc(name)}</button><span>${id === "caps" && c.saved ? n(`${esc(c.saved)} (whole run)`, c.tag) : `<span class="r7miss">not measured in this run</span>`}</span></li>`).join("")}</ul>
      ${optionBars(c.id, rec)}
    </div></section>`).join("")}</div>
  <section class="cmp glass cmp-where"><header><h2 class="cmp-t">Where should this model run?</h2>${infoBtn("router", "the router")}</header>
    <p class="cmp-sub">Your GPU vs Baseten vs Modal, compared on every run.</p><button class="btn" data-pop="where">Open the head-to-head</button></section>`;
}
export function wireCompare(root, rec, price = 0.1387, water = 0.36) {
  const cards = Object.fromEntries(cardsData(rec, price, water).map((c) => [c.id, c]));
  root.querySelectorAll(".cmp-dd").forEach((b) => (b.onclick = () => {
    const x = document.getElementById(b.getAttribute("aria-controls")); const open = x.hidden;
    x.hidden = !open; b.setAttribute("aria-expanded", String(open)); b.textContent = open ? "Hide ▴" : "Show over the run ▾";
  }));
  root.querySelectorAll("[data-pop]").forEach((b) => (b.onclick = () => {
    const k = b.dataset.pop;
    if (k === "where") return popup("Where should this model run?", whereHtml(rec), { sub: "All three are compared in this run." });
    if (k.startsWith("chart-")) return openPowerGraph(rec, cards[k.slice(6)]);
    const [, cid, mid] = k.split("-"); methodPopup(rec, cid, mid, price, water);
  }));
}
const HOW = {
  caps: "Lower the GPU's watt limit and keep the lowest cap that passes the quality and speed gates. Own GPU only: hosted platforms give no GPU access.",
  topk: "Send each word to fewer experts when one or two carry almost all the weight.",
  tiered: "Keep busy experts on the GPU at higher precision, quiet ones at lower precision or in CPU RAM.",
  gate: "Refuse any setting that drops answer quality or slows p99 past the limit; its 'saving' is the risk it blocked.",
  waste: "Stop agent loops that call the model again without new information.",
  router: "Send each kind of work to the place (your GPU, Baseten or Modal) that is cheapest and passes the gates.",
  cache: "Reuse a repeated prompt prefix instead of computing it again.",
  quant: "Store the model's experts in fewer bits: tiered keeps busy experts at higher precision; k-means groups experts used together. RUN1 found tiered placement tied plain uniform 4-bit (no gain, no loss).",
  baseten: "Run the same open model on Baseten and pay per answer. Baseten does not share power meters, so its energy is an estimate.",
  modal: "Run the same open model on Modal and pay per GPU-second. Energy is an estimate.",
};
// Minimal radial cards: only power caps has a measured share in this run.
export const CHILDREN = [["caps", "Power caps"], ["topk", "Adaptive top-k"], ["quant", "Quantization"], ["baseten", "Baseten (hosted)"], ["modal", "Modal (hosted)"], ["gate", "Quality gate"], ["waste", "Agent-waste saver"], ["sam", "Cooling hotspots (SAM 3.1)"]];
// Children that only make sense under one branch.
export const CHILD_ONLY = { sam: "power" };
export const DERIVED_HINT = "Derived from power: same % because dollars and litres scale linearly with kWh (ESTIMATE).";
export function savedPct(rec, branch) {
  const b = rec?.phases?.baseline?.metrics?.j_per_req, w = rec?.phases?.wattnot?.metrics?.j_per_req;
  if (b == null || w == null) return null;
  return { text: `${fmt(((b - w) / b) * 100, 1)}%`, tag: branch === "power" ? "MEASURED" : "ESTIMATE" };
}
// Hosted A/B (spark_open/hosted_ab.py): rec.hosted_ab.hosts.<baseten|modal>
export const hostAB = (rec, host) => { const h = rec?.hosted_ab?.hosts?.[host]; return h && h.status === "ran" ? h : null; };
export function childSaved(rec, branch, mid) {
  if (mid === "caps") return savedPct(rec, branch);
  // SAM room scan: only a MEASURED hotspot saving counts; the floor-plan heat is simulated.
  if (mid === "sam") { const v = rec?.room?.hotspot_saved_pct; return v?.label === "MEASURED" && v.value != null ? { text: `${fmt(v.value, 1)}%`, tag: "MEASURED" } : null; }
  if (mid === "baseten" || mid === "modal") {
    const h = hostAB(rec, mid); if (!h) return null;
    if (branch !== "cost") { const p = h.estimate?.power_saved_pct?.value; return p == null ? null : { text: `${fmt(p, 1)}%`, tag: "ESTIMATE" }; }
    const a = h.without?.usd_per_1k?.value, b = h.with?.usd_per_1k?.value;
    if (!a) return null;
    return { text: `${fmt((100 * (a - b)) / a, 1)}%`, tag: "MEASURED" };
  }
  return null;
}
// ---------- Compare tree ----------
// Power's branches as a nested tree. Cost and Water reuse the SAME shape; their
// values are derived from Power (dollars and litres scale linearly with kWh).
// Each node: id, name, optional kids, kind ("gate" shows pass/fail not a
// saving), own_only (own-GPU-only lever). A card shows only its name, one value
// with a label, and an info button; clicking a card opens the next ring.
export const TREE = [
  { id: "waste", name: "Agent-waste saver", kids: [
    { id: "loopstop", name: "Loop stopper" },
    { id: "smallfirst", name: "Small model first" },
    { id: "context", name: "Context saver", kids: [
      { id: "prefix", name: "Prefix cache", own_only: true },
      { id: "memcomp", name: "Memory compression" },
      { id: "dupcut", name: "Duplicate cut" },
    ] },
  ] },
  { id: "where", name: "Where it runs", kids: [
    { id: "own", name: "Your GPU", kids: [
      { id: "caps", name: "Power caps", own_only: true, kids: [
        { id: "capsweep", name: "Fixed cap sweep" },
        { id: "clocks", name: "Phase-aware clocks" },
      ] },
      { id: "topk", name: "Adaptive top-k / tiered experts", own_only: true },
      { id: "quant", name: "Quantization", own_only: true },
    ] },
    { id: "baseten", name: "Baseten", host: "baseten", kids: [
      { id: "bt_routing", name: "Routing", host: "baseten", lever: "routing" },
      { id: "bt_gate", name: "Quality gate", kind: "gate", host: "baseten" },
      { id: "bt_waste", name: "Agent-waste saver", host: "baseten", lever: "waste" },
    ] },
    { id: "modal", name: "Modal", host: "modal", kids: [
      { id: "md_routing", name: "Routing", host: "modal", lever: "routing" },
      { id: "md_gate", name: "Quality gate", kind: "gate", host: "modal" },
      { id: "md_waste", name: "Agent-waste saver", host: "modal", lever: "waste" },
    ] },
  ] },
  { id: "gate", name: "Quality gate", kind: "gate", kids: [
    { id: "q", name: "Quality", kind: "gate" },
    { id: "speed", name: "Speed (p99)", kind: "gate" },
    { id: "budget", name: "Budget gates", kind: "gate" },
  ] },
  { id: "sam", name: "Cooling hotspots (SAM 3.1)" },
];
// The kids at a path (array of ids from the branch root). Empty path -> the top
// level. Returns [] for a leaf or an unknown path.
export function treeChildren(path = []) {
  let kids = TREE;
  for (const id of path) {
    const n = kids.find((k) => k.id === id);
    if (!n || !n.kids) return [];
    kids = n.kids;
  }
  return kids;
}
// Find a node by id anywhere in the tree (first match).
export function treeFind(id, kids = TREE) {
  for (const k of kids) { if (k.id === id) return k; const f = k.kids && treeFind(id, k.kids); if (f) return f; }
  return null;
}
// The context saver's recorded numbers, read defensively from wherever a later
// build puts them. Returns the object or null (then the UI shows "–" pending).
export function contextSaver(rec) {
  return rec?.context_saver || rec?.receipt?.context_saver || rec?.phases?.wattnot?.context_saver || null;
}
// Gate pass/fail from the run's own phases. Returns {pass} or null (not run).
export function gateStatus(rec, id = "gate") {
  const m = rec?.phases?.wattnot?.metrics, b = rec?.phases?.baseline?.metrics;
  if (!m) return null;
  const q = m.quality_pct != null && (b?.quality_pct == null || m.quality_pct >= b.quality_pct - 1);
  const s = m.p99_change_pct != null && m.p99_change_pct <= 10;
  if (id === "q") return m.quality_pct == null ? null : { pass: q };
  if (id === "speed") return m.p99_change_pct == null ? null : { pass: s };
  if (id === "budget") return null; // no budget data in the run record yet
  return m.quality_pct == null || m.p99_change_pct == null ? null : { pass: q && s };
}
// A hosted API-lever's value (Baseten/Modal routing or agent-waste saver). The
// saving is a cost saving: MEASURED on the cost branch, a derived ESTIMATE on
// power and water. Null (pending) when the host did not run.
export function hostLeverValue(rec, host, lever, branch) {
  const h = hostAB(rec, host); if (!h) return null;
  const s = h.levers?.[lever]?.saved_pct; if (s?.value == null) return null;
  return { text: `${fmt(s.value, 1)}%`, tag: branch === "cost" ? (s.label || "MEASURED") : "ESTIMATE" };
}
// A tree node's value for one branch. Returns {text, tag} (a saving),
// {gate:true, pass} (a quality/speed gate), or null (pending, shows "–"). The
// SAME tree feeds power, cost and water; gate nodes read the same pass/fail
// under all three, and hosted items are cost-MEASURED, power/water-ESTIMATE.
export function treeValue(rec, branch, node) {
  const id = node?.id;
  if (node?.kind === "gate") {
    if (node.host) { const h = hostAB(rec, node.host); return h == null ? null : { gate: true, pass: !!h.gate_passed }; }
    const g = gateStatus(rec, id); return g == null ? null : { gate: true, pass: g.pass };
  }
  if (node?.host && node.lever) return hostLeverValue(rec, node.host, node.lever, branch);
  if (id === "caps" || id === "own") return savedPct(rec, branch);
  if (id === "baseten" || id === "modal") return childSaved(rec, branch, id);
  if (id === "sam") return childSaved(rec, "power", "sam");
  if (id === "context" || id === "prefix" || id === "memcomp" || id === "dupcut") {
    const cs = contextSaver(rec); if (!cs) return null;
    const part = id === "context" ? cs : (cs.parts?.[id] || cs[id]);
    const v = part?.tokens_cut_pct ?? part?.tokens_saved_pct;
    const label = v?.label || part?.label || cs.label || "ESTIMATE";
    if (v?.value != null) return { text: `${fmt(v.value, 1)}%`, tag: label };
    if (typeof v === "number") return { text: `${fmt(v, 1)}%`, tag: label };
    return null;
  }
  return null; // owngpu/waste/where/topk/quant/... not attributed in this run
}
const OWN_ONLY = { caps: "Power caps", quant: "Quantization", topk: "Adaptive top-k / tiered experts" };
export function hostedChain(e) {
  if (!e) return "";
  return `<li>Tokens saved: ${n(fmt(e.tokens_saved?.value, 0), e.tokens_saved?.label || "ESTIMATE")} (with vs without, ${esc(e.basis || "")}; scaled up from ${esc(String(e.n_per_arm ?? "a few"))} calls per arm).</li>
    <li>× ${n(fmt(e.j_per_token?.value, 2), "ESTIMATE")} J/token = ${n(fmt(e.energy_j?.value, 0), "ESTIMATE")} J. Assumption: ${esc(e.j_per_token?.method || "")}; energy per token scales with active parameters.</li>
    <li>÷ 3,600,000 = ${n(fmt(e.energy_kwh?.value, 5), "ESTIMATE")} kWh.</li>
    <li>× 0.36 L/kWh = ${n(fmt(e.water_l?.value, 5), "ESTIMATE")} L water.</li>
    <li>Cost saved: ${n(`$${fmt(e.usd_saved?.value, 4)}`, "MEASURED")} (tokens × list price).</li>`;
}
function hostedDetail(rec, host) {
  const h = hostAB(rec, host), raw = rec?.hosted_ab?.hosts?.[host];
  const own = `<h3>Own GPU only</h3><ul class="wr-gates">${Object.values(OWN_ONLY).map((x) => `<li>${esc(x)}: <span class="r7miss">own GPU only</span>, hosted platforms give no GPU access</li>`).join("")}</ul>`;
  if (!h) return `<p><b>${raw?.status === "not_run" || raw?.status === "stopped" ? `Not run: ${esc(raw.why)}` : "Measured in the long run."}</b> The same prompts run on ${esc(host === "modal" ? "Modal" : "Baseten")} once without and once with WattNot's API levers: routing, the quality gate and the agent-waste saver.</p>${own}`;
  const L = h.levers || {}, v = (x) => (x?.value == null ? `<span class="r7miss">—</span>` : n(`${fmt(x.value, 1)}%`, x.label));
  const wo = h.without, wi = h.with;
  const mock = rec?.hosted_ab?.mode === "MOCK" ? `<p class="r7miss"><b>MOCK</b> dry-run data, no real endpoint was called.</p>` : "";
  return `${mock}<h3>Per lever</h3><ul class="wr-gates">
    <li><b>Routing:</b> ${v(L.routing?.saved_pct)} cost saved${L.routing?.note ? ` · ${esc(L.routing.note)}` : ""}</li>
    <li><b>Agent-waste saver:</b> ${v(L.waste?.saved_pct)} cost saved · ${esc(L.waste?.calls_without)} → ${esc(L.waste?.calls_with)} calls</li>
    <li><b>Quality gate:</b> ${h.gate_passed ? "passed" : "failed"} · ${n(`${fmt(wi?.pass_pct?.value, 1)}%`, "MEASURED")} with vs ${fmt(wo?.pass_pct?.value, 1)}% without</li></ul>
    <h3>Worked example</h3><ol class="wex"><li>Cost per 1k answers: $${fmt(wo?.usd_per_1k?.value, 4)} without → $${fmt(wi?.usd_per_1k?.value, 4)} with (${n("measured", "MEASURED")}, tokens × list price).</li>
    ${hostedChain(h.estimate)}</ol>${own}`;
}
export function gatesHtml(rec) {
  const m = rec?.phases?.wattnot?.metrics, b = rec?.phases?.baseline?.metrics;
  if (!m) return `<p class="r7miss">No gate results in this record.</p>`;
  const row = (name, ok, v) => `<li><b>${name}:</b> ${ok == null ? `<span class="r7miss">not measured in this run</span>` : `${ok ? "passed" : "failed"} · ${v}`}</li>`;
  const q = m.quality_pct, qb = b?.quality_pct, p = m.p99_change_pct;
  return `<ul class="wr-gates">${row("Quality gate", q == null ? null : qb == null || q >= qb - 1, `${n(`${fmt(q, 1)}%`, "MEASURED")} vs ${fmt(qb, 1)}% without`)}${row("Speed gate (p99 within +10%)", p == null ? null : p <= 10, `${n(`${p > 0 ? "+" : ""}${fmt(p, 1)}%`, "MEASURED")}`)}</ul>`;
}
// Every method opens its own detail with a worked example. Only power caps has
// a measured number in this run; the others show the calculation with an
// illustrative 10% saving, clearly marked as an example, not a result.
export function methodPopup(rec, cid, mid, price = 0.1387, water = 0.36) {
  const c = cardsData(rec, price, water).find((x) => x.id === cid), m = METHODS.find(([i]) => i === mid);
  if (!c || !m) return null;
  const bj = rec?.phases?.baseline?.metrics?.j_per_req, wj = rec?.phases?.wattnot?.metrics?.j_per_req;
  if (mid === "baseten" || mid === "modal") return popup(`${c.title} · ${m[1]}`, `<div class="mp"><p class="lead">${esc(HOW[mid] || "")}</p>${hostedDetail(rec, mid)}</div>`, { sub: esc(rec?.run_name || "") });
  const measured = mid === "caps" && c.saved;
  const body = measured
    ? `<p>This run records power caps as the method, so the whole-run saving is shown: <b>${n(esc(c.saved), c.tag)}</b>. It is not split across methods.</p><h3>Worked example (this run's numbers)</h3>${workedExample(bj, wj, price, water)}<h3>Gate results</h3>${gatesHtml(rec)}`
    : `<p><b>Not measured in this run.</b> The record does not attribute any ${esc(c.title.toLowerCase())} saving to ${esc(m[1])}. The long run records each method on its own.</p><h3>Worked example (illustrative 10%, not a result)</h3>${bj != null ? workedExample(bj, bj * 0.9, price, water) : "<p class=\"r7miss\">No baseline in this record.</p>"}`;
  return popup(`${c.title} · ${m[1]}`, `<div class="mp"><p class="lead">${esc(HOW[mid] || "")}</p>${body}</div>`, { sub: esc(rec?.run_name || "") });
}
// Radial compare: one branch's card content (graph, per-method, per-place) in a popup.
export function branchPopup(rec, id, price = 0.1387, water = 0.36) {
  const c = cardsData(rec, price, water).find((x) => x.id === id); if (!c) return null;
  const html = `<div class="cmp bp" data-cmp="${c.id}">
    <div class="cmp-saved"><span class="lvl">Saved</span><span class="cmp-v">${c.saved ? n(esc(c.saved), c.tag) : `<span class="r7miss">${PENDING}</span>`}</span>${infoBtn(`calc-${c.id}`, `${c.title} calculation`)}</div>
    <p class="cmp-sub">${esc(c.sub || "")}</p>
    <button class="cmp-chart" data-pop="chart-${c.id}" aria-label="Open the ${c.title} graph bigger">${lineChart(series(rec, c.f), { unit: c.unit, yLabel: c.unit, xLabel: "Repeat (per-repeat RUN2 data; the long run adds a time series)", w: 760, h: 280, y0: 0, xticks: [1, 2, 3].map((x) => ({ x, label: `#${x}` })), aria: `${c.title} with and without WattNot` })}</button>
    <div class="bp-cols"><div><div class="lvl">By method</div>
    <ul class="cmp-m">${METHODS.map(([mid, name]) => `<li><button class="linkish" data-pop="m-${c.id}-${mid}">${esc(name)}</button><span>${mid === "caps" && c.saved ? n(`${esc(c.saved)} (whole run)`, c.tag) : `<span class="r7miss">not measured in this run</span>`}</span></li>`).join("")}</ul></div>
    <div>${optionBars(c.id, rec)}</div></div>${metaInRun(rec)}</div>`;
  return popup(c.title, html, { sub: `${esc(rec?.run_name || "")} · red is without WattNot, green is with`, onMount: (root) => wireCompare(root, rec, price, water) });
}
const BEXP = { power: "Energy per answer, in joules, with and without WattNot on the same prompts.", cost: "Electricity cost of a million answers, from the measured joules at the run's price per kWh. Derived from power: the % is the same as Power because dollars scale linearly with kWh, so it is an ESTIMATE, not a separate result.", water: "Cooling water for a million answers, from the measured joules at 0.36 L per kWh. Derived from power: the % is the same as Power because litres scale linearly with kWh, so it is an ESTIMATE, not a separate result." };
export function branchInfo(rec, id, price = 0.1387, water = 0.36) {
  const c = cardsData(rec, price, water).find((x) => x.id === id); if (!c) return null;
  const bj = rec?.phases?.baseline?.metrics?.j_per_req, wj = rec?.phases?.wattnot?.metrics?.j_per_req;
  const html = `<div class="bp"><p class="lead">${esc(BEXP[id])}</p>
    <div class="cmp-saved"><span class="lvl">Saved</span><span class="cmp-v">${c.saved ? n(esc(c.saved), c.tag) : `<span class="r7miss">—</span>`}</span></div><p class="cmp-sub">${esc(c.sub || "")}</p>
    <button class="cmp-chart" data-pop="chart-${c.id}" aria-label="Open the graph bigger">${lineChart(series(rec, c.f), { unit: c.unit, yLabel: c.unit, xLabel: "Repeat (per-repeat RUN2 data; the long run adds a time series)", w: 760, h: 260, y0: 0, xticks: [1, 2, 3].map((x) => ({ x, label: `#${x}` })), aria: `${c.title} with and without WattNot` })}</button>
    <h3>Worked example</h3>${workedExample(bj, wj, price, water)}<h3>Gate results</h3>${gatesHtml(rec)}${metaInRun(rec)}</div>`;
  return popup(c.title, html, { sub: `${esc(rec?.run_name || "")} · red is without WattNot, green is with`, onMount: (root) => wireCompare(root, rec, price, water) });
}
export const cmpHeadHtml = (rec, runs = [], selId = "") => {
  const opts = (runs || []).map((r) => `<option value="${esc(r.id)}" ${r.id === selId ? "selected" : ""}>${esc(r.name)}</option>`).join("");
  return `<div class="cmp-head"><h1>What WattNot saved</h1><div class="cmp-run-wrap"><select id="cmpRunSel" class="btn cmp-run-sel" aria-label="Select run"><option value="" disabled ${!selId ? "selected" : ""}>Select run…</option>${opts}${bundledRecord(rec) || rec ? `<option value="${BUNDLED_ID}" ${selId === BUNDLED_ID ? "selected" : ""}>Bundled sample · RUN2</option>` : ""}</select></div><span class="lead">${esc(rec?.run_name || "Pick a run")} ${infoBtn("compare", "how this is compared")}</span></div><div class="cmp-meta-bar">${metaInRun(rec)}</div><div class="cmp-legend-bar">${legend()}</div>`;
};
export function openWhere(rec) { return popup("Where should this model run?", whereHtml(rec), { sub: "All three are compared in this run." }); }
export function openPowerGraph(rec, c) {
  c ||= cardsData(rec, 0.1387, 0.36)[0];
  const html = `<div class="pg">${lineChart(series(rec, c.f), { unit: c.unit, yLabel: c.unit, xLabel: "Repeat", w: 980, h: 440, y0: 0, xticks: [1, 2, 3].map((x) => ({ x, label: `repeat ${x}` })), aria: `${c.title} with and without WattNot` })}
    <p class="r7note">Now: RUN2 per-repeat averages (3 repeats each side, ${esc(rec?.model || "")}), ${c.tag === "MEASURED" ? "measured" : "derived from measured joules, an estimate"}. The same chart takes a watts-over-time series: the long H100 run logs one sample every 100 ms, and each point becomes one sample.</p></div>`;
  return popup(`${c.title}: with vs without WattNot`, html, { pan: true, sub: "Drag to pan, scroll to zoom. Hover a point for its value." });
}

// ---------- Meta tools really used (checked against the repo) ----------
export const META_TOOLS = [
  { id: "spark", name: "Muse Spark", role: "Plans the next cap to try in the sweep" },
  { id: "wonder", name: "Muse Wonder", role: "Supervisor: reviewed the measured RUN2 sweep live once; local gates still decide" },
  { id: "spark_open", name: "Muse Spark Open on Baseten", role: "The hosted open model: cap-planner call and prompt-cache A/B" },
  { id: "sam", name: "SAM 3.1", role: "Outlines each rack in the room photo" },
  { id: "llama", name: "Llama 3.1 8B", role: "The workload measured on the H100 in RUN2" },
  { id: "glimmer", name: "Muse Glimmer 30B", role: "Open weights swept on the H100 in RUN1" },
  { id: "code", name: "Muse Code task set", role: "Coding prompts used as a test workload" },
];
export const metaTags = (ids) => META_TOOLS.filter((t) => !ids || ids.includes(t.id)).map((t) => `<button class="mtag" data-info="meta-${t.id}"><b>Meta · ${esc(t.name)}</b><span>${esc(t.role)}</span></button>`).join("");
export const metaTile = () => `<section class="glass ov-meta"><h3>Built with Meta</h3><div class="mtags">${metaTags()}</div></section>`;
export const metaInRun = (rec) => {
  const llama = /llama/i.test(rec?.model || "");
  return `<div class="mrun"><span class="lvl">Meta models in this run</span><div class="mtags sm">${metaTags(llama ? ["llama", "spark", "sam"] : ["spark_open", "spark"])}</div></div>`;
};
registerInfo({
  "meta-spark": { title: "Muse Spark", html: `<p>Meta's Muse Spark API is WattNot's planner. It reads the last measurements and proposes the next power cap to try, or says stop.</p><p>Every call is logged with its cost in the ledger. It only proposes; the gates and a person decide.</p>` },
  "meta-spark_open": { title: "Muse Spark Open on Baseten", html: `<p>Meta's open model, served by Baseten. WattNot calls it for the cap planner step and for the prompt-cache test, and it is one of the three places a model can run.</p><p>Baseten does not share power meters, so its energy is always an estimate.</p>` },
  "meta-wonder": { title: "Muse Wonder", html: `<p>Meta's Muse Wonder (muse-wonder-1.0) was called LIVE once as a supervisor over the MEASURED RUN2 sweep (inputs are a REPLAY of measured steps). It proposed a 300 W trial; WattNot's local p99 gate would block it because 325 W already failed (+10.9% vs a 10% limit). The decision and the response ids are in a signed addendum on the RUN2 receipt; see the Live Meta calls card on the RUN2 report.</p>` },
  "meta-sam": { title: "SAM 3.1", html: `<p>Meta's Segment Anything 3.1 turns a photo of a server room into one outline per rack, from the prompt "server rack".</p><p>WattNot uses the outlines to put savings in a place: which rack is hot, which one it can cap.</p>` },
  "meta-llama": { title: "Llama 3.1 8B Instruct", html: `<p>Meta's open-weights model, served with vLLM on a rented H100. RUN2 measured it without and with a 350 W cap: energy per answer fell 27.1% and quality stayed at 93.7%.</p>` },
  "meta-glimmer": { title: "Muse Glimmer 30B", html: `<p>Meta's open-weights 30B model. RUN1 swept caps from 700 W down on it; 400 W used 21% less energy per answer with p99 +3.7%.</p>` },
  "meta-code": { title: "Muse Code task set", html: `<p>A fixed set of coding prompts written with Muse Spark. It is one of the workloads WattNot can replay to check that a cap does not hurt coding answers.</p>` },
});

// ---------- overview ----------
export function overviewHtml(S) {
  const r = S.run2 || {}, led = S.story?.ledger || {}, runs = S.runs?.runs || [];
  const rec = S.radRecs?.[runs[0]?.id];
  const saved = r.saved_pct != null ? `${fmt(-r.saved_pct, 1)}%` : null;
  const kpi = (label, v, tag, sub, key) => `<div class="ov-kpi glass"><span class="lvl">${label}${key ? infoBtn(key, label) : ""}</span><b>${v == null ? `<span class="r7miss">${PENDING}</span>` : n(esc(v), tag)}</b><small>${esc(sub)}</small></div>`;
  const used = r.j_per_req_cap, cut = r.j_per_req_stock != null && used != null ? r.j_per_req_stock - used : null;
  return `<div class="ov">
  <div class="ov-main">
    <div class="ov-hd"><div><h1>Less power per AI answer, with proof</h1><p class="lead">WattNot tries a safer GPU setting, checks the answers, and keeps it only if nothing got worse. ${infoBtn("what", "what WattNot does")}</p>
      <p class="ov-jump">On this page: ${[["pCfg", "Configure"], ["pRun", "Run"], ["pReport", "Compare"], ["pProof", "Proof"], ["pLearn", "How it works"]].map(([p, t]) => `<a href="#${p}">${t}</a>`).join(" · ")}</p>
      <div class="ov-legend">${legend()}</div></div><button class="btn pri" data-go="pCfg">Configure a run →</button></div>
    <div class="ov-kpis">
      ${(() => { // Headline: joules per correct answer (energy per answer / fixed-check pass rate), MEASURED.
        const js = r.j_per_req_stock, jc = r.j_per_req_cap, qs = r.quality_stock_pct, qc = r.quality_cap_pct;
        const ok = js != null && jc != null && qs && qc;
        const pct = ok ? ((jc / (qc / 100)) / (js / (qs / 100)) - 1) * 100 : null;
        return kpi("Joules per correct answer", pct != null ? `${fmt(pct, 1)}%` : saved, "MEASURED",
          ok ? `${fmt(js / (qs / 100), 2)} → ${fmt(jc / (qc / 100), 2)} J · ${r.cap_w} W cap vs ${r.stock_w} W. Cost and water are ESTIMATES from these joules` : "", "calc-power");
      })()}
      ${kpi("Slowest 1% (p99)", r.p99_change_pct != null ? `${r.p99_change_pct > 0 ? "+" : ""}${fmt(r.p99_change_pct, 1)}%` : null, "MEASURED", "limit +10%", "p99")}
      ${kpi("Quality: fixed-check pass rate", r.quality_cap_pct != null ? `${fmt(r.quality_cap_pct, 1)}%` : null, "MEASURED", r.quality_stock_pct != null ? `uncapped ${fmt(r.quality_stock_pct, 1)}% · share of fixed checks passed · quality gate: drop ≤ 1.0 pt (held); latency gate p99 ≤ +10% decides the cap` : "", "quality")}
      ${kpi("Meta API spend", led.total_usd != null ? `$${fmt(led.total_usd)}` : null, "MEASURED", led.calls != null ? `${fmt(led.calls, 0)} calls in the bundled ledger snapshot (later live calls are on the receipt addenda)` : "", null)}
    </div>
    ${metaTile()}
    <div class="ov-grid">
      <section class="glass ov-trend"><h3>Energy per answer, by repeat ${infoBtn("calc-power", "energy")}</h3>${rec ? lineChart(series(rec, (j) => j), { unit: "J", yLabel: "J per answer", w: 620, h: 220, y0: 0, xticks: [1, 2, 3].map((x) => ({ x, label: `#${x}` })) }) : `<p class="r7miss">${PENDING}</p>`}<button class="btn" data-pg="1">Open bigger</button></section>
      <section class="glass ov-donut"><h3>Where the energy went</h3>${cut != null ? donut([{ name: "Still used", v: used, color: "#8E7A9E" }, { name: "Saved by WattNot", v: cut, color: "#7FA88A" }], { center: saved || "", centerSub: "saved", aria: "energy used vs saved" }) : `<p class="r7miss">${PENDING}</p>`}</section>
      <section class="glass ov-gauge"><h3>Speed budget used</h3>${r.p99_change_pct != null ? gauge(r.p99_change_pct, 15, { text: `+${fmt(r.p99_change_pct, 1)}%`, sub: "p99 change · limit 10%", limit: 10, cls: r.p99_change_pct > 10 ? "warn" : "", aria: "p99 change against the 10% limit" }) : `<p class="r7miss">${PENDING}</p>`}<p class="r7note">Red tick: the 10% limit.</p></section>
      <section class="glass ov-table"><h3>Recorded runs</h3><table><thead><tr><th>Run</th><th>Model</th><th>Date</th></tr></thead><tbody>${runs.map((x) => `<tr><td>${esc(x.name)}</td><td>${esc(x.model)}</td><td>${esc(x.date)}</td></tr>`).join("") || `<tr><td colspan="3" class="r7miss">${PENDING}</td></tr>`}</tbody></table></section>
    </div></div></div>`;
}

// ---------- info panel texts ----------
registerInfo({
  what: { title: "What WattNot does", html: `<p>AI models run on GPUs that can draw 700 W each, often more than the work needs.</p><p>WattNot lowers the limit a little, measures energy, speed and answer quality on real prompts, and keeps the change only if all three stay inside your limits. A person approves risky changes and every change can be undone.</p>` },
  p99: { title: "p99 latency", html: `<p>Sort all answer times. p99 is the one 99% of answers beat.</p><h3>Example</h3><p>100 answers; 99 finish in under 3.9 s, one takes 5 s. p99 = 3.9 s. A +7.7% change from 3.6 s is 3.88 s.</p>` },
  quality: { title: "Answer quality", html: `<p>For a power cap, quality is the <b>fixed-check pass rate</b>: the share of the 666 checked answers (as recorded) that pass their fixed checks, run with and without the cap. The cap is rejected if it loses more than 1.0 point. RUN2: 93.7% uncapped, 93.7% at 350 W (MEASURED). The 95% figure is a different gate, used only for adaptive top-k and model routing: token or answer agreement with the unchanged setup.</p>` },
  compare: { title: "How the comparison works", html: `<p>Every run is done twice on the same prompts: first without WattNot (the baseline), then with WattNot. Each card shows the difference.</p><p>Power is measured on the GPU. Cost and water are arithmetic on that measurement, so they are estimates. Open the ⓘ on any card for the worked example.</p>` },
  "calc-power": { title: "Energy per answer", html: `<p>Joules the GPU used, divided by answers served. Read from NVIDIA's NVML counter 10 times a second.</p><h3>Example</h3><p>350 W for 33 s serving 1,000 answers = 11,550 J ÷ 1,000 = 11.6 J per answer.</p>` },
  "calc-cost": { title: "From joules to dollars", html: `<p>1 kWh = 3,600,000 J. Cost = kWh × electricity price ($0.1387 per kWh, EIA US average).</p><h3>Worked example</h3>${workedExample(15.96, 11.63)}` },
  "calc-water": { title: "From joules to litres of water", html: `<p>Data centres use water to cool the servers. Water = kWh × 0.36 L per kWh (LBNL 2024, on-site cooling only).</p><h3>Worked example</h3>${workedExample(15.96, 11.63)}` },
  tiered: { title: "Tiered and k-means experts", html: `<p>A mixture-of-experts model has many small "experts" and uses only a few per word. Some experts are used all the time, most rarely.</p><p><b>Tiered</b> keeps the hot experts on the GPU at higher precision, warm ones at lower precision, and cold ones in RAM or on the CPU. <b>k-means</b> groups experts that are used together so each group can live in one place.</p><h3>Example</h3><p>40 experts: 8 hot ones stay on the GPU in 8-bit, 16 warm ones in 4-bit, 16 cold ones in CPU RAM. Less GPU memory, fewer watts, if quality holds.</p><p><b>What we measured:</b> in RUN1, tiered placement tied plain uniform 4-bit quantization (no gain, no loss).</p>` },
  router: { title: "WattNot picks per workload", html: `<p>All three places run on every compare: your GPU, Baseten and Modal. WattNot then picks one per kind of work.</p><ul><li><b>Interactive chat:</b> lowest p99 that passes the quality gate.</li><li><b>Batch:</b> lowest cost per 1k answers.</li><li><b>Private data:</b> your GPU only; prompts never leave.</li></ul><p>It always says why: "WattNot chose X because…", with the gate results. Hosted energy is an estimate because hosts do not share power meters.</p>` },
  thermal: { title: "Thermal floor plan", html: `<p>SAM 3.1 outlines each rack in a room photo. Each rack is coloured by temperature like an infrared camera: dark blue cool, yellow and white hot.</p><p>Rack outlines come from the scan. Temperatures are simulated from replayed GPU power: inlet 22 °C + 0.05 °C per watt.</p>` },
  experts: { title: "Expert routing", html: `<p>Each column is one step of the prompt; each row is one expert. Brighter cells mean more words were sent to that expert.</p><p>Adaptive top-k drops experts that carry little weight for a word: the bottom strip shows how many experts each word used, 8 without and about 6.7 with.</p>` },
});
export { wireR7 };

// ---------- Compare empty / unreadable state ----------
// Compare never silently shows the bundled RUN2 numbers in place of a missing run.
// The bundled sample is still available, but only when the user picks it, and it is labelled.
export const BUNDLED_ID = "bundled-run2";
export const readableRecord = (rec) => !!(rec && typeof rec === "object" && !Array.isArray(rec) && rec.phases && typeof rec.phases === "object" && rec.phases.baseline && rec.phases.wattnot);
export function compareState(runs, selId, rec) {
  if (selId === BUNDLED_ID) return { kind: "bundled" };
  if (!Array.isArray(runs) || !runs.length) return { kind: "empty" };
  if (!readableRecord(rec)) return { kind: "unreadable", id: selId };
  return { kind: "ok" };
}
export function bundledRecord(run2) {
  if (!run2 || run2.j_per_req_stock == null || run2.j_per_req_cap == null) return null;
  return { id: BUNDLED_ID, bundled: true, run_name: `Bundled sample · RUN2 ${run2.model || ""} @ ${run2.cap_w ?? ""} W`,
    phases: { baseline: { metrics: { j_per_req: run2.j_per_req_stock } }, wattnot: { metrics: { j_per_req: run2.j_per_req_cap } } } };
}
export function emptyStateHtml(st, hasBundled) {
  const t = st.kind === "empty" ? "No runs yet" : "This run couldn't be read";
  const p = st.kind === "empty" ? "There are no run records to compare. Start a run, or open the bundled sample below."
    : `The record for “${esc(st.id || "this run")}” is missing or not in the expected format, so no savings are shown. Pick another run, or open the bundled sample.`;
  return `<section class="cmp-empty glass" id="cmpEmpty" role="status" data-state="${st.kind}"><h2>${t}</h2><p>${p}</p>`
    + (hasBundled ? `<button class="btn sm" data-bundled>Show the bundled RUN2 sample</button><p class="r7miss">The bundled sample is H100 RUN2 data that ships with WattNot, not your run.</p>` : "") + `</section>`;
}
export const bundledBanner = () => `<p class="cmp-bundled" id="cmpBundled" role="note"><b>Bundled sample</b>: H100 RUN2 data that ships with WattNot, not one of your runs.</p>`;
