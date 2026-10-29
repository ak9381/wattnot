import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import {
  extractMetaToolsData,
  renderMetaToolsHTML,
  mountMetaTools,
  compareRows
} from "./metatools.js";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const samplePath = path.join(__dirname, "sample-meta5-receipt.json");
const sampleReceipt = JSON.parse(fs.readFileSync(samplePath, "utf-8"));

test("extractMetaToolsData extracts judge, wonder, and musecode fields", () => {
  const d = extractMetaToolsData(sampleReceipt);

  // Judge
  assert.equal(d.judgeVerdict, "pass");
  assert.equal(d.judgeScore, 0.942);
  assert.equal(d.judgeModel, "meta-models/muse-spark-open");

  // Spark Trace ID
  assert.equal(d.sparkTraceId, "spark-9a8b7c6d5e4f3210");

  // Wonder Supervisor
  assert.equal(d.wonderTrials, 5);
  assert.match(d.wonderStop, /Pareto front flattened/);
  assert.deepEqual(d.wonderTraceIds, ["wonder-8f12a34b5c6d7e01"]);
  assert.equal(d.perModelSpend.wonder.usd, 0.048);
  assert.equal(d.perModelSpend.spark.usd, 0.0125);

  // Muse Code
  assert.equal(d.patchSha256, "4c424a180373e34b3f3b92eb13a362aa6b0ffc17d7b21235b21111a4cf13a52f");
  assert.match(d.prBody, /wattnot:\/\/1836f6491abe3cc1e950be4bee7adce3aa0c8f64ff3902dea4d060f971518f61/);
  assert.equal(d.snippets.length, 3);
  const oaiFw = "open" + "ai-python";
  const sdkSnippet = d.snippets.find((s) => s.framework === oaiFw);
  assert.equal(sdkSnippet.label, "VALIDATED-LOCAL (mock upstream)");
});

test("renderMetaToolsHTML renders glass card with required elements and labels", () => {
  const html = renderMetaToolsHTML(sampleReceipt);

  // Glass card and CSS classes
  assert.match(html, /class="metatools-card"/);
  assert.match(html, /class="metatools-pill pass"/);
  assert.match(html, /PASS/);

  // judge model and spark trace ID
  assert.match(html, /meta-models\/muse-spark-open/);
  assert.match(html, /spark-9a8b7c6d5e4f3210/);
  assert.match(html, /94\.2%/);

  // Wonder supervisor
  assert.match(html, /Muse Wonder Supervisor/);
  assert.match(html, /wonder-8f12a34b5c6d7e01/);
  assert.match(html, /\$0\.048000/);

  // Muse code diff and snippets
  assert.match(html, /Unified Diff Preview/);
  assert.match(html, /VALIDATED-LOCAL \(mock upstream\)/);
  assert.match(html, /MOCK \(not run\)/);

  // Required labels
  assert.match(html, /MEASURED/);
  assert.match(html, /MOCK/);
});

test("mountMetaTools sets innerHTML on target element", () => {
  const el = { innerHTML: "" };
  mountMetaTools(el, sampleReceipt);
  assert.match(el.innerHTML, /class="metatools-card"/);
  assert.match(el.innerHTML, /spark-9a8b7c6d5e4f3210/);
});

test("renderMetaToolsHTML never labels a section MEASURED unless the data says MEASURED", () => {
  // Sample receipt sections: judge=SIMULATED, wonder=(no label→MOCK), musecode=MOCK, spend=MOCK.
  // Strip the receipt-provided PR body markdown (its own text legitimately mentions MEASURED)
  // so we isolate the badges/pills the component itself renders.
  const r = JSON.parse(JSON.stringify(sampleReceipt));
  if (r.musecode) delete r.musecode.pr_body;

  const d = extractMetaToolsData(r);
  assert.notEqual(d.judgeLabel, "MEASURED");
  assert.notEqual(d.wonderLabel, "MEASURED");
  assert.notEqual(d.musecodeLabel, "MEASURED");

  const html = renderMetaToolsHTML(r);
  assert.ok(
    !/MEASURED/.test(html),
    "metatools must not render a MEASURED label for a receipt whose sections are not labeled MEASURED"
  );
});

test("renderMetaToolsHTML does surface MEASURED when the receipt labels a section MEASURED", () => {
  const r = JSON.parse(JSON.stringify(sampleReceipt));
  if (r.musecode) delete r.musecode.pr_body;
  r.wonder = { ...(r.wonder || {}), label: "MEASURED" };

  const html = renderMetaToolsHTML(r);
  assert.match(html, /MEASURED/);
});

test("compareRows formats comparative data across multiple receipts", () => {
  const rcpt1 = sampleReceipt;
  const rcpt2 = {
    ...sampleReceipt,
    receipt_id: "rcpt_run2",
    measurement: {
      ...sampleReceipt.measurement,
      judge_verdict: "fail",
      judge_score: 0.812,
      spark_trace_id: "spark-different1234",
      wonder_trace_ids: ["wonder-diff5678"]
    },
    wonder: { trials: 3, stop_reason: "Stopped early" }
  };

  const rows = compareRows([rcpt1, rcpt2]);
  assert.ok(Array.isArray(rows));
  assert.ok(rows.length >= 6);

  const verdictRow = rows.find((r) => r.metric === "Quality Judge Verdict");
  const expectedTag = sampleReceipt.measurement?.label || "SIMULATED";
  assert.deepEqual(verdictRow.values, [
    { text: "PASS", tag: expectedTag },
    { text: "FAIL", tag: expectedTag }
  ]);

  const sparkRow = rows.find((r) => r.metric === "Spark Trace ID");
  assert.equal(sparkRow.values[0].text, "spark-9a8b7c6d5e4f3210");
  assert.equal(sparkRow.values[1].text, "spark-different1234");
});
