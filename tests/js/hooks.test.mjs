import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const dashDir = path.resolve(__dirname, "../../web/dash");

test("mic button slot exists on the 4 dashboard pages and not on the landing page", () => {
  const pages = ["runs.html", "report.html", "v2.html", "graph.html"];
  for (const page of pages) {
    const html = fs.readFileSync(path.join(dashDir, page), "utf8");
    assert.match(
      html,
      /<span id="wn-mic-slot" class="wn-mic-slot" aria-live="polite"><\/span>/,
      `expected #wn-mic-slot on ${page}`
    );
  }

  const landingHtml = fs.readFileSync(path.join(dashDir, "index.html"), "utf8");
  assert.doesNotMatch(landingHtml, /id="wn-mic-slot"/, "landing page should not have #wn-mic-slot");
});

test("data-explain hooks appear in report.js, runs.js, and v2.js", () => {
  const reportJs = fs.readFileSync(path.join(dashDir, "report.js"), "utf8");
  assert.match(reportJs, /data-explain="report\.saving"/);
  assert.match(reportJs, /data-explain="report\.quality"/);
  assert.match(reportJs, /data-explain="report\.receipt"/);
  assert.match(reportJs, /data-explain="\${esc\(s\.explainId\)}"/);
  assert.match(reportJs, /data-explain-value=/);
  assert.match(reportJs, /data-explain-label=/);
  assert.match(reportJs, /data-explain-source=/);

  const runsJs = fs.readFileSync(path.join(dashDir, "runs.js"), "utf8");
  assert.match(runsJs, /data-explain="runs\.\${esc\(r\.id\)}\.saving"/);
  assert.match(runsJs, /data-explain="runs\.\${esc\(r\.id\)}\.p99"/);
  assert.match(runsJs, /data-explain="runs\.\${esc\(r\.id\)}\.quality"/);
  assert.match(runsJs, /data-explain-value=/);
  assert.match(runsJs, /data-explain-label=/);
  assert.match(runsJs, /data-explain-source=/);

  const v2Js = fs.readFileSync(path.join(dashDir, "v2.js"), "utf8");
  assert.match(v2Js, /data-explain/);
  assert.match(v2Js, /compare\.\${S\.cmpActive}\.\${stableId}/);
  assert.match(v2Js, /compare\.\${id}/);
  assert.match(v2Js, /data-explain-value/);
  assert.match(v2Js, /data-explain-label/);
  assert.match(v2Js, /data-explain-source/);
});

test("HOOKS.md documents the slot and explain attributes", () => {
  const doc = fs.readFileSync(path.join(dashDir, "HOOKS.md"), "utf8");
  assert.match(doc, /wn-mic-slot/);
  assert.match(doc, /data-explain/);
  assert.match(doc, /data-explain-value/);
  assert.match(doc, /data-explain-label/);
  assert.match(doc, /data-explain-source/);
  assert.match(doc, /report\.saving/);
  assert.match(doc, /report\.step\.baseline/);
  assert.match(doc, /runs\.<runid>\.saving/);
  assert.match(doc, /compare\.power\.agent-waste/);
});

test("every page includes chrome.js and chrome.css", () => {
  const dashPages = ["index.html", "runs.html", "report.html", "v2.html", "graph.html"];
  for (const page of dashPages) {
    const html = fs.readFileSync(path.join(dashDir, page), "utf8");
    assert.match(html, /<link rel="stylesheet" href="chrome\.css">/, `expected chrome.css on ${page}`);
    assert.match(html, /<script src="chrome\.js" defer><\/script>/, `expected chrome.js on ${page}`);
  }

  const runHtml = fs.readFileSync(path.resolve(__dirname, "../../web/run/index.html"), "utf8");
  assert.match(runHtml, /<link rel="stylesheet" href="\.\.\/dash\/chrome\.css">/, "expected ../dash/chrome.css on run/index.html");
  assert.match(runHtml, /<script src="\.\.\/dash\/chrome\.js" defer><\/script>/, "expected ../dash/chrome.js on run/index.html");
});

test("landing page has OG tags, Inter Tight, and no filter on hero image", () => {
  const html = fs.readFileSync(path.join(dashDir, "index.html"), "utf8");
  assert.match(html, /<meta property="og:title" content="[^"]+">/);
  assert.match(html, /<meta property="og:description" content="[^"]+">/);
  assert.match(html, /<meta property="og:image" content="[^"]+">/);
  assert.match(html, /<meta property="og:type" content="[^"]+">/);
  assert.match(html, /<meta property="og:url" content="[^"]+">/);
  assert.match(html, /Inter\+Tight/);
  assert.match(html, /hero\.webp/);
  assert.match(html, /hero-1\.png/);
  assert.doesNotMatch(html, /filter:\s*[^;}]*hue-rotate/);
  assert.doesNotMatch(html, /filter:\s*[^;}]*sepia/);
  assert.doesNotMatch(html, /filter:\s*[^;}]*grayscale/);
});

