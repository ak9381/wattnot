import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { loadData, buildPanels } from "./scene4.js";

const here = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(here, "../..");
const fakeFetch = async (u) => ({ json: async () => JSON.parse(fs.readFileSync(u.startsWith("/") ? path.join(root, u) : path.join(here, u), "utf-8")) });

test("scene4 panels build from recorded files only", async () => {
  const p = buildPanels(await loadData(fakeFetch));
  assert.deepEqual(Object.keys(p), ["2", "3", "5", "6"]);
  const all = JSON.stringify(p);
  assert.match(all, /2254\.7 → 821\.6 J/);
  assert.match(all, /rolled back/);
  assert.match(all, /all 10 eye-test images read correctly/);
  assert.match(all, /wattnot-demo-deploy\/pull\/4/);
  assert.match(all, /Pebble/);
  assert.ok(!all.includes("NaN") && !all.includes("undefined"));
});
