import test from "node:test";
import assert from "node:assert/strict";
import { budgetRows, bindsWording, verdictLine, labelChip, renderBudget, costFootnote, RERUN_SAMPLE } from "../../web/dash/costbudget.js";

const THROUGHPUT_NOTE = "throughput not yet measured; real numbers come from the H100 run";
// A fixed fixture for the rendering tests (not shown in the UI; the page uses RERUN_SAMPLE).
const admit = {
  verdict: "admit", binds: "cost", chosen: 350, greenest: 350, label: "MEASURED",
  budget_j_per_correct: 15.0, achieved_j_per_correct: 12.42, headroom_j: 2.58,
  floor_j_per_correct: 12.42, p99_ceiling_pct: 10.0,
  budget_usd_per_1k_correct: 1.75, usd_per_hour: 2.5,
  achieved_usd_per_1k_correct: 1.5625, cost_basis: "ESTIMATE", headroom_usd: 0.1875,
  cost_note: "throughput not yet measured; real numbers come from the H100 run",
  cost_floor_usd_per_1k_correct: 1.5625, cost_floor_basis: "ESTIMATE",
};
const infeasible = {
  verdict: "infeasible", binds: "both", chosen: null, greenest: 350, label: "MEASURED",
  budget_j_per_correct: 16.0, achieved_j_per_correct: null, headroom_j: null, floor_j_per_correct: 12.42,
  budget_usd_per_1k_correct: 1.7, usd_per_hour: 2.5,
  achieved_usd_per_1k_correct: null, cost_basis: null, cost_note: THROUGHPUT_NOTE, headroom_usd: null,
  cost_floor_usd_per_1k_correct: 1.5625, cost_floor_basis: "ESTIMATE",
};

test("verdictLine says where to serve, or that nothing fits", () => {
  assert.equal(verdictLine(admit), "Yes — serve at 350 W.");  // RUN2 is the signed 350 W cap run
  assert.equal(verdictLine({ verdict: "admit", chosen: null }), "Yes — serve at uncapped.");
  assert.equal(verdictLine(infeasible), "No setting fits the budget yet.");
  assert.equal(verdictLine(null), "");
});

test("bindsWording names the binding budget in plain words", () => {
  assert.match(bindsWording(admit), /dollar budget is the tighter/);
  assert.match(bindsWording({ binds: "joule" }), /energy budget is the tighter/);
  assert.match(bindsWording({ binds: "both" }), /pull against each other/);
  assert.match(bindsWording({ binds: "quality/p99" }), /quality and latency gates/);
});

test("budgetRows carries the honesty label and marks missing numbers pending", () => {
  const rows = budgetRows(admit);
  const by = Object.fromEntries(rows.map((r) => [r.id, r]));
  assert.equal(by.joule.value, "12.42 J");  // RUN2 cap350: 11.64 J/req over the 0.937 pass rate
  assert.equal(by.joule.tag, "MEASURED");
  assert.equal(by.joule.pending, false);
  assert.equal(by.cost.value, "$1.5625");
  assert.equal(by.cost.tag, "ESTIMATE");   // RUN2 throughput is synthetic, so cost is an estimate
  assert.equal(by.cost.pending, false);
  assert.match(by.cost.note, /budget \$1\.75/);
  assert.match(by.cost.note, /cheapest \$1\.5625/);
});

test("an infeasible verdict leaves achieved numbers pending, not zeroed", () => {
  const by = Object.fromEntries(budgetRows(infeasible).map((r) => [r.id, r]));
  assert.equal(by.joule.value, null);
  assert.equal(by.joule.pending, true);
  assert.equal(by.cost.value, null);
  assert.equal(by.cost.pending, true);
  // the floors are still shown so the caller learns the tightest promise
  assert.match(by.joule.note, /greenest 12\.42 J/);
  assert.match(by.cost.note, /cheapest \$1\.5625/);
});

test("a cost row with no budget set says so rather than inventing one", () => {
  const r = { ...admit, budget_usd_per_1k_correct: null, headroom_usd: null };
  const by = Object.fromEntries(budgetRows(r).map((x) => [x.id, x]));
  assert.match(by.cost.note, /no cost budget set/);
});

test("labelChip dims pending with a dashed dot and colours measured green", () => {
  const pend = labelChip("MEASURED", true);
  assert.match(pend, /dashed/);
  assert.match(pend, /pending/);
  assert.match(pend, /opacity:0\.55/);
  const measured = labelChip("MEASURED", false);
  assert.match(measured, /2DD4A7/);
  assert.match(measured, /MEASURED/);
  // MEASURED-derived keeps the measured green but shows its own word
  const derived = labelChip("MEASURED-derived", false);
  assert.match(derived, /2DD4A7/);
  assert.match(derived, /MEASURED-derived/);
});

test("renderBudget is a string with the verdict, binds line and both rows", () => {
  const html = renderBudget(admit);
  assert.equal(typeof html, "string");
  assert.match(html, /serve at 350 W/);
  assert.match(html, /dollar budget is the tighter/);
  assert.match(html, /Energy per correct answer/);
  assert.match(html, /Cost per 1,000 correct answers/);
  // RUN2 cost is an ESTIMATE off a synthetic throughput, with the honesty note shown on the page.
  assert.match(html, /ESTIMATE/);
  assert.match(html, /throughput not yet measured/);
  assert.equal(renderBudget(null), "");
});

test("costFootnote names the basis: ESTIMATE with a note, else MEASURED-derived", () => {
  const est = costFootnote(admit);
  assert.match(est, /ESTIMATE/);
  assert.match(est, /throughput not yet measured/);
  // No note -> the measured-time footnote with the MEASURED-derived word.
  const derived = costFootnote({ ...admit, cost_note: null });
  assert.match(derived, /MEASURED-derived/);
  assert.doesNotMatch(derived, /throughput not yet measured/);
});

test("RERUN_SAMPLE serves at the signed 325 W rerun cap, its one measured config", () => {
  // The rerun is the signed cap325 run, so the served cap, the greenest cap and the cost floor are all
  // that single config: achieved energy/cost equal the floors, never a higher hypothetical cap.
  assert.equal(RERUN_SAMPLE.chosen, 325);
  assert.equal(RERUN_SAMPLE.greenest, 325);
  assert.equal(RERUN_SAMPLE.achieved_j_per_correct, RERUN_SAMPLE.floor_j_per_correct);
  assert.equal(RERUN_SAMPLE.achieved_usd_per_1k_correct, RERUN_SAMPLE.cost_floor_usd_per_1k_correct);
  // headrooms are budget minus achieved, and the cost basis stays an ESTIMATE
  assert.ok(Math.abs(RERUN_SAMPLE.headroom_j - (RERUN_SAMPLE.budget_j_per_correct - RERUN_SAMPLE.achieved_j_per_correct)) < 1e-9);
  assert.ok(Math.abs(RERUN_SAMPLE.headroom_usd - (RERUN_SAMPLE.budget_usd_per_1k_correct - RERUN_SAMPLE.achieved_usd_per_1k_correct)) < 1e-9);
  assert.equal(RERUN_SAMPLE.cost_basis, "ESTIMATE");
});

test("renderBudget escapes nothing dangerous but stays well-formed for infeasible", () => {
  const html = renderBudget(infeasible);
  assert.match(html, /No setting fits the budget yet/);
  assert.match(html, /pending/);
});
