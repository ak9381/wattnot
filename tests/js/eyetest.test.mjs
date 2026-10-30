import test from "node:test";
import assert from "node:assert/strict";
import { extractEyeTest, renderEyeTestHTML, mountEyeTest } from "../../web/dash/eyetest.js";
import { poweredByFor } from "../../web/dash/partners.js";

const PASS = {
  measurement: {
    eye_test: {
      label: "SYNTHETIC", passed: true, checked: 2, passed_count: 2, missed: [],
      reason: "all 2 eye-test images read correctly", model: "meta-models/Muse-Glimmer-30B",
      est_wh_per_image: { value: 0.5, label: "ESTIMATE" },
      images: [
        { id: "plate-bus789", kind: "text", expected: "BUS-789", got: "BUS-789", ok: true, label: "SYNTHETIC" },
        { id: "count-cubes", kind: "count", expected: "7", got: "7", ok: true, label: "SYNTHETIC" },
      ],
    },
  },
};

const FAIL = {
  measurement: {
    eye_test: {
      label: "SYNTHETIC", passed: false, checked: 2, passed_count: 1, missed: ["plate-bus789"],
      reason: "vision misread 1/2 image(s): plate-bus789", model: "meta-models/Muse-Glimmer-30B",
      est_wh_per_image: { value: 0.5, label: "ESTIMATE" },
      images: [
        { id: "plate-bus789", kind: "text", expected: "BUS-789", got: "BUS-788", ok: false, label: "SYNTHETIC" },
        { id: "count-cubes", kind: "count", expected: "7", got: "7", ok: true, label: "SYNTHETIC" },
      ],
    },
  },
};

test("extractEyeTest reads the measurement block", () => {
  const d = extractEyeTest(PASS);
  assert.equal(d.hasData, true);
  assert.equal(d.passed, true);
  assert.equal(d.checked, 2);
  assert.equal(d.whLabel, "ESTIMATE");
});

test("no eye test means no panel", () => {
  const d = extractEyeTest({ measurement: {} });
  assert.equal(d.hasData, false);
  assert.equal(renderEyeTestHTML(d), "");
});

test("a passing eye test renders SYNTHETIC, the ESTIMATE energy stamp and every image", () => {
  const h = renderEyeTestHTML(extractEyeTest(PASS));
  assert.match(h, /Eye test/);
  assert.match(h, /Muse Image/);
  assert.match(h, /SYNTHETIC/);
  assert.match(h, /ESTIMATE/);
  assert.match(h, /0\.5 Wh\/image/);
  assert.match(h, /Passed/);
  assert.match(h, /plate-bus789/);
  assert.match(h, /count-cubes/);
});

test("a miss shows blocked and the misread answer", () => {
  const h = renderEyeTestHTML(extractEyeTest(FAIL));
  assert.match(h, /Blocked/);
  assert.match(h, /BUS-788/);
  assert.match(h, /eye-fail/);
});

test("mountEyeTest fills the element and toggles hidden", () => {
  const el = { innerHTML: "", hidden: true };
  mountEyeTest(el, PASS);
  assert.match(el.innerHTML, /Eye test/);
  assert.equal(el.hidden, false);

  const empty = { innerHTML: "x", hidden: false };
  mountEyeTest(empty, { measurement: {} });
  assert.equal(empty.innerHTML, "");
  assert.equal(empty.hidden, true);
});

test("Muse Image is a powered-by badge on the keep step", () => {
  const list = poweredByFor({ id: "keep" });
  assert.ok(list.some((p) => p.id === "muse_image" && /Muse Image/.test(p.name)));
});
