import test from "node:test";
import assert from "node:assert/strict";
import { labelOf, LABELS, BENTO } from "./bento.js";
test("label chips are recognised exactly, sentences are not", () => {
  assert.equal(labelOf("MEASURED"), "MEASURED"); assert.equal(labelOf(" ● estimate "), "ESTIMATE");
  assert.equal(labelOf("segmentation MEASURED"), null);
  for (const l of ["LIVE", "MEASURED", "ESTIMATE", "SIMULATED", "REPLAY", "ADVICE", "MOCK"]) assert.ok(LABELS[l]);
});
test("bento covers the headline cards", () => {
  const sels = BENTO.map((b) => b.sel);
  for (const s of [".ov-kpi", ".hero-item", ".bd-card", ".lv-row", ".hotspot-stat-box"]) assert.ok(sels.includes(s));
});
