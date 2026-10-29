// Cooling Hotspots Component (SAM 3.1) - Meta Tools v5

export function extractHotspotData(receipt) {
  const m = receipt?.measurement || {};
  const hc = receipt?.hotspot_cap || m.hotspot_cap || {};
  const mask = hc.mask || {};
  const decision = hc.decision || {};

  return {
    hasData: Boolean(hc && (decision.gpu_id !== undefined || mask.bbox)),
    gpuId: decision.gpu_id ?? 1,
    oldCapW: decision.old_cap_w ?? 400,
    newCapW: decision.new_cap_w ?? 350,
    deltaW: decision.delta_w ?? -50,
    minCapW: decision.min_cap_w ?? 250,
    decisionLabel: decision.label || "ADVICE",
    maskLabel: mask.label || "MOCK",
    maskSha256: mask.sha256 || "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
    polygon: mask.polygon || [[190, 280], [430, 280], [430, 420], [190, 420]],
    bbox: mask.bbox || [190, 280, 430, 420],
    reason: hc.reason || "Hotspot detected in rack slot 1; power cap derated for target GPU only.",
    live: Boolean(hc.live),
    heatLabel: hc.heat_label || (hc.live ? "SIMULATED" : "MOCK"),
    // Stock caps of the other GPUs are demo values: MOCK without a live call, SIMULATED with one.
    capLabel: hc.live ? "SIMULATED" : "MOCK",
    responseId: hc.response_id || null,
    // Real photo + SAM box (live only): image size comes from the SAM raw output (w=960, h=720).
    photo: hc.photo || null,
  };
}

