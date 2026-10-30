import test from "node:test";
import assert from "node:assert/strict";
import { extractWonderChart, renderWonderChartHTML, mountWonderChart } from "../../web/dash/wonderchart.js";

const POINTS = [
  { setting: "375W/base", cap_w: 375, clock_profile: "base", joules_per_correct: 1.0, quality: 0.99, kept: true, label: "MEASURED" },
  { setting: "350W/base", cap_w: 350, clock_profile: "base", joules_per_correct: 0.94, quality: 0.98, kept: true, label: "MEASURED" },
  { setting: "300W/base", cap_w: 300, clock_profile: "base", joules_per_correct: 0.95, quality: 0.88, kept: false, label: "MEASURED" },
];

const WONDER_TRY = {
  measurement: {
    wonder_chart: {
      label: "SYNTHETIC",
      chart_sha256: "730db1395ab84eb674c80b1f221c7d8d3a0678b73e32b34cb843d325e8eb33be",
      source: "wonder",
      offline_fallback: false,
      model: "muse-wonder-1.0",
      suggestion: { action: "try", cap_w: 325, clock_profile: "locked-mid" },
      reason: "325 W is the efficiency knee on the kept front; a locked-mid clock should trim joules-per-correct further.",
      trace_id: "wonder-2fe98b728061a6bc",
      est_wh: { value: 0.5, label: "ESTIMATE" },
      points: POINTS,
    },
  },
};

const FALLBACK_STOP = {
  measurement: {
    wonder_chart: {
      label: "SYNTHETIC",
      chart_sha256: "abc123",
      source: "fallback",
      offline_fallback: true,
      model: "deterministic",
      suggestion: { action: "stop", cap_w: null, clock_profile: null },
      reason: "offline fallback: Pareto front has flattened over the last 3 trials",
      trace_id: "wonder-deadbeef",
      est_wh: { value: 0.5, label: "ESTIMATE" },
      points: POINTS,
    },
  },
};

test("extractWonderChart reads the measurement block", () => {
  const d = extractWonderChart(WONDER_TRY);
  assert.equal(d.hasData, true);
  assert.equal(d.source, "wonder");
  assert.equal(d.offlineFallback, false);
  assert.equal(d.whLabel, "ESTIMATE");
  assert.equal(d.suggestion.action, "try");
  assert.equal(d.suggestion.capW, 325);
  assert.equal(d.points.length, 3);
  assert.equal(d.chartShaShort, "730db1395ab8");
});

test("no wonder chart means no panel", () => {
  const d = extractWonderChart({ measurement: {} });
  assert.equal(d.hasData, false);
  assert.equal(renderWonderChartHTML(d), "");
});

test("a 'try' suggestion renders the title, model, SYNTHETIC, ESTIMATE, next setting, scatter and reason", () => {
  const h = renderWonderChartHTML(extractWonderChart(WONDER_TRY));
  assert.match(h, /Wonder reads the chart/);
  assert.match(h, /muse-wonder-1\.0/);
  assert.match(h, /SYNTHETIC/);
  assert.match(h, /ESTIMATE/);
  assert.match(h, /0\.5 Wh/);
  assert.match(h, /325 W/);
  assert.match(h, /locked-mid/);
  assert.match(h, /Muse Wonder read the chart/);
  assert.match(h, /efficiency knee/);
  // the chart is redrawn as inline SVG with green kept + red rejected dots
  assert.match(h, /<svg/);
  assert.match(h, /wc-kept/);
  assert.match(h, /wc-rejected/);
  // honest: a plot of trial data, never a measurement
  assert.match(h, /not a measurement/);
  assert.match(h, /730db1395ab8/);
});

test("an offline fallback that stops shows the fallback note and stop verdict", () => {
  const h = renderWonderChartHTML(extractWonderChart(FALLBACK_STOP));
  assert.match(h, /Offline deterministic fallback/);
  assert.match(h, /deterministic/);
  assert.match(h, /Stop/);
  assert.match(h, /flattened/);
});

test("mountWonderChart fills the element and toggles hidden", () => {
  const el = { innerHTML: "", hidden: true };
  mountWonderChart(el, WONDER_TRY);
  assert.match(el.innerHTML, /Wonder reads the chart/);
  assert.equal(el.hidden, false);

  const empty = { innerHTML: "x", hidden: false };
  mountWonderChart(empty, { measurement: {} });
  assert.equal(empty.innerHTML, "");
  assert.equal(empty.hidden, true);
});

test("the panel escapes untrusted fields", () => {
  const h = renderWonderChartHTML(extractWonderChart({
    measurement: {
      wonder_chart: {
        source: "wonder", model: "m", suggestion: { action: "try", cap_w: 300, clock_profile: "base" },
        reason: "<script>alert(1)</script>", est_wh: { value: 0.5, label: "ESTIMATE" },
        points: [{ setting: "x", cap_w: 300, clock_profile: "base", joules_per_correct: 1, quality: 0.9, kept: true }],
      },
    },
  }));
  assert.doesNotMatch(h, /<script>/);
  assert.match(h, /&lt;script&gt;/);
});
