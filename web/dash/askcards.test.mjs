import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { matchCard, pickCards, answerFor, explainCard, cardText, cardTitle } from "./askcards.js";

const fakeEl = (title, text, id = "") => ({ id, innerText: text, querySelector: () => ({ textContent: title }), getAttribute: () => null });

test("matchCard picks the longest title named in the question", () => {
  const cards = [{ title: "Meta spend" }, { title: "Meta spend · $26 of $120 cap" }, { title: "Run matrix" }];
  assert.equal(matchCard("what does the run matrix show?", cards).title, "Run matrix");
  assert.equal(matchCard("explain meta spend please", cards).title, "Meta spend");
  assert.equal(matchCard("hello", cards), null);
  assert.equal(matchCard("Explain the run run Matrix.", [{ title: "Run matrix · 0 of 12 rounds run" }]).title, "Run matrix · 0 of 12 rounds run");
});

test("pickCards keeps only outermost cards", () => {
  const inner = {}; const outer = { contains: (x) => x === inner }; inner.contains = () => false;
  assert.deepEqual(pickCards([outer, inner]), [outer]);
});

test("explainCard posts title + trimmed text and keeps the label", async () => {
  let sent;
  const f = async (url, o) => { sent = { url, body: JSON.parse(o.body) }; return { status: 200, json: async () => ({ label: "MOCK", explanation: "[MOCK] x", focus: "" }) }; };
  const el = fakeEl("Meta spend", "a ".repeat(3000), "m1");
  const { res, title } = await explainCard(el, f);
  assert.equal(sent.url, "/api/dash/ask/card"); assert.equal(sent.body.card_id, "m1");
  assert.ok(sent.body.text.length <= 2500); assert.equal(title, "Meta spend"); assert.equal(res.label, "MOCK");
  assert.match(answerFor(res, title).text, /^Meta spend: \[MOCK\]/);
  assert.equal(answerFor(res, title).label, "MOCK");
});

test("network failure is an ERROR, never a made-up answer", async () => {
  const { res } = await explainCard(fakeEl("T", "x"), async () => { throw new Error("down"); });
  assert.equal(res.label, "ERROR");
});

test("text helpers collapse whitespace", () => {
  assert.equal(cardText({ innerText: " a \n b " }), "a b"); assert.equal(cardTitle(fakeEl(" X  Y ", "")), "X Y");
});

test("the Ask path never speaks", () => {
  for (const f of ["askcards.js", "voiceguide.js"]) {
    const src = readFileSync(new URL(f, import.meta.url), "utf8");
    assert.ok(!/speechSynthesis|speakSummary\(|new Audio\(/.test(src), f);
  }
});
