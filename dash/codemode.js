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

// Where the results artifact is served from, relative to the dashboard page. The file lives in
// data/agent-waste/codemode/ in the repo; build_pages.py copies it to dash/codemode/ for Pages.
export const CODEMODE_RESULTS = "codemode/results.json";

// Fetch the results artifact; returns null (not a throw) when it is absent, so the card can
// render its safe placeholder on a server that has not recorded Code Mode results yet.
export async function loadCodemodeResults(url = CODEMODE_RESULTS) {
  try { const r = await fetch(url); return r.ok ? await r.json() : null; } catch { return null; }
}

// Mount the card into `el`. Pass pre-loaded results (e.g. in a test) or let it fetch them.
export async function mountCodemode(el, results) {
  if (!el) return;
  const res = results ?? (await loadCodemodeResults());
  el.innerHTML = cardHtml(res);
}

// ---------------------------------------------------------------------------
// Sandbox view: the Code Mode task actually RUN as one program in a locked-down
// subprocess. Unlike the detection card (which models the "after"), here the
// "after" is measured. Each number keeps its own honesty label, shown side by
// side: MEASURED (ran the sandbox), TOKENIZER (counted bytes, repo chars//4),
// ESTIMATE (would need a live model we never call), or pending (null, e.g.
// CPU-package energy when RAPL is not readable).
// ---------------------------------------------------------------------------

// The after-side rows shown for each sandbox run, in order.
export const SB_ROWS = [
  { key: "model_round_trips", name: "Model round trips", d: 0 },
  { key: "code_executions", name: "Code executions", d: 0 },
  { key: "code_tokens", name: "Code tokens", d: 0 },
  { key: "result_tokens", name: "Result tokens", d: 0 },
  { key: "total_tokens", name: "Total tokens (code + result)", d: 0 },
  { key: "sandbox_wall_s", name: "Sandbox wall time (s)", d: 4 },
  { key: "cpu_s", name: "CPU time (s)", d: 4 },
  { key: "peak_rss_kb", name: "Peak memory (KB)", d: 0 },
  { key: "model_latency_s", name: "Model latency (s)", d: 2 },
  { key: "sandbox_energy_j", name: "Sandbox energy (J)", d: 2 },
  { key: "model_energy_j", name: "Model energy (J)", d: 1 },
];

// Which label class a sandbox cell wears. TOKENIZER and the MEASURED-vs-* saving
// labels are mapped to explicit classes so the card colours them without touching
// the shared theme. A null value, or a PENDING label, is dimmed with a dot.
export function sbLabelClass(label) {
  if (label === "TOKENIZER") return "TOKENIZER";
  if (label && label.indexOf("MEASURED") === 0) return "MEASURED";
  if (label && label.indexOf("TOKENIZER") >= 0) return "TOKENIZER";
  if (label === "ESTIMATE") return "ESTIMATE";
  return "ESTIMATE";
}

export function sbCell(c, d = 2) {
  const v = c && c.value;
  const lab = (c && c.label) || "";
  const title = (c && c.note) ? `${lab}: ${c.note}` : lab;
  if (v == null || lab === "PENDING") {
    const t = (c && c.note) ? esc(c.note) : "pending: not run";
    return `<span class="n n-pending" title="${t}" aria-label="pending">–<i class="pdot n-pending" aria-hidden="true"></i></span>`;
  }
  return `<span class="n lab-${sbLabelClass(lab)}" title="${esc(title)}" aria-label="${esc(lab)}">${num(v, d)}</span>`;
}

