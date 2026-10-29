// EU efficiency report: a top bento card (one click opens the report) plus the detail card.
// Honest labels: every number and its label come from the receipt. Missing => "–" + pending dot.

export const EU_DIR = "eu/";
export const EU_RECEIPT = "eu/run2-receipt.json";
export const reportHref = (rid, fmt = "html") => `${EU_DIR}eu_report_${rid}.${fmt}`;
const DASH = "–";
const num = (v) => (typeof v === "number" && Number.isFinite(v) ? v : null);
const fix = (v, d) => (v == null ? DASH : Number(v.toFixed(d)).toString());
const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

export function extractEuLinkData(receipt) {
  const r = receipt || {};
  const m = r.measurement || {}, cand = r.candidate || {}, res = r.result || {}, est = r.estimates || {};
  const J = r.joules || {};
  const label = m.label || null;
  const n = num(r.subject?.workload?.n_requests);
  const whMean = num(cand.wh_per_req?.mean);
  const samples = Array.isArray(cand.wh_per_req?.samples) ? cand.wh_per_req.samples.filter((x) => num(x) != null) : null;
  const windowKwh = n != null && samples?.length ? (samples.reduce((a, b) => a + b, 0) * n) / 1000 : null;
  return {
    receiptId: r.receipt_id || null,
    created: typeof r.created_at === "string" ? r.created_at.slice(0, 10) : null,
    label,
    windowKwh,
    energyPerReqWh: whMean,
    jBase: num(J.baseline?.j_per_good_answer),
    jCand: num(J.candidate?.j_per_good_answer),
    jChangePct: num(J.change_pct?.j_per_good_answer),
    passRate: num(J.candidate?.pass_rate ?? cand.quality?.fixed_pass_rate),
    jLabel: J.label || null,
    waterMlPerReq: num(est.water_ml_per_req?.value),
    billedSavingPct: num(res.billed_saving_pct),
    method: "NVML energy counter on the GPU; J per correct = Wh/req x 3600 / pass rate",
    disclaimer: "Not a compliance claim; facility-level metrics require operator metering.",
    csrdTag: "CSRD roadmap",
  };
}

const TIPS = { MEASURED: "Read from a meter or a real run.", ESTIMATE: "Calculated from measured numbers plus a stated assumption.", SIMULATED: "Produced by a model or simulator, not measured.", MOCK: "Sample data that shows the layout only.", REPLAY: "Replayed from saved, signed records; nothing new was run." };
const tip = (l) => esc(`${l}: ${TIPS[l] || "label from the receipt"}`);
const dot = (l) => (l ? `<span class="wn-dot wn-dot-${esc(l.toLowerCase())}" data-wnq="${esc(l)}" tabindex="0" title="${tip(l)}" aria-label="${tip(l)}"></span>`
  : `<span class="wn-dot wn-dot-pending" data-wnq="PENDING" tabindex="0" title="Pending: not in this receipt yet" aria-label="Pending: not in this receipt yet"></span>`);
const val = (v, d, unit, l) => (v == null ? `${DASH}${dot(null)}` : `${fix(v, d)}${unit ? ` <small>${unit}</small>` : ""}${dot(l)}`);

// Top-of-page bento card: big number + short label, one-click Open report, popup with what/how.
export function renderEuCardHTML(d) {
  const ok = !!d.receiptId;
  const big = d.jChangePct == null ? `${DASH}${dot(null)}` : `${d.jChangePct > 0 ? "+" : ""}${fix(d.jChangePct, 1)}%${dot(d.jLabel || d.label)}`;
  const open = ok ? `<a class="eucard-btn" href="${reportHref(d.receiptId)}" target="_blank" rel="noopener">Open EU report</a>
      <a class="eucard-btn eucard-btn2" href="${reportHref(d.receiptId, "csv")}" download>CSV</a>` : `<span class="eucard-btn eucard-off">Report pending${dot(null)}</span>`;
  return `<section class="eucard" id="eu-report-card" aria-label="EU efficiency report">
    <button class="eucard-main" type="button" aria-haspopup="dialog" title="What is this and how was it worked out?">
      <span class="eucard-k">EU efficiency report</span>
      <span class="eucard-big">${big}</span>
      <span class="eucard-l">J per correct answer${d.receiptId ? ` · ${esc(d.receiptId)}${d.created ? `, ${esc(d.created)}` : ""}` : ""}</span>
      <span class="eucard-l">${val(d.jBase, 2, "", d.jLabel || d.label)} → ${val(d.jCand, 2, "J", d.jLabel || d.label)} · pass ${d.passRate == null ? `${DASH}${dot(null)}` : `${fix(d.passRate * 100, 1)}%`}</span>
    </button>
    <div class="eucard-acts">${open}<a class="eucard-btn eucard-btn2" href="eu.html">EU Compliance tab</a><span class="eucard-road">${d.csrdTag}</span></div>
    <dialog class="eucard-pop" aria-label="EU efficiency report: what and how">
      <button class="eucard-x" type="button" aria-label="Close">×</button>
      <h3>EU efficiency report</h3>
      <p><b>What:</b> the fields software can fill for EU Energy Efficiency Directive data-centre reporting (Delegated Reg. (EU) 2024/1364), from one signed receipt. Facility fields (PUE, renewables, heat reuse) show NOT_AVAILABLE.</p>
      <p><b>How:</b> ${esc(d.method)}. Baseline vs 350 W cap, same prompts and grader. The report is mixed: energy MEASURED, water ESTIMATE, facility fields NOT_AVAILABLE.</p>
      <p><b>Receipt:</b> <code>${esc(d.receiptId || DASH)}</code> · label ${d.label ? esc(d.label) : DASH}${dot(d.label)}</p>
      <p class="eucard-note">${esc(d.disclaimer)} CSRD: roadmap.</p>
    </dialog>
  </section>`;
}

