// Eye test panel (vision quality gate). Shows the served vision model's
// pass/fail on each SYNTHETIC test image, the overall gate verdict, and the
// ESTIMATE energy stamp per generated image. Pure formatting (no DOM at import
// time) so node can test it, mirroring hotspot.js / partners.js.

const esc = (s) =>
  String(s == null ? "" : s).replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#039;" }[c]));

// Pull the eye-test block from a receipt (measurement.eye_test), defensively.
// hasData is false when a run carries no eye test, so the panel stays hidden.
export function extractEyeTest(receipt) {
  const et = receipt?.measurement?.eye_test || receipt?.eye_test || null;
  const images = Array.isArray(et?.images) ? et.images : [];
  const checked = et?.checked ?? images.length;
  const missed = Array.isArray(et?.missed) ? et.missed : images.filter((i) => !i.ok).map((i) => i.id);
  const passedCount = et?.passed_count ?? checked - missed.length;
  const wh = et?.est_wh_per_image || {};
  return {
    hasData: Boolean(et && checked > 0),
    label: et?.label || "SYNTHETIC",
    passed: et?.passed ?? missed.length === 0,
    checked,
    passedCount,
    missed,
    reason: et?.reason || "",
    model: et?.model || "served vision model",
    whPerImage: wh.value == null ? 0.5 : wh.value,
    whLabel: wh.label || "ESTIMATE",
    images,
  };
}

// One row per test image: expected vs the model's answer, with a pass/fail mark.
function imageRow(img) {
  const ok = Boolean(img.ok);
  const got = img.got === "" || img.got == null ? "—" : img.got;
  return `
    <tr class="eye-row ${ok ? "eye-pass" : "eye-fail"}">
      <td class="eye-mark" aria-hidden="true">${ok ? "✓" : "✗"}</td>
      <td class="eye-id">${esc(img.id)}<span class="eye-kind">${esc(img.kind || "")}</span>
        <span class="eye-syn" title="generated test image, not a measurement">${esc(img.label || "SYNTHETIC")}</span></td>
      <td class="eye-exp">${esc(img.expected)}</td>
      <td class="eye-got">${esc(got)}</td>
      <td class="eye-stat">${ok ? "pass" : "miss"}</td>
    </tr>`;
}

export function renderEyeTestHTML(data) {
  if (!data.hasData) return "";
  const verdictClass = data.passed ? "eye-ok" : "eye-blocked";
  const verdict = data.passed
    ? `Passed · ${data.passedCount}/${data.checked} images read correctly`
    : `Blocked · ${data.missed.length} of ${data.checked} misread`;
  const energy = `${Number(data.whPerImage).toLocaleString("en-US", { maximumFractionDigits: 4 })} Wh/image`;
  return `
    <div class="eye-card" id="eye-test-card">
      <div class="eye-header">
        <h3 class="eye-title">
          <span>Eye test</span>
          <span class="eye-badge">Muse Image &bull; ${esc(data.label)}</span>
          <span class="eye-badge eye-energy" title="assumed generation energy, not metered">~ ${esc(energy)} &bull; ${esc(data.whLabel)}</span>
        </h3>
        <span class="eye-verdict ${verdictClass}">${esc(verdict)}</span>
      </div>
      <p class="eye-sub">The served vision model (${esc(data.model)}) answers known-answer SYNTHETIC images after each candidate change. Any miss rejects the change, blocks the Muse Code PR, and rolls back to the last safe config.</p>
      ${data.reason ? `<p class="eye-reason"><strong>Gate:</strong> ${esc(data.reason)}</p>` : ""}
      <table class="eye-table">
        <thead><tr><th aria-label="result"></th><th>Image</th><th>Expected</th><th>Model read</th><th>Status</th></tr></thead>
        <tbody>${data.images.map(imageRow).join("")}</tbody>
      </table>
    </div>`;
}

export function mountEyeTest(el, receipt) {
  if (!el) return;
  const data = extractEyeTest(receipt);
  el.innerHTML = renderEyeTestHTML(data);
  el.hidden = !data.hasData;
}
