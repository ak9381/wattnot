import { test } from "node:test";
import assert from "node:assert/strict";
import { renderSparkTrace, sparkCallFrom } from "./sparktrace.js";

test("spark trace shows four steps with trace id, latency and labelled cost", () => {
  const ev = { type: "step.finished", data: { kind: "spark_call", step: "gate", status: "ok", trace_id: "resp_1", latency_ms: 812.4, latency_label: "MEASURED", usd: 0.00042, usd_label: "ESTIMATE", label: "MEASURED", fixed_gate: "PASS", agrees_with_fixed_gate: false, summary: "Spark FAIL" } };
  const c = sparkCallFrom(ev);
  assert.ok(c);
  assert.equal(sparkCallFrom({ type: "step.started", data: { kind: "spark_call" } }), null);
  const html = renderSparkTrace([c]);
  assert.ok(html.includes("resp_1") && html.includes("812 ms") && html.includes("$0.00042"));
  assert.ok(html.includes("ESTIMATE") && html.includes("fixed gate stands"));
  assert.equal((html.match(/sparkRow/g) || []).length, 4);
  assert.ok(html.includes("1/4 calls"));
});