test("root redirect page has description and OG tags", () => {
  const buildPy = fs.readFileSync(path.resolve(__dirname, "../../scripts/build_pages.py"), "utf8");
  assert.match(buildPy, /<meta name="description"/);
  assert.match(buildPy, /<meta property="og:title"/);
  assert.match(buildPy, /<meta property="og:description"/);
  assert.match(buildPy, /<meta property="og:image"/);
  assert.match(buildPy, /<meta property="og:type"/);
  assert.match(buildPy, /<meta property="og:url"/);

  const siteIndex = path.resolve(__dirname, "../../site/index.html");
  if (fs.existsSync(siteIndex)) {
    const html = fs.readFileSync(siteIndex, "utf8");
    assert.match(html, /<meta name="description"/);
    assert.match(html, /<meta property="og:title"/);
    assert.match(html, /<meta property="og:description"/);
    assert.match(html, /<meta property="og:image"/);
    assert.match(html, /<meta property="og:type"/);
    assert.match(html, /<meta property="og:url"/);
  }
});

test("shared chrome header has updated nav links and Live graph", () => {
  const chromeJs = fs.readFileSync(path.join(dashDir, "chrome.js"), "utf8");
  assert.match(chromeJs, /href="\${prefix}runs\.html">Dashboard<\/a>/);
  assert.match(chromeJs, /href="\${prefix}runs\.html#runs">Runs<\/a>/);
  assert.match(chromeJs, /href="\${prefix}v2\.html#pReport">Compare<\/a>/);
  assert.match(chromeJs, /href="\${prefix}report\.html\?run=run2-llama-350w">Reports<\/a>/);
  assert.match(chromeJs, /href="\${prefix}graph\.html">Live graph<\/a>/);
  assert.doesNotMatch(chromeJs, />Architecture<\/a>/);

  const graphHtml = fs.readFileSync(path.join(dashDir, "graph.html"), "utf8");
  assert.match(graphHtml, /<title>WattNot: Live graph<\/title>/);
  assert.doesNotMatch(graphHtml, /<header class="top">/);
});

test("shared chrome footer has credits, track, github, and year", () => {
  const chromeJs = fs.readFileSync(path.join(dashDir, "chrome.js"), "utf8");
  assert.match(chromeJs, /Project by Akash K Raju/);
  assert.match(chromeJs, /Meta hackathon 2026 · Indie track/);
  assert.match(chromeJs, /https:\/\/github\.com\/ak9381\/wattnot/);
  assert.doesNotMatch(chromeJs, /No compliance claims/);
  assert.match(chromeJs, /2026/);
});

test("header and footer are fixed with Apple glassmorphism and glow blobs", () => {
  const chromeCss = fs.readFileSync(path.join(dashDir, "chrome.css"), "utf8");
  assert.match(chromeCss, /nav\.wn-nav\.gl\s*\{[^}]*position:\s*fixed;/);
  assert.match(chromeCss, /footer\.wn-footer\.gl\s*\{[^}]*position:\s*fixed;/);
  assert.match(chromeCss, /bottom:\s*14px;/);
  assert.match(chromeCss, /blur\(24px\)\s*saturate\(170%\)/);
  assert.match(chromeCss, /rgba\(255,\s*255,\s*255,\s*0?\.14\)/);
  assert.match(chromeCss, /inset 0 1px 0 rgba\(255,\s*255,\s*255,\s*0?\.12\)/);
  assert.match(chromeCss, /padding-top:\s*74px;/);
  assert.match(chromeCss, /padding-bottom:\s*74px;/);
  assert.match(chromeCss, /#6C5E7C/);
  assert.match(chromeCss, /#8E7A9E/);
  assert.match(chromeCss, /#4F4A63/);

  const v2Css = fs.readFileSync(path.join(dashDir, "v2.css"), "utf8");
  assert.match(v2Css, /height:\s*calc\(100vh - 340px\)/);
  assert.doesNotMatch(v2Css, /footer\.wn-footer\.gl\s*\{\s*display:\s*none/);
});

test("Cost and Water cards and nodes have no ESTIMATE label chips", () => {
  const runsJs = fs.readFileSync(path.join(dashDir, "runs.js"), "utf8");
  assert.doesNotMatch(runsJs, /Water saved<\/div>\s*<div class="big">[\s\S]*?\$\{dot\("ESTIMATE"\)\}/);
  assert.doesNotMatch(runsJs, /Cost saved<\/div>\s*<div class="big">[\s\S]*?\$\{dot\("ESTIMATE"\)\}/);

  const reportJs = fs.readFileSync(path.join(dashDir, "report.js"), "utf8");
  assert.doesNotMatch(reportJs, /<dt>Cost saved<\/dt><dd>[^<]*\$\{chip\("ESTIMATE"\)\}/);
  assert.doesNotMatch(reportJs, /<dt>Water saved<\/dt><dd>[^<]*\$\{chip\("ESTIMATE"\)\}/);

  const v2Js = fs.readFileSync(path.join(dashDir, "v2.js"), "utf8");
  assert.doesNotMatch(v2Js, /<span>\$\{name\}<\/span>\$\{badge\(id === "power" \? "MEASURED" : "ESTIMATE"\)\}/);
  assert.match(v2Js, /<span>\$\{name\}<\/span>\$\{id === "power" \? badge\("MEASURED"\) : ""\}/);
});
