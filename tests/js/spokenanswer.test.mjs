import test from "node:test";
import assert from "node:assert/strict";
import { extractSpokenAnswer, renderSpokenAnswerHTML, mountSpokenAnswer } from "../../web/dash/spokenanswer.js";
import { poweredByFor } from "../../web/dash/partners.js";

const PASS = {
  measurement: {
    spoken_test: {
      label: "SYNTHETIC", transcriber: "muse-voice-transcribe-1.0", passed: true, checked: 2, passed_count: 2,
      missed: [], missed_noisy: [], reason: "all 2 spoken answers correct", model: "meta-models/Muse-Glimmer-30B",
      energy: {
        label: "MEASURED", meter: "NVML internal energy counter via pynvml",
        wh_per_answer: 0.35, j_per_spoken_answer: 1260.0, j_per_correct_spoken: 1326.3,
        j_per_token: 210.0, j_per_correct_answer: 42.0,
      },
      items: [
        { id: "q-gpus-clean", kind: "text", noise: "clean", expected: "8", got: "8", ok: true, label: "SYNTHETIC" },
        { id: "q-cap-datacenter", kind: "number", noise: "datacenter", expected: "350", got: "350", ok: true, label: "SYNTHETIC" },
      ],
    },
  },
};

const FAIL = {
  measurement: {
    spoken_test: {
      label: "SYNTHETIC", transcriber: "muse-voice-transcribe-1.0", passed: false, checked: 2, passed_count: 1,
      missed: ["q-cap-datacenter"], missed_noisy: ["q-cap-datacenter"],
      reason: "1/2 spoken answers wrong: q-cap-datacenter", model: "meta-models/Muse-Glimmer-30B",
      energy: {
        label: "MEASURED", meter: "NVML internal energy counter via pynvml",
        wh_per_answer: 0.35, j_per_spoken_answer: 1260.0, j_per_correct_spoken: 1326.3,
        j_per_token: 210.0, j_per_correct_answer: 42.0,
      },
      items: [
        { id: "q-gpus-clean", kind: "text", noise: "clean", expected: "8", got: "8", ok: true, label: "SYNTHETIC" },
        { id: "q-cap-datacenter", kind: "number", noise: "datacenter", expected: "350", got: "3 fifty", ok: false, label: "SYNTHETIC" },
      ],
    },
  },
};

test("extractSpokenAnswer reads the measurement block", () => {
  const d = extractSpokenAnswer(PASS);
  assert.equal(d.hasData, true);
  assert.equal(d.passed, true);
  assert.equal(d.checked, 2);
  assert.equal(d.transcriber, "muse-voice-transcribe-1.0");
  assert.equal(d.energy.label, "MEASURED");
});

test("no spoken test means no panel", () => {
  const d = extractSpokenAnswer({ measurement: {} });
  assert.equal(d.hasData, false);
  assert.equal(renderSpokenAnswerHTML(d), "");
});

test("checked == 0 keeps the panel hidden", () => {
  const d = extractSpokenAnswer({ measurement: { spoken_test: { label: "SYNTHETIC", checked: 0, items: [] } } });
  assert.equal(d.hasData, false);
  assert.equal(renderSpokenAnswerHTML(d), "");
});

test("a passing spoken test renders SYNTHETIC, the energy numbers and every item", () => {
  const h = renderSpokenAnswerHTML(extractSpokenAnswer(PASS));
  assert.match(h, /Spoken answers/);
  assert.match(h, /Muse Voice/);
  assert.match(h, /SYNTHETIC/);
  assert.match(h, /1,326 J\/correct spoken/);
  assert.match(h, /210 J\/token/);
  assert.match(h, /42 J\/correct answer/);
  assert.match(h, /MEASURED/);
  assert.match(h, /Passed/);
  assert.match(h, /q-gpus-clean/);
  assert.match(h, /q-cap-datacenter/);
  assert.match(h, /data-center/);
});

test("no energy block omits the energy badge", () => {
  const noEnergy = { measurement: { spoken_test: { ...PASS.measurement.spoken_test, energy: undefined } } };
  const h = renderSpokenAnswerHTML(extractSpokenAnswer(noEnergy));
  assert.doesNotMatch(h, /J\/correct spoken/);
  assert.match(h, /Spoken answers/);
});

test("a miss shows blocked and the misheard answer", () => {
  const h = renderSpokenAnswerHTML(extractSpokenAnswer(FAIL));
  assert.match(h, /Blocked/);
  assert.match(h, /3 fifty/);
  assert.match(h, /spk-fail/);
  // the data-center miss row is visible
  assert.match(h, /q-cap-datacenter/);
  assert.match(h, /data-center/);
});

test("mountSpokenAnswer fills the element and toggles hidden", () => {
  const el = { innerHTML: "", hidden: true };
  mountSpokenAnswer(el, PASS);
  assert.match(el.innerHTML, /Spoken answers/);
  assert.equal(el.hidden, false);

  const empty = { innerHTML: "x", hidden: false };
  mountSpokenAnswer(empty, { measurement: {} });
  assert.equal(empty.innerHTML, "");
  assert.equal(empty.hidden, true);
});

test("Muse Voice is a powered-by badge on the keep step", () => {
  const list = poweredByFor({ id: "keep" });
  assert.ok(list.some((p) => p.id === "muse_voice" && /Muse Voice/.test(p.name)));
  // the existing Muse Image badge stays
  assert.ok(list.some((p) => p.id === "muse_image"));
});
