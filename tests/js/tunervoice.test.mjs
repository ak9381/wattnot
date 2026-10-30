import test from "node:test";
import assert from "node:assert/strict";
import {
  VOICE_BADGE,
  isTuneResponse,
  speechTextFor,
  renderTunePlanHTML,
  speakSummary,
  stopSpeaking,
} from "../../web/dash/tunervoice.js";
import { classifyLocalIntent, quickAckForQuery, looksLikeTuneCommand } from "../../web/dash/voiceguide.js";

const TUNE = {
  intent: "command",
  action: "tune",
  mode: "dry-run",
  label: "MOCK",
  text: "Dry run — nothing was changed. Spark planned 2 changes from your command.",
  speech: "Dry run. Spark planned 2 changes. Set the gpu power cap to 350 watts: a person must approve this.",
  voice: { engine: "browser speechSynthesis", label: "browser voice", note: "not a Meta model" },
  plan: {
    command: "try 350W with FP8",
    planner: { name: "Spark", model: "muse-spark-1.3" },
    decisions: [],
    unsupported: [],
  },
  guard: {
    changes: [
      { kind: "power_cap", change: "GPU power cap 700 W -> 350 W", risk: "high",
        reasons: ["more than 15% below stock"], route: "pending", needs_person: true },
      { kind: "vllm_config", change: "quantization none -> fp8", risk: "high",
        reasons: ["changes quantization; a person decides"], route: "pending", needs_person: true },
    ],
  },
};

const TUNE_LOW = {
  intent: "command", action: "tune", mode: "dry-run", label: "MOCK",
  speech: "Dry run.", text: "ok",
  plan: { planner: { name: "Spark" }, unsupported: ["max_num_seqs=300 (allowed: 64, 128, 256, 512)"] },
  guard: { changes: [{ kind: "vllm_config", change: "max_num_seqs None -> 256", risk: "low",
                       reasons: ["batch size, KV cache and other tweaks only"],
                       route: "auto-low-risk", needs_person: false }] },
};

test("a knob command classifies locally as 'command', questions do not", () => {
  assert.equal(classifyLocalIntent("try 350W with FP8"), "command");
  assert.equal(classifyLocalIntent("turn on prefix caching"), "command");
  assert.equal(classifyLocalIntent("talk to the tuner"), "command");
  assert.equal(looksLikeTuneCommand("try 350W with FP8"), true);
  // questions / explain / existing intents are NOT commands
  assert.equal(looksLikeTuneCommand("what does fp8 mean?"), false);
  assert.equal(classifyLocalIntent("what does fp8 mean?"), "explain");
  assert.equal(classifyLocalIntent("compare runs"), "navigate");
  assert.equal(classifyLocalIntent("configure a new run"), "configure");
});

test("quickAckForQuery gives a command ack that names Spark", () => {
  const ack = quickAckForQuery("try 350W with FP8");
  assert.equal(ack.intent, "command");
  assert.match(ack.ack, /Spark/);
});

test("isTuneResponse recognises a tune result", () => {
  assert.equal(isTuneResponse(TUNE), true);
  assert.equal(isTuneResponse({ intent: "answer", action: "answer" }), false);
  assert.equal(isTuneResponse(null), false);
});

test("renderTunePlanHTML shows the mode, the browser-voice badge and each routed change", () => {
  const h = renderTunePlanHTML(TUNE);
  assert.match(h, /DRY-RUN/);
  assert.match(h, new RegExp(VOICE_BADGE));
  assert.match(h, /vg-speak-btn/);
  assert.match(h, /GPU power cap 700 W -&gt; 350 W/); // escaped '>'
  assert.match(h, /quantization none -&gt; fp8/);
  assert.match(h, /needs a person/);
  assert.match(h, /more than 15% below stock/);
  assert.match(h, /Spark planned this/);
  assert.match(h, /Nothing was changed/);
});

test("renderTunePlanHTML marks a low-risk change auto-approved and lists unsupported knobs", () => {
  const h = renderTunePlanHTML(TUNE_LOW);
  assert.match(h, /auto-approved/);
  assert.match(h, /vg-tune-auto/);
  assert.match(h, /Not planned \(unknown to Spark\)/);
  assert.match(h, /max_num_seqs=300/);
});

test("renderTunePlanHTML escapes any markup in the command text", () => {
  const evil = { ...TUNE_LOW, text: "<img src=x onerror=alert(1)>",
                 guard: { changes: [] }, plan: { planner: { name: "Spark" }, unsupported: [] } };
  const h = renderTunePlanHTML(evil);
  assert.doesNotMatch(h, /<img/);
  assert.match(h, /&lt;img/);
});

test("speechTextFor prefers the speech line, falls back to text", () => {
  assert.equal(speechTextFor(TUNE), TUNE.speech);
  assert.equal(speechTextFor({ text: "only text" }), "only text");
  assert.equal(speechTextFor(null), "");
});

test("speakSummary speaks via an injected synth and is labelled browser voice", () => {
  const spoken = [];
  const synth = { speak: (u) => spoken.push(u.text), cancel: () => spoken.push("[cancel]") };
  const make = (t) => ({ text: t });
  const said = speakSummary(TUNE, { synth, make });
  assert.equal(said, TUNE.speech);
  assert.deepEqual(spoken, ["[cancel]", TUNE.speech]);
  // the result the voice reads is explicitly the browser voice, not a Meta model
  assert.equal(TUNE.voice.label, "browser voice");
});

test("speakSummary returns null when no speech engine is available", () => {
  assert.equal(speakSummary(TUNE, { synth: null, make: null }), null);
  assert.equal(speakSummary({ speech: "" }, { synth: { speak() {}, cancel() {} }, make: (t) => ({ text: t }) }), null);
});

test("speakSummary never throws if the engine fails", () => {
  const synth = { speak: () => { throw new Error("boom"); }, cancel: () => {} };
  assert.equal(speakSummary(TUNE, { synth, make: (t) => ({ text: t }) }), null);
});

test("stopSpeaking cancels an injected synth and tolerates none", () => {
  let cancelled = false;
  stopSpeaking({ cancel: () => { cancelled = true; } });
  assert.equal(cancelled, true);
  assert.doesNotThrow(() => stopSpeaking(null));
});
