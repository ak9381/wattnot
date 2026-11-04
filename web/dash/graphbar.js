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
import { gwlHeadline, gwlSpend, gwlCorrect } from "./gwlcard.js";
import { getJSON } from "./plugsteps.js";

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
      <button type="button" id="gFull" title="Full screen (Esc closes)" aria-label="Open the graph full screen" aria-pressed="false">⛶ Full screen</button>
    </div>
    <div class="gbar-grp" role="group" aria-label="Replay the selected run">
      <button type="button" id="gPlay" class="on" title="Replay the selected run's recorded events on this canvas" aria-pressed="false">▶ Replay</button>
      <button type="button" id="gRestart" title="Start the replay again from the first event">↺ Restart</button>
      <span class="seg" id="gSpeed" role="group" aria-label="Replay speed"><button type="button" data-s="1">1×</button><button type="button" data-s="2" class="on">2×</button><button type="button" data-s="4">4×</button><button type="button" data-s="8">8×</button></span>
    </div>
    <div class="gbar-grp seg" role="group" aria-label="Replay arm">
      <button type="button" data-arm="off" aria-pressed="${arm === "off"}">Without WattNot</button>
      <button type="button" data-arm="on" aria-pressed="${arm === "on"}">With WattNot</button>
    </div>
    <span class="gbar-chip ${liveRun ? "live" : "replay"}">${liveRun ? "Live run" : "Replay"}</span>
    <button type="button" id="gLive" class="gbar-badge golive-btn" aria-expanded="false" aria-controls="golivedrawer">⚡ Go live</button>
    <button type="button" id="gMeta" class="gbar-badge" aria-expanded="false" aria-controls="infodrawer">Meta models</button>
    <button type="button" id="gFeat" class="gbar-badge" aria-expanded="false" aria-controls="infodrawer">WattNot features</button>
    <button type="button" id="gLegend" class="gbar-legend" aria-expanded="false" aria-controls="legdrawer">Legend</button>`;
  document.body.appendChild(bar);
  wireReplay(bar, liveRun);

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

  // ---- Go live drawer: Start gateway, Connect GPU, Simulate traffic and Live savings, in this app.
  // The plug screens load in a frame (plug.html?embed=1) so their ids never clash with the graph's.
  const gl = document.createElement("aside");
  gl.id = "golivedrawer"; gl.className = "legdrawer golive"; gl.setAttribute("aria-hidden", "true");
  gl.innerHTML = `<div class="legdrawer-head"><span class="golive-tabs" role="tablist">
      <button type="button" role="tab" data-gl="plug.html?embed=1#setup" aria-selected="true">Go live</button>
      <button type="button" role="tab" data-gl="v2.html?embed=1#pCfg" aria-selected="false">Test on my prompts</button></span>
      <button type="button" class="legdrawer-x" id="goliveX" aria-label="Close Go live">Close</button></div>
    <iframe id="goliveFrame" title="Plug WattNot in: start gateway, connect GPU, simulate traffic, live savings"></iframe>`;
  document.body.appendChild(gl);
  const gLive = $("#gLive", bar);
  gl.querySelectorAll("[data-gl]").forEach((b) => b.addEventListener("click", () => {
    gl.querySelectorAll("[data-gl]").forEach((x) => x.setAttribute("aria-selected", String(x === b)));
    $("#goliveFrame", gl).src = b.dataset.gl;
  }));
  // ---- the gateway's own live feed, drawn ON the graph (bottom-left), so the graph keeps
  // moving while you connect and send traffic from the drawer. Real receipts only.
  const gwl = document.createElement("div");
  gwl.id = "gwLive"; gwl.className = "gwlive"; gwl.hidden = true; gwl.dataset.wnFull = "1";   // keep the MEASURED/ESTIMATE words (no dot shorthand)
  gwl.innerHTML = `<div class="gwlive-h"><b>Gateway live</b> <span id="gwlLab" class="wnlab m">LIVE</span></div>
    <div id="gwlTxt" class="gwlive-t"></div><canvas id="gwlC" width="320" height="70"></canvas>
    <div class="gwlive-k"><i class="k1"></i>with WattNot <i class="k2"></i>without (stock)</div>
    <div id="gwlQ" class="gwlive-q"></div>
    <div class="gwlive-r"><span>Receipts:</span> <span id="gwlRc"></span></div>
    <div class="gwlive-b"><button type="button" id="gwlEu">EU report</button></div>`;
  document.body.appendChild(gwl);
  // ---- Receipt / EU report viewer: a modal over the graph; every number carries its label.
  const rv = document.createElement("div");
  rv.id = "gwView"; rv.className = "gwview"; rv.hidden = true; rv.dataset.wnFull = "1";
  rv.innerHTML = `<div class="gwview-box" role="dialog" aria-modal="true" aria-labelledby="gwvT">
    <div class="legdrawer-head"><b id="gwvT">Receipt</b><button type="button" class="legdrawer-x" id="gwvX">Close</button></div>
    <div id="gwvB" class="gwview-body"></div></div>`;
  document.body.appendChild(rv);
  const esc = (v) => String(v ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);
  const chip = (l) => l ? ` <span class="wnlab ${/MEASURED/.test(l) ? "m" : "e"}">${esc(l)}</span>` : "";
  const row = (k, v, l) => `<tr><th>${esc(k)}</th><td>${esc(v)}${chip(l)}</td></tr>`;
  const show = (title, html) => { $("#gwvT", rv).textContent = title; $("#gwvB", rv).innerHTML = html; rv.hidden = false; };
  $("#gwvX", rv).addEventListener("click", () => (rv.hidden = true));
  rv.addEventListener("click", (e) => { if (e.target === rv) rv.hidden = true; });
  window.__wnShowReceipt = async (id) => {
    const r = await fetch(`/api/dash/plug/receipt/${encodeURIComponent(id)}`).then((x) => (x.ok ? x.json() : null)).catch(() => null);
    if (!r) return show("Receipt", `<p>Receipt ${esc(id)} not found.</p>`);
    const a = r.actual || {}, b = r.baseline || {}, L = r.labels || {};
    const v = await fetch(`/api/dash/plug/receipt/${encodeURIComponent(id)}/verify`).then((x) => (x.ok ? x.json() : null)).catch(() => null);
    show(`Receipt ${r.receipt_id}`, `<table>${[
      row("time", new Date(r.ts * 1000).toLocaleString()), row("request", r.run), row("status", r.status),
      row("model calls", r.upstream_calls), row("stepped up", r.stepped_up), row("agent guard stopped", r.guard_stopped),
      row("images shrunk", r.images_shrunk),
      row("tokens with WattNot", `${a.prompt_tokens} in / ${a.completion_tokens} out`, L.tokens),
      row("tokens stock", `${b.prompt_tokens} in / ${b.completion_tokens} out`, b.label),
      row("energy with WattNot", `${(a.joules / 3600).toFixed(3)} Wh`, L.joules),
      row("energy stock", `${(b.joules / 3600).toFixed(3)} Wh`, b.label),
      row("cost with WattNot", `$${Number(a.usd).toFixed(5)}`, L.usd), row("cost stock", `$${Number(b.usd).toFixed(5)}`, L.usd),
      row("chained to", r.prev_receipt_id || "(first)"),
      ...(v ? [row("hash check", v.id_matches && v.chain_ok ? `OK (id = sha256 of the record; ${v.position} of ${v.of} in the chain)` : "FAILED", v.id_matches && v.chain_ok ? "MEASURED" : ""),
               row("signature", v.signer === "UNSIGNED" ? "UNSIGNED (no local signing key on this server)" : `${v.signer} · ${v.signature.slice(0, 24)}…`)] : [])].join("")}</table>
      <p class="note"><a href="/api/dash/plug/receipt/${esc(r.receipt_id)}" target="_blank" rel="noopener">Raw receipt (JSON)</a></p>`);
  };
  window.__wnShowEU = async () => {
    const r = await fetch("/api/dash/plug/eu").then((x) => x.json()).catch(() => null);
    if (!r || r.source === "replay") return show("EU efficiency report", `<p>${esc((r && r.note) || "No live gateway traffic yet.")} (REPLAY)</p>`);
    const v = (o) => (o && typeof o === "object" ? o.value : o), l = (o) => (o && typeof o === "object" ? o.label : "");
    show("EU efficiency report", `<p class="note">Inference-level fields toward an EU 2024/1364 report, built from this gateway's savings log and the GPU helper's NVML log. ${esc(r.basis)}.</p><table>${[
      row("requests", r.requests), row("energy used", `${v(r.energy_kwh)} kWh`, l(r.energy_kwh)),
      row("energy saved vs stock", `${v(r.energy_saved_kwh)} kWh`, l(r.energy_saved_kwh)),
      row("water", `${v(r.water_litres)} L`, l(r.water_litres)),
      row("receipts", `${r.first_receipt} … ${r.last_receipt}`)].join("")}</table>
      <p class="note"><a href="/api/dash/plug/eu" target="_blank" rel="noopener">Download JSON</a></p>`);
  };
  $("#gwlEu", gwl).addEventListener("click", () => window.__wnShowEU());
  const drawGwl = (f, t) => {
    const s = f.series || [];
    if (!s.length && !(t && t.running)) { gwl.hidden = true; return; }
    gwl.hidden = false;
    const last = s[s.length - 1] || {};
    // headline = the latest sequential run; running totals across runs go on a second line
    const hd = gwlHeadline(f, t);
    $("#gwlLab", gwl).textContent = hd.label;
    const txt = $("#gwlTxt", gwl);
    txt.textContent = hd.main;
    if (hd.all) { const d = document.createElement("div"); d.className = "gwlive-all"; d.textContent = hd.all; txt.appendChild(d); }
    $("#gwlRc", gwl).innerHTML = (f.receipts || []).slice(0, 4).map((r) =>
      `<button type="button" class="rc" data-rid="${r.receipt_id}">${r.receipt_id.slice(-6)}</button>`).join(" ");
    gwl.querySelectorAll("[data-rid]").forEach((b) => (b.onclick = () => window.__wnShowReceipt(b.dataset.rid)));
    const c = $("#gwlC", gwl), x = c.getContext("2d"), mx = Math.max(1e-9, ...s.map((r) => Math.max(r.wh, r.wh_baseline)));
    x.clearRect(0, 0, c.width, c.height);
    [["wh_baseline", "#8a93a3"], ["wh", "#7fd1c9"]].forEach(([k, col]) => {
      x.beginPath(); x.strokeStyle = col; x.lineWidth = 2;
      s.forEach((r, i) => { const px = s.length < 2 ? 0 : (i / (s.length - 1)) * c.width, py = c.height - 4 - (r[k] / mx) * (c.height - 8);
        i ? x.lineTo(px, py) : x.moveTo(px, py); });
      x.stroke();
    });
  };
  // Marching ants for gateway traffic: the dashboard's own traffic runs write no step-events file,
  // so nothing lit the graph's nodes and every edge stayed a static dashed line. While requests are
  // really flowing (a run is sending and new receipts keep arriving) every edge on the live graph
  // (#amap, the same element in normal and full-screen view) marches; it stops when traffic stops.
  let antN = -1, antTs = 0;
  const liveAnts = (f, t) => {
    const n = (f && f.source !== "replay" && f.series) ? f.series.length : 0, now = Date.now();
    if (n !== antN) { if (antN >= 0 && n > antN) antTs = now; antN = n; }
    const on = !!(t && t.running) || now - antTs < 6000;
    document.querySelectorAll("#amap").forEach((m) => m.classList.toggle("wn-req-flow", on));
  };
  const pollGw = async () => {
    try {
      fetch("/api/dash/plug/quality").then((r) => (r.ok ? r.json() : null)).then((q) => {
        if (!q || !q.requests) return;
        const pc = (v) => (v == null ? "n/a" : Math.round(v * 100) + "%");
        const runs = q.by_run || [];
        $("#gwlQ", gwl).innerHTML = (runs.length
          ? runs.map((r) => `Quality gate, ${r.workload}: ${r.agreed}/${r.shadow_pairs} shadow pairs agreed (${pc(r.agreement)})`
              + (r.switched_off.length
                ? ` · switched off after request ${r.switched_off[0].after_request} of ${r.switched_off[0].of}: ${r.switched_off.map((g) => g.saver).join(", ")} (${r.switched_off[0].why}); rest of the run used stock requests + the power cap`
                : r.carried_off.length ? ` · still off from an earlier run: ${r.carried_off.join(", ")}` : " · all savers on")
              + ((r.no_effect || []).length ? ` · no effect on this model, left out: ${r.no_effect.map((g) => g.saver).join(", ")}` : "")
              + (r.setup ? ` · one-time gate setup: ${r.setup.joules} J (${r.setup.shadow_calls} shadow checks${r.setup.requests ? `, ${r.setup.requests} warm-up requests` : ""}), shown apart, not in the WattNot arm <span class="wnlab m">MEASURED</span> split <span class="wnlab e">ESTIMATE</span>` : "")
              + ' <span class="wnlab m">MEASURED</span>').join("<br>")
          : `Quality gate: ${q.shadow_pairs} shadow pairs, agreement ${pc(q.agreement)}`
            + (q.gate_off.length ? ` · off: ${q.gate_off.map((g) => g.saver).join(", ")}` : " · all savers on"))
          + (q.measured
            ? gwlCorrect(q)
              + (q.j_per_request || []).map((x) => `<br>${x.workload}: ${x.with} J per request (stock ${x.without} J), not scored <span class="wnlab m">MEASURED</span>`).join("")
            : `<br>J per correct answer: ${q.j_per_correct ?? "n/a"} J (stock ${q.j_per_correct_stock ?? "n/a"} J/request) <span class="wnlab e">ESTIMATE</span>`)
          + `<br>${gwlSpend(q)} <span class="wnlab e">${q.spend_label}</span>`;
      }).catch(() => {});
      const [f, t] = await Promise.all([getJSON("/api/dash/plug?last=500", 15000), getJSON("/api/dash/traffic/status")]);
      if (f && f.source !== "replay") drawGwl(f, t); else if (!gl.classList.contains("open")) gwl.hidden = true;
      liveAnts(f, t);
    } catch { /* not served here: no gateway, nothing to draw */ }
    setTimeout(pollGw, gl.classList.contains("open") ? 1500 : 5000);
  };
  pollGw();
  const openLive = (on) => {
    if (on) { open(false); closeInfo(); const f = $("#goliveFrame", gl); if (!f.src) f.src = "plug.html?embed=1#setup"; }
    gl.classList.toggle("open", on); gl.setAttribute("aria-hidden", String(!on));
    gLive.setAttribute("aria-expanded", String(on)); gLive.classList.toggle("on", on);
    if (on && location.hash !== "#golive") history.replaceState(null, "", "#golive");
    if (!on && location.hash === "#golive") history.replaceState(null, "", location.pathname + location.search);
    layout();
  };
  gLive.addEventListener("click", () => openLive(!gl.classList.contains("open")));
  $("#goliveX", gl).addEventListener("click", () => openLive(false));
  if (location.hash === "#golive") setTimeout(() => openLive(true), 0);

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

  // ---- full screen: the same live graph (#amap), lifted into a full-viewport overlay, so it keeps
  // updating with no second copy. X or Esc closes. Camera: show the whole flow for ~3 s, glide in to
  // the active stage, and every 25 s (or on a stage change) glide out to the whole flow for 4 s and
  // back. A small switch pins "Whole flow" or "Follow stage" instead of Auto.
  const gFull = $("#gFull", bar);
  const pc = (id) => document.querySelector("#amap #" + id)?.click();
  const glide = (fn) => {
    const inner = document.querySelector("#amap .pc-inner");
    if (inner) { inner.classList.add("wn-glide"); clearTimeout(glide.t); glide.t = setTimeout(() => inner.classList.remove("wn-glide"), 1300); }
    fn();
  };
  const follow = (on) => {   // the live view re-centres on each event while Follow is on
    const f = document.querySelector("#amap #pc-follow");
    if (f && (f.getAttribute("aria-pressed") === "true") !== on) f.click();
    bar.querySelector('[data-pc="pc-follow"]')?.classList.toggle("on", on);
  };
  const whole = () => glide(() => { follow(false); pc("pc-fit"); });
  const stageIn = () => glide(() => { follow(true); pc("pc-goto"); });
  const cam = { mode: "auto", timers: [], lastStep: null };
  const clearCam = () => { cam.timers.forEach(clearTimeout); clearInterval(cam.iv); cam.timers = []; cam.iv = 0; };
  const later = (fn, ms) => cam.timers.push(setTimeout(fn, ms));
  const tour = (holdMs) => { whole(); later(() => { if (cam.mode === "auto") stageIn(); }, holdMs); };
  const startCam = () => {
    clearCam();
    if (cam.mode === "whole") return void whole();
    if (cam.mode === "follow") return void stageIn();
    tour(3000);
    cam.iv = setInterval(() => tour(4000), 25000);
  };
  const onStep = (e) => {
    if (!document.body.classList.contains("wn-graph-full") || cam.mode === "whole") return;
    const step = e?.detail?.node || e?.detail?.step || null;
    if (step && step !== cam.lastStep) {
      const first = cam.lastStep === null; cam.lastStep = step;
      if (!first && cam.mode === "auto") { clearCam(); tour(3000); cam.iv = setInterval(() => tour(4000), 25000); }
      else if (cam.mode === "follow") stageIn();
    }
  };
  window.addEventListener("wn:archevent", onStep);
  const camBar = () => {
    const d = document.createElement("div");
    d.id = "gFullCam"; d.className = "gfull-cam"; d.setAttribute("role", "group"); d.setAttribute("aria-label", "Camera");
    d.innerHTML = [["auto", "Auto"], ["whole", "Whole flow"], ["follow", "Follow stage"]].map(([k, t]) =>
      `<button type="button" data-cam="${k}" aria-pressed="${cam.mode === k}">${t}</button>`).join("");
    d.querySelectorAll("[data-cam]").forEach((btn) => btn.addEventListener("click", () => {
      cam.mode = btn.dataset.cam;
      d.querySelectorAll("[data-cam]").forEach((x) => x.setAttribute("aria-pressed", String(x === btn)));
      startCam();
    }));
    return d;
  };
  const setFull = (on) => {
    document.body.classList.toggle("wn-graph-full", on);
    gFull.setAttribute("aria-pressed", String(on));
    let x = document.getElementById("gFullX");
    if (on && !x) {
      x = document.createElement("button");
      x.id = "gFullX"; x.type = "button"; x.className = "gfull-x";
      x.title = "Close full screen (Esc)"; x.setAttribute("aria-label", "Close full screen");
      x.textContent = "×";
      x.addEventListener("click", () => setFull(false));
      document.body.appendChild(x);
      document.body.appendChild(camBar());
      x.focus();
      requestAnimationFrame(() => requestAnimationFrame(startCam));
    } else if (!on && x) {
      x.remove(); document.getElementById("gFullCam")?.remove(); clearCam();
      gFull.focus();
      requestAnimationFrame(() => requestAnimationFrame(() => pc("pc-fit")));
    }
  };
  gFull.addEventListener("click", () => setFull(!document.body.classList.contains("wn-graph-full")));
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && document.body.classList.contains("wn-graph-full")) { e.stopImmediatePropagation(); setFull(false); }
  }, true);
  window.__wnGraphFull = setFull;
  window.__wnGraphCam = (m) => document.querySelector(`#gFullCam [data-cam="${m}"]`)?.click();

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

