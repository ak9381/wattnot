import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { extractEuLinkData, renderEuLinkHTML, renderEuCardHTML, reportHref } from "./eulink.js";

const run2 = JSON.parse(readFileSync(new URL("./eu/run2-receipt.json", import.meta.url)));

test("RUN2 receipt gives the measured headline numbers", () => {
  const d = extractEuLinkData(run2);
  assert.equal(d.label, "MEASURED");
  assert.equal(d.jBase.toFixed(2), "17.03");
  assert.equal(d.jCand.toFixed(2), "12.42");
  assert.equal(d.jChangePct.toFixed(1), "-27.1");
  assert.equal((d.passRate * 100).toFixed(1), "93.7");
  const h = renderEuCardHTML(d);
  assert.match(h, /EU efficiency report/);
  assert.match(h, /-27\.1%/);
  assert.match(h, /eu\/eu_report_rcpt_20261027_run2_cap350\.html/);
  assert.match(h, /href="eu\.html"/);
  assert.match(h, /CSRD roadmap/);
});

test("missing fields show a dash and pending dot, never invented values or MOCK", () => {
  const d = extractEuLinkData({});
  assert.equal(d.label, null);
  assert.equal(d.energyPerReqWh, null);
  assert.equal(d.billedSavingPct, null);
  for (const h of [renderEuLinkHTML(d), renderEuCardHTML(d)]) {
    assert.match(h, /–/);
    assert.match(h, /wn-dot-pending/);
    assert.doesNotMatch(h, /0\.04322|9\.4|MOCK/);
  }
});

test("label comes from the receipt", () => {
  const d = extractEuLinkData({ receipt_id: "r1", measurement: { label: "SIMULATED" }, result: { billed_saving_pct: 5 } });
  assert.match(renderEuLinkHTML(d), /wn-dot-simulated/);
  assert.equal(reportHref("r1", "csv"), "eu/eu_report_r1.csv");
});
