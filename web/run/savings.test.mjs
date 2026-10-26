import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { attribute, waterfall, inUnit, usd, litres, monthlyKwh } from "./savings.js";

const load = (f) => readFileSync(new URL(f, import.meta.url), "utf8").trim().split("\n").map((l) => JSON.parse(l));
const demo = load("./demo-events.jsonl");
const close = (a, b) => assert.ok(Math.abs(a - b) < 1e-9, `${a} != ${b}`);

test("bar values sum to the total saving, in every unit", () => {
  const a = attribute(demo);
  close(a.bars.reduce((s, b) => s + b.netWh, 0), a.totalWh);
  close(a.bars.reduce((s, b) => s + b.netW, 0), a.totalW);
  for (const u of ["wh", "usd", "water", "w"]) {
    const w = waterfall(a.bars, u);
    const tot = u === "wh" ? a.totalWh : u === "usd" ? usd(a.totalWh) : u === "water" ? litres(a.totalWh) : a.totalW;
    close(w[w.length - 1].end, tot);
  }
});

test("a reverted step nets out to zero and is not the top saver", () => {
  const a = attribute(demo);
  const r = a.bars.filter((b) => b.outcome === "reverted");
  assert.equal(r.length, 1);
  assert.equal(r[0].netWh, 0);
  assert.ok(r[0].triedWh > 0, "it did save energy before the gate failed");
  assert.ok(!r[0].top);
  assert.equal(a.bars.filter((b) => b.top).length, 1);
  assert.equal(a.cap, 550);
});

test("each try is measured against the last kept state, not the baseline", () => {
  const a = attribute(demo);
  const [b1, b2] = a.bars;
  const wh650 = demo.find((e) => e.type === "trial.measured" && e.step_id === "cap:650").data.wh_per_req.mean;
  close(b1.triedWh, a.base.wh - wh650);
  assert.equal(b2.triedW, 50);
  close(a.base.wh - a.kept.wh, a.totalWh);
});

test("money and water use the impact maths and are estimates", () => {
  close(monthlyKwh(0.001), 0.001 * 100000 * 8 * 30 / 1000);
  close(usd(0.001), monthlyKwh(0.001) * 0.12);
  close(litres(0.001), monthlyKwh(0.001) * 1.8);
  const c = attribute(demo).counters;
  assert.equal(c.usd.label, "ESTIMATE");
  assert.equal(c.water.label, "ESTIMATE");
  assert.equal(c.wh.label, "MEASURED");
});

test("tokens per answer stay the same; tokens/Wh rises only because Wh falls", () => {
  const c = attribute(demo).counters;
  assert.ok(c.tokPerWh.v > c.tokPerWh.base);
  close(c.tokPerWh.v / c.tokPerWh.base, c.wh.base / c.wh.v);
});

test("partial streams: pending try counts nothing, live file without tokens is handled", () => {
  const i = demo.findIndex((e) => e.type === "trial.measured" && e.step_id === "cap:650");
  const a = attribute(demo.slice(0, i + 1));
  assert.equal(a.bars[0].outcome, "pending");
  assert.equal(a.totalWh, 0);
  assert.equal(waterfall(a.bars, "wh").at(-1).end, a.bars[0].netWh);
  const live = attribute(load("./demo-events.live.jsonl"));
  close(live.bars.reduce((s, b) => s + b.netWh, 0), live.totalWh);
  assert.equal(live.counters.tokPerWh, null);
  assert.ok(live.bars.some((b) => b.why.includes("Spark reason")));
});

test("why lines come from event data", () => {
  const a = attribute(demo);
  assert.match(a.bars[0].why, /^650 W cap: −0\.\d{4} Wh\/req, p99 \+\d+\.\d% still in budget, rule: /);
  assert.match(a.bars.find((b) => b.outcome === "reverted").why, /over the \+10% budget, gave it back: reverted to 550 W/);
  assert.equal(inUnit(a.bars[0], "w").net, 50);
});
