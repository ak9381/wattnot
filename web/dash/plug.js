import { SAVERS, MODES, UPSTREAMS, defaultChoice, applyPrefill, headline, path, fmtWh, fromQuery, nodeHeadline, trafficText } from "./pluglib.js";
import { latestHelperEvents, getJSON, rackResultText } from "./plugsteps.js";
const $ = (s) => document.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
let C = JSON.parse(localStorage.getItem("wn-plug2") || "null") || defaultChoice();
const fromTest = fromQuery(location.search);
if (fromTest) { C = fromTest; localStorage.setItem("wn-plug2", JSON.stringify(C)); }
let os = /Win/.test(navigator.platform) ? "powershell" : "posix", last = null;

function drawSetup() {
  $("#fromTest").hidden = !fromTest;
  $("#modes").innerHTML = MODES.map((m) => `<label><input type="radio" name="mode" value="${m.v}" ${C.deployment === m.v ? "checked" : ""}><b>${esc(m.t)}</b><small>${esc(m.h)}</small></label>`).join("");
  $("#upstream").innerHTML = UPSTREAMS.map((u) => `<option value="${u.v}" ${C.upstream === u.v ? "selected" : ""}>${esc(u.t)}</option>`).join("");
  const gw = C.deployment !== "gpu";
  $("#gwFields").hidden = !gw;
  $("#startGw").hidden = !gw; $("#startGpu").hidden = C.deployment === "gateway";
  $("#savers").innerHTML = gw ? SAVERS.map((s) => `<label class="sv"><input type="checkbox" data-s="${s.id}" ${C.savers[s.id] ? "checked" : ""}> ${esc(s.t)}<small>${esc(s.h)}</small></label>`).join("")
    : `<p class="small">The GPU helper picks a safe power limit inside your range and checks answers stay right. Set the range under Advanced.</p>`;
  $("#gpuFields").hidden = C.deployment === "gateway";
  $("#port").value = C.port; $("#gpus").value = C.gpus; $("#capMin").value = C.cap_min_w; $("#capMax").value = C.cap_max_w;
  $("#sam").checked = C.sam_rack_check; $("#approval").value = C.approval; $("#lf").checked = C.langfuse;
  $$all("input[name=mode]").forEach((r) => (r.onchange = () => { C.deployment = r.value; save(); }));
  $$all("[data-s]").forEach((b) => (b.onchange = () => { C.savers[b.dataset.s] = b.checked; save(); }));
  render();
}
function $$all(s) { return [...document.querySelectorAll(s)]; }
function save() { localStorage.setItem("wn-plug2", JSON.stringify(C)); drawSetup(); }
async function render() {
  const r = await fetch("/api/dash/deploy", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(C) }).catch(() => null);
  if (!r || !r.ok) { $("#err").textContent = r ? (await r.json()).detail : "The dashboard server is not reachable."; return; }
  $("#err").textContent = ""; last = await r.json();
  $("#cmds").textContent = last.commands[os].join("\n");
  $("#clientLine").textContent = last.client.python ? `Then, in your app: client = ${last.client.python}` : "";
}
for (const [id, k, num] of [["#upstream", "upstream"], ["#port", "port", 1], ["#gpus", "gpus"], ["#capMin", "cap_min_w", 1], ["#capMax", "cap_max_w", 1], ["#approval", "approval"]])
  $(id).onchange = (e) => { C[k] = num ? Number(e.target.value) : e.target.value; save(); };