// ---- Replay / Restart / speed on the Live graph: replays the run picked in the header
// (or the server's own events file) on this canvas, from its recorded events.
function wireReplay(bar, liveRun) {
  const play = bar.querySelector("#gPlay"), rs = bar.querySelector("#gRestart");
  const ap = () => window.__archPage;
  let state = "idle"; // idle | playing | paused
  const show = () => { play.textContent = state === "playing" ? "❚❚ Pause" : state === "paused" ? "▶ Resume" : "▶ Replay"; play.setAttribute("aria-pressed", String(state === "playing")); };
  const begin = () => { const a = ap(); if (!a) return; a.stopLive(); a.replay = "live"; a.liveW = null; a.renderMeter(0); a.startLive(); state = "playing"; show(); };
  play.addEventListener("click", () => {
    const a = ap(); if (!a) return;
    if (state === "idle") return begin();
    if (state === "playing") { a.setLivePaused(true); state = "paused"; } else { a.setLivePaused(false); state = "playing"; }
    show();
  });
  rs.addEventListener("click", begin);
  bar.querySelectorAll("#gSpeed button").forEach((b) => b.addEventListener("click", () => {
    bar.querySelectorAll("#gSpeed button").forEach((x) => x.classList.toggle("on", x === b));
    const a = ap(); if (!a) return; a.speed = +b.dataset.s;
    if (state === "playing") begin(); // the server paces the stream, so a new speed restarts it
  }));
  if (liveRun) { state = "playing"; show(); }
  window.__liveReplayState = () => state;
  mountCanvasControls(bar);
}

