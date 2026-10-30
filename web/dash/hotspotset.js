// SAM synthetic hotspot-set panel (SYNTHETIC tests). Shows how the SAM 3.1
// hotspot detector scored against a fixed set of SYNTHETIC IR-style thermal
// FAILURE scenes (blocked fan, clogged heatsink, cable blocking airflow):
// per-scene IoU against the ground-truth hot slot, the overall gate verdict,
// and the ESTIMATE energy stamp per generated image. These are SYNTHETIC
// tests, never a real thermal measurement. Pure formatting (no DOM at import
// time) so node can test it, mirroring eyetest.js / hotspot.js / partners.js.

const esc = (s) =>
  String(s == null ? "" : s).replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#039;" }[c]));

const FAULT_NAMES = {
  blocked_fan: "blocked fan",
  clogged_heatsink: "clogged heatsink",
  cable_airflow: "cable blocking airflow",
};

// Pull the hotspot-set block from a receipt (measurement.hotspot_test),
// defensively. hasData is false when a run carries no hotspot set, so the
// panel stays hidden.
export function extractHotspotSet(receipt) {
  const ht = receipt?.measurement?.hotspot_test || receipt?.hotspot_test || null;
  const scenes = Array.isArray(ht?.scenes) ? ht.scenes : [];
  const checked = ht?.checked ?? scenes.length;
  const missed = Array.isArray(ht?.missed) ? ht.missed : scenes.filter((s) => !s.ok).map((s) => s.id);
  const passedCount = ht?.passed_count ?? checked - missed.length;
  const wh = ht?.est_wh_per_image || {};
  return {
    hasData: Boolean(ht && checked > 0),
    label: ht?.label || "SYNTHETIC",
    detector: ht?.detector || "SAM 3.1",
    passed: ht?.passed ?? missed.length === 0,
    checked,
    passedCount,
    missed,
    faults: Array.isArray(ht?.faults) ? ht.faults : [],
    iouPass: ht?.iou_pass == null ? 0.5 : ht.iou_pass,
    reason: ht?.reason || "",
    whPerImage: wh.value == null ? 0.5 : wh.value,
    whLabel: wh.label || "ESTIMATE",
    scenes,
  };
}

// One row per scene: the fault, its slot, the detector's IoU against the
// ground-truth hot slot, and a pass/fail mark.
function sceneRow(scene, iouPass) {
  const ok = Boolean(scene.ok);
  const iou = Number(scene.iou || 0).toLocaleString("en-US", { maximumFractionDigits: 3 });
  const faultName = FAULT_NAMES[scene.fault] || String(scene.fault || "").replace(/_/g, " ");
  return `
    <tr class="hset-row ${ok ? "hset-pass" : "hset-fail"}">
      <td class="hset-mark" aria-hidden="true">${ok ? "✓" : "✗"}</td>
      <td class="hset-id">${esc(scene.id)}<span class="hset-syn" title="generated test scene, not a measurement">${esc(scene.label || "SYNTHETIC")}</span></td>
      <td class="hset-fault">${esc(faultName)}</td>
      <td class="hset-slot">GPU ${esc(scene.slot)}</td>
      <td class="hset-iou">${iou} <span class="hset-thresh">/ ≥ ${esc(iouPass)}</span></td>
      <td class="hset-stat">${ok ? "found" : "missed"}</td>
    </tr>`;
}

export function renderHotspotSetHTML(data) {
  if (!data.hasData) return "";
  const verdictClass = data.passed ? "hset-ok" : "hset-blocked";
  const verdict = data.passed
    ? `Passed · ${data.passedCount}/${data.checked} hotspots found`
    : `Blocked · ${data.missed.length} of ${data.checked} missed`;
  const energy = `${Number(data.whPerImage).toLocaleString("en-US", { maximumFractionDigits: 4 })} Wh/image`;
  const faults = data.faults.map((f) => esc(FAULT_NAMES[f] || String(f).replace(/_/g, " "))).join(", ");
  return `
    <div class="hset-card" id="hotspot-set-card">
      <div class="hset-header">
        <h3 class="hset-title">
          <span>SAM hotspot set</span>
          <span class="hset-badge">${esc(data.detector)} &bull; SYNTHETIC tests</span>
          <span class="hset-badge hset-energy" title="assumed generation energy, not metered">~ ${esc(energy)} &bull; ${esc(data.whLabel)}</span>
        </h3>
        <span class="hset-verdict ${verdictClass}">${esc(verdict)}</span>
      </div>
      <p class="hset-sub">${esc(data.detector)} is run over a fixed set of SYNTHETIC IR-style thermal failure scenes${faults ? ` (${faults})` : ""} and scored by IoU against the known hot slot. These are SYNTHETIC tests, never a real thermal measurement. Any miss rejects the change, blocks the Muse Code PR, and rolls back to the last safe config.</p>
      ${data.reason ? `<p class="hset-reason"><strong>Gate:</strong> ${esc(data.reason)}</p>` : ""}
      <table class="hset-table">
        <thead><tr><th aria-label="result"></th><th>Scene</th><th>Fault</th><th>Slot</th><th>IoU</th><th>Status</th></tr></thead>
        <tbody>${data.scenes.map((s) => sceneRow(s, data.iouPass)).join("")}</tbody>
      </table>
    </div>`;
}

export function mountHotspotSet(el, receipt) {
  if (!el) return;
  const data = extractHotspotSet(receipt);
  el.innerHTML = renderHotspotSetHTML(data);
  el.hidden = !data.hasData;
}
