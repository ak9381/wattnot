import { test } from "node:test";
import assert from "node:assert/strict";
import { LINEUP, renderLineupHTML, parseLightup, lightupSequence } from "./metatools-lineup.js";

test("lineup lists all 8 Meta tools in narration order", () => {
  assert.deepEqual(LINEUP.map((t) => t.name), ["Muse Glimmer 30B", "Muse Spark Open", "Muse Spark", "Muse Wonder",
    "Muse Image", "Muse Voice Transcribe", "SAM 3.1", "Muse Code"]);
  for (const t of LINEUP) { assert.ok(t.role.length > 5); assert.ok(!/\d+(\.\d+)?\s*(%|W|kWh|\$)/.test(t.role)); }
  assert.equal((renderLineupHTML().match(/class="lineup-card"/g) || []).length, 8);
});
test("lightup params and sequence", () => {
  assert.deepEqual(parseLightup("?lightup=1"), { on: true, step: 1400 });
  assert.deepEqual(parseLightup("?lightup=1&step=900"), { on: true, step: 900 });
  assert.equal(parseLightup("").on, false);
  assert.deepEqual(lightupSequence(3), [0, 1, 2, "all"]);
});