export function wireEuCard(root) {
  const sec = root?.querySelector?.(".eucard"); if (!sec) return;
  const dlg = sec.querySelector("dialog");
  sec.querySelector(".eucard-main").onclick = () => (dlg.showModal ? dlg.showModal() : dlg.setAttribute("open", ""));
  sec.querySelector(".eucard-x").onclick = () => (dlg.close ? dlg.close() : dlg.removeAttribute("open"));
  dlg.addEventListener("click", (e) => { if (e.target === dlg) dlg.close(); });
}

export function renderEuLinkHTML(d) {
  const L = d.label;
  const href = d.receiptId ? reportHref(d.receiptId) : null;
  return `
    <div class="eulink-card" id="eu-directive-card">
      <div class="eulink-header">
        <h3 class="eulink-title"><span>EU Energy Efficiency Directive</span><span class="eulink-badge">EU 2024/1364</span></h3>
        <span class="eulink-roadmap-tag">${d.csrdTag}</span>
      </div>
      <div class="eulink-body">Supports EU Energy Efficiency Directive data-center reporting. Every value comes from the signed receipt; missing ones show ${DASH}.</div>
      <div class="eulink-metrics">
        <div class="eulink-metric-box"><div class="eulink-metric-label">Window IT Energy</div><div class="eulink-metric-val">${val(d.windowKwh, 4, "kWh", L)}</div></div>
        <div class="eulink-metric-box"><div class="eulink-metric-label">Joules / Correct</div><div class="eulink-metric-val">${val(d.jCand, 2, "J/ans", d.jLabel || L)}</div></div>
        <div class="eulink-metric-box"><div class="eulink-metric-label">Water Consumption</div><div class="eulink-metric-val">${val(d.waterMlPerReq, 4, "mL/req", d.waterMlPerReq == null ? null : "ESTIMATE")}</div></div>
        <div class="eulink-metric-box"><div class="eulink-metric-label">Billed Saving</div><div class="eulink-metric-val">${d.billedSavingPct == null ? `${DASH}${dot(null)}` : `${fix(d.billedSavingPct, 1)}%${dot(L)}`}</div></div>
      </div>
      <div class="eulink-actions">${href ? `<a class="eulink-btn" href="${href}" target="_blank" rel="noopener"><span>View EU Report (HTML)</span></a>
        <a class="eulink-btn eulink-btn-secondary" href="${reportHref(d.receiptId, "csv")}" download><span>Download CSV</span></a>` : `<span class="eulink-btn eulink-btn-secondary">Report pending</span>`}</div>
      <div class="eulink-disclaimer">Explicit note: ${d.disclaimer} Method: ${d.method}.</div>
    </div>`;
}

export function mountEuLink(el, receipt) {
  if (!el) return;
  el.innerHTML = renderEuLinkHTML(extractEuLinkData(receipt));
}

export async function loadEuReceipt(url = EU_RECEIPT) {
  try { const r = await fetch(url); return r.ok ? await r.json() : null; } catch { return null; }
}

export async function mountEuCard(el, receipt) {
  if (!el) return;
  const rc = receipt ?? (await loadEuReceipt());
  el.innerHTML = renderEuCardHTML(extractEuLinkData(rc));
  wireEuCard(el);
}

// Dashboard (v2.html#pHome): pHome is re-rendered by v2.js, so re-insert the card on top when it changes.
export function autoMountDashboard(doc = globalThis.document) {
  const home = doc?.getElementById?.("pHome"); if (!home) return;
  let rc;
  const put = async () => {
    if (home.querySelector("#eu-report-card")) return;
    rc ??= await loadEuReceipt();
    if (home.querySelector("#eu-report-card")) return;
    const w = doc.createElement("div"); w.className = "eucard-wrap"; home.prepend(w);
    await mountEuCard(w, rc);
  };
  new MutationObserver(() => put()).observe(home, { childList: true });
  put();
}
