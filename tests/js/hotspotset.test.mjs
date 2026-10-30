import test from "node:test";
import assert from "node:assert/strict";
import { extractHotspotSet, renderHotspotSetHTML, mountHotspotSet } from "../../web/dash/hotspotset.js";

const PASS = {
  measurement: {
    hotspot_test: {
      label: "SYNTHETIC", detector: "SAM 3.1", passed: true, checked: 2, passed_count: 2,
      missed: [], faults: ["blocked_fan", "clogged_heatsink"], iou_pass: 0.5,
      reason: "SAM 3.1 found all 2 SYNTHETIC hotspots",
      est_wh_per_image: { value: 0.5, label: "ESTIMATE" },
      scenes: [
        { id: "blocked-fan-0", fault: "blocked_fan", slot: 0, expected_bbox: [190, 110, 430, 250],
          got_bbox: [190, 110, 430, 250], iou: 1.0, ok: true, mask_label: "MOCK", label: "SYNTHETIC" },
        { id: "clogged-heatsink-1", fault: "clogged_heatsink", slot: 1, expected_bbox: [190, 280, 430, 420],
          got_bbox: [190, 280, 430, 420], iou: 1.0, ok: true, mask_label: "MOCK", label: "SYNTHETIC" },
      ],
    },
  },
};

const FAIL = {
  measurement: {
    hotspot_test: {
      label: "SYNTHETIC", detector: "SAM 3.1", passed: false, checked: 2, passed_count: 1,
      missed: ["blocked-fan-0"], faults: ["blocked_fan", "cable_airflow"], iou_pass: 0.5,
      reason: "SAM 3.1 missed 1/2 SYNTHETIC hotspot(s): blocked-fan-0",
      est_wh_per_image: { value: 0.5, label: "ESTIMATE" },
      scenes: [
        { id: "blocked-fan-0", fault: "blocked_fan", slot: 0, expected_bbox: [190, 110, 430, 250],
          got_bbox: [], iou: 0.0, ok: false, mask_label: "none", label: "SYNTHETIC" },
        { id: "cable-airflow-2", fault: "cable_airflow", slot: 2, expected_bbox: [190, 450, 430, 590],
          got_bbox: [190, 450, 430, 590], iou: 1.0, ok: true, mask_label: "MOCK", label: "SYNTHETIC" },
      ],
    },
  },
};

test("extractHotspotSet reads the measurement block", () => {
  const d = extractHotspotSet(PASS);
  assert.equal(d.hasData, true);
  assert.equal(d.passed, true);
  assert.equal(d.checked, 2);
  assert.equal(d.detector, "SAM 3.1");
  assert.equal(d.iouPass, 0.5);
  assert.equal(d.whLabel, "ESTIMATE");
});

test("no hotspot set means no panel", () => {
  const d = extractHotspotSet({ measurement: {} });
  assert.equal(d.hasData, false);
  assert.equal(renderHotspotSetHTML(d), "");
});

test("a passing set renders SAM 3.1, SYNTHETIC tests, the ESTIMATE energy stamp and every scene", () => {
  const h = renderHotspotSetHTML(extractHotspotSet(PASS));
  assert.match(h, /SAM hotspot set/);
  assert.match(h, /SAM 3\.1/);
  assert.match(h, /SYNTHETIC tests/);
  assert.match(h, /ESTIMATE/);
  assert.match(h, /0\.5 Wh\/image/);
  assert.match(h, /Passed/);
  assert.match(h, /blocked-fan-0/);
  assert.match(h, /clogged-heatsink-1/);
  assert.match(h, /blocked fan/);
  assert.match(h, /clogged heatsink/);
  // IoU threshold shown; never claims a real thermal measurement.
  assert.match(h, /≥ 0\.5/);
  assert.match(h, /never a real thermal measurement/);
});

test("a miss shows blocked and the missed scene", () => {
  const h = renderHotspotSetHTML(extractHotspotSet(FAIL));
  assert.match(h, /Blocked/);
  assert.match(h, /hset-fail/);
  assert.match(h, /missed/);
  assert.match(h, /blocked-fan-0/);
});

test("mountHotspotSet fills the element and toggles hidden", () => {
  const el = { innerHTML: "", hidden: true };
  mountHotspotSet(el, PASS);
  assert.match(el.innerHTML, /SAM hotspot set/);
  assert.equal(el.hidden, false);

  const empty = { innerHTML: "x", hidden: false };
  mountHotspotSet(empty, { measurement: {} });
  assert.equal(empty.innerHTML, "");
  assert.equal(empty.hidden, true);
});

test("the panel escapes untrusted scene fields", () => {
  const h = renderHotspotSetHTML(extractHotspotSet({
    measurement: { hotspot_test: { checked: 1, passed: false, missed: ["x"], scenes: [
      { id: "<script>", fault: "blocked_fan", slot: 0, iou: 0, ok: false, label: "SYNTHETIC" }] } },
  }));
  assert.doesNotMatch(h, /<script>/);
  assert.match(h, /&lt;script&gt;/);
});