// One sandbox run: how it was isolated, then its before (recorded) vs after
// (measured) numbers side by side with a label per cell.
export function sandboxRunHtml(row) {
  const sb = row.sandbox || {};
  const before = row.before || {};
  const after = row.after || {};
  const beforeFor = (key) => {
    if (key === "model_round_trips") return before.round_trips;
    if (key === "total_tokens") return before.total_tokens;
    if (key === "sandbox_wall_s") return before.wall_s;
    if (key === "sandbox_energy_j") return before.joules;
    return null;
  };
  const rows = SB_ROWS.map(({ key, name, d }) => {
    const b = beforeFor(key);
    const bcell = b ? cell(b, d) : `<span class="n n-na" aria-label="not applicable">–</span>`;
    return `<tr><th scope="row">${esc(name)}</th><td class="num">${bcell}</td><td class="num">${sbCell(after[key], d)}</td></tr>`;
  }).join("");
  const status = sb.ok ? "ran" : `did not complete (${esc(String(sb.error || sb.exit_status || ""))})`;
  return `<div class="cm-cand cm-sb">
    <h4>${esc(row.task)}</h4>
    <ul class="cm-why">
      <li>net isolation: <b>${esc(sb.net_isolation || "?")}</b> — network ${sb.net_blocked ? "blocked" : "not blocked"}</li>
      <li>writes outside temp dir: <b>${sb.writes_outside_tmp_blocked ? "blocked" : "allowed"}</b></li>
      <li>one program, ${esc(String(sb.tool_calls))} tool calls over ${esc(String(sb.distinct_tools))} tools (${status})</li>
    </ul>
    <table class="cmpt"><thead><tr><th></th><th>Before ${esc("(recorded trace)")}</th><th>After ${esc("(sandbox run)")}</th></tr></thead><tbody>${rows}</tbody></table>
  </div>`;
}

// Legend naming every label the sandbox view can show, including the pending dot.
export function sandboxLegendHtml() {
  return `<div class="cm-legend" role="note" aria-label="number labels">
    <span><i class="pdot MEASURED"></i>measured (ran the sandbox)</span>
    <span><i class="pdot lab-TOKENIZER"></i>tokenizer (counted, chars/4)</span>
    <span><i class="pdot ESTIMATE"></i>estimate (no live model)</span>
    <span><i class="pdot n-pending"></i>pending (e.g. RAPL unreadable)</span>
  </div>`;
}

export function sandboxHeadline(sandbox) {
  const n = (sandbox && sandbox.candidates) || 0;
  if (!n) return "No Code Mode candidates were run in the sandbox.";
  const iso = (sandbox.env && sandbox.env.net_isolation) || "audit-hook";
  return `${num(n, 0)} Code Mode candidate${n === 1 ? "" : "s"} run as one program each in a locked-down sandbox (network isolation: ${esc(iso)}). “After” numbers are MEASURED; the single model round trip and model-side latency/energy are ESTIMATE; code tokens are counted with the repo tokenizer.`;
}

export function sandboxCardHtml(sandbox) {
  if (!sandbox || !sandbox.rows) return `<section class="cm-card cm-sbcard"><p class="hnt">No Code Mode sandbox results yet.</p></section>`;
  const notes = (sandbox.notes || []).map((t) => `<p class="hnt">${esc(t)}</p>`).join("");
  const runs = sandbox.rows.map(sandboxRunHtml).join("");
  return `<section class="cm-card cm-sbcard">
    <h3 class="wsub">Code Mode sandbox (measured run)</h3>
    <p class="cm-head">${esc(sandboxHeadline(sandbox))}</p>
    ${sandboxLegendHtml()}
    ${runs}
    ${notes}
  </section>`;
}

export const SANDBOX_RESULTS = "codemode/sandbox_results.json";

export async function loadSandboxResults(url = SANDBOX_RESULTS) {
  try { const r = await fetch(url); return r.ok ? await r.json() : null; } catch { return null; }
}

// Mount the sandbox card. Pass pre-loaded results (e.g. in a test) or let it fetch them.
export async function mountSandbox(el, sandbox) {
  if (!el) return;
  const res = sandbox ?? (await loadSandboxResults());
  el.innerHTML = sandboxCardHtml(res);
}
