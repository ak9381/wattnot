// Muse Spark trace panel: the four operator calls (probe, proposal, gate, receipt) read from the
// run's event log (step.finished events with data.kind === "spark_call"). Each row shows the
// trace id, latency and $ cost with its label; nothing here is computed, only displayed.
const STEPS = ["probe", "proposal", "gate", "receipt"];
const TITLE = { probe: "Probe: reads GPU telemetry + preflight", proposal: "Proposal: cap or serving change", gate: "Gate: judges the quality verdict", receipt: "Receipt: explains the signed receipt" };
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const tag = (l) => (l ? ` <span class="sparkLbl" data-label="${esc(l)}">${esc(l)}</span>` : "");

export function sparkCallFrom(ev) {
  const d = (ev && ev.data) || {};
  return ev && ev.type === "step.finished" && d.kind === "spark_call" ? d : null;
}

export function renderSparkTrace(calls) {
  const by = Object.fromEntries((calls || []).map((c) => [c.step || c.sub, c]));
  const rows = STEPS.map((s, i) => {
    const c = by[s];
    if (!c) return `<li class="sparkRow wait"><b>${i + 1}. ${esc(TITLE[s])}</b> <span class="muted">waiting</span></li>`;
    const usd = c.usd == null ? "–" : `$${Number(c.usd).toFixed(5)}`;
    const agree = s === "gate" && c.fixed_gate ? ` · fixed gate ${esc(c.fixed_gate)}${c.agrees_with_fixed_gate === false ? " (Spark disagrees; fixed gate stands)" : ""}` : "";
    return `<li class="sparkRow ${c.status === "ok" ? "ok" : "bad"}"><b>${i + 1}. ${esc(TITLE[s])}</b>${tag(c.label)}
      <div class="sparkMeta"><code>${esc(c.trace_id)}</code> · ${c.latency_ms == null ? "–" : `${Math.round(c.latency_ms)} ms`}${tag(c.latency_label)} · ${usd}${tag(c.usd_label)}${agree}</div>
      <div class="sparkSum">${esc(c.summary || c.error || "")}</div></li>`;
  }).join("");
  const total = (calls || []).reduce((a, c) => a + (Number(c.usd) || 0), 0);
  const lbl = (calls || [])[0]?.usd_label;
  return `<div class="sparkTrace"><h5>Muse Spark trace <span class="muted">(${(calls || []).length}/4 calls, ${(calls || []).length ? `$${total.toFixed(5)}` : "$0"}${lbl ? ` ${esc(lbl)}` : ""})</span></h5><ol>${rows}</ol></div>`;
}
