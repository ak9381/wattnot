// Pure helpers for plug.html (tested in plug.test.mjs). No DOM.
export const SAVERS = [
  { id: "agent_waste", t: "Stop wasted agent loops", h: "Ends an agent that repeats itself or goes in circles." },
  { id: "routing", t: "Try the small model first", h: "Uses the bigger model only when the answer looks shaky." },
  { id: "context_saver", t: "Trim long chats", h: "Drops repeated tool output and old turns from long chats." },
  { id: "thinking_cap", t: "Cap thinking", h: "Stops the model from thinking far longer than it needs." },
  { id: "vision", t: "Shrink big images", h: "Sends images at a size the model can still read." },
  { id: "voice", t: "Voice input", h: "Turns speech into text with Muse Voice." },
];
export const MODES = [
  { v: "gateway", t: "Change one URL", h: "Your app talks to WattNot instead of the model. Works with any hosted model." },
  { v: "gpu", t: "On my own GPUs", h: "A small helper next to your model server sets safe power limits." },
  { v: "both", t: "Both", h: "The most savings: one URL plus the GPU helper." },
];
export const UPSTREAMS = [
  { v: "meta", t: "Meta API (Muse Spark)" }, { v: "vllm", t: "My vLLM server" }, { v: "baseten", t: "Baseten" },
];
export function defaultChoice() {
  return { deployment: "gateway", upstream: "meta", port: 8780, savers: Object.fromEntries(SAVERS.map((s) => [s.id, true])),
    gpus: "all", cap_min_w: 300, cap_max_w: 375, sam_rack_check: true, approval: "manual", langfuse: false };
}
// Fold a voice-guide deploy prefill into a choice. Unknown (null) fields keep their value.
export function applyPrefill(choice, p) {
  const c = structuredClone(choice);
  if (!p) return c;
  for (const k of ["deployment", "upstream", "port", "cap_min_w", "cap_max_w", "sam_rack_check", "langfuse"]) {
    if (p[k] !== null && p[k] !== undefined) c[k] = p[k];
  }
  for (const s of p.savers_off || []) if (s in c.savers) c.savers[s] = false;
  return c;
}
export function fmtWh(wh) {
  if (wh == null) return "–";
  if (Math.abs(wh) >= 1000) return `${(wh / 1000).toFixed(2)} kWh`;
  if (Math.abs(wh) >= 1) return `${wh.toFixed(2)} Wh`;
  return `${(wh * 1000).toFixed(1)} mWh`;
}
// The headline: energy saved, with its label. Replay or empty feeds say what to do next.
export function headline(feed) {
  if (!feed || feed.source !== "live" || !feed.summary) {
    return { big: "Nothing plugged in yet", sub: "Do step 3 under Plug in, then send one request. Until then you see the recorded run, labelled REPLAY.", label: "REPLAY" };
  }
  const e = feed.summary.energy_wh, u = feed.summary.usd;
  const saved = e.baseline - e.actual;
  return { big: `${fmtWh(saved)} saved`, pct: e.saved_pct, label: e.label,
    usd: (u.baseline - u.actual), litres: (feed.water.litres_baseline - feed.water.litres), waterLabel: feed.water.label,
    sub: `${feed.summary.requests} requests through WattNot` };
}
// SVG path for a cumulative series (values already sorted by time).
export function path(points, key, w, h, max) {
  if (!points.length) return "";
  const m = max || Math.max(...points.map((p) => p[key]), 1e-9);
  return points.map((p, i) => `${i ? "L" : "M"}${((i / Math.max(1, points.length - 1)) * w).toFixed(1)},${(h - (p[key] / m) * h).toFixed(1)}`).join(" ");
}

// Test -> live: the savers that passed in a test run become the Go-live choice.
// Test ids (run harness) to gateway saver ids. Unknown ids are ignored.
export const TEST_TO_LIVE = { waste: "agent_waste", router: "routing", route: "routing", context: "context_saver",
  think: "thinking_cap", vision: "vision", voice: "voice", caps: "__gpu" };
export function liveFromTest(passed, base = defaultChoice()) {
  const c = structuredClone(base);
  const ids = new Set((passed || []).map((p) => TEST_TO_LIVE[p]).filter(Boolean));
  if (!ids.size) return c;
  for (const s of Object.keys(c.savers)) c.savers[s] = ids.has(s);
  if (ids.has("__gpu")) c.deployment = [...ids].some((i) => i !== "__gpu") ? "both" : "gpu";
  return c;
}
export function liveLink(passed) { return `plug.html?from=test&passed=${encodeURIComponent((passed || []).join(","))}#setup`; }
export function fromQuery(search, base = defaultChoice()) {
  const q = new URLSearchParams(search || "");
  if (q.get("from") !== "test") return null;
  return liveFromTest((q.get("passed") || "").split(",").filter(Boolean), base);
}
// Headline for a GPU-only plug (no gateway requests yet): the kept cap and its recorded change.
export function nodeHeadline(feed) {
  const ev = (feed?.node?.events || []);
  const kept = [...ev].reverse().find((e) => e.step === "kept");
  if (!kept) return null;
  const pct = kept.energy_change_pct;
  return { big: `GPU limited to ${kept.cap_w} W`, label: feed.node.label,
    sub: pct != null ? `${Math.abs(pct)}% less energy per answer at this limit (${kept.label}), answers checked at every step.` : "Limit kept after the quality check." };
}
