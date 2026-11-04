import test from "node:test";
import assert from "node:assert/strict";
import { latestHelperEvents, getJSON, rackResultText } from "./plugsteps.js";

test("only the latest helper press's rack check is shown", () => {
  const ev = [
    { step: "gpu_found" }, { step: "rack_check", label: "REPLAY (saved SAM 3.1 output)" }, { step: "waiting_approval" },
    { step: "gpu_found" }, { step: "rack_check", label: "MEASURED (live SAM 3.1 call)" }, { step: "waiting_approval" },
    { step: "arm_start" }, { step: "quality_check" }, { step: "restored" }];
  const out = latestHelperEvents(ev);
  assert.deepEqual(out.filter((e) => e.step === "rack_check").map((e) => e.label), ["MEASURED (live SAM 3.1 call)"]);
  assert.ok(out.some((e) => e.step === "quality_check"));
  assert.ok(!out.some((e) => e.step === "arm_start"));
});

test("getJSON gives up on a request that never answers", async () => {
  const hang = (_u, o) => new Promise((_res, rej) => o.signal.addEventListener("abort", () => rej(new Error("abort"))));
  const t0 = Date.now();
  assert.equal(await getJSON("/x", 50, hang), null);
  assert.ok(Date.now() - t0 < 2000);
  assert.deepEqual(await getJSON("/x", 50, async () => ({ ok: true, json: async () => ({ a: 1 }) })), { a: 1 });
});

test("rack OK message carries the SAM label", () => {
  assert.match(rackResultText({ rack_label: "MEASURED (live SAM 3.1 call)" }), /MEASURED \(live SAM 3.1 call\)/);
});
