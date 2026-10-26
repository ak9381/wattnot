import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { replay, reduce, initialState, capPath, route, groupBounds, NODES, EDGES, GROUPS } from "./state.js";

const events = readFileSync(new URL("./demo-events.jsonl", import.meta.url), "utf8")
  .trim().split("\n").map((l) => JSON.parse(l));
const idx = (pred) => events.findIndex(pred);
const byId = Object.fromEntries(NODES.map((n) => [n.id, n]));

test("graph model is consistent: every node has a group, every edge has ends and a kind", () => {
  const groups = new Set(GROUPS.map((g) => g.id));
  for (const n of NODES) assert.ok(groups.has(n.group), n.id);
  for (const e of EDGES) {
    assert.ok(byId[e.from] && byId[e.to], e.id);
    assert.ok(["data", "meta", "revert", "optional"].includes(e.kind));
  }
  assert.equal(EDGES.filter((e) => e.kind === "revert").length, 2);
});

test("run.started fills the GPU node and marks the demo as EXAMPLE", () => {
  const s = replay(events, 1);
  assert.equal(s.active, "gpu");
  assert.equal(s.example, true);
  assert.match(s.nodes.gpu.sub, /gpu 0 · H100 \(fake\) · 200–700 W/);
});

test("mid-loop: Spark is active on try 3, the loop edge carries data and says the cap", () => {
  const n = idx((e) => e.type === "decision" && e.step_id === "cap:600") + 1;
  const s = replay(events, n);
  assert.equal(s.active, "choice");
  assert.deepEqual(s.hot, ["e-loop"]);
  assert.equal(s.loop, 2);
  const s3 = replay(events, idx((e) => e.type === "step.started" && e.step_id === "cap:600" && e.data.kind === "apply") + 1);
  assert.equal(s3.loopLabel, "try 2 · 600 W");
  assert.equal(capPath(s3), "700 → 650");
  assert.equal(s3.nodes.cap.sub, "650 → 600 W");
});

test("fake backend never shows MEASURED: baseline and power are SIMULATED", () => {
  const s = replay(events, idx((e) => e.type === "trial.measured" && e.step_id === "cap:650") + 1);
  assert.equal(s.simulated, true);
  assert.deepEqual(s.nodes.base.pills, ["SIMULATED"]);
  assert.match(s.nodes.base.sub, /pass 95%/);
  assert.deepEqual(s.nodes.power.pills, ["SIMULATED"]);
  assert.equal(s.progress, 100);
});

test("gate nodes show quality delta and p99 against their limits", () => {
  const s = replay(events, idx((e) => e.type === "action.applied" && e.step_id === "cap:650") + 1);
  assert.match(s.nodes.latency.sub, /^1025 \/ 1100 ms · pass$/);
  assert.match(s.nodes.quality.sub, /pass$/);
  assert.equal(s.nodes.verdict.verdict, "pass");
  assert.equal(s.active, "kept");
});

test("fail then auto-revert lights the red path", () => {
  const s = replay(events, idx((e) => e.type === "action.rolled_back") + 1);
  assert.equal(s.active, "revert");
  assert.deepEqual(s.hot, ["e-verdict-revert", "e-revert-cap"]);
  assert.equal(s.nodes.verdict.verdict, "fail");
  assert.deepEqual(s.reverted, { from: 500, to: 550, failed: ["p99"] });
  assert.equal(capPath(s), "700 → 650 → 600 → 550 → 500✕");
  assert.equal(s.current.cap_w, 550);
});

test("results: receipt, recommendations, comparison", () => {
  const s = replay(events);
  assert.equal(s.done, true);
  assert.equal(s.active, null);
  assert.equal(s.nodes.recs.status, "done");
  assert.equal(s.final.cap_w, 550);
  assert.equal(s.receiptView.signed, false); // demo is hashed, never claimed as signed
  assert.ok(s.receiptView.usd > 0 && s.receiptView.water > 0);
  assert.equal(s.compare.gate_passed, false);
  for (const a of s.recommendations.actions) assert.ok(["SIMULATED", "ESTIMATE", "ADVICE"].includes(a.label));
});

test("only real model calls count as Meta API calls", () => {
  let s = replay(events);
  assert.equal(s.meta.calls, 0);
  s = reduce(s, { seq: 999, type: "decision", step_id: "plan:9", data: {
    choice: { kind: "knob", knob: "max_num_seqs", value: 256 }, reason: "x", model: "muse-spark-1.3",
    tokens: { input: 382, output: 348 }, clamped: true } });
  assert.deepEqual(s.meta, { calls: 1, input: 382, output: 348, models: ["muse-spark-1.3"] });
  assert.match(s.nodes.choice.sub, /muse-spark-1.3 · 382\/348 tok · clamped/);
  assert.equal(s.nodes.knob.sub, "max_num_seqs = 256");
});

test("routing: orthogonal paths, labels on the longest segment, groups wrap their nodes", () => {
  const r = route(EDGES.find((e) => e.id === "e-loop"), byId);
  assert.ok(r.pts.every((p, i) => i === 0 || p[0] === r.pts[i - 1][0] || p[1] === r.pts[i - 1][1]));
  const g = groupBounds("gate", NODES);
  for (const n of NODES.filter((x) => x.group === "gate")) assert.ok(n.x >= g.x && n.y >= g.y);
  const moved = NODES.map((n) => (n.id === "verdict" ? { ...n, y: n.y + 200 } : n));
  assert.ok(groupBounds("gate", moved).h === g.h + 200);
});

test("reducer is pure", () => {
  const a = initialState();
  reduce(a, events[0]);
  assert.equal(a.active, null);
});
