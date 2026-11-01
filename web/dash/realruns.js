// Real 1101 rounds, every number linked to the file it came from.
// The with/without toggle in the eligibility bar picks which arm is shown.
import { lab, getArm } from "./elig.js";
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const src = (p) => `/api/launch/file?path=${encodeURIComponent(p)}`;
const num = (v, d = 2) => (v == null ? "n/a" : Number(v).toFixed(d));
const arm = () => getArm();
const PROV = { baseten: "Baseten (hosted)", modal_a100: "Modal A100 (own server)", e2e_h100: "E2E H100", combined: "Combined" };

export function row(r, a = arm()) {
  const x = (r.arms || {})[a] || {}, s = r.source, l = (t) => `<a href="${src(s)}" title="${esc(s)}">${t}</a>`;
  const rc = r.receipt ? `<a href="${src(r.receipt)}">receipt</a>` : "no receipt";
  const sv = r.energy_saved_pct || {};
  return `<tr data-provider="${esc(r.provider)}">
    <td>${esc(PROV[r.provider] || r.provider)}, round ${esc(String(r.round).replace(/^r0?/, ""))}${r.smoke ? " (small test)" : ""}</td>
    <td>${esc(String(r.model || "").split("/").pop())}</td>
    <td>${l(num(x.accuracy * 100, 0) + "%")} ${lab("MEASURED")}</td>
    <td>${l(x.energy_unit === "J" ? num(x.energy / 3600, 2) + " Wh" : num(x.energy, 3) + " " + esc(x.energy_unit || ""))} ${lab(x.energy_label || "ESTIMATE")}</td>
    <td>${l(num(x.j_per_correct, 1) + " J")} ${lab(x.energy_label || "ESTIMATE")}</td>
    <td>${l(num(x.p99_s, 2) + " s")} ${lab("MEASURED")}</td>
    <td>${sv.value == null ? "n/a" : l(num(sv.value, 2) + "%")} ${sv.label ? lab(sv.label) : ""}</td>
    <td>${rc}${r.events ? ` · <a href="${src(r.events)}">events</a>` : ""}</td></tr>`;
}

export async function mount(host) {
  if (!host) return;
  let d;
  try { d = await fetch("/api/launch/rounds").then((x) => x.json()); } catch { host.innerHTML = '<p class="hint">Real rounds need the local server (python -m wattnot.dash).</p>'; return; }
  const draw = () => {
    const a = arm();
    host.innerHTML = `<section class="card wn-real">
      <h2>Newer test rounds (1 November) · showing the <b>${a === "with_wattnot" ? "with-WattNot" : "without-WattNot"}</b> run</h2>
      <p class="hint">Each round ran the same questions twice, once without WattNot and once with it. Use the toggle in the bar at the top to switch which run's numbers you see. Click any number to open the file it came from. "Energy saved" compares the two runs of the same round; a negative value means WattNot used more energy that round.</p>
      <div style="overflow-x:auto"><table class="wn-rtab"><thead><tr><th>Round</th><th>Model</th><th>Accuracy</th><th>Energy</th><th>Energy per correct</th><th>p99</th><th>Energy saved</th><th>Proof</th></tr></thead>
      <tbody>${d.rounds.map((r) => row(r, a)).join("")}</tbody></table></div></section>`;
  };
  draw();
  window.addEventListener("wn:arm", draw);
}
if (!globalThis.__WN_TEST__) mount(document.getElementById("wn-real-runs"));
