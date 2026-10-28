// explain.test.mjs — Node tests for explainmap.js matcher, ambiguity handling, and popover rendering.

import { test } from "node:test";
import assert from "node:assert/strict";

import {
  PAGE_SELECTORS,
  tagElementsWithExplain,
  matchExplainElement,
  showExplainPopover,
  showAmbiguousChoices,
  clearHighlights,
  handleExplainQuery,
} from "./explainmap.js";

// Mock minimal DOM Node & Document for Node environment
class MockClassList {
  constructor() {
    this.classes = new Set();
  }
  add(c) { this.classes.add(c); }
  remove(c) { this.classes.delete(c); }
  contains(c) { return this.classes.has(c); }
}

class MockElement {
  constructor(tagName = "div", attrs = {}) {
    this.tagName = tagName.toUpperCase();
    this.attributes = { ...attrs };
    this.children = [];
    this.parentNode = null;
    this.textContent = "";
    this.classList = new MockClassList();
    this.style = {};
  }
  setAttribute(k, v) { this.attributes[k] = String(v); }
  getAttribute(k) { return this.attributes[k] || null; }
  hasAttribute(k) { return k in this.attributes; }
  appendChild(child) {
    child.parentNode = this;
    this.children.push(child);
    return child;
  }
  removeChild(child) {
    const idx = this.children.indexOf(child);
    if (idx !== -1) {
      this.children.splice(idx, 1);
      child.parentNode = null;
    }
    return child;
  }
  remove() {
    if (this.parentNode) this.parentNode.removeChild(this);
  }
  querySelector(sel) {
    const res = this.querySelectorAll(sel);
    return res[0] || null;
  }
  querySelectorAll(sel) {
    const parts = sel.split(",").map((s) => s.trim());
    const matched = [];
    const walk = (node) => {
      if (parts.some((p) => nodeMatches(node, p))) matched.push(node);
      for (const c of node.children) walk(c);
    };
    for (const c of this.children) walk(c);
    return matched;
  }
  getBoundingClientRect() {
    return { left: 100, top: 150, right: 300, bottom: 250, width: 200, height: 100 };
  }
  scrollIntoView() {}
  addEventListener() {}
}

function nodeMatches(node, sel) {
  if (sel.startsWith(".")) {
    const cls = sel.slice(1);
    return node.classList.contains(cls);
  }
  if (sel.startsWith("#")) {
    return node.attributes.id === sel.slice(1);
  }
  if (sel.startsWith("[")) {
    const attrName = sel.replace(/[\[\]]/g, "").split("=")[0];
    return node.hasAttribute(attrName);
  }
  if (sel.toUpperCase() === node.tagName) return true;
  return false;
}

class MockDocument extends MockElement {
  constructor() {
    super("#document");
    this.body = new MockElement("body");
    this.head = new MockElement("head");
    this.appendChild(this.head);
    this.appendChild(this.body);
  }
  createElement(tag) {
    const el = new MockElement(tag);
    el.ownerDocument = this;
    return el;
  }
  getElementById(id) {
    return this.querySelector(`#${id}`);
  }
}

test("PAGE_SELECTORS documents selectors for all key pages", () => {
  assert.ok(PAGE_SELECTORS["runs.html"].some((s) => s.sel.includes(".kpis .kpi")));
  assert.ok(PAGE_SELECTORS["report.html"].some((s) => s.sel.includes(".flow .card")));
  assert.ok(PAGE_SELECTORS["compare"].some((s) => s.sel.includes(".cmp.glass")));
  assert.ok(PAGE_SELECTORS["web/run"].some((s) => s.sel.includes("#savings .cnt")));
  assert.ok(PAGE_SELECTORS["metatools.html"].some((s) => s.sel.includes(".demo-explain-card")));
});

