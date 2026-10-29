// Code Mode detection card: the dashboard reads the real results.json, keeps every number's
// honesty label, and dims pending values with a dot (prompt-side energy is never invented).
import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { cardHtml, candidateHtml, totalsHtml, cell, headline, num, ROWS,
  mountCodemode, CODEMODE_RESULTS } from "../../web/dash/codemode.js";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const resultsPath = path.join(__dirname, "..", "..", "data", "agent-waste", "codemode", "results.json");
const results = JSON.parse(fs.readFileSync(resultsPath, "utf-8"));

test("results artifact is the Code Mode schema with candidates", () => {
  assert.equal(results.spec, "wattnot.codemode/1");
  assert.ok(results.candidates >= 1, "expected at least one candidate");
  assert.equal(results.rows.length, results.candidates);
});

test("cell colours a measured number and dims a pending one with a dot", () => {
  const measured = cell({ value: 15, label: "MEASURED" }, 0);
  assert.match(measured, /lab-MEASURED/);
  assert.ok(measured.includes("15"));

  const pending = cell({ value: null, label: "PENDING" }, 1);
  assert.match(pending, /n-pending/);
  assert.match(pending, /pdot/); // the dot
  assert.match(pending, /–/); // no fabricated number

  // a MEASURED-floor (seen-tools lower bound) still colours as measured
  assert.match(cell({ value: 6, label: "MEASURED-floor" }, 0), /lab-MEASURED/);
});

test("num shows a dash for missing values, never NaN", () => {
  assert.equal(num(null), "–");
  assert.equal(num(undefined), "–");
  assert.equal(num(1234, 0), "1,234");
});

test("a candidate table shows before, after and a saving per row", () => {
  const row = results.rows[0];
  const html = candidateHtml(row);
  assert.ok(html.includes(row.task));
  for (const { name } of ROWS) assert.ok(html.includes(name), `missing row ${name}`);
  // before round trips must exceed after round trips (the whole point of Code Mode)
  assert.ok(row.before.round_trips.value > row.after.round_trips.value);
});

test("energy after is rendered pending, never a fabricated number", () => {
  const row = results.rows[0];
  assert.equal(row.after.joules.value, null);
  assert.equal(row.after.joules.label, "PENDING");
  const html = candidateHtml(row);
  // the energy row carries a pending dot, not an invented joule figure
  assert.match(html, /n-pending/);
});

test("totals strip sums the candidates and shows a modelled saving", () => {
  const html = totalsHtml(results.totals);
  assert.ok(html.includes("Round trips"));
  assert.ok(html.includes("Tool-schema tokens resent"));
  const saved = results.totals.round_trips.saved.value;
  assert.ok(saved >= 0);
});

test("headline is honest about 'modelled' and counts candidates", () => {
  const h = headline(results);
  assert.ok(h.includes(String(results.candidates)));
  assert.match(h, /Code Mode|candidates/);
});

test("full card renders without a live/measured energy claim for the 'after'", () => {
  const html = cardHtml(results);
  assert.ok(html.includes("Code Mode detection"));
  assert.doesNotMatch(html, /live/i);
  // the model is named as such in the notes
  assert.match(html.toLowerCase(), /estimate|modelled|model/);
});

test("empty results render a safe placeholder, not a crash", () => {
  assert.match(cardHtml(null), /No Code Mode results/);
  assert.match(cardHtml({}), /No Code Mode results/);
});

test("headline with zero candidates says none were found, invents no saving", () => {
  const h = headline({ candidates: 0, runs_examined: 42 });
  assert.match(h, /No Code Mode candidates in 42 recorded runs\./);
  assert.doesNotMatch(h, /collapse/); // no modelled-saving sentence when there is nothing to save
  // a missing count is treated as zero, and the examined count still shows a dash-safe number
  assert.match(headline({}), /No Code Mode candidates in 0 recorded runs\./);
});

test("totalsHtml(null) is empty, so a card without totals omits the strip", () => {
  assert.equal(totalsHtml(null), "");
  assert.equal(totalsHtml(undefined), "");
});

test("mountCodemode renders the card into an element from pre-loaded results (no fetch)", async () => {
  const el = { innerHTML: "" };
  await mountCodemode(el, results);
  assert.match(el.innerHTML, /Code Mode detection/);
  assert.ok(el.innerHTML.includes(results.rows[0].task));
});

test("mountCodemode tolerates a null element and shapes its fetch URL", async () => {
  // a missing mount point is a no-op, never a throw
  await mountCodemode(null, results);
  // the fetched artifact sits beside the dashboard page (dash/codemode/results.json)
  assert.equal(CODEMODE_RESULTS, "codemode/results.json");
});
