// What the mic does after the server answers. Pure helpers (no DOM globals unless passed in)
// so tests can run them in node.
//  - "Explain the dashboard": a short spoken summary of the cards on screen. It never selects
//    or changes a run.
//  - "Configure a run ...": every parsed setting goes into the Set up a run form, which opens
//    with the values visible. Nothing here ever presses Start.
import { CARD_SEL, cardTitle, cardText, pickCards } from "./askcards.js";
import { speakSummary } from "./tunervoice.js";

export const PREFILL_KEY = "wn.voicePrefill";

export function isPageExplainQuery(text) {
  const t = String(text || "").toLowerCase();
  return /\bexplain\b/.test(t) && /(dashboard|page|screen|view|everything)/.test(t)
    || /what am i looking at|what'?s on (the|this) (page|screen)/.test(t);
}

// Visible cards as {title, text}, capped so the request stays small.
export function collectScreen(doc = globalThis.document, max = 12) {
  if (!doc?.querySelectorAll) return [];
  const all = [...doc.querySelectorAll(CARD_SEL)].filter((el) => !el.closest?.("#wattnot-voiceguide-slot")
    && (el.offsetParent !== null || el.getClientRects?.().length));
  return pickCards(all).slice(0, max).map((el) => ({ title: cardTitle(el), text: cardText(el, 300) }));
}

export function savePrefill(prefill, storage = globalThis.localStorage) {
  try { storage.setItem(PREFILL_KEY, JSON.stringify({ ...prefill, at: Date.now() })); return true; } catch { return false; }
}

export function takePrefill(storage = globalThis.localStorage, maxAgeMs = 10 * 60 * 1000) {
  try {
    const raw = storage.getItem(PREFILL_KEY);
    if (!raw) return null;
    storage.removeItem(PREFILL_KEY);
    const p = JSON.parse(raw);
    return p && Date.now() - (p.at || 0) <= maxAgeMs ? p : null;
  } catch { return null; }
}

// Settings the form shows in the "From your voice request" box.
export const VOICE_FIELDS = [
  ["cap_w", "Power cap", "W"], ["thinking_budget", "Thinking budget", "toggle"],
  ["prefix_cache", "Prefix caching", "toggle"], ["fp8", "FP8", "toggle"],
  ["provider", "Platform", ""], ["model", "Model", ""], ["workload", "Workload", ""],
  ["framework", "Framework", ""], ["p99_pct", "p99 limit", "%"], ["rounds", "Rounds", ""],
  ["max_spend_usd", "Spend cap", "$"],
];

// Copy a voice pre-fill into the form state. Returns the keys that were set.
export function applyPrefill(pf, choice, cfg) {
  const set = [];
  if (!pf) return set;
  cfg.voice = cfg.voice || {};
  for (const [k] of VOICE_FIELDS) {
    if (pf[k] === null || pf[k] === undefined) continue;
    cfg.voice[k] = pf[k];
    set.push(k);
  }
  if (pf.framework) choice.framework = pf.framework;
  if (pf.provider && pf.provider !== "replay") cfg.provider = pf.provider;
  if (pf.p99_pct != null) { cfg.p99_pct = pf.p99_pct; choice.p99_slack_pct = pf.p99_pct; }
  if (pf.rounds != null) cfg.rounds = pf.rounds;
  if (!choice.name) choice.name = "Voice set-up";
  return set;
}

// Run the follow-up for a server answer. `env` is injectable for tests.
export function afterAnswer(data, env = {}) {
  const loc = env.location || globalThis.location;
  const win = env.window || globalThis.window;
  if (!data) return "none";
  if (data.intent === "explain" && data.scope === "page") {
    const said = speakSummary({ speech: data.speak || data.text }, env.speech || {});
    return said ? "spoke" : "shown";
  }
  if (data.action === "prefill" && data.prefill) {
    savePrefill(data.prefill, env.storage);
    const onV2 = /v2\.html$/.test(loc?.pathname || "");
    if (onV2) {
      if (loc.hash !== "#pCfg") loc.hash = "pCfg";
      win?.dispatchEvent?.(new (win.CustomEvent || CustomEvent)("wn:voice-prefill"));
    } else if (loc) {
      loc.href = (data.route || "/dash/v2.html#pCfg");
    }
    return "prefilled";
  }
  return "none";
}
