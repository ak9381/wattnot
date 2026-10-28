import test from "node:test";
import assert from "node:assert/strict";
import * as R from "../../web/dash/v2r7.js";

const good = { phases: { baseline: { metrics: { j_per_req: 10 } }, wattnot: { metrics: { j_per_req: 8 } } } };
const run2 = { model: "Llama 3.1 8B Instruct", cap_w: 350, j_per_req_stock: 15.959, j_per_req_cap: 11.635 };

test("compareState: empty, unreadable, ok, bundled", () => {
  assert.equal(R.compareState([], "default", good).kind, "empty");
  assert.equal(R.compareState(null, "x", good).kind, "empty");
  for (const bad of [null, undefined, {}, { junk: 1 }, [], { phases: {} }, { phases: { baseline: {} } }, "x"])
    assert.equal(R.compareState([{ id: "a" }], "a", bad).kind, "unreadable");
  assert.equal(R.compareState([{ id: "a" }], "a", good).kind, "ok");
  assert.equal(R.compareState([], R.BUNDLED_ID, null).kind, "bundled");
});

test("bundled record is labelled and only built from real RUN2 numbers", () => {
  const b = R.bundledRecord(run2);
  assert.ok(b.bundled && /Bundled sample/.test(b.run_name));
  assert.equal(R.savedPct(b, "power").text, "27.1%");
  assert.equal(R.bundledRecord(null), null);
  assert.equal(R.bundledRecord({ model: "x" }), null);
});

test("empty-state html", () => {
  assert.match(R.emptyStateHtml({ kind: "empty" }, true), /No runs yet[\s\S]*data-bundled/);
  const u = R.emptyStateHtml({ kind: "unreadable", id: "<r1>" }, false);
  assert.match(u, /couldn't be read/); assert.ok(!u.includes("data-bundled")); assert.ok(u.includes("&lt;r1&gt;"));
});
