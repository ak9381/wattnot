// graphbar.js — graph.html only. Builds ONE compact toolbar directly below the
// shared site header holding the canvas controls (zoom, fit, go to step, follow),
// the Without/With WattNot arm toggle and the live/replay status, a "Legend"
// button that opens a right-side drawer of every legend, and two badges —
// "Meta models" and "WattNot features" — that open a second right-side drawer
// listing the selected run's eligibility (see below). The canvas starts below the
// toolbar; the footer stays pinned to the page end. Keeps ?live=1 untouched.
import { fetchElig, getArm, NODE_FEATURE, MODEL_ALIAS } from "./elig.js";
import { modelOf } from "./metastrip.js";
import { getRun } from "./runpick.js";

const $ = (s, r = document) => r.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const params = new URLSearchParams(location.search);

// Two badges in the toolbar — "Meta models" and "WattNot features" — open one
// right-side drawer (same place as Legend, below the header) listing the eligible
// items for the SELECTED run's configuration. Everything is from the eligibility
// API; ineligible items are greyed with their reason. During a live run the badge
// text and the drawer highlight follow the run's real events, never invented.
let eligData = null;              // cached POST /api/launch/eligibility for this run
let drawerMode = "meta";          // which list the info drawer shows: "meta" | "feat"
let drawerArm = getArm();         // "with_wattnot" | "without_wattnot" — drawer view only
const live = { modelId: null, modelName: null, featId: null, featName: null };

// Humanise a runner-feature id into a plain label (e.g. power_caps -> "Power caps").
// Names come from ids, never hard-coded, so no model or tool names live in this file.
const ACRO = new Set(["fp8", "kv", "moe", "eu", "slo", "pr", "nvml"]);
const humanize = (id) => String(id || "").split(/[_\s]+/).filter(Boolean)
  .map((w, i) => ACRO.has(w) ? w.toUpperCase() : i ? w : w[0].toUpperCase() + w.slice(1)).join(" ");

// The selected run's provider drives its eligibility (runpick.js getRun()).
async function runProvider() {
  const id = getRun();
  let runs = window.__wnRuns;
  if (!runs) { try { runs = (await fetch("/api/launch/runs").then((r) => r.json())).runs || []; } catch { runs = []; } }
  const r = (runs || []).find((x) => x.id === id);
  return r?.provider || window.__wnElig?.provider || null;
}
async function ensureElig() {
  if (eligData) return eligData;
  const provider = await runProvider();
  try { eligData = await fetchElig({ provider: provider || "baseten" }); }
  catch { eligData = window.__wnElig || null; }
  return eligData;
}
const modelName = (eid) => eligData?.models?.find((m) => m.id === eid)?.name || null;

// The number-label legend, written out so bento never dots the words away.
const LABELS = [
  ["MEASURED", "metered directly"],
  ["ESTIMATE", "modelled from measured inputs, not metered"],
  ["SIMULATED", "from a simulated or replayed run, not live hardware"],
  ["MOCK", "placeholder data, not a real measurement"],
  ["REPLAY", "replayed from previously recorded logs"],
];

