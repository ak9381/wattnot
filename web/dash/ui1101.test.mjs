import test from "node:test";
import assert from "node:assert/strict";
globalThis.__WN_TEST__ = true;
globalThis.localStorage = { getItem: () => null, setItem() {} };
globalThis.location = { search: "" };
const { row } = await import("./realruns.js");
const { card } = await import("./approvals.js");

const R = { provider: "baseten", round: "r01", model: "meta-models/muse-spark-open", smoke: true, source: "data/b/r01.json", receipt: "data/b/receipt.json",
  arms: { with_wattnot: { accuracy: 1, energy: 0.3578, energy_unit: "Wh", energy_label: "ESTIMATE", j_per_correct: 429.38, p99_s: 4.25 },
          without_wattnot: { accuracy: 1, energy: 0.4447, energy_unit: "Wh", energy_label: "ESTIMATE", j_per_correct: 533.61, p99_s: 3.5 } },
  energy_saved_pct: { value: 19.54, label: "ESTIMATE" } };

test("real-run row links every number to its source and labels it", () => {
  const h = row(R, "with_wattnot");
  assert.match(h, /0\.358 Wh/); assert.match(h, /ESTIMATE/); assert.match(h, /MEASURED/);
  assert.match(h, /path=data%2Fb%2Fr01\.json/); assert.match(h, /receipt/);
});
test("arm toggle switches the numbers shown", () => {
  assert.match(row(R, "without_wattnot"), /0\.445 Wh/);
});
const A = { id: "data/b/r01", risk: "high", source: "data/b/approval.json", held_change: { kind: "reasoning_effort" },
  checks: [{ id: "p99", title: "p99", ok: false, detail: "x", required: true }], pr: null, decision: null };
test("approve is locked when a required check fails", () => {
  assert.match(card(A), /data-d="approve" disabled/);
  const ok = { ...A, checks: [{ ...A.checks[0], ok: true }] };
  assert.doesNotMatch(card(ok), /data-d="approve" disabled/);
});
