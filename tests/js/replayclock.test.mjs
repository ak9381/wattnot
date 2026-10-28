import test from "node:test";
import assert from "node:assert/strict";
import { createClock } from "../../web/dash/replayclock.js";

test("play and pause reach every subscriber", () => {
  const c = createClock();
  const seen = [];
  const off = c.subscribe((ev) => seen.push(ev.type));
  c.setPlaying(true);
  c.setPlaying(false);
  assert.deepEqual(seen, ["play", "pause"]);
  off();
  c.setPlaying(true);
  assert.deepEqual(seen, ["play", "pause"]);
});

test("phase moves between 1 and 2 only, progress stays in 0..1", () => {
  const c = createClock();
  const seen = [];
  c.subscribe((ev) => seen.push(ev));
  c.setPhase(2);
  c.setPhase(9);
  assert.equal(c.phase, 2);
  c.setProgress(0.25);
  c.setProgress(4);
  assert.equal(c.progress, 1);
  assert.deepEqual(seen.map((e) => e.type), ["phase", "progress", "progress"]);
  assert.equal(seen[0].phase, 2);
});

test("reset returns to phase 1 and zero progress", () => {
  const c = createClock();
  c.setPhase(2);
  c.setProgress(0.7);
  c.reset();
  assert.equal(c.phase, 1);
  assert.equal(c.progress, 0);
});
