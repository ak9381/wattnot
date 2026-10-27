import test from "node:test";
import assert from "node:assert/strict";
import { stageState, ledgerRows, reportRows, run2Rows, verifySummary, badge, num } from "../../web/dash/v2lib.js";

const pipe = [{ id: "baseline" }, { id: "sweep" }, { id: "receipt" }];
const ev = (type, node, extra = {}) => ({ event: { type, data: { node, ...extra } } });
const evs = [ev("step.started", "baseline"), ev("step.finished", "baseline", { status: "ok", summary: "done" }),
  ev("gate.result", "sweep", { check: "p99", passed: true, detail: { score: 1, threshold: 2 } }), ev("step.started", "receipt")];

test("stageState marks earlier stages done and tracks the active one", () => {
  const a = stageState(pipe, evs, 3);
  assert.equal(a.st.baseline, "done"); assert.equal(a.st.sweep, "run"); assert.equal(a.active, "sweep"); assert.equal(a.st.receipt, "todo");
  const b = stageState(pipe, evs, evs.length);
  assert.equal(b.active, null); assert.equal(b.st.receipt, "done");
});

test("ledgerRows lists steps and gates in plain words", () => {
  const r = ledgerRows(evs);
  assert.equal(r.length, 2); assert.match(r[1].text, /p99 check passed \(1 vs limit 2\)/);
});

test("reportRows keeps labels and never claims carbon", () => {
  const { rows } = reportRows({ levers: [{ wh_before: 0.014, wh_after: 0.011, saved_pct: 21, p99_before_s: 3.6, p99_after_s: 3.8, p99_change_pct: 3.7, quality: { state: "pending", text: "pending" }, usd_before: 2e-6, usd_after: 1.5e-6, per_million: { usd: 0.41, litres: 1.06 }, litres_before: 5e-6, litres_after: 4e-6 }] });
  const by = Object.fromEntries(rows.map((r) => [r.id, r]));
  assert.equal(by.energy.tag, "MEASURED"); assert.equal(by.cost.tag, "ESTIMATE"); assert.equal(by.carbon.wattnot, "n/a"); assert.equal(by.quality.tag, null);
});

test("verifySummary reads hash and signature steps", () => {
  const v = verifySummary({ receipts: [{ sample: "real", grade: "ESTIMATE", steps: [{ name: "sha256", ok: true, expected: "ab" }, { name: "signature", ok: true, signer: "k" }] }] });
  assert.equal(v[0].hash, true); assert.equal(v[0].sig, true); assert.equal(v[0].sha, "ab");
});

test("badge and num", () => { assert.match(badge("MEASURED"), /b-measured/); assert.equal(num(null), "n/a"); assert.equal(num(21.04, 1), "21"); });

test("run2Rows labels measured energy honest estimates", () => {
  const r = { j_per_req_stock: 15.959, j_per_req_cap: 11.635, saved_pct: -27.1, p99_s_stock: 3.605, p99_s_cap: 3.882, p99_change_pct: 7.7, quality_stock_pct: 93.7, quality_cap_pct: 93.7, cap_w: 350, source: "data/h100-run2/vm/steps/cap350.json" };
  const rows = run2Rows(r, { usd_per_kwh: 0.1387, name: "x" }, { factor_l_per_kwh: 0.36, name: "y" });
  const by = Object.fromEntries(rows.map((x) => [x.id, x]));
  assert.equal(by.energy.tag, "MEASURED"); assert.equal(by.energy.delta, "-27.1%");
  assert.equal(by.p99.tag, "MEASURED"); assert.equal(by.p99.delta, "+7.7%");
  assert.equal(by.quality.tag, "MEASURED"); assert.equal(by.quality.delta, "unchanged");
  assert.equal(by.cost.tag, "ESTIMATE"); assert.equal(by.water.tag, "ESTIMATE");
  assert.deepEqual(run2Rows(null, {}, {}), []);
});
