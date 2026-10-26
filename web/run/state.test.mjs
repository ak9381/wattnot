import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { replay, reduce, initialState, capPath } from "./state.js";

const events = readFileSync(new URL("./demo-events.jsonl", import.meta.url), "utf8")
  .trim().split("\n").map((l) => JSON.parse(l));
const idx = (pred) => events.findIndex(pred);

test("run.started lights Read telemetry and marks the demo as EXAMPLE", () => {
  const s = replay(events, 1);
  assert.equal(s.active, "read");
  assert.equal(s.example, true);
});

test("mid-loop: Spark node is active and the cap path grows", () => {
  const n = idx((e) => e.type === "decision" && e.step_id === "cap:550") + 1;
  const s = replay(events, n);
  assert.equal(s.active, "spark");
  assert.equal(s.nodes.baseline.status, "done");
  assert.equal(capPath(s), "700 → 650 → 600");
  assert.equal(s.loop, 3);
});

test("each node keeps its labels", () => {
  const s = replay(events, idx((e) => e.type === "gate.result" && e.step_id === "cap:650") + 1);
  assert.deepEqual(s.nodes.baseline.stats.map((x) => x.label), ["MEASURED", "MEASURED", "MEASURED"]);
  assert.equal(s.nodes.read.stats[0].label, "ESTIMATE"); // fake power source is an estimate
  assert.equal(s.nodes.gate.verdict, "pass");
});

test("fail then revert", () => {
  const s = replay(events, idx((e) => e.type === "action.rolled_back") + 1);
  assert.equal(s.active, "keep");
  assert.equal(s.nodes.keep.verdict, "reverted");
  assert.equal(capPath(s), "700 → 650 → 600 → 550 → 500✕");
  assert.equal(s.current.cap_w, 550);
});

test("results: recommendations, before/after and comparison", () => {
  const s = replay(events);
  assert.equal(s.done, true);
  assert.equal(s.active, null);
  assert.equal(s.recommendations.verdict, "change recommended");
  assert.equal(s.final.cap_w, 550);
  assert.ok(s.final.wh < s.baseline.wh);
  assert.ok(s.compare && s.compare.gate_passed === false);
  for (const a of s.recommendations.actions) assert.ok(["MEASURED", "ESTIMATE", "ADVICE"].includes(a.label));
});

test("only real model calls count as Meta API calls", () => {
  let s = replay(events);
  assert.equal(s.meta.calls, 0); // demo uses the fallback policy
  s = reduce(s, { seq: 99, type: "decision", step_id: "plan:9", data: {
    choice: { kind: "stop" }, reason: "x", model: "muse-spark-1.3",
    tokens: { input: 382, output: 348 }, clamped: false } });
  assert.deepEqual(s.meta, { calls: 1, input: 382, output: 348, models: ["muse-spark-1.3"] });
  assert.equal(s.nodes.spark.detail.model, "muse-spark-1.3");
});

test("reducer does not mutate its input", () => {
  const a = initialState();
  reduce(a, events[0]);
  assert.equal(a.active, null);
});
