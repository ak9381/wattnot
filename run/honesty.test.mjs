// Label honesty: a fake-backend stream must never show MEASURED anywhere on the run screen.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { initialState, reduce } from "./state.js";
import { attribute } from "./savings.js";

const load = (f) => readFileSync(new URL(f, import.meta.url), "utf8").trim().split("\n").map((l) => JSON.parse(l));

for (const f of ["./demo-events.jsonl", "./demo-events.live.jsonl"]) {
  test(`${f}: no MEASURED label at any point of a fake-backend replay`, () => {
    const events = load(f);
    assert.equal(events[0].data.host.provider, "fake");
    let s = initialState();
    for (const e of events) {
      s = reduce(s, e) || s;
      for (const n of Object.values(s.nodes)) assert.ok(!n.pills.includes("MEASURED"), `${e.type} -> ${n.pills}`);
    }
    assert.equal(s.simulated, true);
    for (const a of s.recommendations.actions) assert.notEqual(a.label, "MEASURED");
    const c = attribute(events).counters;
    assert.equal(c.power.label, "SIMULATED");
    assert.equal(c.wh.label, "SIMULATED");
    assert.equal(c.water.label, "ESTIMATE");
  });
}
