// "Meta models at work": one chip per Meta model, filled only from the run's own event log.
// A model shows its call count, latest trace id, latency and $ (each with the event's label);
// a model with no event in this run says so instead of showing a number.
export const ROSTER = [
  { id: "spark", name: "Muse Spark", role: "operator: probe, proposal, gate, receipt", match: /^muse-spark(?!-open)/ },
  { id: "wonder", name: "Muse Wonder", role: "plans the next trial, reads the Pareto chart", match: /^muse-wonder/ },
  { id: "glimmer", name: "Glimmer 30B", role: "the served model being tuned", match: /glimmer/ },
  { id: "sam", name: "SAM 3.1", role: "rack hotspot audit", match: /^sam/ },
  { id: "voice", name: "Muse Voice", role: "spoken Ask and tune", match: /^muse-voice/ },
  { id: "code", name: "Muse Code", role: "sandboxed PR for the change", match: /^muse-code/ },
  { id: "open", name: "Spark Open", role: "routing on Baseten", match: /spark-open/ },
  // Muse Image made the eye-check test sets ahead of the run; no Muse Image call during this run.
  { id: "image", name: "Muse Image", role: "vision quality gate images (any miss rejects a change); SAM hotspot test set", match: /^$/, static: "SYNTHETIC", note: "generated with Muse Image before the run (SYNTHETIC test set)" },
];
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

export function modelOf(ev) {
  const m = String((ev?.data || {}).model || "").toLowerCase();
  return m ? ROSTER.find((r) => r.match.test(m))?.id || null : null;
}

// Fold one event into the per-model tally. Only finished calls with a model count.
export function addEvent(state, ev) {
  const id = modelOf(ev); if (!id || !ev || !["step.finished", "gate.result", "decision", "proposal.created", "trial.proposed"].includes(ev.type)) return state;
  const d = ev.data || {}, s = (state[id] ||= { calls: 0, usd: 0, usdLabel: null });
  s.calls += 1;
  if (d.usd != null) { s.usd += Number(d.usd) || 0; s.usdLabel = d.usd_label || d.label || s.usdLabel; }
  s.trace = d.trace_id || d.request_id || d.response_id || s.trace;
  s.latency = d.latency_ms ?? (d.latency_s != null ? d.latency_s * 1000 : (d.duration_s != null ? d.duration_s * 1000 : s.latency));
  s.label = d.latency_label || d.label || s.label;
  return state;
}

export function renderMetaStrip(state) {
  const chips = ROSTER.map((r) => {
    const s = r.static ? null : state?.[r.id];
    const body = r.static ? `<span class="muted">${esc(r.note)}</span> <span class="sparkLbl">${esc(r.static)}</span>` : s
      ? `<b>${s.calls}</b> call${s.calls === 1 ? "" : "s"} · <code>${esc(s.trace || "–")}</code> · ${s.latency == null ? "–" : `${Math.round(s.latency)} ms`} · ${s.usdLabel ? `$${s.usd.toFixed(5)} <span class="sparkLbl">${esc(s.usdLabel)}</span>` : "$ –"}${s.label ? ` <span class="sparkLbl">${esc(s.label)}</span>` : ""}`
      : `<span class="muted">no call in this run's event log</span>`;
    return `<div class="metaChip ${s || r.static ? "on" : "off"}" data-model="${r.id}"><div class="metaChipName">${esc(r.name)}</div><div class="metaChipRole">${esc(r.role)}</div><div class="metaChipBody">${body}</div></div>`;
  }).join("");
  return `<div class="metaStrip"><h5>Meta models at work</h5><div class="metaChips">${chips}</div></div>`;
}
