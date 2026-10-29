// Standing rule: one header per page, the shared sticky header from web/dash/chrome.js.
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
const web = new URL("../../web/", import.meta.url);
const read = (p) => readFileSync(new URL(p, web), "utf8");
const PAGES = ["dash/index.html", "dash/v2.html", "dash/runs.html", "dash/report.html", "dash/graph.html", "dash/metatools.html", "run/index.html", "room/index.html"];
// room/node.html is an embeddable widget, not a page; it carries no header at all.

test("every page loads the shared header", () => {
  for (const p of PAGES) assert.match(read(p), /chrome\.js/, `${p} must load chrome.js`);
});

test("no page markup ships its own nav or brand header", () => {
  for (const p of PAGES) {
    const html = read(p).replace(/<div id="drawer"[\s\S]*?<\/div>\s*<\/div>/, ""); // deep-dive dialog tabs are not page nav
    const body = html.slice(html.indexOf("<body"));
    const drawerless = body.replace(/<div id="drawer"[\s\S]*$/, "");
    assert.doesNotMatch(drawerless, /<nav\b/, `${p} has a <nav> outside the shared header`);
    assert.doesNotMatch(drawerless, /<header[^>]*>\s*<div class="brand"/, `${p} has a second brand header`);
  }
});

test("shared header has no second tab row and highlights per page", () => {
  const c = read("dash/chrome.js");
  assert.doesNotMatch(c, /wn-nav-subline|wn-tabs|id="tabs"/);
  assert.match(c, /hash === "#pReport" \? "compare" : "dashboard"/);
});

test("overview has no sidebar nav, only in-page anchors and a legend", () => {
  const v = read("dash/v2r7.js");
  assert.doesNotMatch(v, /ov-side|class="ov-nav/);
  for (const id of ["pCfg", "pRun", "pReport", "pProof", "pLearn"]) assert.match(v, new RegExp(`\\["${id}"`));
  assert.match(v, /ov-jump/);
});
