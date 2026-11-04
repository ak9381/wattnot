const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const fmt = (v, d = 2) => (v == null || Number.isNaN(+v) ? "–" : (+v).toLocaleString("en-US", { maximumFractionDigits: d }));
// Plugin runs through the gateway: every take as recorded, one row each, never averaged.
export function takesTable(r) {
  if (!Array.isArray(r?.takes) || !r.takes.length) return "";
  const sg = (x) => (x == null ? "not reported" : `${x > 0 ? "+" : ""}${fmt(x, 1)}%`);
  const rows = r.takes.map((t) => `<tr${t.run_id === r.headline_take ? ' class="on"' : ""}><td>${esc(t.workload)}</td><td>${esc(t.run_id)}</td>
    <td>${t.j_per_req?.[0] != null ? `${fmt(t.j_per_req[0], 1)} → ${fmt(t.j_per_req[1], 1)} J` : "—"}</td><td><b>${sg(t.energy_change_pct)}</b> <span class="tk-lab" data-wn-keep data-l="${esc(t.label)}"></span></td>
    <td>${t.quality ? `${esc(t.quality[0])} → ${esc(t.quality[1])} correct` : t.agreement != null ? `agreement ${esc(t.agreement)}` : ""}${t.p_value != null ? ` · p ${t.p_value < 0.001 ? "< 0.001" : fmt(t.p_value, 3)}` : ""}${t.quality_why ? esc(t.quality_why) : ""}${t.why ? ` · ${esc(t.why)}` : ""}</td></tr>`).join("");
  return `<style>.tk-lab{display:inline-block;font:600 9px/1.4 ui-monospace,monospace;letter-spacing:.04em;padding:1px 5px;border-radius:5px;vertical-align:middle;color:#8fe3a8;background:rgba(80,200,120,.14);border:1px solid rgba(80,200,120,.4)}.tk-lab::after{content:attr(data-l)}.tk-lab[data-l="ESTIMATE"]{color:#F0C674;background:rgba(240,198,116,.14);border-color:rgba(240,198,116,.45)}.ov-takes th{text-align:left;color:var(--mu);font-weight:500}.ov-takes td,.ov-takes th{padding:4px 8px;border-bottom:1px solid var(--ln)}.ov-takes tr.on td{background:rgba(140,200,255,.07)}</style><section class="glass ov-takes" style="margin:14px 0;padding:14px 18px"><h3>Every take in this run (energy per request, with vs without WattNot)</h3>
    <table style="width:100%;font-size:14px"><thead><tr><th>Workload</th><th>Take</th><th>J per request</th><th>Change</th><th>Answers</th></tr></thead><tbody>${rows}</tbody></table>
    <p class="r7note">Headline row highlighted: ${esc(r.headline_why || "")}. ${(r.gaps || []).map(esc).join(" ")}</p></section>`;
}

// Where each take's numbers came from: signed receipts (H100) or the recorded result files (Baseten).
export function takesSources(id, takes) {
  return (takes || []).map((t) => t.receipt ? `<a href="/${esc(t.receipt)}">${esc(t.run_id)}</a> (signed receipt)` : `<a href="/replays/${esc(id)}/${esc(t.file)}">${esc(t.run_id)}</a> (result file, no signed receipt)`).join(" · ");
}
