import test from "node:test";
import assert from "node:assert/strict";
import { renderMatrix, roundRow, gradesCard, metaCard, providerCard } from "./matrix.js";

const planned = { id: "r01", leg: "baseten", round: 1, datasets: ["ours", "gsm8k", "humaneval"], arms: ["without_wattnot", "with_wattnot"], status: "PLANNED", label: "PLANNED", results: { accuracy_with: 0.9 } };

test("a planned round shows PLANNED and no numbers", () => {
  const h = roundRow(planned);
  assert.match(h, /PLANNED/); assert.doesNotMatch(h, /0\.9/); assert.match(h, /without \+ with WattNot/);
});

test("a real round shows its results and label", () => {
  const h = roundRow({ ...planned, status: "done", label: "MEASURED", results: { accuracy_without: 0.8, accuracy_with: 0.81 }, grades: { agreement: 0.95 } });
  assert.match(h, /MEASURED/); assert.match(h, /0\.81/); assert.match(h, /95\.0%/);
});

test("grades: none yet is PLANNED; disagreements are listed", () => {
  assert.match(gradesCard({ rounds: [planned] }), /No graded answers yet/);
  const g = { spark_graded: 10, disagreements: [{ id: "q7", dataset: "gsm8k", deterministic: true, spark: false }] };
  const h = gradesCard({ rounds: [{ ...planned, grades: g }], deterministic_decides: true });
  assert.match(h, /Agreement 90\.0%/); assert.match(h, /r01\/gsm8k\/q7/); assert.match(h, /decides/);
});

test("meta meter shows cap, warning, credits and all models", () => {
  const per = Object.fromEntries(["a","b","c","d","e","f","g","h"].map((k) => [k, { name: `M-${k}`, usd: 0, calls: 0 }]));
  const h = metaCard({ label: "MEASURED", total_usd: 26.8, cap_usd: 120, soft_usd: 100, credits_usd: 150, per_model: per, matrix_estimate_usd: [19, 23], source: "ledger" });
  assert.match(h, /of \$120 cap/); assert.match(h, /\$100 warning/); assert.match(h, /\$150 total/);
  assert.equal((h.match(/M-/g) || []).length, 8); assert.match(h, /\$19–23/);
});

test("provider meters keep labels and escape text", () => {
  const h = providerCard([{ name: "E2E <x>", unit: "INR", credit: 8801.29, credit_label: "MEASURED", cap: 7300, planned: 7219, planned_label: "ESTIMATE", credit_note: "n" }]);
  assert.match(h, /₹/); assert.match(h, /MEASURED/); assert.match(h, /ESTIMATE/); assert.match(h, /E2E &lt;x&gt;/);
});

test("renderMatrix composes all four cards", () => {
  const h = renderMatrix({ rounds: [planned], meta: { per_model: {} }, providers: [] });
  for (const t of ["Run matrix", "Spark grades", "Meta spend", "Provider credits"]) assert.match(h, new RegExp(t));
});
