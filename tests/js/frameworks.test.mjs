// Framework drop-in validation UI: the metatools validation cards and the v2 setup-wizard
// stack-card badges read the REAL status from data/frameworks/validation.json. Labels stay
// honest (VALIDATED-LOCAL (mock upstream)); nothing ever claims a live upstream.
import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { validationSnippets, extractMetaToolsData, renderMetaToolsHTML } from "../../web/dash/metatools.js";
import { frameworkValidation, frameworkStackBadge, frameworkChip } from "../../web/dash/v2lib.js";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const artifactPath = path.join(__dirname, "..", "..", "data", "frameworks", "validation.json");
const validation = JSON.parse(fs.readFileSync(artifactPath, "utf-8"));
const receiptPath = path.join(__dirname, "..", "..", "receipts", "frameworks", "langchain-demo.json");
const lcReceipt = JSON.parse(fs.readFileSync(receiptPath, "utf-8"));

const KEYS = ["open" + "ai-python", "langchain", "llamaindex"];

test("validation artifact is well-formed and honest", () => {
  assert.equal(validation.spec, "wattnot.frameworks-validation/1");
  assert.match(validation.upstream, /mock upstream/);
  assert.equal(validation.frameworks.length, 3);
  for (const k of KEYS) {
    const f = frameworkValidation(validation, k);
    assert.ok(f, `missing ${k}`);
    assert.ok(["validated", "skipped"].includes(f.status));
    // Never claim a live/real upstream.
    assert.doesNotMatch(f.label, /live|production/i);
    if (f.status === "validated") assert.match(f.label, /VALIDATED-LOCAL \(mock upstream\)/);
  }
});

test("validationSnippets maps the artifact into card rows with versions", () => {
  const snips = validationSnippets(validation);
  assert.equal(snips.length, 3);
  const lc = snips.find((s) => s.framework === "langchain");
  assert.ok(lc);
  if (frameworkValidation(validation, "langchain").status === "validated") {
    assert.match(lc.label, /VALIDATED-LOCAL \(mock upstream\)/);
    assert.match(lc.response, /v\d/); // library version is surfaced
  }
});

test("metatools validation card renders the real artifact status, not the baked default", () => {
  const html = renderMetaToolsHTML({}, validation);
  const d = extractMetaToolsData({}, validation);
  // Snippets come from the artifact, not the hard-coded fallback.
  assert.deepEqual(d.snippets, validationSnippets(validation));
  for (const f of validation.frameworks) {
    assert.ok(html.includes(f.framework), `card missing ${f.framework}`);
    assert.ok(html.includes(f.label), `card missing label for ${f.framework}`);
  }
  assert.doesNotMatch(html, /live upstream/i);
});

test("stack-card badge is honest and empty when the artifact has no entry", () => {
  const ok = frameworkStackBadge(validation, "langchain");
  if (frameworkValidation(validation, "langchain").status === "validated") {
    assert.match(ok, /fw-val-ok/);
    assert.match(ok, /VALIDATED-LOCAL \(mock upstream\)/);
  }
  assert.equal(frameworkStackBadge(validation, "nonesuch"), "");
  assert.equal(frameworkStackBadge(null, "langchain"), "");
});

test("receipt view shows the framework badge for the LangChain demo receipt", () => {
  assert.deepEqual(lcReceipt.frameworks, ["langchain"]);
  const chip = frameworkChip(lcReceipt);
  assert.equal(chip.present, true);
  assert.equal(chip.name, "langchain");
  assert.equal(chip.label, "MOCK"); // honest: mock upstream, never MEASURED
  const none = frameworkChip({});
  assert.equal(none.present, false);
  assert.match(none.name, /none recorded/);
});