$("#sam").onchange = (e) => { C.sam_rack_check = e.target.checked; save(); };
$("#lf").onchange = (e) => { C.langfuse = e.target.checked; save(); };
$("#osPosix").onclick = () => { os = "posix"; $("#osPosix").classList.add("on"); $("#osPs").classList.remove("on"); render(); };
$("#osPs").onclick = () => { os = "powershell"; $("#osPs").classList.add("on"); $("#osPosix").classList.remove("on"); render(); };
// Copy: the async clipboard first; inside a frame or on plain http it can be missing or refused,
// so fall back to a hidden textarea + execCommand. The button says "Copied" only when one worked.
function copyText(t) {
  const legacy = () => { const a = document.createElement("textarea"); a.value = t; a.setAttribute("readonly", "");
    a.style.cssText = "position:fixed;left:-9999px;top:0"; document.body.appendChild(a); a.select();
    let ok = false; try { ok = document.execCommand("copy"); } catch { ok = false; } a.remove(); return ok; };
  if (navigator.clipboard?.writeText) return navigator.clipboard.writeText(t).then(() => true, legacy);
  return Promise.resolve(legacy());
}
const copied = (b, t) => {
  const o = b.dataset.label || (b.dataset.label = b.textContent);
  copyText(t).then((ok) => { b.textContent = ok ? "Copied" : "Copy failed: select the text"; b.classList.toggle("ok", ok);
    clearTimeout(b._t); b._t = setTimeout(() => { b.textContent = o; b.classList.remove("ok"); }, 1500); });
};
$("#copy").onclick = (e) => copied(e.target, $("#cmds").textContent);
let snipKey = "langchain", gwInfo = null;
const post = (u, b) => fetch(u, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(b || {}) });
function drawGw(st) {
  gwInfo = st || gwInfo; if (!gwInfo) return;
  $("#gwLight").className = `light ${gwInfo.running ? "on" : "off"}`;
  $("#gwState").textContent = gwInfo.running ? `Running · savers on: ${gwInfo.savers_on.map((i) => (SAVERS.find((x) => x.id === i) || { t: i }).t).join(", ")}` : "Not running";
  $("#gwStart").textContent = gwInfo.running && gwInfo.ui_started ? "Restart gateway" : "Start gateway";
  $("#gwConn").hidden = !gwInfo.running; $("#gwNote").hidden = !!gwInfo.running;
  $("#baseUrl").textContent = gwInfo.base_url; $("#snip").textContent = gwInfo.snippets[snipKey];
}
$("#gwStart").onclick = async () => {
  $("#gwState").textContent = "Starting…";
  await post("/api/dash/deploy/save", C);
  const r = await post("/api/dash/gateway/start").catch(() => null);
  if (!r || !r.ok) { $("#gwState").textContent = r ? (await r.json()).detail : "Server not reachable."; return; }
  drawGw(await r.json());
};
$$all("[data-snip]").forEach((b) => (b.onclick = () => { snipKey = b.dataset.snip; $$all("[data-snip]").forEach((x) => x.classList.toggle("on", x === b)); drawGw(); }));
$$all("[data-copy]").forEach((b) => (b.onclick = () => copied(b, $(b.dataset.copy).textContent)));
$("#gpuConnect").onclick = async () => {
  await post("/api/dash/deploy/save", C);
  const r = await post("/api/dash/node/run", { approve: false }).catch(() => null);
  $("#gpuState").textContent = !r ? "Server not reachable." : r.ok ? "Checking the rack photo… then open Live savings to OK the power check." : (await r.json()).detail;
};
fetch("/api/dash/gateway/status").then((r) => r.json()).then(drawGw).catch(() => {});
$("#dl").onclick = () => { if (!last) return; const a = document.createElement("a"); a.href = URL.createObjectURL(new Blob([last.yaml], { type: "text/yaml" })); a.download = "wattnot.yaml"; a.click(); };
window.addEventListener("wn:prefill-deploy", (e) => { C = applyPrefill(C, e.detail); location.hash = "#setup"; save(); });

