import assert from "node:assert";
globalThis.document = { getElementById: () => null };
const { timeline, phrase } = await import("./replaypick.js");
const tl = timeline([{ ts: "2026-11-03T10:00:00Z" }, { ts: "2026-11-03T10:00:01Z" }, { ts: "2026-11-03T11:00:00Z" }]);
assert.equal(tl.items[1].at, 1); assert.equal(tl.items[2].at, 4); assert.ok(tl.cut > 3500);
assert.equal(phrase({ type: "telemetry.sample", data: { power_w: 300 } }), "GPU power 300 W");
assert.match(phrase({ type: "gate.result", data: { check: "quality", passed: false } }), /rolled back/);
console.log("replaypick ok");
