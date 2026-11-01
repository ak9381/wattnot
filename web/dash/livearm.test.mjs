import test from "node:test";
import assert from "node:assert/strict";
import { armPhase, stageOff } from "./livearm.js";

test("without arm greys all but its baseline nodes", () => {
  assert.deepEqual(armPhase({ type: "run.started", data: { arm: "without_wattnot", baseline_nodes: ["workload", "baseten_model"] } }),
    [1, ["workload", "baseten_model"]]);
});
test("with arm lights the full map", () => {
  assert.deepEqual(armPhase({ type: "run.started", data: { arm: "with_wattnot" } }), [2, null]);
});
test("other events change nothing", () => {
  assert.equal(armPhase({ type: "step.finished", data: { arm: "without_wattnot" } }), null);
  assert.equal(armPhase({ type: "run.started", data: {} }), null);
});
test("stage table greys GPU-side nodes up front", () => {
  assert.deepEqual(stageOff({ type: "stage.features", data: { off_nodes: { telemetry: "hosted API exposes no power meter" } } }),
    { telemetry: "hosted API exposes no power meter" });
  assert.equal(stageOff({ type: "run.started", data: {} }), null);
});
import { stepFacts } from "./livearm.js";
test("step block shows only what the event carried, with labels", () => {
  const f = stepFacts({ type: "step.finished", data: { effort: "low", step_up: { from: "low", to: "medium" }, usd: 0.0012, usd_label: "MEASURED", wh: 0.01, passed: false, label: "MEASURED" } });
  assert.deepEqual(f.map((x) => x[0]), ["effort", "step-up", "gate", "Wh", "cost $"]);
  assert.equal(f[1][1], "low→medium"); assert.equal(f[2][1], "fail"); assert.equal(f[3][2], "ESTIMATE"); assert.equal(f[4][2], "MEASURED");
  assert.deepEqual(stepFacts({ type: "x", data: {} }), []);
});
