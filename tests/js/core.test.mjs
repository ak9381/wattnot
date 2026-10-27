import test from "node:test";
import assert from "node:assert/strict";
import * as C from "../../web/dash/core.js";

const E = 4, L = 2;
const steps = [{ id: 0, topic: "code", prompt: "a" }, { id: 1, topic: "math", prompt: "b" }];
function meta() {
  return {
    meta: { layers: L, experts: E, top_k: 2, steps, model: "toy" },
    modes: { tier: { assign: [0, 0, 1, 2, 0, 1, 1, 2], names: ["hot", "warm", "cold"],
      report: [{ units: 3, top_tokens: [] }, { units: 3, top_tokens: [] }, { units: 2, top_tokens: [] }] } },
    energy: { off: 2, on: 1.5, label: "MEASURED" },
    totals: { off: { generated: 10, routed_slots: 40, tokens: 12, active_per_token: 2 },
              on: { generated: 10, routed_slots: 30, tokens: 12, active_per_token: 1.5 } },
    vocab: { 1: "the", 2: "def" }, counts: { off: new Array(8).fill(1), on: new Array(8).fill(1) },
  };
}
const tick = (step, arm, r, extra = {}) => ({ step, arm, r, tt: [[0, 1, 2]], tokens: 1, slots: 2, kept_slots: 2,
  ent: 0.5, top1: 0.4, phase: "decode", ...extra });

test("a unit is layer * experts + expert and groups cover every unit", () => {
  const st = C.createState(meta());
  assert.equal(st.U, 8);
  assert.equal(st.assign.length, 8);
});

test("energy per slot is the measured rate times generated tokens over routed slots", () => {
  const st = C.createState(meta());
  assert.equal(C.jPerSlot(st, "off"), (2 * 10) / 40);
});

test("group energy parts add up to the window total", () => {
  const st = C.createState(meta());
  C.applyTick(st, tick(0, "off", [[0, 3], [3, 1]]));
  C.applyTick(st, tick(0, "off", [[5, 2], [7, 2]]));
  const g = C.groupTotals(st, "off");
  const sum = g.reduce((s, x) => s + x.joules, 0);
  assert.ok(Math.abs(sum - st.arm.off.joules) < 1e-9);
  assert.ok(Math.abs(g.reduce((s, x) => s + x.share, 0) - 1) < 1e-9);
});

test("a meter in the tick beats the priced rate", () => {
  const st = C.createState(meta());
  C.applyTick(st, tick(0, "off", [[0, 1], [1, 1]], { energy_j: 10 }));
  assert.equal(st.arm.off.joules, 10);
});

test("step attribution keeps steps apart", () => {
  const st = C.createState(meta());
  C.applyTick(st, tick(0, "off", [[0, 4]]));
  C.applyTick(st, tick(1, "off", [[7, 4]]));
  assert.equal(C.sumSteps(st, [0], "off")[0], 4);
  assert.equal(C.sumSteps(st, [0], "off")[7], 0);
  const using = C.stepsUsingGroup(st, st.assign[7], "off");
  assert.equal(using[0].step, 1);
  assert.match(C.captionForSteps(st, [1], "off"), /ESTIMATE/);
});

test("decay halves recent activity over its half life", () => {
  const st = C.createState(meta());
  C.bump(st, tick(0, "off", [[0, 8]]));
  const a = st.recent[0];
  C.decay(st, 0.7);
  assert.ok(Math.abs(st.recent[0] - a / 2) < 1e-9);
});
