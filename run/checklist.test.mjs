import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { replay } from "./state.js";
import { buildChecklist, toggle, loadDone, progress, actionPayload, storageKey, fmtValue } from "./checklist.js";

const events = readFileSync(new URL("./demo-events.jsonl", import.meta.url), "utf8")
  .trim().split("\n").map((l) => JSON.parse(l));
const rec = replay(events).recommendations;

class Mem { constructor() { this.m = {}; } getItem(k) { return this.m[k] ?? null; } setItem(k, v) { this.m[k] = String(v); } }

test("one item per recommended action, each with a label, value and primary button", () => {
  const items = buildChecklist(rec);
  assert.equal(items.length, rec.actions.length);
  for (const i of items) assert.ok(i.button && ["MEASURED", "ESTIMATE", "SIMULATED", "ADVICE"].includes(i.label));
  const by = Object.fromEntries(items.map((i) => [i.id, i]));
  assert.equal(by.power_cap_w.button, "Apply cap");
  assert.equal(by.power_cap_w.value, "550 W");
  assert.equal(by.reverify.button, "Schedule re-run");
  assert.equal(by.usd_saved.button, "Download receipt");
  assert.match(by.usd_saved.value, /^\$\d+\.\d\d$/);
});

test("progress counts done items and persists per run", () => {
  const st = new Mem();
  const items = buildChecklist(rec);
  let done = loadDone(st, "r1");
  assert.equal(progress(items, done).text, `0 of ${items.length} done`);
  done = toggle(st, "r1", done, "power_cap_w");
  done = toggle(st, "r1", done, "reverify");
  assert.equal(progress(items, done).text, `2 of ${items.length} done`);
  assert.deepEqual([...loadDone(st, "r1")].sort(), ["power_cap_w", "reverify"]);
  assert.equal(loadDone(st, "r2").size, 0); // other runs are separate
  done = toggle(st, "r1", done, "reverify");
  assert.deepEqual(JSON.parse(st.getItem(storageKey("r1"))), ["power_cap_w"]);
});

test("corrupt storage starts empty instead of crashing", () => {
  const st = new Mem();
  st.setItem(storageKey("r1"), "{not json");
  assert.equal(loadDone(st, "r1").size, 0);
});

test("primary actions hand over commands and files, never run anything", () => {
  const items = Object.fromEntries(buildChecklist(rec).map((i) => [i.id, i]));
  assert.deepEqual(actionPayload(items.power_cap_w, { gpuIndex: 0 }), { type: "copy", text: "sudo nvidia-smi -i 0 -pl 550" });
  assert.equal(actionPayload({ kind: "copy_flag", raw: { value: "enable_prefix_caching=True" } }).text, "--enable-prefix-caching");
  assert.equal(actionPayload({ kind: "copy_flag", raw: { value: "max_num_seqs=256" } }).text, "--max-num-seqs 256");
  const ics = actionPayload(items.reverify, { now: new Date("2026-10-27T00:00:00Z"), runId: "r1" });
  assert.equal(ics.filename, "wattnot-rerun.ics");
  assert.match(ics.text, /DTSTART:20261126T000000Z/);
  const r = actionPayload(items.usd_saved, { runId: "r1", receipt: { a: 1 } });
  assert.equal(r.filename, "wattnot-r1-receipt.json");
});

test("value formatting", () => {
  assert.equal(fmtValue({ value: 1200, unit: "W" }), "1,200 W");
  assert.equal(fmtValue({ value: 8.1, unit: "%" }), "8.1%");
  assert.equal(fmtValue({ value: null }), "");
});
