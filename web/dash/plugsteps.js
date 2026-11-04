// Pure helpers for plug.js (tested in plugsteps.test.mjs). No DOM.
const HELPER = new Set(["gpu_found", "rack_check", "waiting_approval"]);

// node.jsonl keeps every "Add GPU helper" press. Show only the latest press's helper steps, so an
// old REPLAY rack check from before a fix never sits under a newer live (MEASURED) one.
export function latestHelperEvents(events) {
  const ev = (events || []).filter((e) => !["arm_start", "arm_end", "restored"].includes(e.step));
  let last = -1;
  ev.forEach((e, i) => { if (e.step === "gpu_found") last = i; });
  return ev.filter((e, i) => !HELPER.has(e.step) || i >= last);
}

// fetch JSON that can never hang a poll loop: aborts after ms, returns null on any failure.
export async function getJSON(url, ms = 8000, f = globalThis.fetch) {
  const ac = typeof AbortController !== "undefined" ? new AbortController() : null;
  const t = ac ? setTimeout(() => ac.abort(), ms) : null;
  try {
    const r = await f(url, ac ? { signal: ac.signal } : {});
    return r && r.ok ? await r.json() : null;
  } catch { return null; } finally { if (t) clearTimeout(t); }
}

export function rackResultText(res) {
  return `Rack photo OK${res && res.rack_label ? ` (${res.rack_label})` : ""}. Press Approve to start the power check.`;
}