test("matcher returns exact match for specific query", () => {
  const doc = new MockDocument();
  const card = doc.createElement("div");
  card.classList.add("kpi");
  card.setAttribute("data-explain-id", "kpi_energy_saving");
  card.setAttribute("data-explain-value", "25%");
  card.setAttribute("data-explain-label", "MEASURED");
  card.setAttribute("data-explain-source", "rcpt_20261027_run2_cap350");
  card.setAttribute("data-explain-summary", "WattNot reduced electrical energy consumption by 25% while maintaining accuracy.");
  card.setAttribute("data-explain-title", "Energy Saved");
  doc.body.appendChild(card);

  const res = matchExplainElement("what does 25% mean?", doc);
  assert.equal(res.ambiguous, false);
  assert.ok(res.match);
  assert.equal(res.candidate.value, "25%");
  assert.equal(res.candidate.label, "MEASURED");
  assert.equal(res.candidate.id, "kpi_energy_saving");
});

test("matcher detects ambiguous query with multiple candidate matches", () => {
  const doc = new MockDocument();

  // Create two distinct elements both having 25%
  const card1 = doc.createElement("div");
  card1.setAttribute("data-explain-id", "kpi_energy_saving_1");
  card1.setAttribute("data-explain-value", "25%");
  card1.setAttribute("data-explain-label", "MEASURED");
  card1.setAttribute("data-explain-title", "Power Cut");
  doc.body.appendChild(card1);

  const card2 = doc.createElement("div");
  card2.setAttribute("data-explain-id", "kpi_energy_saving_2");
  card2.setAttribute("data-explain-value", "25%");
  card2.setAttribute("data-explain-label", "ESTIMATE");
  card2.setAttribute("data-explain-title", "Billed Saving");
  doc.body.appendChild(card2);

  const res = matchExplainElement("what does 25% mean?", doc);
  assert.equal(res.ambiguous, true);
  assert.equal(res.match, null);
  assert.ok(res.candidates.length >= 2);
});

test("matcher gracefully returns null when no elements match", () => {
  const doc = new MockDocument();
  const card = doc.createElement("div");
  card.setAttribute("data-explain-id", "kpi_speed");
  card.setAttribute("data-explain-value", "100ms");
  card.setAttribute("data-explain-label", "MEASURED");
  doc.body.appendChild(card);

  const res = matchExplainElement("what does 9999% mean?", doc);
  assert.equal(res.match, null);
  assert.equal(res.ambiguous, false);
});

test("popover content includes id, label, plain-language explanation, and source", () => {
  const doc = new MockDocument();
  const el = doc.createElement("div");
  el.classList.add("kpi");
  doc.body.appendChild(el);

  const data = {
    id: "rcpt_llama_350w_energy",
    value: "9.4%",
    label: "MEASURED",
    source: "rcpt_llama_350w",
    title: "Billed Saving",
    summary: "WattNot reduced energy consumption by 9.4% [MEASURED] across all prompts.",
  };

  const pop = showExplainPopover(el, data, doc);
  assert.ok(pop);
  assert.ok(el.classList.contains("explain-highlight"));

  // Check popover HTML content
  const html = pop.innerHTML;
  assert.match(html, /9\.4%/);
  assert.match(html, /MEASURED/);
  assert.match(html, /rcpt_llama_350w/);
  assert.match(html, /Billed Saving/);
});

test("runtime tagging preserves any existing data-explain attribute", () => {
  const doc = new MockDocument();
  const customEl = doc.createElement("div");
  customEl.classList.add("kpi");
  customEl.setAttribute("data-explain", JSON.stringify({
    id: "pre_existing_id",
    value: "42 W",
    label: "ADVICE",
    source: "custom_source",
    title: "Pre-existing Card",
    summary: "Custom explanation that must not be overwritten.",
  }));
  doc.body.appendChild(customEl);

  tagElementsWithExplain(doc);

  // Assert pre-existing data-explain is preserved unchanged
  const val = JSON.parse(customEl.getAttribute("data-explain"));
  assert.equal(val.id, "pre_existing_id");
  assert.equal(val.value, "42 W");
  assert.equal(val.label, "ADVICE");
});
