import test from "node:test";
import assert from "node:assert/strict";
import { headline, trafficText, clock } from "./pluglib.js";
import { gwlCorrect } from "./gwlcard.js";

const m = { latest: { run_id: "seq-20261104-032655-text", workload: "text", requests: 60, ts: 1793763000,
  energy_wh: { baseline: 20, actual: 17.5, saved_pct: 12.49, label: "MEASURED" }, spend_usd: 0.15, spend_usd_stock: 0.15,
  correct: { with: 54, without: 54, scored: 60 }, j_per_correct: 1100, j_per_correct_stock: 1300 },
  all_runs: { runs: 7, requests: 380, energy_wh: { baseline: 99.1, actual: 84.4, saved_pct: 14.83 } } };
const feed = { source: "live", summary: { energy_wh: { baseline: 99.1, actual: 84.4, saved_pct: 14.83 }, usd: { baseline: 1, actual: 1 }, requests: 380 },
  water: { litres: 0, litres_baseline: 0 }, measured: m };

test("headline is the latest run, cumulative only as 'All runs'", () => {
  const h = headline(feed);
  assert.equal(h.pct, 12.49);
  assert.match(h.big, /2\.5/);
  assert.match(h.sub, /^Latest run seq-20261104-032655-text \(text\)/);
  assert.match(h.sub, /60 requests through WattNot/);
  assert.match(h.sub, /All runs \(7\): 380 requests/);
  assert.match(h.sub, new RegExp(clock(1793763000).replace(/[()+]/g, ".")));
});

test("no measured runs: falls back to the gateway summary", () => {
  assert.match(headline({ ...feed, measured: null }).sub, /^380 requests/);
});

test("finished run says which run and never shows more than the total", () => {
  const s = trafficText({ running: false, done: 26, total: 20, run_id: "seq-x", finished_ts: 1793763000 });
  assert.match(s, /^Done: 20 of 20 · last run seq-x finished /);
});

test("correct answers: latest run first, totals labelled All runs", () => {
  const out = gwlCorrect({ latest: m.latest, all_runs: m.all_runs, j_per_correct: 1200, j_per_correct_stock: 1400,
    correct: { with: 180, without: 180, scored: 200 } });
  const [a, b] = out.split("<br>").filter(Boolean);
  assert.match(a, /latest run seq-20261104-032655-text: 1100 J .*54\/60 vs 54\/60/);
  assert.match(b, /^All runs \(7\): .*180\/200/);
});
