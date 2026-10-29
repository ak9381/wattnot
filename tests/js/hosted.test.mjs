import test from "node:test";
import assert from "node:assert/strict";
import * as R from "../../web/dash/v2r7.js";

const L = (v, l) => ({ value: v, label: l });
const rec = { hosted_ab: { hosts: {
  baseten: { status: "ran", without: { usd_per_1k: L(2, "MEASURED") }, with: { usd_per_1k: L(1.5, "MEASURED") },
    estimate: { tokens_saved: L(1000, "ESTIMATE"), j_per_token: L(7.86, "ESTIMATE"), energy_j: L(7860, "ESTIMATE"),
      energy_kwh: L(0.00218, "ESTIMATE"), water_l: L(0.003924, "ESTIMATE"), usd_saved: L(0.5, "MEASURED"), power_saved_pct: L(12.5, "ESTIMATE") } },
  modal: { status: "not_run", why: "down" } } } };

test("hosted child cards: cost MEASURED, power/water ESTIMATE, missing gives null", () => {
  assert.deepEqual(R.childSaved(rec, "cost", "baseten"), { text: "25%", tag: "MEASURED" });
  assert.deepEqual(R.childSaved(rec, "power", "baseten"), { text: "12.5%", tag: "ESTIMATE" });
  assert.deepEqual(R.childSaved(rec, "water", "baseten"), { text: "12.5%", tag: "ESTIMATE" });
  assert.equal(R.childSaved(rec, "power", "modal"), null);
  assert.equal(R.childSaved({}, "cost", "baseten"), null);
});

test("worked chain shows tokens, J, kWh, L and $", () => {
  const h = R.hostedChain(rec.hosted_ab.hosts.baseten.estimate);
  for (const s of ["Tokens saved", "J/token", "kWh", "L water", "Cost saved", "1.8"]) assert.ok(h.includes(s), s);
  assert.equal(R.hostedChain(null), "");
});
