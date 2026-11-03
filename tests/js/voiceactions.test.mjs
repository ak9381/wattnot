import test from "node:test";
import assert from "node:assert/strict";
import { applyPrefill, afterAnswer, isPageExplainQuery, savePrefill, takePrefill, PREFILL_KEY } from "../../web/dash/voiceactions.js";

const mem = () => { const m = new Map(); return { getItem: (k) => m.get(k) ?? null, setItem: (k, v) => m.set(k, v), removeItem: (k) => m.delete(k) }; };

test("explain the dashboard is a page explain, a card question is not", () => {
  assert.ok(isPageExplainQuery("Explain the dashboard."));
  assert.ok(!isPageExplainQuery("what does 25% mean?"));
});

test("page explain speaks the summary and never touches the run or location", () => {
  const spoken = [];
  const loc = { pathname: "/dash/v2.html", hash: "#pHome", href: "x" };
  const r = afterAnswer({ intent: "explain", scope: "page", speak: "This page has 3 cards." },
    { location: loc, speech: { synth: { cancel() {}, speak: (u) => spoken.push(u.text) }, make: (t) => ({ text: t }) } });
  assert.equal(r, "spoke");
  assert.deepEqual(spoken, ["This page has 3 cards."]);
  assert.equal(loc.hash, "#pHome");
  assert.equal(loc.href, "x");
});

test("configure fills the form with every setting and opens Set up a run, never Start", () => {
  const storage = mem(); const events = [];
  const loc = { pathname: "/dash/v2.html", hash: "#pHome" };
  const win = { dispatchEvent: (e) => events.push(e.type), CustomEvent: class { constructor(t) { this.type = t; } } };
  const pf = { cap_w: 375, thinking_budget: true, prefix_cache: false, provider: "baseten", p99_pct: 10, rounds: 2, framework: "langchain" };
  assert.equal(afterAnswer({ intent: "configure", action: "prefill", prefill: pf, starts_run: false }, { storage, location: loc, window: win }), "prefilled");
  assert.equal(loc.hash, "pCfg");
  assert.deepEqual(events, ["wn:voice-prefill"]);
  const got = takePrefill(storage);
  const choice = {}, cfg = { provider: "replay" };
  const set = applyPrefill(got, choice, cfg);
  assert.deepEqual(cfg.voice, { cap_w: 375, thinking_budget: true, prefix_cache: false, provider: "baseten", framework: "langchain", p99_pct: 10, rounds: 2 });
  assert.ok(set.includes("thinking_budget") && set.includes("cap_w"));
  assert.equal(cfg.provider, "baseten"); assert.equal(cfg.p99_pct, 10); assert.equal(cfg.rounds, 2);
  assert.equal(choice.framework, "langchain");
  assert.equal(storage.getItem(PREFILL_KEY), null); // used once
});

test("from another page, configure navigates to the form", () => {
  const loc = { pathname: "/dash/runs.html", hash: "", href: "" };
  afterAnswer({ action: "prefill", prefill: { cap_w: 350 }, route: "/dash/v2.html#pCfg" }, { storage: mem(), location: loc });
  assert.equal(loc.href, "/dash/v2.html#pCfg");
});

test("a stale pre-fill is ignored", () => {
  const s = mem(); s.setItem(PREFILL_KEY, JSON.stringify({ cap_w: 1, at: 0 }));
  assert.equal(takePrefill(s), null);
  assert.ok(savePrefill({ cap_w: 2 }, s) && takePrefill(s).cap_w === 2);
});

test("no code path in the voice helpers presses Start", async () => {
  const { readFileSync } = await import("node:fs");
  const src = readFileSync(new URL("../../web/dash/voiceactions.js", import.meta.url), "utf8");
  assert.ok(!/startRun|#startRun|l-live|\.click\(/.test(src));
});
