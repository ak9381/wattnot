// Joule-and-cost SLO card: show an admit() verdict from src/wattnot/longrun/jslo.py in plain
// words. Pure string/row builders here so node can test them; only mountCostBudget() touches the
// DOM, and it is never called at import time. No network, no money: the $/h is a list price the
// Python side was handed, and every number keeps the honesty label it arrived with - MEASURED,
// MEASURED-derived (measured time at a list price) or ESTIMATE. A number we do not have yet is
// shown pending: dimmed, with a dashed dot, never hidden.

export const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

export function num(v, d = 2) {
  if (v == null || Number.isNaN(+v)) return "n/a";
  const a = Math.abs(+v);
  return (+v).toLocaleString("en-US", { maximumFractionDigits: a >= 100 ? 0 : a >= 10 ? 1 : d });
}

// Colour by honesty label. MEASURED-derived keeps the measured green (the time is measured) but
// carries its own word so the list-price caveat is never lost.
const LABEL_COLOR = { MEASURED: "#2DD4A7", "MEASURED-DERIVED": "#2DD4A7", ESTIMATE: "#F5C451" };
const DOT = "display:inline-block;width:9px;height:9px;border-radius:50%;box-sizing:border-box;flex:none";

// One row: a dot + colour from the label, the number, and the label word. Pending rows are dimmed
// with a dashed hollow dot and the word "pending" - shown, not dropped.
export function labelChip(tag, pending) {
  if (pending) return `<span style="display:inline-flex;align-items:center;gap:6px;opacity:0.55"><i style="${DOT};border:1.5px dashed #9AA0AE;background:transparent"></i>pending</span>`;
  const key = String(tag || "").toUpperCase();
  const color = LABEL_COLOR[key] || "#9AA0AE";
  return `<span style="display:inline-flex;align-items:center;gap:6px"><i style="${DOT};background:${color}"></i>${esc(tag || "")}</span>`;
}

// Plain-words verdict line. chosen null + admit == uncapped is the least aggressive config.
export function verdictLine(r) {
  if (!r) return "";
  if (r.verdict === "admit") {
    const where = r.chosen == null ? "uncapped" : `${num(r.chosen)} W`;
    return `Yes — serve at ${where}.`;
  }
  return "No setting fits the budget yet.";
}

export function bindsWording(r) {
  if (!r) return "";
  return {
    joule: "The energy budget is the tighter limit here.",
    cost: "The dollar budget is the tighter limit here.",
    both: "The two budgets pull against each other — no single setting meets both.",
    "quality/p99": "No setting passes the quality and latency gates.",
  }[r.binds] || "";
}

// The display rows: energy per correct answer, and dollars per 1,000 correct answers. A number we
// cannot show (no verdict value, or cost with no price/throughput) is marked pending.
export function budgetRows(r) {
  if (!r) return [];
  const rows = [];
  const j2 = (x) => (+x).toFixed(2); // joules-per-correct are small; keep 2 decimals, not rounded
  const jVal = r.achieved_j_per_correct;
  rows.push({
    id: "joule", dim: "Energy per correct answer",
    value: jVal == null ? null : `${j2(jVal)} J`,
    tag: r.label || "MEASURED", pending: jVal == null,
    note: `budget ${j2(r.budget_j_per_correct)} J`
      + (r.headroom_j != null ? `, ${j2(r.headroom_j)} J under` : "")
      + (r.floor_j_per_correct != null ? ` · greenest ${j2(r.floor_j_per_correct)} J` : ""),
  });
  const cVal = r.achieved_usd_per_1k_correct;
  const hasBudget = r.budget_usd_per_1k_correct != null;
  rows.push({
    id: "cost", dim: "Cost per 1,000 correct answers",
    value: cVal == null ? null : `$${num(cVal, 4)}`,
    tag: r.cost_basis, pending: cVal == null,
    note: (hasBudget ? `budget $${num(r.budget_usd_per_1k_correct, 4)}` : "no cost budget set")
      + (r.headroom_usd != null ? `, $${num(r.headroom_usd, 4)} under` : "")
      + (r.cost_floor_usd_per_1k_correct != null ? ` · cheapest $${num(r.cost_floor_usd_per_1k_correct, 4)}` : ""),
  });
  return rows;
}

