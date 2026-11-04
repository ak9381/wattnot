import test from "node:test";
import assert from "node:assert/strict";
import { gwlHeadline, gwlSpend } from "./gwlcard.js";

const f = { series: new Array(240).fill({ wh: 0.01, wh_baseline: 0.012 }), measured: {
  latest: { workload: "voice", requests: 60, energy_wh: { baseline: 0.02, actual: 0.015 }, spend_usd: 0.1 },
  all_runs: { runs: 4, requests: 240, energy_wh: { baseline: 0.1, actual: 0.08 }, spend_usd: 0.9 } } };

test("headline is the latest run, totals are a second all-runs line", () => {
  const h = gwlHeadline(f, null);
  assert.match(h.main, /^Latest run \(voice\): 60 requests · 15\.0 mWh with vs 20\.0 mWh stock \(25\.0% saved\)/);
  assert.ok(!h.main.includes("240"));
  assert.match(h.all, /^All runs \(4\): 240 requests · 80\.0 mWh with vs 100\.0 mWh stock \(20\.0% saved\)/);
  assert.equal(h.label, "MEASURED");
});

test("one run: no all-runs line; no measured runs: live series fallback", () => {
  const one = { measured: { ...f.measured, all_runs: { ...f.measured.all_runs, runs: 1 } } };
  assert.equal(gwlHeadline(one, null).all, "");
  const live = gwlHeadline({ series: [{ wh: 0.001, wh_baseline: 0.002, label: "ESTIMATE" }] }, { running: true, done: 1, total: 20 });
  assert.match(live.main, /^1 requests .* sending 1\/20$/);
  assert.equal(live.label, "ESTIMATE");
});

test("spend: latest run first, all runs second", () => {
  assert.equal(gwlSpend({ latest: { spend_usd: 0.1 }, all_runs: { spend_usd: 0.9 }, spend_usd: 0.9 }),
    "Spend, latest run: $0.1000 · all runs: $0.9000");
  assert.equal(gwlSpend({ spend_usd: 0.25 }), "Spend: $0.2500");
});
