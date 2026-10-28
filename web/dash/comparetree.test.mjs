// Structure + value tests for the Compare nested tree (v2r7.js). These run
// with `node --test web/`; v2r7.js is import-safe in node (no DOM at load).
import { test } from "node:test";
import assert from "node:assert/strict";
import { TREE, treeChildren, treeFind, treeValue, gateStatus, contextSaver } from "./v2r7.js";

const ids = (arr) => arr.map((n) => n.id);

test("top level under Power is the five branches, in order", () => {
  assert.deepEqual(ids(treeChildren([])), ["owngpu", "waste", "where", "gate", "sam"]);
  assert.deepEqual(ids(TREE), ["owngpu", "waste", "where", "gate", "sam"]);
});

test("nested levels match the spec", () => {
  assert.deepEqual(ids(treeChildren(["owngpu"])), ["caps", "topk", "quant"]);
  assert.deepEqual(ids(treeChildren(["owngpu", "caps"])), ["capsweep", "clocks"]);
  assert.deepEqual(ids(treeChildren(["waste"])), ["loopstop", "smallfirst", "context"]);
  assert.deepEqual(ids(treeChildren(["waste", "context"])), ["prefix", "memcomp", "dupcut"]);
  assert.deepEqual(ids(treeChildren(["where"])), ["own", "baseten", "modal"]);
  assert.deepEqual(ids(treeChildren(["gate"])), ["q", "speed", "budget"]);
});

test("leaves and unknown paths have no children", () => {
  assert.deepEqual(treeChildren(["sam"]), []);
  assert.deepEqual(treeChildren(["owngpu", "topk"]), []);
  assert.deepEqual(treeChildren(["nope"]), []);
});

test("gate nodes are marked, own-GPU-only levers are flagged", () => {
  assert.equal(treeFind("gate").kind, "gate");
  assert.equal(treeFind("speed").kind, "gate");
  for (const id of ["caps", "quant", "topk", "prefix"]) assert.equal(treeFind(id).own_only, true, id);
  assert.equal(treeFind("context").kids.length, 3);
});

const REC = {
  phases: {
    baseline: { metrics: { j_per_req: 15.96, quality_pct: 93.7 } },
    wattnot: { cap_w: 350, metrics: { j_per_req: 11.63, quality_pct: 93.7, p99_change_pct: 7.7, saved_pct: -27.1 } },
  },
};

test("gate value is pass/fail, not a saving", () => {
  const v = treeValue(REC, "power", treeFind("gate"));
  assert.deepEqual(v, { gate: true, pass: true });
  assert.deepEqual(treeValue(REC, "power", treeFind("speed")), { gate: true, pass: true });
  // a p99 over the limit fails the speed gate
  const slow = { phases: { baseline: { metrics: { quality_pct: 93 } }, wattnot: { metrics: { quality_pct: 93, p99_change_pct: 18 } } } };
  assert.deepEqual(gateStatus(slow, "speed"), { pass: false });
  // budget has no data in the record yet
  assert.equal(treeValue(REC, "power", treeFind("budget")), null);
});

test("caps reads the measured whole-run saving; unattributed levers are pending", () => {
  const caps = treeValue(REC, "power", treeFind("caps"));
  assert.equal(caps.tag, "MEASURED");
  assert.match(caps.text, /%$/);
  assert.equal(treeValue(REC, "power", treeFind("topk")), null); // not split out in this run
  assert.equal(treeValue(REC, "power", treeFind("quant")), null);
});

test("context saver is read defensively and is pending without data", () => {
  assert.equal(contextSaver(REC), null);
  assert.equal(treeValue(REC, "power", treeFind("context")), null);
  const withCS = { ...REC, context_saver: { tokens_cut_pct: { value: 12, label: "ESTIMATE" }, parts: { prefix: { tokens_cut_pct: { value: 5, label: "MOCK" } } } } };
  assert.deepEqual(treeValue(withCS, "power", treeFind("context")), { text: "12%", tag: "ESTIMATE" });
  assert.deepEqual(treeValue(withCS, "power", treeFind("prefix")), { text: "5%", tag: "MOCK" });
  assert.equal(treeValue(withCS, "power", treeFind("memcomp")), null);
});
