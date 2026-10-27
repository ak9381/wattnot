// node --test web/room/room.test.mjs
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { palette } from "./room.js";

const scan = JSON.parse(readFileSync(new URL("./data/arsat/scan.json", import.meta.url), "utf8"));
const LABELS = new Set(["MEASURED", "ESTIMATE", "SIMULATED", "REPLAY", "MOCK", "ADVICE"]);

test("palette runs black to near white", () => {
  assert.deepEqual(palette(0), [0, 0, 4]);
  assert.deepEqual(palette(1), [255, 255, 225]);
});

test("scan json has the integration schema", () => {
  for (const k of ["room_id", "image", "thermal_image", "labels", "racks", "before", "after"]) assert.ok(k in scan, k);
  for (const r of scan.racks) for (const k of ["id", "polygon", "temp_c", "power_w", "gpus", "decision", "reason", "before", "after"]) assert.ok(k in r, k);
});

test("every label is a known label and the frame is not passed off as measured", () => {
  for (const v of Object.values(scan.labels)) assert.ok(LABELS.has(v), v);
  assert.equal(scan.thermal.mode, "simulated");
  assert.equal(scan.labels.thermal_frame, "SIMULATED");
  assert.equal(scan.labels.gpu_power_w, "REPLAY");
});

test("a cooling fault is held, not capped", () => {
  const hold = scan.racks.filter((r) => r.decision === "hold");
  assert.ok(hold.length >= 1);
  for (const r of hold) assert.equal(r.cap_w, null);
});
