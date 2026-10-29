import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { renderLive } from "./live.js";
import { extractHotspotData, renderHotspotHTML } from "./hotspot.js";

const rd = (n) => JSON.parse(readFileSync(new URL(`../../receipts/run2/rcpt_20261027_run2_cap350.${n}.json`, import.meta.url)));
const L = { wonder_live: rd("wonder_live"), musecode_pr: rd("musecode_pr"), sam_hotspot: rd("sam_hotspot"), voice_transcribe: rd("voice_transcribe") };

test("live card shows real ids from the signed addenda", () => {
  const el = { innerHTML: "" };
  renderLive(el, L);
  for (const c of L.wonder_live.data.calls) assert.ok(el.innerHTML.includes(c.response_id));
  assert.ok(el.innerHTML.includes(L.musecode_pr.data.pr_url));
  assert.ok(el.innerHTML.includes(L.sam_hotspot.data.sam.response_id));
  assert.ok(el.innerHTML.includes(L.voice_transcribe.data.session_id));
  assert.ok(el.innerHTML.includes("SIMULATED"), "TTS audio and heat must stay SIMULATED");
});

test("live card renders nothing without addenda (no invented values)", () => {
  const el = { innerHTML: "x" };
  renderLive(el, {});
  assert.equal(el.innerHTML, "");
});

test("addenda link to the RUN2 receipt hash", () => {
  const parent = JSON.parse(readFileSync(new URL("../../receipts/run2/rcpt_20261027_run2_cap350.json", import.meta.url)));
  for (const d of Object.values(L)) assert.equal(d.parent.sha256, parent.integrity.sha256);
});

test("hotspot: live SAM drops MOCK; without live it stays MOCK", () => {
  const s = L.sam_hotspot.data;
  const live = extractHotspotData({ hotspot_cap: { live: true, mask: s.mask, decision: s.decision, heat_label: "SIMULATED" } });
  const html = renderHotspotHTML(live);
  assert.ok(!html.includes("MOCK"));
  assert.ok(html.includes("SIMULATED") && html.includes(s.mask.label));
  assert.ok(renderHotspotHTML(extractHotspotData({})).includes("MOCK"));
});

test("voice guide has a live transcript replay and no blanket (MOCK) placeholder", () => {
  const src = readFileSync(new URL("./voiceguide.js", import.meta.url), "utf8");
  assert.ok(src.includes("voice_transcribe.json"));
  assert.ok(!src.includes("Ask Voice Guide (MOCK)"));
});