function build() {
  if (document.getElementById("gbar")) return true;
  const stage = document.getElementById("stage");
  const afil = document.getElementById("afil"); // the partner filter / legend built by the arch page
  if (!stage || !afil) return false;            // wait until the arch page has rendered

  const liveRun = params.get("live") === "1";
  const arm = params.get("arm") === "on" ? "on" : "off"; // matches app.js: default is without WattNot

  // ---- the single toolbar
  const bar = document.createElement("div");
  bar.id = "gbar";
  bar.className = "gbar";
  bar.setAttribute("role", "toolbar");
  bar.setAttribute("aria-label", "Graph controls");
  bar.innerHTML = `
    <div class="gbar-grp" role="group" aria-label="Zoom and move">
      <button type="button" data-pc="pc-zoom-out" title="Zoom out" aria-label="Zoom out">−</button>
      <button type="button" data-pc="pc-zoom-in" title="Zoom in" aria-label="Zoom in">+</button>
      <button type="button" data-pc="pc-fit" title="Fit to screen">Fit</button>
      <button type="button" data-pc="pc-goto" title="Go to the current step">Go to step</button>
      <button type="button" data-pc="pc-follow" class="on" title="Follow the active step">Follow</button>
    </div>
    <div class="gbar-grp seg" role="group" aria-label="Replay arm">
      <button type="button" data-arm="off" aria-pressed="${arm === "off"}">Without WattNot</button>
      <button type="button" data-arm="on" aria-pressed="${arm === "on"}">With WattNot</button>
    </div>
    <span class="gbar-chip ${liveRun ? "live" : "replay"}">${liveRun ? "Live run" : "Replay"}</span>
    <button type="button" id="gMeta" class="gbar-badge" aria-expanded="false" aria-controls="infodrawer">Meta models</button>
    <button type="button" id="gFeat" class="gbar-badge" aria-expanded="false" aria-controls="infodrawer">WattNot features</button>
    <button type="button" id="gLegend" class="gbar-legend" aria-expanded="false" aria-controls="legdrawer">Legend</button>`;
  document.body.appendChild(bar);

  // ---- the right-side legend drawer (below the header, above the footer)
  const dr = document.createElement("aside");
  dr.id = "legdrawer";
  dr.className = "legdrawer";
  dr.setAttribute("aria-label", "Legend");
  dr.setAttribute("aria-hidden", "true");
  dr.setAttribute("data-wn-keep", ""); // keep the label words readable (bento quiet() skips this)
  dr.innerHTML = `
    <div class="legdrawer-head"><b>Legend</b><button type="button" class="legdrawer-x" aria-label="Close legend">Close</button></div>
    <div class="legdrawer-body" id="legbody"></div>`;
  document.body.appendChild(dr);
  const body = $("#legbody", dr);

  // ---- the info drawer (same position as the legend drawer): Meta models / WattNot features.
  const inf = document.createElement("aside");
  inf.id = "infodrawer";
  inf.className = "legdrawer infodrawer";
  inf.dataset.mode = "meta";
  inf.setAttribute("aria-label", "Run configuration");
  inf.setAttribute("aria-hidden", "true");
  inf.setAttribute("data-wn-keep", "");
  inf.innerHTML = `
    <div class="legdrawer-head"><b id="infoTitle">Meta models</b><button type="button" class="legdrawer-x" id="infoX" aria-label="Close">Close</button></div>
    <div class="legdrawer-body" id="infobody"></div>`;
  document.body.appendChild(inf);

  // Number labels — a .wn-legend so bento keeps its words and adds no second floating legend.
  const labSec = document.createElement("section");
  labSec.className = "leg-sec wn-legend";
  labSec.setAttribute("data-wn-keep", "");
  labSec.innerHTML = `<h4>Number labels</h4>` +
    LABELS.map(([l, d]) => `<div class="leg-row"><i class="pdot ${l}"></i><b>${l}</b><span>${d}</span></div>`).join("") +
    `<div class="leg-row"><i class="pdot" style="background:transparent;border:1.5px dashed #9AA0AE"></i><b>–</b><span>not metered, never guessed</span></div>`;
  body.appendChild(labSec);

  // Partner filter — move the live control in so the map filter still works, now from the drawer.
  const partSec = document.createElement("section");
  partSec.className = "leg-sec";
  partSec.setAttribute("data-wn-keep", "");
  partSec.innerHTML = `<h4>Partners · filter the map</h4>`;
  partSec.appendChild(afil); // move #afil; its wired click handlers travel with the node
  body.appendChild(partSec);

  // Meter note — the label line the meter strip used to carry inline.
  const meterSec = document.createElement("section");
  meterSec.className = "leg-sec";
  meterSec.setAttribute("data-wn-keep", "");
  meterSec.innerHTML = `<h4>Meters</h4><p>Each meter tile shows a label dot for where its number comes from. “–” means the value was not metered and is never guessed.</p>`;
  body.appendChild(meterSec);

  // ---- wiring
  bar.querySelectorAll("[data-pc]").forEach((b) => b.addEventListener("click", () => {
    const t = document.querySelector("#amap #" + b.dataset.pc);
    if (t) t.click();
    if (b.dataset.pc === "pc-follow" && t) b.classList.toggle("on", t.getAttribute("aria-pressed") === "true");
  }));
  bar.querySelectorAll("[data-arm]").forEach((b) => b.addEventListener("click", () => {
    const u = new URL(location.href);
    u.searchParams.set("arm", b.dataset.arm);
    location.href = u.toString(); // reload the arm, keeping ?live and the rest
  }));

  const lbtn = $("#gLegend", bar);
  const open = (on) => {
    if (on) closeInfo();                 // one drawer at a time
    dr.classList.toggle("open", on);
    dr.setAttribute("aria-hidden", String(!on));
    lbtn.setAttribute("aria-expanded", String(on));
    lbtn.classList.toggle("on", on);
    window.__wnDrawerBounds?.();
  };
  lbtn.addEventListener("click", () => open(!dr.classList.contains("open")));
  $(".legdrawer-x", dr).addEventListener("click", () => open(false));

  // ---- the two badges open the info drawer; Esc or Close shuts whichever is open.
  const gMeta = $("#gMeta", bar), gFeat = $("#gFeat", bar);
  const openInfo = (mode) => {
    const same = inf.classList.contains("open") && inf.dataset.mode === mode;
    if (same) return closeInfo();
    open(false);                         // close the legend drawer if it is up
    inf.dataset.mode = drawerMode = mode;
    inf.classList.add("open");
    inf.setAttribute("aria-hidden", "false");
    gMeta.setAttribute("aria-expanded", String(mode === "meta"));
    gFeat.setAttribute("aria-expanded", String(mode === "feat"));
    gMeta.classList.toggle("on", mode === "meta");
    gFeat.classList.toggle("on", mode === "feat");
    layout();                            // refresh --wn-gbar-bottom so the drawer clears the toolbar
    renderInfo();
  };
  gMeta.addEventListener("click", () => openInfo("meta"));
  gFeat.addEventListener("click", () => openInfo("feat"));
  $("#infoX", inf).addEventListener("click", closeInfo);
  document.addEventListener("keydown", (e) => {
    if (e.key !== "Escape") return;
    if (inf.classList.contains("open")) closeInfo();
    else if (dr.classList.contains("open")) open(false);
  });

  // ---- live: follow the run's real events so the badges and the drawer show the active item.
  window.addEventListener("wn:archevent", onArchEvent);
  ensureElig().then(() => { if (inf.classList.contains("open")) renderInfo(); });

  layout();
  return true;
}

