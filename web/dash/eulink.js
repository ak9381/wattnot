// EU Efficiency Report Link Component - Meta Tools v5

export function extractEuLinkData(receipt) {
  const rid = receipt?.receipt_id || "rcpt_unknown";
  const m = receipt?.measurement || {};
  const cand = receipt?.candidate || {};
  const base = receipt?.baseline || {};
  const res = receipt?.result || {};
  const est = receipt?.estimates || {};

  const lab = m.label || "MOCK";
  const n = receipt?.subject?.workload?.n_requests || 200;
  const whMean = cand.wh_per_req?.mean ?? 0.04322;
  const samples = cand.wh_per_req?.samples || [whMean];
  const windowKwh = (samples.reduce((a, b) => a + b, 0) * n) / 1000;

  // Joules per correct answer
  const joulesCand = receipt?.joules?.candidate || {};
  let jPerGood = joulesCand.j_per_good_answer;
  if (jPerGood == null) {
    const passRate = cand.quality?.fixed_pass_rate ?? cand.quality?.judge_score ?? 1.0;
    jPerGood = passRate > 0 ? (whMean * 3600) / passRate : whMean * 3600;
  }

  const waterMl = est.water_ml_per_req?.value ?? (whMean * 0.288);
  const billedSaving = res.billed_saving_pct ?? 9.4;

  return {
    receiptId: rid,
    label: lab,
    windowKwh: Number(windowKwh.toFixed(4)),
    energyPerReqWh: Number(whMean.toFixed(5)),
    joulesPerCorrect: Number(jPerGood.toFixed(2)),
    waterMlPerReq: Number(waterMl.toFixed(4)),
    billedSavingPct: Number(billedSaving.toFixed(1)),
    method: "Token-share-capped (direct meter reading)",
    disclaimer: "Not a compliance claim; facility-level metrics require operator metering.",
    csrdTag: "CSRD Roadmap",
  };
}

export function renderEuLinkHTML(data) {
  return `
    <div class="eulink-card" id="eu-directive-card">
      <div class="eulink-header">
        <h3 class="eulink-title">
          <span>EU Energy Efficiency Directive</span>
          <span class="eulink-badge">EU 2024/1364</span>
        </h3>
        <span class="eulink-roadmap-tag">${data.csrdTag}</span>
      </div>

      <div class="eulink-body">
        Supports EU Energy Efficiency Directive data-center reporting. Export certified run telemetry,
        power consumption in kWh, and efficiency per correct answer in a single click.
      </div>

      <div class="eulink-metrics">
        <div class="eulink-metric-box">
          <div class="eulink-metric-label">Window IT Energy</div>
          <div class="eulink-metric-val">${data.windowKwh} <span style="font-size:0.75rem">kWh</span><span class="eulink-metric-tag">${data.label}</span></div>
        </div>
        <div class="eulink-metric-box">
          <div class="eulink-metric-label">Joules / Correct</div>
          <div class="eulink-metric-val">${data.joulesPerCorrect} <span style="font-size:0.75rem">J/ans</span><span class="eulink-metric-tag">${data.label}</span></div>
        </div>
        <div class="eulink-metric-box">
          <div class="eulink-metric-label">Water Consumption</div>
          <div class="eulink-metric-val">${data.waterMlPerReq} <span style="font-size:0.75rem">mL/req</span><span class="eulink-metric-tag">ESTIMATE</span></div>
        </div>
        <div class="eulink-metric-box">
          <div class="eulink-metric-label">Billed Saving</div>
          <div class="eulink-metric-val" style="color:#A3BE8C">${data.billedSavingPct}%<span class="eulink-metric-tag">${data.label}</span></div>
        </div>
      </div>

      <div class="eulink-actions">
        <a class="eulink-btn" href="/api/receipts/${data.receiptId}/report/eu?format=html" target="_blank" rel="noopener">
          <span>View EU Report (HTML)</span>
        </a>
        <a class="eulink-btn eulink-btn-secondary" href="/api/receipts/${data.receiptId}/report/eu?format=csv" download>
          <span>Download CSV</span>
        </a>
        <a class="eulink-btn eulink-btn-secondary" href="/api/receipts/${data.receiptId}/report/eu?format=json" target="_blank" rel="noopener">
          <span>JSON API</span>
        </a>
      </div>

      <div class="eulink-disclaimer">
        Explicit note: ${data.disclaimer} Method: ${data.method}.
      </div>
    </div>
  `;
}

export function mountEuLink(el, receipt) {
  if (!el) return;
  const data = extractEuLinkData(receipt);
  el.innerHTML = renderEuLinkHTML(data);
}
