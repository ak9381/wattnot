import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { replaySummary, selectedReplay } from "./replaystats.js";
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
const f = async (u) => { const p = path.join(root, u); return fs.existsSync(p) ? { ok: true, json: async () => JSON.parse(fs.readFileSync(p, "utf-8")), text: async () => fs.readFileSync(p, "utf-8") } : { ok: false }; };
test("modal-r1 summary from its receipts", async () => {
  const s = await replaySummary("modal-r1", f);
  assert.equal(s.j_per_correct_stock, 2254.7); assert.equal(s.j_per_correct_cap, 821.6);
  assert.equal(s.energy_label, "MEASURED"); assert.equal(s.water_label, "ESTIMATE");
  assert.ok(s.p99_change_pct < 0); assert.ok(s.usd_per_1k_saved > 0);
});
test("h100-max-1103 summary from the signed proof", async () => {
  const s = await replaySummary("h100-max-1103", f);
  assert.equal(s.cap_w, 375); assert.equal(s.quality_cap_pct, 93.919); assert.ok(s.cost_why);
});
test("baseten has no water and says why", async () => {
  const s = await replaySummary("baseten-r1", f);
  assert.ok(s.water_why); assert.equal(s.cost_label, "MEASURED");
});
test("selectedReplay", () => { assert.equal(selectedReplay("?run=replay:modal-r1"), "modal-r1"); assert.equal(selectedReplay("", "x"), "plugin-h100-1104"); assert.equal(selectedReplay("", "replay:modal-r2"), "modal-r2"); });
test("series and runs list", async () => {
  const m = await replaySummary("modal-r1", f); assert.equal(m.series.sets[1].pts.length, 6); assert.equal(m.date, "2026-11-02");
  const h = await replaySummary("h100-max-1103", f); assert.equal(h.series.sets[0].pts.length, 2);
  const { replayRuns } = await import("./replaystats.js"); const rr = await replayRuns(f);
  assert.deepEqual(rr.map((x) => x.id), ["plugin-h100-1104", "plugin-modal-1104", "plugin-baseten-1104", "h100-max-1103", "modal-r2", "baseten-r1"]);
});
test("plugin gateway takes: headline take, every take listed, labels kept", async () => {
  const h = await replaySummary("plugin-h100-1104", f);
  assert.equal(h.headline_take, "seq-20261104-000651-text"); assert.equal(h.takes.length, 9); assert.equal(h.energy_label, "MEASURED");
  assert.ok(Math.abs(h.energy_saved_pct - 12.88) < 0.01);
  const b = await replaySummary("plugin-baseten-1104", f);
  assert.equal(b.energy_label, "ESTIMATE"); assert.ok(b.takes.some((t) => t.workload === "images")); assert.ok(b.water_why);
  const m = await replaySummary("plugin-modal-1104", f); assert.equal(m.energy_saved_pct, 74.36);
});