async function drawLive() {
  const f = await getJSON("/api/dash/plug", 15000);
  if (!f) { $("#heroSub").textContent = "Server not reachable, retrying (last view kept)."; return; }
  const h = (f && !f.summary && nodeHeadline(f)) || headline(f);
  if (f && f.simulated_traffic) h.sub = `${h.sub || ""} · includes Simulated traffic`;
  $("#big").textContent = h.big + (h.pct != null ? ` (${h.pct}%)` : "");
  $("#bigLabel").textContent = h.label; $("#heroSub").textContent = h.sub;
  $("#usd").textContent = h.usd != null ? `$${h.usd.toFixed(4)} saved (ESTIMATE)` : "";
  $("#litres").textContent = h.litres != null ? `${(h.litres * 1000).toFixed(2)} mL water saved (${h.waterLabel.split(" ")[0]})` : "";
  const s = (f && f.series) || [];
  const max = Math.max(...s.map((p) => p.wh_baseline), 1e-9);
  $("#graph").innerHTML = s.length ? `<path d="${path(s, "wh_baseline", 600, 170, max)}" stroke="#999" fill="none" stroke-width="2"/><path d="${path(s, "wh", 600, 170, max)}" stroke="#2a9" fill="none" stroke-width="3"/>`
    : `<text x="300" y="95" text-anchor="middle" fill="currentColor">No requests yet. Send one through the gateway and this fills in.</text>`;
  const p = f && f.power && f.power.length ? f.power.at(-1) : null;
  const plab = p ? (p.label === "MEASURED" ? "MEASURED, NVML" : "REPLAY of the recorded H100, SIMULATED where between recorded limits") : "";
  $("#power").innerHTML = p ? `<p class="small">GPU power now: <b>${p.power_w} W</b> (${plab})${p.cap_w ? `, limit ${p.cap_w} W` : ""}</p>` : "";
  const ev = latestHelperEvents((f && f.node && f.node.events) || []);
  $("#nodeSteps").innerHTML = ev.length ? ev.map((e) => `<li>${esc(stepText(e))}</li>`).join("") : `<li class="small">No GPU helper yet. Press "Add GPU helper" to start one.</li>`;
  $("#receipts").innerHTML = (f && f.receipts || []).slice(0, 20).map((r) => `<li data-id="${esc(r.receipt_id)}">${esc(r.receipt_id)} · ${fmtWh((r.baseline.joules - r.actual.joules) / 3600)} saved · ${esc(r.labels.joules)}${r.stepped_up ? " · stepped up" : ""}${r.guard_stopped ? " · loop stopped" : ""}</li>`).join("")
    || `<li class="small">No receipts yet. Each request through the gateway adds one here.</li>`;
  $$all("#receipts li[data-id]").forEach((li) => (li.onclick = () => (window.parent?.__wnShowReceipt && window.parent !== window
    ? window.parent.__wnShowReceipt(li.dataset.id) : window.open(`/api/dash/plug/receipt/${li.dataset.id}`, "_blank"))));
}
function stepText(e) {
  const L = e.gpu_label === "MEASURED" ? "MEASURED" : "REPLAY";
  switch (e.step) {
    case "gpu_found": return `Found ${e.name} (${e.gpu_label === "MEASURED" ? "real GPU, MEASURED" : "no real GPU here: replaying the recorded H100, REPLAY"})`;
    case "rack_check": return e.ok ? `Rack photo checked: ${e.visible_racks.length} racks visible, acting only on ${e.rack} (${e.label})` : `Rack check stopped: ${e.why}`;
    case "waiting_approval": return "Waiting for your OK before any power change";
    case "cap_applied": return `Power limit set to ${e.cap_w} W (${L})`;
    case "quality_check": return `${e.cap_w} W: ${e.accepted ? "answers OK" : "refused"}${e.energy_change_pct != null ? `, energy ${e.energy_change_pct}%` : ""}${e.p99_change_pct != null ? `, slowest answers +${e.p99_change_pct}%` : ""}${e.accepted ? "" : ` (${(e.why_not || []).join("; ")})`} (${e.check_label || e.label || "MEASURED"})`;
    case "stock_answers": return `Asked ${e.n} sample questions at stock power: ${e.joules_per_answer != null ? `${Math.round(e.joules_per_answer)} J per answer` : "stock and capped answers are asked in interleaved pairs"} (${e.label})`;
    case "rolled_back": return `Rolled back to ${e.to_cap_w} W${(e.failed || []).length ? ` (${e.failed.join("; ")})` : ""}`;
    case "gpu_warmup": return `GPU warm-up (not measured)${e.seconds != null ? `: ${Math.round(e.seconds)} s at ${e.cap_w} W` : ""}`;
    case "cap_kept": return e.message || `Kept ${e.cap_w} W for ${e.workload_class} traffic`;
    case "kept": return `Kept ${e.cap_w} W (${e.label})${e.note ? `: ${e.note}` : ""}`;
    default: return e.note ? `${e.step}: ${e.note}` : e.step;
  }
}
async function addNode(approve) {
  const r = await fetch("/api/dash/node/run", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ approve }) }).catch(() => null);
  $("#nodeMsg").textContent = !r ? "The dashboard server is not reachable." : r.ok ? (approve ? "Running the power check…" : "Checking the rack photo…") : (await r.json()).detail;
  if (r && r.ok) pollNode();
}
// Follow the GPU helper job until it ends, then show its result or its error (never stuck on "Running…").
async function pollNode() {
  clearTimeout(pollNode._t);
  const r = await fetch("/api/dash/node/status").catch(() => null);
  const s = r && r.ok ? await r.json() : null;
  if (!s) { $("#nodeMsg").textContent = "Lost the dashboard server while checking the GPU helper."; return; }
  if (s.running) { pollNode._t = setTimeout(pollNode, 1500); return; }
  $("#nodeMsg").textContent = nodeResultText(s.result);
}
function nodeResultText(res) {
  if (!res) return "";
  if (res.status === "error") return `The power check stopped: ${res.why}. The power limit was put back to stock.`;
  if (res.status === "waiting_approval") return rackResultText(res);
  if (res.status === "stopped") return `Not started: ${res.why || "rack check refused"}.`;
  return res.cap_w != null ? `Done: power limit ${res.cap_w} W (${res.label || "MEASURED"}).` : `Done (${res.status || "ok"}).`;
}
$("#addNode").onclick = () => addNode(false);
$("#approveNode").onclick = () => addNode(true);
const tChoice = () => ({ workload: $("#tWork").value, n: Number($("#tN").value), speed: $("#tSpeed").value, with_without: $("#tAB").checked, mode: $("#tAB").checked ? "test" : "live" });
async function tEstimate() {
  const r = await post("/api/dash/traffic/estimate", tChoice()).catch(() => null);
  if (r && r.ok) { const e = await r.json(); $("#tCost").textContent = e.self_hosted
    ? `API cost: $0 (your own GPU). List-price equivalent about $${e.usd.toFixed(2)} (${e.requests} requests, ESTIMATE, ${e.basis})`
    : `Estimated cost: about $${e.usd.toFixed(2)} (${e.requests} requests, ESTIMATE, ${e.basis})`; }
}
["#tWork", "#tN", "#tAB"].forEach((id) => ($(id).onchange = tEstimate));
$("#tStart").onclick = async () => {
  let r = await post("/api/dash/traffic/start", tChoice()).catch(() => null);
  if (r && r.status === 402 && confirm((await r.json()).detail + "?")) r = await post("/api/dash/traffic/start", { ...tChoice(), confirm_over_1: true });
  tErr = !r ? "Server not reachable." : r.ok ? "" : (await r.json()).detail;
  $("#tState").textContent = tErr || "Sending…";
};
let tErr = "", tRunning = false;
const RETRY = " · (status not answering, retrying)";
$("#tStop").onclick = () => post("/api/dash/traffic/stop");
async function drawTraffic() {
  const t = await getJSON("/api/dash/traffic/status");
  if (!t) { if (tRunning && !$("#tState").textContent.endsWith(RETRY)) $("#tState").textContent += RETRY; return; }
  tRunning = !!t.running;
  if (tErr && !t.running) $("#tState").textContent = tErr;
  else if (t.total) $("#tState").textContent = `${t.running ? `Sending: ${Math.min(t.done, t.total)} of ${t.total}` : trafficText(t)} · spent $${Number(t.usd).toFixed(4)}${t.errors ? ` · ${t.errors} errors` : ""}${t.stopped ? ` · ${t.stopped}` : ""}${t.running && t.phase ? ` · ${t.phase}` : ""}${t.cap_rollback ? ` · ${t.cap_rollback}` : ""} (Simulated traffic)`;
  const w = t.without_totals;
  $("#tAbOut").textContent = w && w.requests ? `Without WattNot (same requests): ${fmtWh(w.wh)}, $${w.usd.toFixed(4)} (${w.label}).` : "";
}
function route() { const live = location.hash === "#live"; $("#live").hidden = !live; $("#setup").hidden = live; if (live) drawLive(); }
window.addEventListener("hashchange", route);
drawSetup(); route(); tEstimate(); setInterval(() => { if (location.hash === "#live") drawLive(); if (location.hash === "#live" || tRunning) drawTraffic(); }, 3000);

// inside the live graph's Go live drawer, "Test on my prompts" switches the drawer tab
if (document.documentElement.classList.contains("embed")) {
  const tc = document.getElementById("testCard");
  if (tc) tc.onclick = (e) => { const b = window.parent?.document.querySelector('[data-gl^="v2.html"]'); if (b) { e.preventDefault(); b.click(); } };
}
{ const eu = document.getElementById("eu");
  if (eu) { eu.textContent = "EU report (from these requests)"; eu.onclick = (e) => { if (window.parent !== window && window.parent.__wnShowEU) { e.preventDefault(); window.parent.__wnShowEU(); } }; } }
