// Spoken answers panel (watts-per-spoken-answer gate). Shows the served
// model's pass/fail on each SYNTHETIC recorded question (clean + data-center
// noise), the overall gate verdict, and the energy per correct spoken answer
// next to joules per token and joules per correct (text) answer. Pure
// formatting (no DOM at import time) so node can test it, mirroring eyetest.js.

const esc = (s) =>
  String(s == null ? "" : s).replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#039;" }[c]));

// Pull the spoken-test block from a receipt (measurement.spoken_test),
// defensively. hasData is false when a run carries no spoken test, so the
// panel stays hidden.
export function extractSpokenAnswer(receipt) {
  const st = receipt?.measurement?.spoken_test || receipt?.spoken_test || null;
  const items = Array.isArray(st?.items) ? st.items : [];
  const checked = st?.checked ?? items.length;
  const missed = Array.isArray(st?.missed) ? st.missed : items.filter((i) => !i.ok).map((i) => i.id);
  const passedCount = st?.passed_count ?? checked - missed.length;
  const energy = st?.energy && typeof st.energy === "object" ? st.energy : null;
  return {
    hasData: Boolean(st && checked > 0),
    label: st?.label || "SYNTHETIC",
    transcriber: st?.transcriber || "muse-voice-transcribe-1.0",
    passed: st?.passed ?? missed.length === 0,
    checked,
    passedCount,
    missed,
    missedNoisy: Array.isArray(st?.missed_noisy) ? st.missed_noisy : [],
    reason: st?.reason || "",
    model: st?.model || "served model",
    energy,
    items,
  };
}

// The noise tag text: "data-center" for a take recorded over data-center noise,
// "clean" otherwise.
function noiseTag(noise) {
  return noise === "datacenter" ? "data-center" : "clean";
}

// One row per spoken question: expected vs the model's transcribed answer,
// with a pass/fail mark, kind, noise and SYNTHETIC tags.
function itemRow(it) {
  const ok = Boolean(it.ok);
  const got = it.got === "" || it.got == null ? "—" : it.got;
  return `
    <tr class="spk-row ${ok ? "spk-pass" : "spk-fail"}">
      <td class="spk-mark" aria-hidden="true">${ok ? "✓" : "✗"}</td>
      <td class="spk-id">${esc(it.id)}<span class="spk-kind">${esc(it.kind || "")}</span>
        <span class="spk-noise" title="recording condition">${esc(noiseTag(it.noise))}</span>
        <span class="spk-syn" title="known-answer recorded question, not a measurement">${esc(it.label || "SYNTHETIC")}</span></td>
      <td class="spk-exp">${esc(it.expected)}</td>
      <td class="spk-got">${esc(got)}</td>
      <td class="spk-stat">${ok ? "pass" : "miss"}</td>
    </tr>`;
}

// The three energy numbers (joules per correct spoken answer, per token, and
// per correct text answer) with their label word.
function energyBadge(e) {
  if (!e) return "";
  const fmt = (v) => Number(v).toLocaleString("en-US", { maximumFractionDigits: 0 });
  const text = `${fmt(e.j_per_correct_spoken)} J/correct spoken · ${fmt(e.j_per_token)} J/token · ${fmt(e.j_per_correct_answer)} J/correct answer · ${e.label || "MEASURED"}`;
  return `<span class="spk-badge spk-energy" title="${esc(e.meter || "energy per spoken answer")}">${esc(text)}</span>`;
}

export function renderSpokenAnswerHTML(data) {
  if (!data.hasData) return "";
  const verdictClass = data.passed ? "spk-ok" : "spk-blocked";
  const verdict = data.passed
    ? `Passed · ${data.passedCount}/${data.checked} answers correct`
    : `Blocked · ${data.missed.length} of ${data.checked} misheard`;
  return `
    <div class="spk-card" id="spoken-answer-card">
      <div class="spk-header">
        <h3 class="spk-title">
          <span>Spoken answers</span>
          <span class="spk-badge">Muse Voice &bull; ${esc(data.label)}</span>
          ${energyBadge(data.energy)}
        </h3>
        <span class="spk-verdict ${verdictClass}">${esc(verdict)}</span>
      </div>
      <p class="spk-sub">The served model (${esc(data.model)}) answers known-answer SYNTHETIC spoken questions (clean + data-center noise) transcribed once by Muse Voice Transcribe. Any wrong answer rejects the change, blocks the Muse Code PR, and rolls back to the last safe config.</p>
      ${data.reason ? `<p class="spk-reason"><strong>Gate:</strong> ${esc(data.reason)}</p>` : ""}
      <table class="spk-table">
        <thead><tr><th aria-label="result"></th><th>Question</th><th>Expected</th><th>Model answer</th><th>Status</th></tr></thead>
        <tbody>${data.items.map(itemRow).join("")}</tbody>
      </table>
    </div>`;
}

export function mountSpokenAnswer(el, receipt) {
  if (!el) return;
  const data = extractSpokenAnswer(receipt);
  el.innerHTML = renderSpokenAnswerHTML(data);
  el.hidden = !data.hasData;
}
