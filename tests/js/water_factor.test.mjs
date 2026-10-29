// Standing rule (owner decision): every dashboard module states the SAME water
// factor — an industry-average WUE of 1.8 L/kWh, always labelled ESTIMATE. This
// test fails if any module drifts (e.g. the old 0.36 fleet-average) so the
// litres shown on one page can never disagree with another.
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { WATER_L_PER_KWH as R7 } from "../../web/dash/r7.js";
import { WATER_L_PER_KWH as V2R7 } from "../../web/dash/v2r7.js";

const FACTOR = 1.8;
const dash = new URL("../../web/dash/", import.meta.url);
const read = (p) => readFileSync(new URL(p, dash), "utf8");
// Modules that show a litres number or carry the factor in prose.
const MODULES = ["runs.js", "v2.js", "v2r7.js", "r7.js", "report.js"];

test("the shared constant is the owner-standardised 1.8 L/kWh everywhere it is exported", () => {
  assert.equal(R7, FACTOR, "r7.js WATER_L_PER_KWH");
  assert.equal(V2R7, FACTOR, "v2r7.js re-export");
  assert.equal(R7, V2R7, "r7.js and v2r7.js must agree");
});

test("no dashboard module still carries the retired 0.36 L/kWh factor", () => {
  for (const m of MODULES) {
    assert.doesNotMatch(read(m), /0\.36/, `${m} still mentions 0.36 L/kWh`);
  }
});

test("every dashboard module that shows water states 1.8 L/kWh", () => {
  for (const m of MODULES) {
    const src = read(m);
    // Either it imports/uses the shared constant, or it names the 1.8 figure directly.
    const usesConst = /WATER_L_PER_KWH/.test(src);
    const names18 = /1\.8\b/.test(src);
    assert.ok(usesConst || names18, `${m} neither uses WATER_L_PER_KWH nor names 1.8`);
  }
});
