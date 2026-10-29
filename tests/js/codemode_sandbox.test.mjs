// Code Mode sandbox card: the dashboard renders the MEASURED run side by side with the
// recorded "before", keeps each number's honesty label (MEASURED / TOKENIZER / ESTIMATE),
// and dims pending values (e.g. CPU-package energy when RAPL is unreadable) with a dot.
import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { sandboxCardHtml, sandboxRunHtml, sbCell, sbLabelClass, sandboxHeadline,
  SB_ROWS, mountSandbox, SANDBOX_RESULTS } from "../../web/dash/codemode.js";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const resultsPath = path.join(__dirname, "..", "..", "data", "agent-waste", "codemode", "sandbox_results.json");
const sandbox = JSON.parse(fs.readFileSync(resultsPath, "utf-8"));

test("sandbox artifact is the Code Mode sandbox schema with measured rows", () => {
  assert.equal(sandbox.spec, "wattnot.codemode-sandbox/1");
  assert.ok(sandbox.candidates >= 1);
  assert.equal(sandbox.rows.length, sandbox.candidates);
  // net isolation is one of the two honest options
  assert.ok(["unshare -n", "audit-hook"].includes(sandbox.env.net_isolation));
});

test("sbLabelClass maps the sandbox labels, defaulting to estimate", () => {
  assert.equal(sbLabelClass("MEASURED"), "MEASURED");
  assert.equal(sbLabelClass("MEASURED-vs-ESTIMATE"), "MEASURED");
  assert.equal(sbLabelClass("TOKENIZER"), "TOKENIZER");
  assert.equal(sbLabelClass("ESTIMATE"), "ESTIMATE");
  assert.equal(sbLabelClass(""), "ESTIMATE");
});

test("sbCell colours a measured number, a tokenizer count, and dims pending", () => {
  assert.match(sbCell({ value: 0.02, label: "MEASURED" }, 4), /lab-MEASURED/);
  const tok = sbCell({ value: 856, label: "TOKENIZER", note: "chars//4" }, 0);
  assert.match(tok, /lab-TOKENIZER/);
  assert.ok(tok.includes("856"));
  const pending = sbCell({ value: null, label: "PENDING", note: "no RAPL" }, 2);
  assert.match(pending, /n-pending/);
  assert.match(pending, /pdot/);
  assert.match(pending, /–/);
});

test("MEASURED and ESTIMATE are shown side by side for a real run", () => {
  const html = sandboxRunHtml(sandbox.rows[0]);
  // the measured sandbox wall time and the estimate model round trip both render with labels
  assert.match(html, /lab-MEASURED/);
  assert.match(html, /lab-ESTIMATE/);
  assert.match(html, /lab-TOKENIZER/);
  // every after-row name appears
  for (const { name } of SB_ROWS) assert.ok(html.includes(name), `missing row ${name}`);
  // the isolation facts are stated
  assert.match(html, /net isolation/);
  assert.match(html, /writes outside temp dir/);
});

test("the code-mode 'after' never fabricates a model latency number", () => {
  const row = sandbox.rows[0];
  assert.equal(row.after.model_latency_s.value, null);
  assert.equal(row.after.model_latency_s.label, "ESTIMATE");
  const html = sandboxRunHtml(row);
  assert.match(html, /n-pending/); // latency (and energy, when RAPL is unreadable) are dimmed
});

test("energy is measured-with-value or pending-with-reason, never invented", () => {
  const je = sandbox.rows[0].after.sandbox_energy_j;
  const okMeasured = je.label === "MEASURED" && je.value != null;
  const okPending = je.value == null && typeof je.note === "string" && je.note.length > 0;
  assert.ok(okMeasured || okPending, "energy must be measured or honestly pending");
});

test("headline names the isolation and is honest about measured vs estimate", () => {
  const h = sandboxHeadline(sandbox);
  assert.match(h, /sandbox/i);
  assert.match(h, /MEASURED/);
  assert.match(h, /ESTIMATE/);
  assert.match(h, /tokenizer/i);
  // zero candidates says none, invents nothing
  assert.match(sandboxHeadline({ candidates: 0 }), /No Code Mode candidates/);
});

test("full sandbox card renders and names the lock-down, no 'live' claim", () => {
  const html = sandboxCardHtml(sandbox);
  assert.match(html, /Code Mode sandbox/);
  assert.ok(html.includes(sandbox.rows[0].task));
  // it is explicit that nothing ran live: no live model, no network, no GPU
  assert.match(html, /No live model, no network, no GPU/);
  // the notes explain the labels
  assert.match(html.toLowerCase(), /measured|tokenizer|estimate/);
});

test("empty / missing sandbox results render a safe placeholder", () => {
  assert.match(sandboxCardHtml(null), /No Code Mode sandbox results/);
  assert.match(sandboxCardHtml({}), /No Code Mode sandbox results/);
});

test("mountSandbox renders from pre-loaded results and the fetch URL is shaped right", async () => {
  const el = { innerHTML: "" };
  await mountSandbox(el, sandbox);
  assert.match(el.innerHTML, /Code Mode sandbox/);
  await mountSandbox(null, sandbox); // null element is a no-op, never a throw
  assert.equal(SANDBOX_RESULTS, "codemode/sandbox_results.json");
});
