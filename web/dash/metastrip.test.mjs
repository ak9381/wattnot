import { test } from "node:test";
import assert from "node:assert/strict";
import { addEvent, renderMetaStrip, modelOf } from "./metastrip.js";

test("strip fills only from events, keeps labels, and says when a model made no call", () => {
  assert.equal(modelOf({ data: { model: "muse-spark-open" } }), "open");
  assert.equal(modelOf({ data: { model: "muse-spark-1.3" } }), "spark");
  let s = {};
  s = addEvent(s, { type: "step.finished", data: { model: "muse-spark-1.3", trace_id: "resp_9", latency_ms: 700, usd: 0.0004, usd_label: "ESTIMATE", latency_label: "MEASURED" } });
  s = addEvent(s, { type: "step.started", data: { model: "muse-spark-1.3" } });
  s = addEvent(s, { type: "step.finished", data: { model: "muse-wonder-1.0", label: "REPLAY", duration_s: 2 } });
  const html = renderMetaStrip(s);
  assert.equal(s.spark.calls, 1);
  assert.ok(html.includes("resp_9") && html.includes("700 ms") && html.includes("ESTIMATE"));
  assert.ok(html.includes("REPLAY") && html.includes("2000 ms"));
  assert.equal((html.match(/no call in this run/g) || []).length, 5);
});
