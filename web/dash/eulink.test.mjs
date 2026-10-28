import { test } from "node:test";
import assert from "node:assert/strict";

import {
  extractEuLinkData,
  renderEuLinkHTML
} from "./eulink.js";

test("extractEuLinkData calculates energy, water, and joules per correct answer", () => {
  const sample = {
    receipt_id: "rcpt_test_eu",
    measurement: { label: "MEASURED" },
    candidate: {
      wh_per_req: { mean: 0.05, samples: [0.05, 0.05] },
      quality: { fixed_pass_rate: 0.95 }
    },
    estimates: {
      water_ml_per_req: { value: 0.0144, label: "ESTIMATE" }
    },
    result: { billed_saving_pct: 12.5 }
  };

  const data = extractEuLinkData(sample);
  assert.equal(data.receiptId, "rcpt_test_eu");
  assert.equal(data.label, "MEASURED");
  assert.equal(data.waterMlPerReq, 0.0144);
  assert.equal(data.billedSavingPct, 12.5);
  assert.ok(data.joulesPerCorrect > 0);
  assert.equal(data.csrdTag, "CSRD Roadmap");
  assert.match(data.disclaimer, /Not a compliance claim/);
});

test("renderEuLinkHTML renders EU directive card with labels and links", () => {
  const sample = {
    receipt_id: "rcpt_20261029_120000_m00005",
    measurement: { label: "SIMULATED" },
    candidate: {
      wh_per_req: { mean: 0.04322, samples: [0.04322] }
    },
    result: { billed_saving_pct: 9.4 }
  };

  const data = extractEuLinkData(sample);
  const html = renderEuLinkHTML(data);

  assert.match(html, /EU Energy Efficiency Directive/);
  assert.match(html, /EU 2024\/1364/);
  assert.match(html, /CSRD Roadmap/);
  assert.match(html, /Window IT Energy/);
  assert.match(html, /Joules \/ Correct/);
  assert.match(html, /Water Consumption/);
  assert.match(html, /Not a compliance claim/);
  assert.match(html, /\/api\/receipts\/rcpt_20261029_120000_m00005\/report\/eu\?format=html/);
});