// The honesty footnote for the cost number. Cost is always a list price × throughput, never a
// metered bill. When the throughput is measured that makes it MEASURED-derived; when it is not
// (cost_note set), the cost is an ESTIMATE and the note says why - throughput not measured yet.
export function costFootnote(r) {
  const base = "Cost is the GPU list price per hour × throughput, never a metered bill.";
  if (r && r.cost_note) return `${esc(base)} It is an <b>ESTIMATE</b>: ${esc(r.cost_note)}.`;
  return `${esc(base)} With measured time it is <b>MEASURED-derived</b>.`;
}

export function renderBudget(r) {
  if (!r) return "";
  const rowHtml = budgetRows(r).map((row) => `
    <div class="cb-row" style="display:flex;justify-content:space-between;align-items:baseline;gap:16px;padding:10px 0;border-top:1px solid rgba(255,255,255,0.08)${row.pending ? ";opacity:0.6" : ""}">
      <div style="min-width:0">
        <div style="font-size:14px">${esc(row.dim)}</div>
        <div style="font-size:12px;color:#8E7A9E">${esc(row.note)}</div>
      </div>
      <div style="text-align:right;flex:none">
        <div style="font-size:18px;font-weight:600">${row.value == null ? "—" : esc(row.value)}</div>
        <div style="font-size:11px;margin-top:2px">${labelChip(row.tag, row.pending)}</div>
      </div>
    </div>`).join("");
  return `<div class="cb-card" role="group" aria-label="Energy and cost budget verdict" style="max-width:640px;padding:18px 20px;border-radius:14px;background:rgba(178,166,195,0.06);border:1px solid rgba(255,255,255,0.12)">
    <div style="font-size:12px;letter-spacing:0.04em;text-transform:uppercase;color:#8E7A9E">Energy &amp; cost budget</div>
    <div style="font-size:20px;font-weight:700;margin:4px 0 2px">${esc(verdictLine(r))}</div>
    <div style="font-size:13px;color:#8E7A9E;margin-bottom:6px">${esc(bindsWording(r))}</div>
    ${rowHtml}
    <div style="font-size:11px;color:#8E7A9E;margin-top:10px;border-top:1px solid rgba(255,255,255,0.08);padding-top:8px">${costFootnote(r)}</div>
  </div>`;
}

export function mountCostBudget(el, result) {
  if (!el) return;
  el.innerHTML = renderBudget(result);
}

// The built-in RUN2 sample (no network): RUN2 is the signed 350 W cap run, so the card serves at
// that cap - the one measured config, which is therefore both the greenest and the cost floor.
// Energy is MEASURED (12.42 J/correct = the receipt's 11.64 J/req over the 0.937 pass rate); the
// throughput is still synthetic, so every cost derived from it is an ESTIMATE carrying the
// throughput note - real numbers come from the H100 run.
export const RUN2_SAMPLE = {
  verdict: "admit", binds: "cost", chosen: 350, greenest: 350, label: "MEASURED",
  budget_j_per_correct: 15.0, achieved_j_per_correct: 12.42, headroom_j: 2.58,
  floor_j_per_correct: 12.42, p99_ceiling_pct: 10.0,
  budget_usd_per_1k_correct: 1.75, usd_per_hour: 2.5,
  achieved_usd_per_1k_correct: 1.5625, cost_basis: "ESTIMATE", headroom_usd: 0.1875,
  cost_note: "throughput not yet measured; real numbers come from the H100 run",
  cost_floor_usd_per_1k_correct: 1.5625, cost_floor_basis: "ESTIMATE",
};
