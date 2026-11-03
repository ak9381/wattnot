import { SAVERS, MODES, UPSTREAMS, defaultChoice, applyPrefill, headline, path, fmtWh, fromQuery, nodeHeadline } from "./pluglib.js";
const $ = (s) => document.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
let C = JSON.parse(localStorage.getItem("wn-plug") || "null") || defaultChoice();
const fromTest = fromQuery(location.search);
if (fromTest) { C = fromTest; localStorage.setItem("wn-plug", JSON.stringify(C)); }
let os = /Win/.test(navigator.platform) ? "powershell" : "posix", last = null;

function drawSetup() {
  $("#fromTest").hidden = !fromTest;
  $("#modes").innerHTML = MODES.map((m) => `<label><input type="radio" name="mode" value="${m.v}" ${C.deployment === m.v ? "checked" : ""}><b>${esc(m.t)}</b><small>${esc(m.h)}</small></label>`).join("");
  $("#upstream").innerHTML = UPSTREAMS.map((u) => `<option value="${u.v}" ${C.upstream === u.v ? "selected" : ""}>${esc(u.t)}</option>`).join("");
  const gw = C.deployment !== "gpu";
  $("#gwFields").hidden = !gw;
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
function save() { localStorage.setItem("wn-plug", JSON.stringify(C)); drawSetup(); }
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
$("#copy").onclick = () => navigator.clipboard?.writeText($("#cmds").textContent);
$("#dl").onclick = () => { if (!last) return; const a = document.createElement("a"); a.href = URL.createObjectURL(new Blob([last.yaml], { type: "text/yaml" })); a.download = "wattnot.yaml"; a.click(); };
window.addEventListener("wn:prefill-deploy", (e) => { C = applyPrefill(C, e.detail); location.hash = "#setup"; save(); });

async function drawLive() {
  const f = await fetch("/api/dash/plug").then((r) => r.json()).catch(() => null);
  const h = (f && !f.summary && nodeHeadline(f)) || headline(f);
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
  const ev = (f && f.node && f.node.events) || [];
  $("#nodeSteps").innerHTML = ev.length ? ev.map((e) => `<li>${esc(stepText(e))}</li>`).join("") : `<li class="small">No GPU helper yet. Press "Add GPU helper" to start one.</li>`;
  $("#receipts").innerHTML = (f && f.receipts || []).slice(0, 20).map((r) => `<li data-id="${esc(r.receipt_id)}">${esc(r.receipt_id)} · ${fmtWh((r.baseline.joules - r.actual.joules) / 3600)} saved · ${esc(r.labels.joules)}${r.stepped_up ? " · stepped up" : ""}${r.guard_stopped ? " · loop stopped" : ""}</li>`).join("")
    || `<li class="small">No receipts yet. Each request through the gateway adds one here.</li>`;
  $$all("#receipts li[data-id]").forEach((li) => (li.onclick = () => window.open(`/api/dash/plug/receipt/${li.dataset.id}`, "_blank")));
}
function stepText(e) {
  const L = e.gpu_label === "MEASURED" ? "MEASURED" : "REPLAY";
  switch (e.step) {
    case "gpu_found": return `Found ${e.name} (${e.gpu_label === "MEASURED" ? "real GPU, MEASURED" : "no real GPU here: replaying the recorded H100, REPLAY"})`;
    case "rack_check": return e.ok ? `Rack photo checked: ${e.visible_racks.length} racks visible, acting only on ${e.rack} (${e.label})` : `Rack check stopped: ${e.why}`;
    case "waiting_approval": return "Waiting for your OK before any power change";
    case "cap_applied": return `Power limit set to ${e.cap_w} W (${L})`;
    case "quality_check": return `${e.cap_w} W: ${e.accepted ? "answers OK" : "refused"}${e.energy_change_pct != null ? `, energy ${e.energy_change_pct}%` : ""}${e.p99_change_pct != null ? `, slowest answers +${e.p99_change_pct}%` : ""}${e.accepted ? "" : ` (${(e.why_not || []).join("; ")})`} (${e.check_label})`;
    case "rolled_back": return `Rolled back to ${e.to_cap_w} W`;
    case "kept": return `Kept ${e.cap_w} W (${e.label})`;
    default: return e.step;
  }
}
async function addNode(approve) {
  const r = await fetch("/api/dash/node/run", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ approve }) }).catch(() => null);
  $("#nodeMsg").textContent = !r ? "The dashboard server is not reachable." : r.ok ? (approve ? "Running the power check…" : "Checking the rack photo…") : (await r.json()).detail;
}
$("#addNode").onclick = () => addNode(false);
$("#approveNode").onclick = () => addNode(true);
function route() { const live = location.hash === "#live"; $("#live").hidden = !live; $("#setup").hidden = live; if (live) drawLive(); }
window.addEventListener("hashchange", route);
drawSetup(); route(); setInterval(() => { if (location.hash === "#live") drawLive(); }, 3000);