// Floating Replay / Pause / speed bar on the graph canvas itself (bottom-left, always visible).
// Its buttons drive the same toolbar controls, so both stay in step.
function mountCanvasControls(bar) {
  const host = document.querySelector("main#stage") || document.querySelector("main.page.on");
  if (!host || document.getElementById("cvCtl")) return;
  if (getComputedStyle(host).position === "static") host.style.position = "relative";
  const run = new URLSearchParams(location.search).get("run") || localStorage.getItem("wn.run") || "";
  const el = document.createElement("div"); el.id = "cvCtl"; el.className = "cv-ctl"; el.setAttribute("role", "group"); el.setAttribute("aria-label", "Replay controls");
  el.innerHTML = `<button type="button" data-cv="play"></button><button type="button" data-cv="restart" title="Start again">↺</button>
    <span class="cv-seg">${[1, 2, 4].map((n) => `<button type="button" data-cv-s="${n}">${n}×</button>`).join("")}</span>
    <span class="cv-run">${esc(run.replace(/^replay:/, "") || "server events")} · REPLAY</span>`;
  host.appendChild(el);
  const place = () => { const r = host.getBoundingClientRect(); el.style.left = `${Math.max(8, r.left + 16)}px`; el.style.bottom = `${Math.max(16, innerHeight - Math.min(r.bottom, innerHeight) + 16)}px`; };
  place(); addEventListener("resize", place); addEventListener("scroll", place, true);
  const play = bar.querySelector("#gPlay"), cp = el.querySelector('[data-cv="play"]');
  const sync = () => {
    cp.textContent = play.textContent; cp.setAttribute("aria-pressed", play.getAttribute("aria-pressed"));
    const on = bar.querySelector("#gSpeed button.on")?.dataset.s;
    el.querySelectorAll("[data-cv-s]").forEach((b) => b.classList.toggle("on", b.dataset.cvS === on));
  };
  cp.onclick = () => { play.click(); sync(); };
  el.querySelector('[data-cv="restart"]').onclick = () => { bar.querySelector("#gRestart").click(); sync(); };
  el.querySelectorAll("[data-cv-s]").forEach((b) => (b.onclick = () => { bar.querySelector(`#gSpeed button[data-s="${b.dataset.cvS}"]`)?.click(); sync(); }));
  new MutationObserver(sync).observe(play, { childList: true, characterData: true, subtree: true });
  bar.querySelectorAll("#gSpeed button").forEach((b) => b.addEventListener("click", () => setTimeout(sync)));
  sync();
}
