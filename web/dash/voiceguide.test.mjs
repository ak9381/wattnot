import { test } from "node:test";
import assert from "node:assert/strict";

import {
  classifyLocalIntent,
  quickAckForQuery,
  buildVoiceGuideDOM
} from "./voiceguide.js";

import {
  extractHotspotData,
  renderHotspotHTML
} from "./hotspot.js";

test("classifyLocalIntent categorizes queries correctly", () => {
  assert.equal(classifyLocalIntent("compare runs"), "navigate");
  assert.equal(classifyLocalIntent("show last night's report"), "navigate");
  assert.equal(classifyLocalIntent("configure run parameters"), "configure");
  assert.equal(classifyLocalIntent("prefill 350W caps"), "configure");
  assert.equal(classifyLocalIntent("what is the billed energy saving?"), "answer");
  assert.equal(classifyLocalIntent("random query"), "answer");
});

test("quickAckForQuery provides fast local acknowledgment under 300ms", () => {
  const navQuick = quickAckForQuery("compare runs");
  assert.equal(navQuick.intent, "navigate");
  assert.match(navQuick.ack, /Compare/);
  assert.equal(navQuick.label, "MOCK");

  const cfgQuick = quickAckForQuery("configure vllm");
  assert.equal(cfgQuick.intent, "configure");
  assert.match(cfgQuick.ack, /pre-fill/);
  assert.equal(cfgQuick.label, "MOCK");

  const ansQuick = quickAckForQuery("what was the energy consumption?");
  assert.equal(ansQuick.intent, "answer");
  assert.match(ansQuick.ack, /signed receipts/);
  assert.equal(ansQuick.label, "MOCK");
});

test("extractHotspotData extracts mask, decision, and bounded cap", () => {
  const sample = {
    measurement: {
      label: "SIMULATED",
      hotspot_cap: {
        mask: {
          bbox: [190, 280, 430, 420],
          polygon: [[190, 280], [430, 280], [430, 420], [190, 420]],
          sha256: "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
          label: "MOCK"
        },
        decision: {
          gpu_id: 1,
          old_cap_w: 400,
          new_cap_w: 350,
          delta_w: -50,
          min_cap_w: 250,
          label: "ADVICE"
        },
        reason: "Hotspot derate test"
      }
    }
  };

  const data = extractHotspotData(sample);
  assert.equal(data.hasData, true);
  assert.equal(data.gpuId, 1);
  assert.equal(data.oldCapW, 400);
  assert.equal(data.newCapW, 350);
  assert.equal(data.deltaW, -50);
  assert.equal(data.minCapW, 250);
  assert.equal(data.decisionLabel, "ADVICE");
  assert.equal(data.maskLabel, "MOCK");

  const html = renderHotspotHTML(data);
  assert.match(html, /Cooling Hotspots/);
  assert.match(html, /SAM 3\.1/);
  assert.match(html, /GPU 1/);
  assert.match(html, /350 W/);
  assert.match(html, /ADVICE/);
});
