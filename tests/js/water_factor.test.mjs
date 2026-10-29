// Standing rule (owner decision): every dashboard module states the SAME water
// factor. The headline is 0.36 L/kWh (on-site cooling, LBNL 2024, ESTIMATE); the
// upper bound is 1.8 L/kWh, which additionally includes power-plant (electricity)
// water (also ESTIMATE). This test fails if any module drifts, so the litres shown
// on one page can never disagree with another.
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { WATER_L_PER_KWH as R7, WATER_L_PER_KWH_UPPER as R7U } from "../../web/dash/r7.js";
import { WATER_L_PER_KWH as V2R7, WATER_L_PER_KWH_UPPER as V2R7U } from "../../web/dash/v2r7.js";

const HEADLINE = 0.36;
const UPPER = 1.8;
const dash = new URL("../../web/dash/", import.meta.url);
const read = (p) => readFileSync(new URL(p, dash), "utf8");
// Modules that show a litres number or carry the factor in prose.
const MODULES = ["runs.js", "v2.js", "v2r7.js", "r7.js", "report.js"];

test("the shared constants are the owner-standardised 0.36 headline and 1.8 upper", () => {
  assert.equal(R7, HEADLINE, "r7.js WATER_L_PER_KWH headline");
  assert.equal(R7U, UPPER, "r7.js WATER_L_PER_KWH_UPPER");
  assert.equal(V2R7, HEADLINE, "v2r7.js re-export headline");
  assert.equal(V2R7U, UPPER, "v2r7.js re-export upper");
  assert.equal(R7, V2R7, "r7.js and v2r7.js headline must agree");
  assert.equal(R7U, V2R7U, "r7.js and v2r7.js upper must agree");
});

test("every dashboard module that shows water states both 0.36 headline and 1.8 upper", () => {
  for (const m of MODULES) {
    const src = read(m);
    assert.match(src, /0\.36/, `${m} must name the 0.36 L/kWh headline`);
    assert.match(src, /1\.8\b/, `${m} must name the 1.8 L/kWh upper`);
  }
});
