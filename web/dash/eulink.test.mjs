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

test("headline water is derived at 0.36, the stored 1.8 value is shown only as the upper", () => {
  const d = extractEuLinkData(run2);
  const whMean = run2.candidate.wh_per_req.mean;
  const stored = run2.estimates.water_ml_per_req.value; // signed receipt stored this at the 1.8 upper factor
  // Headline must be the on-site 0.36 factor off measured energy, NOT the stored 1.8 figure.
  assert.equal(d.waterHeadlineMl, Number((whMean * 0.36).toFixed(6)));
  assert.notEqual(d.waterHeadlineMl, stored);
  assert.ok(d.waterHeadlineMl < stored, "0.36 headline must be below the 1.8 stored upper");
  // The stored value surfaces as the upper bound, flagged as coming from the signed receipt.
  assert.equal(d.waterUpperMl, stored);
  assert.equal(d.waterUpperStored, true);
  const h = renderEuLinkHTML(d);
  assert.match(h, /0\.36 L\/kWh on-site cooling, derived from the receipt's measured energy/);
  assert.match(h, /stored in the signed receipt at the 1\.8 upper factor/);
  // the headline value (0.36 x energy, 4-dp display) is the metric value, not the 1.8 stored figure
  assert.match(h, /Water Consumption<\/div><div class="eulink-metric-val">0\.0012 /);
});

test("label comes from the receipt", () => {
  const d = extractEuLinkData({ receipt_id: "r1", measurement: { label: "SIMULATED" }, result: { billed_saving_pct: 5 } });
  assert.match(renderEuLinkHTML(d), /wn-dot-simulated/);
  assert.equal(reportHref("r1", "csv"), "eu/eu_report_r1.csv");
});
