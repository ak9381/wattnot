import { test } from "node:test"; import assert from "node:assert/strict";
import { ARCH, archHTML } from "./learnarch.js";
test("arch flow order and statuses", () => {
  assert.deepEqual(ARCH.slice(0, 11).map((b) => b.id), ["plug", "models", "nvml", "spark", "levers", "gate", "sam", "apply", "receipt", "eu", "code"]);
  assert.ok(ARCH.every((b) => ["built", "partial", "roadmap"].includes(b.st)));
  assert.match(archHTML("baseten-r1"), /baseten-r1/); assert.doesNotMatch(archHTML(), /la-key|Built, partly used/);
});
