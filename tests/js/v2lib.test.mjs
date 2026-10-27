import test from "node:test";
import assert from "node:assert/strict";
import { stageState, ledgerRows, reportRows, verifySummary, badge, num } from "../../web/dash/v2lib.js";

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
