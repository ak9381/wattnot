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
test("selectedReplay", () => { assert.equal(selectedReplay("?run=replay:modal-r1"), "modal-r1"); assert.equal(selectedReplay("", "x"), null); });
test("series and runs list", async () => {
  const m = await replaySummary("modal-r1", f); assert.equal(m.series.sets[1].pts.length, 6); assert.equal(m.date, "2026-11-02");
  const h = await replaySummary("h100-max-1103", f); assert.equal(h.series.sets[0].pts.length, 2);
  const { replayRuns } = await import("./replaystats.js"); const rr = await replayRuns(f);
  assert.deepEqual(rr.map((x) => x.id), ["h100-stitched", "modal-stitched", "baseten-stitched"]);
});