export function renderHotspotHTML(data) {
  const pointsStr = data.polygon.map(pt => `${pt[0]},${pt[1]}`).join(" ");

  return `
    <div class="hotspot-card" id="cooling-hotspots-card">
      <div class="hotspot-header">
        <h3 class="hotspot-title">
          <span>Cooling Hotspots</span>
          <span class="hotspot-badge">SAM 3.1 &bull; ${data.live ? "segmentation " : ""}${data.maskLabel}</span>${data.live ? `<span class="hotspot-badge" title="Not a thermal photo">heat &bull; ${data.heatLabel}</span>` : ""}
        </h3>
        <span class="hotspot-stat-tag">${data.decisionLabel}</span>
      </div>

      <div class="hotspot-grid">
        <div class="hotspot-visual">
          ${data.live && data.photo ? `<svg class="hotspot-svg-wrap" viewBox="0 0 ${data.photo.w} ${data.photo.h}" xmlns="http://www.w3.org/2000/svg" role="img" aria-label="Rack photo with the live SAM 3.1 box">
            <image href="${data.photo.url}" x="0" y="0" width="${data.photo.w}" height="${data.photo.h}" />
            <rect x="${data.bbox[0]}" y="${data.bbox[1]}" width="${data.bbox[2] - data.bbox[0]}" height="${data.bbox[3] - data.bbox[1]}" fill="rgba(224,108,117,0.22)" stroke="#E06C75" stroke-width="4" stroke-dasharray="10,6" />
            <rect x="${data.bbox[0] + 6}" y="${data.bbox[1] + 6}" width="300" height="64" rx="6" fill="rgba(20,16,30,.82)" />
            <text x="${data.bbox[0] + 16}" y="${data.bbox[1] + 32}" fill="#E06C75" font-size="22" font-family="Montserrat" font-weight="bold">SAM 3.1 box [MEASURED]</text>
            <text x="${data.bbox[0] + 16}" y="${data.bbox[1] + 58}" fill="#DCD6E4" font-size="17" font-family="Fira Code">GPU ${data.gpuId} hot: ${data.heatLabel} · ${data.newCapW} W ${data.decisionLabel}</text>
          </svg>
          <p class="hotspot-photo-credit" style="margin:6px 0 0;font-size:11px;color:#8E7A9E">Box [${data.bbox.join(", ")}] from response ${data.responseId}. ${data.photo.credit || ""}</p>` : `<svg class="hotspot-svg-wrap" viewBox="0 0 640 960" xmlns="http://www.w3.org/2000/svg">
            <!-- Rack outline -->
            <rect x="160" y="50" width="310" height="860" rx="8" fill="#1F1A2F" stroke="#4F4A63" stroke-width="3" />
            <!-- Normal slots -->
            <rect x="190" y="110" width="240" height="140" rx="4" fill="#2E2B44" stroke="#6C5E7C" stroke-width="1.5" />
            <text x="310" y="185" fill="#8E7A9E" font-size="20" font-family="Montserrat" text-anchor="middle">GPU 0</text>
            <text x="310" y="215" fill="#6C5E7C" font-size="14" font-family="Fira Code" text-anchor="middle">400 W [${data.capLabel}]</text>

            <!-- Hotspot Slot 1 with Mask Overlay -->
            <polygon points="${pointsStr}" fill="rgba(224, 108, 117, 0.25)" stroke="#E06C75" stroke-width="3" stroke-dasharray="6,4" />
            <text x="310" y="340" fill="#E06C75" font-size="22" font-family="Montserrat" font-weight="bold" text-anchor="middle">GPU ${data.gpuId} HOTSPOT</text>
            <text x="310" y="375" fill="#DCD6E4" font-size="16" font-family="Fira Code" text-anchor="middle">${data.newCapW} W [${data.decisionLabel}]</text>

            <!-- Remaining slots -->
            <rect x="190" y="450" width="240" height="140" rx="4" fill="#2E2B44" stroke="#6C5E7C" stroke-width="1.5" />
            <text x="310" y="525" fill="#8E7A9E" font-size="20" font-family="Montserrat" text-anchor="middle">GPU 2</text>
            <text x="310" y="555" fill="#6C5E7C" font-size="14" font-family="Fira Code" text-anchor="middle">400 W [${data.capLabel}]</text>

            <rect x="190" y="620" width="240" height="140" rx="4" fill="#2E2B44" stroke="#6C5E7C" stroke-width="1.5" />
            <text x="310" y="695" fill="#8E7A9E" font-size="20" font-family="Montserrat" text-anchor="middle">GPU 3</text>
            <text x="310" y="725" fill="#6C5E7C" font-size="14" font-family="Fira Code" text-anchor="middle">400 W [${data.capLabel}]</text>

            <!-- PDU side bar -->
            <rect x="490" y="110" width="70" height="710" rx="4" fill="#2B2637" stroke="#4F4A63" stroke-width="1.5" />
            <text x="525" y="470" fill="#6C5E7C" font-size="16" font-family="Montserrat" text-anchor="middle" transform="rotate(-90 525 470)">PDU FEED</text>
          </svg>`}
        </div>

        <div class="hotspot-details">
          <div class="hotspot-stat-row">
            <div class="hotspot-stat-box">
              <div class="hotspot-stat-label">Target Hardware</div>
              <div class="hotspot-stat-value">GPU ${data.gpuId}</div>
            </div>
            <div class="hotspot-stat-box">
              <div class="hotspot-stat-label">Cap Before</div>
              <div class="hotspot-stat-value">${data.oldCapW} <span style="font-size:0.8rem">W</span><span class="hotspot-stat-tag">${data.capLabel}</span></div>
            </div>
            <div class="hotspot-stat-box">
              <div class="hotspot-stat-label">Cap After</div>
              <div class="hotspot-stat-value" style="color:#A3BE8C">${data.newCapW} <span style="font-size:0.8rem">W</span><span class="hotspot-stat-tag">${data.decisionLabel}</span></div>
            </div>
            <div class="hotspot-stat-box">
              <div class="hotspot-stat-label">Derate Step (not auto-applied)</div>
              <div class="hotspot-stat-value" style="color:#E06C75">${data.deltaW} <span style="font-size:0.8rem">W</span><span class="hotspot-stat-tag">ADVICE</span></div>
            </div>
          </div>

          <div class="hotspot-reason">
            <strong>Planner Decision:</strong> ${data.reason}
          </div>

          <div>
            <div class="hotspot-stat-label hotspot-hash-label">Mask Hash (Covered by Signature)</div>
            <div class="hotspot-hash">${data.maskSha256}</div>
          </div>
        </div>
      </div>
    </div>
  `;
}

export function mountHotspotCard(el, receipt) {
  if (!el) return;
  const data = extractHotspotData(receipt);
  el.innerHTML = renderHotspotHTML(data);
}
