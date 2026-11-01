import { test } from "node:test";
import assert from "node:assert/strict";
import { metaSpend } from "./v2lib.js";

test("live ledger wins and is MEASURED", () => {
  const m = metaSpend({ available: true, total_usd: 26.82, calls: 5307, cap_usd: 120 }, { total_usd: 23.22, calls: 4000 });
  assert.equal(m.usd, 26.82); assert.equal(m.label, "MEASURED"); assert.ok(m.live);
  assert.match(m.note, /live/);
});
test("no live ledger falls back to the bundle, labelled REPLAY", () => {
  for (const live of [null, { available: false }]) {
    const m = metaSpend(live, { total_usd: 23.22, calls: 4000 });
    assert.equal(m.usd, 23.22); assert.equal(m.label, "REPLAY"); assert.equal(m.live, false);
  }
});
test("nothing at all gives no number", () => {
  assert.equal(metaSpend(null, {}).usd, null);
});
