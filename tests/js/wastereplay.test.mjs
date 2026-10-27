import test from "node:test";
import assert from "node:assert/strict";
import { saverActed, endedText, tableHtml, categoriesHtml, stepCount, visible, eventHtml, replayHtml, secondLayerHtml, afterFixesHtml, repeatHtml } from "../../web/dash/wastereplay.js";

const ev = (n, note = "") => ({ n, agent: "main", text: "x <b>", calls: [["service_status", "{}", "500 err"]], tokens: 100, note });
const sc = {
  case: "retry-1-status-500", title: "A tool keeps failing", harness: "react", rep: 1, prompt: "Check billing.", injected: "a 500 each time",
  arms: {
    harness: { trace: [ev(1), ev(2), ev(3)], calls: 3, ended_by: "cap", miss: true, success: true, total_tokens: 900, wall_s: 4 },
    "harness+saver": { trace: [ev(1), ev(2, "saver: nudge (error_storm)")], calls: 2, ended_by: "saver-done", miss: false, success: true, total_tokens: 500, wall_s: 2 },
  },
};
const block = { runs: 4, rows: { "calls per run": { off: 10, on: 4, change_pct: -60, tag: "MEASURED" }, "energy per run (J)": { off: 5, on: 2, change_pct: -60, tag: "ESTIMATE" } },
  success: { off_pct: 100, on_pct: 100 }, miss_runs: { off: 3, on: 0 } };

test("how a run ended is said in plain words", () => {
  assert.match(endedText(sc.arms.harness), /hit its own cap after 3 calls/);
  assert.match(endedText(sc.arms["harness+saver"]), /called done after a nudge/);
  assert.equal(endedText(null), "no run");
});

test("the table shows both arms, the change and a label per row", () => {
  const h = tableHtml(block, "Injected");
  assert.match(h, /−|-60%/);
  assert.match(h, /pdot MEASURED/);
  assert.match(h, /pdot ESTIMATE/);
  assert.match(h, /3 alone, 0 with the saver/);
});

test("replay reveals steps one by one and ends with the outcome only at the end", () => {
  assert.equal(stepCount(sc), 3);
  assert.equal(visible(sc.arms.harness, 2).length, 2);
  const mid = replayHtml(sc, 1);
  assert.match(mid, /running…/);
  const end = replayHtml(sc, 3);
  assert.match(end, /hit its own cap/);
  assert.match(end, /Task passed/);
  assert.match(end, /REPLAY/);
});

test("text from a run is escaped", () => {
  assert.ok(!eventHtml(ev(1)).includes("<b>"));
  assert.match(eventHtml(ev(2, "saver: nudge (x)")), /class="ev sv"/);
});

test("categories and the full section render, and nothing renders without data", () => {
  const cats = [{ id: "retry", name: "Tool retry storms", calls_off: 15, calls_on: 4, tokens_change_pct: -70, success: { off_pct: 100, on_pct: 100 }, miss_runs: { off: 2, on: 0 } }];
  assert.match(categoriesHtml(cats), /Tool retry storms/);
  assert.equal(categoriesHtml([]), "");
  const h = secondLayerHtml({ headline: "H", normal: block, injected: block, categories: cats, scenarios: [sc], notes: ["n"], reps: 2, cases: 12, spend_usd: 1.234 });
  assert.match(h, /Replay a miss/);
  assert.match(h, /A tool keeps failing/);
  assert.match(h, /\$1\.23/);
  assert.equal(secondLayerHtml(null), "");
});

test("a replay where the saver never acted says so", () => {
  assert.equal(saverActed(sc), true);
  const quiet = { ...sc, arms: { ...sc.arms, "harness+saver": { ...sc.arms["harness+saver"], trace: [ev(1), ev(2)] } } };
  assert.equal(saverActed(quiet), false);
  assert.match(replayHtml(quiet, 2), /did not step in/);
  assert.ok(!replayHtml(sc, 2).includes("did not step in"));
});

test("the after-fixes table shows harness, v2 saver and v3 saver, and escapes text", () => {
  const b = (c) => ({ runs: 2, success: 2, calls: c, tokens: c * 1000 });
  const blk = { harness: b(15), saver_v2: b(6), saver_v3: b(3) };
  const h = afterFixesHtml({ fixes: ["fix <one>"], fixed: blk, normal: blk, cases: [{ id: "noprog-3", ...blk }], notes: ["a note"], reps: 2 });
  assert.match(h, /After the two fixes/);
  assert.match(h, /noprog-3/);
  assert.match(h, /3 calls/);
  assert.match(h, /a note/);
  assert.ok(!h.includes("<one>"));
  assert.equal(afterFixesHtml(null), "");
  const sl = secondLayerHtml({ headline: "H", normal: { rows: {} }, injected: { rows: {} }, categories: [], scenarios: [], notes: [], reps: 2, cases: 1, spend_usd: 1, after_fixes: { fixes: [], fixed: blk, normal: blk, cases: [], notes: [], reps: 2 } });
  assert.match(sl, /After the two fixes/);
});

test("the 6-rep sprawl table shows spread and the verdict, and is empty without data", () => {
  const st = (m) => ({ mean: m, sd: 1.5, min: m - 2, max: m + 2, median: m });
  const a = (m) => ({ runs: 6, success: 6, calls: st(m), tokens: st(m * 1000) });
  const h = repeatHtml({ reps: 6, spread: [{ id: "sprawl-1", arms: { harness: a(14), "harness+saver": a(8) } }], verdicts: { "sprawl-1": "Real <b>" }, notes: ["n"] });
  assert.match(h, /6 reps/);
  assert.match(h, /14 \(12–16, sd 1\.5\)/);
  assert.match(h, /ADVICE/);
  assert.match(h, /Real &lt;b&gt;/);
  assert.equal(repeatHtml(null), "");
  assert.equal(repeatHtml({ spread: [] }), "");
});
