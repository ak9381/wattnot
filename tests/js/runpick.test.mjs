import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

globalThis.__WN_TEST__ = true;
const { pickInitial } = await import("../../web/dash/runpick.js");
const dashDir = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../../web/dash");

test("an asked-for run the server does not list is flagged, never swapped for the newest", () => {
  const runs = [{ id: "modal_a100:r04" }, { id: "job:x" }];
  assert.deepEqual(pickInitial("baseten:r04", runs, "job:x"), { id: "baseten:r04", missing: true });
  assert.deepEqual(pickInitial("modal_a100:r04", runs, "job:x"), { id: "modal_a100:r04", missing: false });
  assert.deepEqual(pickInitial(null, runs, "job:x"), { id: "job:x", missing: false });
});

test("the meter title says when the picked run is not the replay it shows", () => {
  const src = fs.readFileSync(path.join(dashDir, "archpage.js"), "utf8");
  assert.match(src, /title: meterTitle\(d\.replays\.h100\.title, pickedRun\(\)\)/);
});
