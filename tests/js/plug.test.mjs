import test from "node:test";
import assert from "node:assert/strict";
import { defaultChoice, applyPrefill, headline, path, fmtWh } from "../../web/dash/pluglib.js";

test("defaults: gateway on Meta API, every saver on, langfuse off", () => {
  const c = defaultChoice();
  assert.equal(c.deployment, "gateway"); assert.equal(c.upstream, "meta"); assert.equal(c.langfuse, false);
  assert.ok(Object.values(c.savers).every(Boolean));
});
test("voice prefill fills known fields and leaves nulls alone", () => {
  const c = applyPrefill(defaultChoice(), { deployment: "both", upstream: null, savers_off: ["vision"], cap_min_w: 280, cap_max_w: 350, langfuse: true });
  assert.equal(c.deployment, "both"); assert.equal(c.upstream, "meta"); assert.equal(c.savers.vision, false);
  assert.equal(c.cap_min_w, 280); assert.equal(c.langfuse, true);
});
test("empty or replay feed says what to do next", () => {
  assert.equal(headline(null).label, "REPLAY");
  assert.match(headline({ source: "replay" }).sub, /step 3/);
});
test("live feed headline leads with energy saved and its label", () => {
  const h = headline({ source: "live", summary: { requests: 3, energy_wh: { actual: 1, baseline: 3, saved_pct: 66.7, label: "ESTIMATE" }, usd: { actual: 0.1, baseline: 0.3 } }, water: { litres: 0.1, litres_baseline: 0.3, label: "ESTIMATE (x)" } });
  assert.equal(h.big, "2.00 Wh saved"); assert.equal(h.label, "ESTIMATE"); assert.ok(Math.abs(h.usd - 0.2) < 1e-9);
});
test("path and units", () => {
  assert.equal(path([], "wh", 10, 10), "");
  assert.match(path([{ wh: 0 }, { wh: 1 }], "wh", 10, 10), /^M0\.0,10\.0 L10\.0,0\.0$/);
  assert.equal(fmtWh(0.5), "500.0 mWh"); assert.equal(fmtWh(2000), "2.00 kWh");
});

import { liveFromTest, liveLink, fromQuery, nodeHeadline } from "../../web/dash/pluglib.js";
test("passing test savers carry into Go live, the rest are off", () => {
  const c = liveFromTest(["waste", "router"]);
  assert.equal(c.savers.agent_waste, true); assert.equal(c.savers.routing, true);
  assert.equal(c.savers.vision, false); assert.equal(c.deployment, "gateway");
  assert.equal(liveFromTest(["caps"]).deployment, "gpu");
  assert.equal(liveFromTest(["caps", "waste"]).deployment, "both");
  assert.deepEqual(liveFromTest([]), liveFromTest(["unknown"]));
  const q = new URL("http://x/" + liveLink(["waste", "caps"])).search;
  assert.equal(fromQuery(q).deployment, "both");
  assert.equal(fromQuery("?a=1"), null);
});
test("GPU-only feed leads with the kept limit and its label", () => {
  assert.equal(nodeHeadline({}), null);
  const h = nodeHeadline({ node: { label: "REPLAY", events: [{ step: "kept", cap_w: 350, energy_change_pct: -27.09, label: "REPLAY" }] } });
  assert.match(h.big, /350 W/); assert.equal(h.label, "REPLAY"); assert.match(h.sub, /27.09%/);
});
test("plug page offers Test vs Go live and the GPU helper; report has Use these settings live", async () => {
  const fs = await import("node:fs");
  const html = fs.readFileSync(new URL("../../web/dash/plug.html", import.meta.url), "utf8");
  assert.match(html, /Test on my prompts/); assert.match(html, /Go live/); assert.match(html, /Add GPU helper/);
  const v2 = fs.readFileSync(new URL("../../web/dash/v2.js", import.meta.url), "utf8");
  assert.match(v2, /Use these settings live/);
});
