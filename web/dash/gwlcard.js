// Pure helpers for the "Gateway live" card on graph.html (tested in gwlcard.test.mjs). No DOM.
// The headline is the LATEST sequential run; running totals across every run are a second line.
const mwh = (wh) => `${((wh || 0) * 1000).toFixed(1)} mWh`;
const pct = (b, w) => (b ? ` (${(100 * (b - w) / b).toFixed(1)}% ${b >= w ? "saved" : "MORE"})` : "");

export function gwlHeadline(f, t) {
  const s = (f && f.series) || [];
  const m = f && f.measured;
  const run = t && t.running ? ` · sending ${t.done}/${t.total}` : "";
  if (m && m.latest) {
    const L = m.latest, A = m.all_runs || {};
    const e = L.energy_wh || {}, ea = A.energy_wh || {};
    const main = `Latest run (${L.workload}): ${L.requests} requests · ${mwh(e.actual)} with vs ${mwh(e.baseline)} stock`
      + pct(e.baseline, e.actual) + " MEASURED, NVML per arm"
      + (L.spend_usd != null ? ` · spend $${Number(L.spend_usd).toFixed(4)} ESTIMATE` : "") + run;
    const all = A.runs > 1
      ? `All runs (${A.runs}): ${A.requests} requests · ${mwh(ea.actual)} with vs ${mwh(ea.baseline)} stock`
        + pct(ea.baseline, ea.actual) + " MEASURED"
        + (A.spend_usd != null ? ` · spend $${Number(A.spend_usd).toFixed(4)} ESTIMATE` : "")
      : "";
    return { main, all, label: "MEASURED" };
  }
  const last = s[s.length - 1] || {};
  const w = last.wh || 0, b = last.wh_baseline || 0;
  return { main: `${s.length} requests · ${mwh(w)} with vs ${mwh(b)} stock` + pct(b, w) + run, all: "",
    label: last.label || "LIVE" };
}

export function gwlSpend(q) {
  if (q && q.latest && q.all_runs) {
    return `Spend, latest run: $${Number(q.latest.spend_usd || 0).toFixed(4)} · all runs: $${Number(q.all_runs.spend_usd || 0).toFixed(4)}`;
  }
  return `Spend: $${Number((q && q.spend_usd) || 0).toFixed(4)}`;
}