function closeInfo() {
  const inf = document.getElementById("infodrawer");
  if (!inf) return;
  inf.classList.remove("open");
  inf.setAttribute("aria-hidden", "true");
  const gMeta = document.getElementById("gMeta"), gFeat = document.getElementById("gFeat");
  gMeta?.setAttribute("aria-expanded", "false"); gMeta?.classList.remove("on");
  gFeat?.setAttribute("aria-expanded", "false"); gFeat?.classList.remove("on");
  window.__wnDrawerBounds?.();
}

// Render the info drawer for the current mode and arm, highlighting the live item.
async function renderInfo() {
  const body = document.getElementById("infobody"), title = document.getElementById("infoTitle");
  if (!body) return;
  title.textContent = drawerMode === "meta" ? "Meta models" : "WattNot features";
  body.innerHTML = `<p class="info-note">Loading…</p>`;
  const elig = await ensureElig();
  if (!elig) { body.innerHTML = `<p class="info-note">Eligibility needs the local WattNot server.</p>`; return; }
  const armNote = drawerArm === "with_wattnot"
    ? "With WattNot: every eligible item is on. Greyed items can't run on this configuration."
    : "Without WattNot (baseline): these items are off in this arm. Greyed items can't run on this configuration.";
  const liveId = drawerMode === "meta" ? live.modelId : live.featId;
  const rows = drawerMode === "meta" ? modelRows(elig, liveId) : featRows(elig, liveId);
  body.innerHTML = `
    <section class="leg-sec">
      <span class="seg info-arm" role="group" aria-label="Replay arm">
        <button type="button" data-arm="without_wattnot" aria-pressed="${drawerArm === "without_wattnot"}">Without WattNot</button>
        <button type="button" data-arm="with_wattnot" aria-pressed="${drawerArm === "with_wattnot"}">With WattNot</button>
      </span>
      <p class="info-note"><b>${esc(elig.provider_label || "")}</b> · ${armNote}</p>
      <div class="infolist" data-arm="${drawerArm}">${rows}</div>
    </section>`;
  body.querySelectorAll("[data-arm]").forEach((b) => b.addEventListener("click", () => { drawerArm = b.dataset.arm; renderInfo(); }));
}
function modelRows(elig, liveId) {
  return (elig.models || []).map((m) => `
    <div class="info-item${m.eligible ? "" : " wn-inel"}${liveId && m.id === liveId ? " live" : ""}" data-model="${esc(m.id)}" title="${esc(m.reason)}">
      <b>${esc(m.name)}</b>${m.role ? `<span class="info-role">${esc(m.role)}</span>` : ""}
      <span class="info-reason">${esc(m.reason)}</span></div>`).join("");
}
function featRows(elig, liveId) {
  const feats = Object.fromEntries((elig.features || []).map((f) => [f.n, f]));
  return (elig.runner || []).map((r) => {
    const full = feats[(r.rows || [])[0]]?.name || "";
    return `
    <div class="info-item${r.eligible ? "" : " wn-inel"}${liveId && r.id === liveId ? " live" : ""}" data-feature="${esc(r.id)}" title="${esc(r.reason)}">
      <b>${esc(humanize(r.id))}</b>${full ? `<span class="info-role">${esc(full)}</span>` : ""}
      <span class="info-reason">${esc(r.reason)}</span></div>`;
  }).join("");
}
function setBadges() {
  const gMeta = document.getElementById("gMeta"), gFeat = document.getElementById("gFeat");
  if (gMeta) gMeta.textContent = live.modelName ? `Model: ${live.modelName}` : "Meta models";
  if (gFeat) gFeat.textContent = live.featName ? `Feature: ${live.featName}` : "WattNot features";
}
// Update the active model/feature from one real run event. Never invents activity.
function onArchEvent(e) {
  const ev = e.detail || {}, d = ev.data || {};
  if (ev.type === "run.finished") { live.modelId = live.modelName = live.featId = live.featName = null; setBadges(); return; }
  const mid = modelOf(ev);
  if (mid) { const eid = MODEL_ALIAS[mid] || mid; live.modelId = eid; live.modelName = modelName(eid) || humanize(eid); }
  const fid = d.node ? NODE_FEATURE[d.node] : null;
  if (fid) { live.featId = fid; live.featName = humanize(fid); }
  setBadges();
  if (document.getElementById("infodrawer")?.classList.contains("open")) renderInfo();
}

// Pin the toolbar to the header bottom and start the canvas below the toolbar.
function layout() {
  const nav = document.querySelector("nav.wn-nav");
  const bar = document.getElementById("gbar");
  const stage = document.getElementById("stage");
  if (!nav || !bar) return;
  const nb = Math.max(0, Math.ceil(nav.getBoundingClientRect().bottom));
  bar.style.top = nb + "px";
  const bb = Math.ceil(bar.getBoundingClientRect().bottom);
  if (stage) stage.style.top = bb + "px";
  // The info drawer starts below the toolbar so its badges stay clickable while it is open.
  document.documentElement.style.setProperty("--wn-gbar-bottom", bb + "px");
  window.__wnDrawerBounds?.();
}

function boot() {
  if (!build()) { requestAnimationFrame(boot); return; }
}
if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot);
else boot();
addEventListener("resize", layout);
addEventListener("load", () => { layout(); setTimeout(layout, 400); });
