// Code Mode detection card for the dashboard. Pure helpers (no DOM at import time, so node
// can test it). Reads data/agent-waste/codemode/results.json, same shape the receipt carries.
//
// Code Mode = the agent writes one program that calls the tools as an API, instead of a big
// tool list taken over many round trips. Every number keeps its honesty label; a pending
// value (one we will not invent, e.g. prompt-side energy) is dimmed and marked with a dot.
import { esc, numLab } from "./theme.js";

export const num = (v, d = 2) =>
  v == null || Number.isNaN(Number(v)) ? "–" : Number(v).toLocaleString("en-US", { maximumFractionDigits: d, minimumFractionDigits: 0 });

// The rows shown in the before/after table, in order, with how many decimals and a plain label.
export const ROWS = [
  { key: "round_trips", name: "Round trips", d: 0 },
  { key: "schema_tokens_resent", name: "Tool-schema tokens resent", d: 0 },
  { key: "total_tokens", name: "Total tokens", d: 0 },
  { key: "wall_s", name: "Wall time (s)", d: 2 },
  { key: "usd", name: "Cost ($)", d: 5 },
  { key: "joules", name: "Energy (J)", d: 1 },
];

// One {value,label} as a number coloured by its label. A pending value (null, or label
// PENDING) is dimmed and gets a dot so the reader can see it is deliberately not filled in.
export function cell(c, d = 2) {
  const v = c && c.value;
  const lab = (c && c.label) || "";
  if (v == null || lab === "PENDING") {
    return `<span class="n n-pending" title="pending: not estimated" aria-label="pending">–<i class="pdot n-pending" aria-hidden="true"></i></span>`;
  }
  // Treat MEASURED-floor (a seen-tools lower bound) as MEASURED for colour.
  const tag = lab === "MEASURED-floor" ? "MEASURED" : lab;
  return `<span class="n${numLab(tag)}">${num(v, d)}</span>`;
}

// Does this row have a real, non-pending saving to show?
const hasSaving = (c) => c && c.value != null;

export function rowHtml(name, before, after, saved, d) {
  const chg = hasSaving(saved) ? `<b>${num(saved.value, d)}</b>` : cell(saved, d);
  return `<tr><th scope="row">${esc(name)}</th><td class="num">${cell(before, d)}</td><td class="num">${cell(after, d)}</td><td class="num chg">${chg}</td></tr>`;
}

// One candidate run: why it fired, then its before/after/saved table.
export function candidateHtml(row) {
  const reasons = (row.reasons || []).map((r) => `<li>${esc(r)}</li>`).join("");
  const rows = ROWS.map(({ key, name, d }) =>
    rowHtml(name, row.before[key], row.after[key], row.saved[key], d)).join("");
  return `<div class="cm-cand">
    <h4>${esc(row.task)}</h4>
    <ul class="cm-why">${reasons}</ul>
    <table class="cmpt"><thead><tr><th></th><th>Before ${esc("(tool-list)")}</th><th>After ${esc("(Code Mode)")}</th><th>Saved</th></tr></thead><tbody>${rows}</tbody></table>
  </div>`;
}

// The totals strip: how much the candidates waste in all, and the modelled saving.
export function totalsHtml(totals) {
  if (!totals) return "";
  const line = (key, name, d) => {
    const t = totals[key] || {};
    return `<tr><th scope="row">${esc(name)}</th><td class="num">${cell(t.before, d)}</td><td class="num">${cell(t.after, d)}</td><td class="num chg"><b>${num((t.saved || {}).value, d)}</b></td></tr>`;
  };
  return `<table class="cmpt cm-tot"><thead><tr><th></th><th>Before</th><th>After ${esc("(est.)")}</th><th>Saved</th></tr></thead><tbody>
    ${line("round_trips", "Round trips", 0)}
    ${line("schema_tokens_resent", "Tool-schema tokens resent", 0)}
    ${line("total_tokens", "Total tokens", 0)}
    ${line("usd", "Cost ($)", 5)}
  </tbody></table>`;
}

export function headline(results) {
  const n = results.candidates || 0;
  const examined = results.runs_examined || 0;
  if (!n) return `No Code Mode candidates in ${num(examined, 0)} recorded runs.`;
  const saved = ((results.totals || {}).round_trips || {}).saved || {};
  return `${num(n, 0)} of ${num(examined, 0)} recorded runs look like Code Mode candidates: a big tool list taken over many round trips. Modelled Code Mode would collapse ${num(saved.value, 0)} round trips.`;
}

// Small label legend so the colours (and the pending dot) are explained once.
export function legendHtml() {
  return `<div class="cm-legend" role="note" aria-label="number labels">
    <span><i class="pdot MEASURED"></i>measured</span>
    <span><i class="pdot ESTIMATE"></i>estimate</span>
    <span><i class="pdot n-pending"></i>pending (not estimated)</span>
  </div>`;
}

// The whole card.
export function cardHtml(results) {
  if (!results || !results.rows) return `<section class="cm-card"><p class="hnt">No Code Mode results yet.</p></section>`;
  const notes = (results.notes || []).map((t) => `<p class="hnt">${esc(t)}</p>`).join("");
  const cands = results.rows.map(candidateHtml).join("");
  return `<section class="cm-card">
    <h3 class="wsub">Code Mode detection</h3>
    <p class="cm-head">${esc(headline(results))}</p>
    ${legendHtml()}
    ${totalsHtml(results.totals)}
    ${cands}
    ${notes}
  </section>`;
}
