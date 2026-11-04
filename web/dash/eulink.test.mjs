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

test("window IT energy is mean Wh/req x requests, not the sum of reps x requests", () => {
  const d = extractEuLinkData(run2);
  const whMean = run2.candidate.wh_per_req.mean;
  const n = run2.subject.workload.n_requests;
  const samples = run2.candidate.wh_per_req.samples;
  // correct: mean energy per request times the request count
  assert.equal(d.windowKwh, (whMean * n) / 1000);
  // the old sum-of-reps formula triple-counts (3 reps) and is ~3x larger
  const buggy = (samples.reduce((a, b) => a + b, 0) * n) / 1000;
  assert.ok(buggy > d.windowKwh * 2.9 && buggy < d.windowKwh * 3.1,
    `old formula (${buggy}) should be ~3x the corrected window (${d.windowKwh})`);
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

test("upload button and upload flow", async () => {
  const { renderEuCardHTML, extractEuLinkData, pickUpload, uploadEu } = await import("./eulink.js");
  assert.match(renderEuCardHTML(extractEuLinkData({})), /Upload receipt<input type="file"/);
  const f = (name, obj) => ({ name, text: async () => JSON.stringify(obj) });
  const rec = f("rcpt_x.json", { receipt_id: "rcpt_x" }), bun = f("rcpt_x.sigstore.json", { mediaType: "m" });
  assert.deepEqual(pickUpload([bun, rec]), { receipt: rec, bundle: bun });
  let sent;
  const ok = await uploadEu([rec, bun], { fetchFn: async (u, o) => { sent = { u, o }; return { ok: true, text: async () => "<table>" }; } });
  assert.equal(ok.ok, true); assert.match(sent.u, /\/api\/report\/eu\/upload\?format=html/);
  assert.deepEqual(Object.keys(JSON.parse(sent.o.body)), ["receipt", "bundle"]);
  const bad = await uploadEu([rec, bun], { fetchFn: async () => ({ ok: false, status: 409, json: async () => ({ code: "bad_signature" }) }) });
  assert.match(bad.msg, /signature does not match/);
  assert.match((await uploadEu([])).msg, /Pick a receipt/);
});

test("SCI carbon box shows only when the receipt has a signed sci block", async () => {
  const m = await import("./eulink.js");
  const base = { candidate: { wh_per_req: { mean: 0.03 } }, measurement: { label: "MEASURED" } };
  assert.equal(m.extractEuLinkData(base).sciGrid, null);
  assert.ok(!m.renderEuLinkHTML(m.extractEuLinkData(base)).includes("Carbon (SCI)"));
  const r = { ...base, sci: { I: { gco2e_per_kwh: 727, region: "IN", year: "FY 2023-24" },
    M: { status: "NOT_AVAILABLE" }, arms: { candidate: { gco2e_per_correct: 0.0234, gco2e_per_request: 0.0218 } } } };
  const d = m.extractEuLinkData(r);
  assert.equal(d.sciPerCorrect, 0.0234);
  const html = m.renderEuLinkHTML(d);
  assert.ok(html.includes("Carbon (SCI)") && html.includes("727 g/kWh") && html.includes("NOT_AVAILABLE"));
  assert.ok(!/neutral|offset/i.test(html));
});

test("CSV button is hidden for the rerun receipt, which has no CSV export", async () => {
  const { hasCsv } = await import("./eulink.js");
  assert.equal(hasCsv("rcpt_20261031_rerun_cap325"), false);
  assert.equal(hasCsv("r1"), true);
  const html = renderEuCardHTML({ receiptId: "rcpt_20261031_rerun_cap325" });
  assert.doesNotMatch(html, /CSV/);
});
