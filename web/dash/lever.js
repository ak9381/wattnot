import { armRows, stateText } from "./leverlib.js";
const $ = (s) => document.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const COL = { without: "#888", with: "#0a7", restart: "#ddd", restore: "#ddd", starting: "#ddd" };
async function j(url, opt) {
  const r = await fetch(url, opt).catch(() => null);
  if (!r) throw new Error("dashboard not reachable");
  const d = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(d.detail || r.status);
  return d;
}
function draw(series) {
  const c = $("#lvGraph"); if (!c) return;
  const g = c.getContext("2d"); g.clearRect(0, 0, c.width, c.height);
  const pts = (series || []).filter((p) => p[1] != null); if (!pts.length) return;
  const t1 = pts[pts.length - 1][0] || 1, wMax = 750;
  pts.forEach((p, i) => { if (!i) return; const q = pts[i - 1];
    g.strokeStyle = COL[p[2]] || "#bbb"; g.lineWidth = 2; g.beginPath();
    g.moveTo((q[0] / t1) * c.width, c.height - (q[1] / wMax) * c.height);
    g.lineTo((p[0] / t1) * c.width, c.height - (p[1] / wMax) * c.height); g.stroke(); });
  g.fillStyle = "#333"; g.font = "12px sans-serif";
  g.fillText(`GPU power ${pts[pts.length - 1][1]} W (MEASURED, NVML) · grey = stock arm, green = lever arm`, 6, 14);
}
async function plan() {
  try { const p = await j(`/api/dash/lever/plan?lever=${$("#lvSel").value}`);
    $("#lvPlan").textContent = `Changes ${Object.entries(p.change).map(([k, v]) => `${k}=${v}`).join(", ")} · ${p.prompts} prompts each arm · ~${p.est_minutes} min (ESTIMATE)`;
  } catch (e) { $("#lvPlan").textContent = String(e.message); }
}
async function poll() {
  try { const s = await j("/api/dash/lever/status");
    $("#lvState").textContent = stateText(s);
    $("#lvArms").innerHTML = armRows(s.arms).map((r, i) => `<tr>${r.map((x) => (i ? `<td>${esc(x)}</td>` : `<th>${esc(x)}</th>`)).join("")}</tr>`).join("");
    draw(s.series); $("#lvStart").disabled = !!s.running;
  } catch (e) { $("#lvState").textContent = String(e.message); }
}
if ($("#lever")) {
  $("#lvSel").onchange = plan;
  $("#lvStart").onclick = async () => {
    try { await j("/api/dash/lever/start", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ lever: $("#lvSel").value }) }); $("#lvOut").textContent = ""; }
    catch (e) { $("#lvOut").textContent = `Not started: ${e.message}`; }
    poll(); };
  $("#lvStop").onclick = async () => { await j("/api/dash/lever/stop", { method: "POST" }).catch(() => null); poll(); };
  plan(); poll(); setInterval(poll, 2000);
}
