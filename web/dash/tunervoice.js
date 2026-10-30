// Talk to the tuner — render a Spark dry-run plan and read its summary aloud with the
// browser's own speech synthesis (labelled "browser voice", never a Meta model).
// Pure helpers here so they can be unit-tested without a browser; voiceguide.js wires them in.

export const VOICE_BADGE = "browser voice";

function esc(s) {
  return String(s == null ? "" : s)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}

// True for a tune response (a Spark-planned command run through the guarded loop).
export function isTuneResponse(data) {
  return !!data && (data.action === "tune" || data.intent === "command");
}

// The line the browser voice should read. Falls back to the display text.
export function speechTextFor(data) {
  if (!data) return "";
  return String(data.speech || data.text || "").trim();
}

// Render the dry-run plan: the mode, the "browser voice" badge, each planned change with
// its keep/reject routing, and what was not understood. All interpolated text is escaped.
export function renderTunePlanHTML(data) {
  if (!isTuneResponse(data)) return "";
  const plan = data.plan || {};
  const changes = (data.guard && data.guard.changes) || [];
  const mode = (data.mode || "dry-run").toUpperCase();
  const planner = (plan.planner && plan.planner.name) || "Spark";

  const rows = changes.map((c) => {
    const person = c.needs_person;
    const verdict = person ? "needs a person" : "auto-approved";
    const cls = person ? "vg-tune-person" : "vg-tune-auto";
    const why = c.reasons && c.reasons.length ? ` — ${esc(c.reasons[0])}` : "";
    return `<li><span class="vg-tune-change">${esc(c.change)}</span>` +
           `<span class="vg-tune-route ${cls}">${esc(verdict)}</span>` +
           `<span class="vg-tune-why">${why}</span></li>`;
  }).join("");

  const unsupported = (plan.unsupported || []).length
    ? `<p class="vg-tune-skip">Not planned (unknown to ${esc(planner)}): ` +
      `${esc(plan.unsupported.join("; "))}.</p>`
    : "";

  const planBlock = changes.length
    ? `<ol class="vg-tune-plan">${rows}</ol>`
    : `<p class="vg-tune-empty">${esc(data.text || "No change could be planned.")}</p>`;

  return (
    `<div class="vg-tune">` +
    `<div class="vg-tune-head">` +
    `<span class="vg-tune-mode">${esc(mode)}</span>` +
    `<button type="button" class="vg-speak-btn" title="Read the summary aloud (browser voice)" ` +
    `aria-label="Read the summary aloud with the browser voice">&#128266; ${esc(VOICE_BADGE)}</button>` +
    `</div>` +
    planBlock +
    unsupported +
    `<p class="vg-tune-note">${esc(planner)} planned this and the guardrail routed it. ` +
    `${mode === "DRY-RUN" ? "Nothing was changed." : "Filed for approval; nothing was changed yet."}</p>` +
    `</div>`
  );
}

// Read the summary aloud via the browser's speechSynthesis. `synth` and `make` are injectable
// for tests. Returns the spoken text, or null when speech is unavailable or there is nothing
// to say. Never throws — a missing/!blocked synth just means no audio.
export function speakSummary(data, opts = {}) {
  const text = speechTextFor(data);
  if (!text) return null;
  const synth = opts.synth || (typeof globalThis !== "undefined" ? globalThis.speechSynthesis : null);
  const make = opts.make || (typeof globalThis !== "undefined" && globalThis.SpeechSynthesisUtterance
    ? (t) => new globalThis.SpeechSynthesisUtterance(t)
    : null);
  if (!synth || !make) return null;
  try {
    synth.cancel();
    const u = make(text);
    u.rate = opts.rate || 1.0;
    u.pitch = opts.pitch || 1.0;
    synth.speak(u);
    return text;
  } catch {
    return null;
  }
}

export function stopSpeaking(synth) {
  const s = synth || (typeof globalThis !== "undefined" ? globalThis.speechSynthesis : null);
  try { if (s) s.cancel(); } catch { /* nothing to stop */ }
}
