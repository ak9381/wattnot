import test from "node:test";
import assert from "node:assert/strict";
import {
  poweredByFor, poweredByHtml, plannerTag, fillPlannerTags,
  partnersHtml, loadPartners,
} from "../../web/dash/partners.js";

const snap = {
  format: "wattnot.partners/1",
  partners: [
    { id: "meta", name: "Meta", what: "Muse Spark API (planner)", status: "used", calls: 464,
      cost: { value: 10.69, unit: "USD", label: "ESTIMATE" }, source: "meta-usage/LOG.md" },
    { id: "baseten", name: "Baseten", what: "hosted-model runs", status: "used", calls: 1,
      cost: { value: 0.0002, unit: "USD", label: "MEASURED" }, source: "meta-usage/BASETEN-LOG.md" },
    { id: "e2e", name: "E2E Networks", what: "the rented H100", status: "used", calls: null,
      cost: { value: 1215.0, unit: "INR", label: "ESTIMATE" }, source: "e2e-runs.json" },
    { id: "nvidia", name: "NVIDIA", what: "GPU power counter (NVML)", status: "free", calls: null,
      cost: { value: null, unit: null, label: null }, source: "read from the card" },
    { id: "fireworks", name: "Fireworks", what: "not used yet", status: "not_used", calls: null,
      cost: { value: null, unit: null, label: null }, source: null },
  ],
};

test("partnersHtml shows name, what, calls and cost with labels", () => {
  const h = partnersHtml(snap);
  assert.match(h, /Partners/);
  assert.match(h, /Muse Spark API/);
  assert.match(h, /464/);
  assert.match(h, /\$10\.69 USD/);
  assert.match(h, /ESTIMATE/);
  assert.match(h, /MEASURED/);
});

test("a null cost is a dash with no label", () => {
  const h = partnersHtml({ partners: [snap.partners[3]] });
  assert.match(h, /NVIDIA/);
  assert.match(h, /–/);
  assert.doesNotMatch(h, /MEASURED|ESTIMATE/);
});

test("a null calls count is a dash", () => {
  const h = partnersHtml({ partners: [snap.partners[2]] });
  assert.match(h, /calls <b class="num">–<\/b>/);
});

test("not_used partners say so and never show numbers", () => {
  const h = partnersHtml({ partners: [snap.partners[4]] });
  assert.match(h, /not used yet/);
  assert.doesNotMatch(h, /\$|calls/);
});

test("hostile strings are escaped", () => {
  const h = partnersHtml({
    partners: [{ id: "x", name: "<img src=x onerror=a>", what: "<b>w</b>", status: "used", calls: 1,
      cost: { value: 2, unit: "USD<script>", label: "MEASURED" } }],
  });
  assert.doesNotMatch(h, /<img src=x/);
  assert.doesNotMatch(h, /<b>w<\/b>/);
  assert.doesNotMatch(h, /<script>/);
});

test("partnersHtml is empty when there is nothing to show", () => {
  assert.equal(partnersHtml(null), "");
  assert.equal(partnersHtml({ partners: [] }), "");
});

test("loadPartners shows nothing when the endpoint 404s or fails", async () => {
  const gone = { innerHTML: "" };
  await loadPartners(gone, async () => ({ ok: false, status: 404 }));
  assert.equal(gone.innerHTML, "");
  const down = { innerHTML: "" };
  await loadPartners(down, async () => {
    throw new Error("down");
  });
  assert.equal(down.innerHTML, "");
});

test("loadPartners fills the block on success", async () => {
  const el = { innerHTML: "" };
  await loadPartners(el, async () => ({ ok: true, json: async () => snap }));
  assert.match(el.innerHTML, /Partners/);
  assert.match(el.innerHTML, /E2E Networks/);
});

test("poweredByHtml names the partner in text", () => {
  const h = poweredByHtml([{ id: "e2e", name: "E2E H100", what: "the GPU it runs on" }]);
  assert.match(h, /powered by/);
  assert.match(h, /E2E H100/);
  assert.equal(poweredByHtml([]), "");
  assert.equal(poweredByHtml(null), "");
});

test("a planner entry shows nothing until the events name it", () => {
  const h = poweredByHtml([{ id: "planner", name: "planner", what: "proposes the next cap" }]);
  assert.doesNotMatch(h, /powered by/);
  assert.match(h, /data-pp/);
});

test("poweredByFor falls back to the static facts", () => {
  assert.deepEqual(poweredByFor({ id: "approve" }), [{ id: "operator", name: "Operator", what: "a person decides" }]);
  assert.deepEqual(poweredByFor({ id: "room" })[0].name, "Meta SAM 3.1");
  const own = [{ id: "e2e", name: "E2E H100", what: "x" }];
  assert.equal(poweredByFor({ id: "sweep", powered_by: own }), own);
  assert.deepEqual(poweredByFor({ id: "nope" }), []);
  assert.deepEqual(poweredByFor(null), []);
});

test("plannerTag resolves meta and baseten, nothing else", () => {
  assert.equal(plannerTag("meta").text, "Meta · Muse Spark API");
  assert.equal(plannerTag("baseten").text, "Baseten · Muse Spark Open");
  assert.equal(plannerTag("other"), null);
  assert.equal(plannerTag(undefined), null);
});

test("fillPlannerTags fills placeholders and is safe without data", () => {
  const els = [
    { hidden: true, textContent: "", style: { setProperty() {} }, setAttribute() {} },
  ];
  const root = { querySelectorAll: (sel) => (sel === "[data-pp]" ? els : []) };
  assert.equal(fillPlannerTags(root, "meta"), 1);
  assert.equal(els[0].hidden, false);
  assert.equal(els[0].textContent, "Meta · Muse Spark API");
  assert.equal(fillPlannerTags(root, undefined), 0);
  assert.equal(fillPlannerTags(null, "meta"), 0);
});

test("the Agent waste node is tagged Meta Muse Spark and the Meta card shows its spend", () => {
  const h = poweredByHtml(poweredByFor({ id: "waste" }));
  assert.match(h, /Meta Muse Spark/);
  const c = partnersHtml({ partners: [{ id: "meta", name: "Meta", what: "x", status: "used", calls: 3,
    cost: { value: 2, unit: "USD", label: "ESTIMATE" },
    agent_waste: { usd: 1.4752, label: "MEASURED" } }] });
  assert.match(c, /of which Agent waste/);
  assert.match(c, /\$1\.4752/);
});
