// Real 1101 rounds, every number linked to the file it came from.
// The with/without toggle in the eligibility bar picks which arm is shown.
import { lab, getArm } from "./elig.js";
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const src = (p) => `/api/launch/file?path=${encodeURIComponent(p)}`;
const num = (v, d = 2) => (v == null ? "n/a" : Number(v).toFixed(d));
const arm = () => getArm();

export function row(r, a = arm()) {
  const x = (r.arms || {})[a] || {}, s = r.source, l = (t) => `<a href="${src(s)}" title="${esc(s)}">${t}</a>`;
  const rc = r.receipt ? `<a href="${src(r.receipt)}">receipt</a>` : "no receipt";
  const sv = r.energy_saved_pct || {};
  return `<tr data-provider="${esc(r.provider)}">
    <td>${esc(r.provider)} ${esc(r.round)}${r.smoke ? ' <span class="hint" style="display:inline">smoke</span>' : ""}</td>
    <td>${esc(String(r.model || "").split("/").pop())}</td>
    <td>${l(num(x.accuracy * 100, 0) + "%")} ${lab("MEASURED")}</td>
    <td>${l(num(x.energy, 3) + " " + esc(x.energy_unit || ""))} ${lab(x.energy_label || "ESTIMATE")}</td>
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
      <h2>Real runs from 1 Nov · showing <b>${a === "with_wattnot" ? "with WattNot" : "without WattNot"}</b></h2>
      <p class="hint">Switch arms with the toggle in the bar at the top. Every number opens the file it was read from. "Saved" compares the two arms of the same round.</p>
      <div style="overflow-x:auto"><table class="wn-rtab"><thead><tr><th>Round</th><th>Model</th><th>Accuracy</th><th>Energy</th><th>Energy per correct</th><th>p99</th><th>Energy saved</th><th>Proof</th></tr></thead>
      <tbody>${d.rounds.map((r) => row(r, a)).join("")}</tbody></table></div></section>`;
  };
  draw();
  window.addEventListener("wn:arm", draw);
}
if (!globalThis.__WN_TEST__) mount(document.getElementById("wn-real-runs"));
