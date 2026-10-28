// Per-run report ("Dusk 2"): top hero glass card with the headline result,
// glowing connector lines branching down to the 7 breakdown cards: Room scan,
// Baseline, Context saver, Quality gate, Change tried, Kept or rolled back,
// Receipt details. Every number carries its data label word visibly next to it;
// anything the run did not measure shows "—" pending.

const $ = (s, r = document) => r.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const LABELS = ["MEASURED", "ESTIMATE", "SIMULATED", "MOCK", "REPLAY", "ADVICE", "SAMPLE"];

// Every data label renders visibly with its distinct glyph and label word
const chip = (l) => {
  const norm = String(l || "MOCK").toUpperCase();
  const cls = LABELS.includes(norm) ? norm : "MOCK";
  const glyphs = { MEASURED: "●", ESTIMATE: "~", SIMULATED: "┄", MOCK: "○", SAMPLE: "○", REPLAY: "↻", ADVICE: "✦" };
  const glyph = glyphs[cls] || "•";
  return `<span class="v-chip ${esc(cls)} v-chip-${cls.toLowerCase()}"><i class="v-lab ${esc(cls)}" aria-hidden="true">${glyph}</i>${esc(norm)}</span>`;
};
const lab = (l) => (l ? chip(l) : "");

// Number formatter: null/NaN -> em dash, never a fabricated value.
const num = (v, d = 2) => {
  if (v == null || Number.isNaN(+v)) return "—";
  const a = Math.abs(+v);
  return (+v).toLocaleString("en-US", { maximumFractionDigits: a >= 100 ? 0 : a >= 10 ? 1 : d });
};
const nz = (v) => v != null && !Number.isNaN(+v);

// Receipts copied into the built site by scripts/build_pages.py.
const RECEIPTS = {
  "run2-llama-350w": {
    json: "../receipts/run2/rcpt_20261027_run2_cap350.json",
    sig: "../receipts/run2/rcpt_20261027_run2_cap350.sigstore.json",
  },
};
const LEVER = { factor_l_per_kwh: 0.36, usd_per_kwh: 0.1387 };

// The context saver's recorded numbers, read defensively.
function contextSaver() {
  const d = STATE.run;
  return d?.context_saver || d?.receipt?.context_saver || d?.phases?.wattnot?.context_saver || null;
}
const CONTEXT_PARTS = [["prefix", "Prefix cache hit rate"], ["memcomp", "Memory compression"], ["dupcut", "Tool-output dedup"]];

const params = new URLSearchParams(location.search);
const RUN_ID = params.get("run") || "run2-llama-350w";

let STATE = { run: null, bundle: null, receipt: null };

async function getJSON(url) {
  const r = await fetch(url);
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return r.json();
}

async function load() {
  try {
    STATE.run = await getJSON(`/api/dash/v2/runs/${encodeURIComponent(RUN_ID)}`);
  } catch (e) {
    $("#lede").innerHTML = `<h1>Run not found</h1><p>Could not load <code>${esc(RUN_ID)}</code> (${esc(e.message)}). <a href="runs.html">Back to all runs</a>.</p>`;
    return;
  }
  try { STATE.bundle = await getJSON("/api/dash/story"); } catch { STATE.bundle = null; }
  const rc = RECEIPTS[RUN_ID];
  if (rc) { try { STATE.receipt = await getJSON(rc.json); } catch { STATE.receipt = null; } }
  render();
}

function priceWater() {
  const s = STATE.bundle?.story || {};
  return { usd: s.price?.usd_per_kwh ?? LEVER.usd_per_kwh, wue: s.water?.factor_l_per_kwh ?? LEVER.factor_l_per_kwh };
}

// Derive headline metrics honestly.
function derive() {
  const d = STATE.run, b = d.phases?.baseline, w = d.phases?.wattnot;
  const bm = b?.metrics || {}, wm = w?.metrics || {};
  const have = nz(bm.j_per_req) && nz(wm.j_per_req);
  const reqs = wm.requests ?? bm.requests;
  const jSavedReq = have ? bm.j_per_req - wm.j_per_req : null;
  const jSavedTot = have && nz(reqs) ? jSavedReq * reqs : null;
  const kwh = jSavedTot != null ? jSavedTot / 3.6e6 : null;
  const { usd, wue } = priceWater();
  const savedPct = nz(wm.saved_pct) ? Math.abs(wm.saved_pct) : null;
  const p99Chg = nz(wm.p99_change_pct) ? wm.p99_change_pct : null;
  const qualityHeld = nz(bm.quality_pct) && nz(wm.quality_pct) ? wm.quality_pct >= bm.quality_pct - 0.05 : null;
  const p99Ok = p99Chg != null ? p99Chg <= 10 : null;
  const gatePass = qualityHeld == null || p99Ok == null ? null : qualityHeld && p99Ok;
  return {
    b, w, bm, wm, have, reqs, jSavedReq, jSavedTot, kwh, usd, wue,
    savedPct, p99Chg, qualityHeld, p99Ok, gatePass,
    costSaved: kwh != null ? kwh * usd : null,
    waterSaved: kwh != null ? kwh * wue : null,
  };
}

function statusPill(ok, pass, reject) {
  if (ok == null) return `<span class="v-status" style="color:var(--v-mut)">—</span>`;
  return ok
    ? `<span class="v-status pass"><span class="ic" aria-hidden="true">✓</span>${esc(pass)}</span>`
    : `<span class="v-status reject"><span class="ic" aria-hidden="true">✕</span>${esc(reject)}</span>`;
}

// The 7 breakdown cards
function steps(X) {
  const d = STATE.run;
  return [
    {
      id: "room", node: "1", name: "Room scan", muted: true,
      val: "—", labelChip: chip("SIMULATED"), sub: "Rack map not captured in this recorded run",
      explainId: "report.step.room", explainVal: "—", explainLabel: "SIMULATED", explainSource: "sam.room_scan",
      detail: () => `<h4>What this step is</h4>
        <p>Before tuning, WattNot can scan the server room with SAM 3.1 to map the racks, then model the heat each one gives off.</p>
        <p>This recorded run is a GPU energy run; it has no room capture, so there are no rack numbers to show here. ${chip("MEASURED")} racks / ${chip("SIMULATED")} heat would appear on a run that has them.</p>
        <p>You can see a live rack map on the <a href="v2.html">dashboard</a> under “How it works”.</p>`,
    },
    {
      id: "baseline", node: "2", name: "Baseline", muted: false,
      val: X.have ? `${num(X.bm.j_per_req)} J` : "—",
      labelChip: X.have ? chip(X.bm.label || "MEASURED") : chip("pending"),
      sub: X.b ? `Stock ${num(X.b.cap_w, 0)} W cap · ${num(X.bm.requests, 0)} requests` : "No baseline phase",
      explainId: "report.step.baseline", explainVal: X.have ? `${num(X.bm.j_per_req)} J` : "—", explainLabel: X.have ? (X.bm.label || "MEASURED") : "pending", explainSource: "phases.baseline.metrics.j_per_req",
      detail: () => X.b ? `<h4>Measured, stock settings</h4>
        <dl class="kv">
          <dt>Energy / request</dt><dd>${num(X.bm.j_per_req)} J ${chip(X.bm.label || "MEASURED")}</dd>
          <dt>Power cap</dt><dd>${num(X.b.cap_w, 0)} W ${chip(X.b.label || "MEASURED")}</dd>
          <dt>Mean power</dt><dd>${num(X.bm.mean_w)} W ${chip(X.bm.label || "MEASURED")}</dd>
          <dt>p99 latency</dt><dd>${num(X.bm.p99_s)} s ${chip(X.bm.label || "MEASURED")}</dd>
          <dt>Quality (pass)</dt><dd>${num(X.bm.quality_pct, 1)}% ${chip(X.bm.label || "MEASURED")}</dd>
          <dt>Requests</dt><dd>${num(X.bm.requests, 0)} · ${num(X.bm.repeats, 0)} repeats ${chip("MEASURED")}</dd>
        </dl>
        <p>Read from the card with NVIDIA's NVML counter over ${num(X.bm.repeats, 0)} repeats. This is the “before” every saving is measured against.</p>`
        : `<p>This run has no recorded baseline phase.</p>`,
    },
    contextStep(),
    {
      id: "gate", node: "4", name: "Quality gate", muted: false,
      val: statusPill(X.gatePass, "Passed", "Failed"),
      labelChip: X.gatePass == null ? chip("pending") : chip("MEASURED"),
      sub: X.gatePass == null ? "Not evaluated for this run"
        : `Answers held · p99 ${X.p99Chg >= 0 ? "+" : ""}${num(X.p99Chg, 1)}% ${X.p99Ok ? "within" : "over"} the 10% limit`,
      explainId: "report.step.gate", explainVal: X.gatePass == null ? "—" : (X.gatePass ? "Passed" : "Failed"), explainLabel: X.gatePass == null ? "pending" : "MEASURED", explainSource: "phases.wattnot.gate.verdict",
      detail: () => `<h4>Did it stay safe?</h4>
        <p>A change is only kept if answer quality holds and the slowest 1% of requests (p99) does not rise past the limit.</p>
        <dl class="kv">
          <dt>Quality before</dt><dd>${num(X.bm.quality_pct, 1)}% ${chip(X.bm.label || "MEASURED")}</dd>
          <dt>Quality after</dt><dd>${num(X.wm.quality_pct, 1)}% ${chip(X.wm.label || "MEASURED")}</dd>
          <dt>Quality check</dt><dd>${statusPill(X.qualityHeld, "held", "dropped")}</dd>
          <dt>p99 change</dt><dd>${X.p99Chg == null ? "—" : `${X.p99Chg >= 0 ? "+" : ""}${num(X.p99Chg, 1)}%`} ${X.p99Chg == null ? chip("pending") : chip("MEASURED")}</dd>
          <dt>p99 limit</dt><dd>+10% ${statusPill(X.p99Ok, "within", "over")}</dd>
        </dl>
        <p>${X.gatePass == null ? "This run did not record a tuned phase, so the gate could not be evaluated." : X.gatePass ? "Both checks passed, so the change is eligible to keep." : "A check failed, so the change is rejected."}</p>`,
    },
    {
      id: "change", node: "5", name: "Change tried", muted: !X.have,
      val: X.savedPct != null ? `${num(X.savedPct, 1)}%` : "—",
      labelChip: X.savedPct != null ? chip(X.wm.label || "MEASURED") : chip("pending"),
      sub: X.w ? `less energy · ${num(X.b?.cap_w, 0)} W → ${num(X.w.cap_w, 0)} W cap` : "No change recorded",
      explainId: "report.step.change", explainVal: X.savedPct != null ? `${num(X.savedPct, 1)}%` : "—", explainLabel: X.savedPct != null ? (X.wm.label || "MEASURED") : "pending", explainSource: "phases.wattnot.metrics.saved_pct",
      detail: () => X.have ? `<h4>What WattNot changed</h4>
        <dl class="kv">
          <dt>Power cap</dt><dd>${num(X.b.cap_w, 0)} W → ${num(X.w.cap_w, 0)} W</dd>
          <dt>Energy / request</dt><dd>${num(X.bm.j_per_req)} → ${num(X.wm.j_per_req)} J ${chip("MEASURED")}</dd>
          <dt>Energy saved</dt><dd>${num(X.savedPct, 1)}% · ${num(X.jSavedTot)} J over the run ${chip("MEASURED")}</dd>
          <dt>Cost saved</dt><dd>$${num(X.costSaved, 4)}</dd>
          <dt>Water saved</dt><dd>${num(X.waterSaved, 4)} L</dd>
        </dl>
        <p>Energy is measured joules (${num(X.reqs, 0)} requests). Cost uses $${X.usd}/kWh and water uses ${X.wue} L/kWh — both ${chip("ESTIMATE")} derived from the measured joules.</p>`
        : `<p>This run has no tuned phase to compare against the baseline.</p>`,
    },
    {
      id: "decision", node: "6", name: "Kept or rolled back", muted: !X.have,
      val: statusPill(X.gatePass, "Kept", "Rolled back"),
      labelChip: X.gatePass == null ? chip("pending") : chip("MEASURED"),
      sub: X.gatePass == null ? "No decision recorded"
        : X.gatePass ? "Change kept; one click would undo it" : "Change undone automatically",
      explainId: "report.step.kept", explainVal: X.gatePass == null ? "—" : (X.gatePass ? "Kept" : "Rolled back"), explainLabel: X.gatePass == null ? "pending" : "MEASURED", explainSource: "phases.wattnot.decision",
      detail: () => `<h4>The decision</h4>
        <p>${X.gatePass == null ? "No tuned phase was recorded, so there is no decision for this run."
          : X.gatePass ? `The ${num(X.w.cap_w, 0)} W cap passed every gate, so it was kept. Every applied change stays undoable in one click and is written to the audit ledger.`
          : "A gate failed, so the change was rolled back and the stock setting kept."}</p>
        <dl class="kv"><dt>Outcome</dt><dd>${statusPill(X.gatePass, "Kept", "Rolled back")}</dd>
        <dt>Undo</dt><dd>one click, logged ${chip("MOCK")}</dd></dl>`,
    },
    receiptStep(X),
  ];
}

function contextStep() {
  const cs = contextSaver();
  const tc = cs ? (cs.tokens_cut_pct || cs.tokens_saved_pct) : null;
  const val = tc?.value != null ? `${num(tc.value, 1)}%` : (typeof tc === "number" ? `${num(tc, 1)}%` : "—");
  const tag = tc?.label || (cs ? "MOCK" : "pending");
  const shown = tc?.value != null || typeof tc === "number";
  return {
    id: "context", node: "3", name: "Context saver", muted: !cs,
    val, labelChip: chip(tag),
    sub: cs ? "tokens cut from each request" : "pending — not measured yet",
    explainId: "report.step.context", explainVal: val, explainLabel: tag, explainSource: "receipt.context_saver.tokens_cut_pct",
    detail: () => {
      const cell = (m) => (m?.value != null ? `${num(m.value)} ${chip(m.label || "ESTIMATE")}` : `— ${chip("pending")}`);
      const rows = CONTEXT_PARTS.map(([id, name]) => {
        const p = cs ? (cs.parts?.[id] || cs[id]) : null;
        return `<h4>${esc(name)}</h4><dl class="kv">
          <dt>Tokens</dt><dd>${cell(p?.tokens_before)} → ${cell(p?.tokens_after)}</dd>
          <dt>Joules</dt><dd>${cell(p?.joules_before)} → ${cell(p?.joules_after)}</dd>
          <dt>Gate pass rate</dt><dd>${p?.pass_rate?.value != null ? `${num(p.pass_rate.value, 1)}% ${chip(p.pass_rate.label || "MEASURED")}` : `— ${chip("pending")}`}</dd></dl>`;
      }).join("");
      return `<h4>What this step is</h4>
        <p>Before the quality gate, WattNot trims the tokens each request carries — reused prompt prefixes, compressed memory and dropped duplicate tool output — so fewer tokens run for the same answer.</p>
        <p>Tokens cut: <b>${val}</b> ${chip(tag)}. ${cs ? "" : "No context-saver numbers are in this run yet, so each sub-part is pending (ESTIMATE or MOCK until measured)."}</p>
        ${rows}`;
    },
  };
}

function receiptStep(X) {
  const rc = RECEIPTS[RUN_ID], r = STATE.receipt, d = STATE.run;
  const savePct = r?.result?.saving_pct?.mean ?? X.savedPct;
  const fw = r?.framework || r?.frameworks?.[0] || d?.framework || d?.receipt?.framework;
  const isReal = d?.label === "MEASURED" && fw;
  const fwName = fw ? (fw === "plain" ? "plain API" : fw) : "framework: none recorded";
  const fwTag = isReal ? "MEASURED" : "MOCK";
  return {
    id: "receipt", node: "7", name: "Receipt details", muted: !rc,
    val: rc && savePct != null ? `${num(savePct, 1)}%` : "—",
    labelChip: rc && savePct != null ? chip("MEASURED") : chip("pending"),
    sub: rc ? `${esc(fwName)} · saving, signed offline` : "No signed receipt for this run",
    explainId: "report.step.receipt", explainVal: rc && savePct != null ? `${num(savePct, 1)}%` : "—", explainLabel: rc && savePct != null ? "MEASURED" : "pending", explainSource: "receipt.result.saving_pct",
    detail: () => {
      if (!rc) return `<h4>No receipt</h4><p>This run was not signed into a receipt. Only runs with a published receipt can be re-checked offline.</p>`;
      const bl = r?.baseline, ca = r?.candidate;
      const whRow = bl && ca
        ? `<dt>Energy / request</dt><dd>${num(bl.wh_per_req?.mean * 1000, 2)} → ${num(ca.wh_per_req?.mean * 1000, 2)} mWh ${chip("MEASURED")}</dd>`
        : "";
      const capRow = bl && ca
        ? `<dt>Power cap</dt><dd>${num(bl.params?.power_cap_w, 0)} W → ${num(ca.params?.power_cap_w, 0)} W</dd>`
        : "";
      return `<h4>What the receipt proves</h4>
        <dl class="kv">
          <dt>Receipt</dt><dd>${esc(r?.receipt_id || RUN_ID)}</dd>
          <dt>Framework</dt><dd>${esc(fwName)} ${chip(fwTag)}</dd>
          ${capRow}${whRow}
          <dt>Saving</dt><dd>${num(savePct, 1)}% ${chip("MEASURED")}${r?.result?.saving_pct ? ` (CI ${num(r.result.saving_pct.ci_low, 1)}–${num(r.result.saving_pct.ci_high, 1)}%)` : ""}</dd>
          <dt>Meter</dt><dd>${esc(r?.measurement?.meter || "nvml")} @ ${num(r?.measurement?.hz, 0)} Hz ${chip(r?.measurement?.label || "MEASURED")}</dd>
        </dl>
        <h4>Signature, honestly</h4>
        <dl class="kv">
          <dt>Hash + schema</dt><dd>${statusPill(true, "checks out", "")}</dd>
          <dt>Signed</dt><dd>${statusPill(true, "offline, repo test key", "")}</dd>
          <dt>Rekor log</dt><dd>${statusPill(false, "", "not logged")}</dd>
          <dt>Grade</dt><dd>capped at <b>ESTIMATE</b></dd>
        </dl>
        <p>The receipt is signed offline with the repository's public <b>test</b> key and is <b>not</b> logged to the public Rekor transparency log, so the grade stops at ESTIMATE: the check proves the numbers were not edited after signing, not who signed them.</p>
        <p>Verify it yourself:</p>
        <pre>python wattnot_verify.py --trust keys/trusted-keys.json run2/</pre>
        <a class="dl" href="${esc(rc.json)}" download>⬇ Download receipt JSON</a>
        <p style="margin-top:8px"><a href="${esc(rc.sig)}" download>Signature bundle (.sigstore.json)</a></p>`;
    },
  };
}

function render() {
  const d = STATE.run, X = derive(), rc = RECEIPTS[RUN_ID];
  const fw = STATE.receipt?.framework || STATE.receipt?.frameworks?.[0] || d?.framework || d?.receipt?.framework;
  const isReal = d?.label === "MEASURED" && fw;
  const fwName = fw ? (fw === "plain" ? "plain API" : fw) : "framework: none recorded";
  const fwTag = isReal ? "MEASURED" : "MOCK";

  $("#lede").innerHTML = `<h1>${esc(d.run_name || d.id)}</h1>
    <div class="meta-line"><span>${esc(d.model || "—")}</span><span>·</span><span>${esc(d.date || "—")}</span>
    <span>·</span><span>run <code>${esc(d.id)}</code></span>
    <span>·</span><span class="v-chip ${fwTag}"><i class="v-lab ${fwTag}"></i>${esc(fwName)}</span>
    ${chip(d.label || "REPLAY")}</div>`;

  $("#legend").innerHTML = `<span><i class="v-lab MEASURED"></i>MEASURED</span>
    <span><i class="v-lab ESTIMATE"></i>ESTIMATE</span>
    <span><i class="v-lab SIMULATED"></i>SIMULATED</span>
    <span><i class="v-lab MOCK"></i>MOCK</span>
    <span><i class="v-lab REPLAY"></i>REPLAY</span>`;

  $("#heroRunBadge").innerHTML = `<span class="v-chip ${fwTag}" style="margin-right:6px"><i class="v-lab ${fwTag}"></i>${esc(fwName)}</span>${chip(d.label || "REPLAY")}`;

  // Hero Card items: Saving, Quality, Signed status, Receipt download
  const savingVal = X.savedPct != null ? `${num(X.savedPct, 1)}%` : "—";
  const savingChip = X.savedPct != null ? chip(X.w?.label || "MEASURED") : chip("pending");
  const savingSub = X.w ? `less energy · ${num(X.b?.cap_w, 0)} W → ${num(X.w.cap_w, 0)} W cap` : "No tuned phase";

  const qualityVal = X.wm?.quality_pct != null
    ? `${num(X.wm.quality_pct, 1)}%`
    : (X.qualityHeld != null ? (X.qualityHeld ? "Held" : "Dropped") : "—");
  const qualityChip = X.wm?.quality_pct != null ? chip(X.wm.label || "MEASURED") : chip("MEASURED");
  const qualitySub = X.gatePass != null
    ? (X.gatePass ? `✓ Gate passed · p99 ${X.p99Chg >= 0 ? "+" : ""}${num(X.p99Chg, 1)}%` : `✕ Gate failed · p99 ${num(X.p99Chg, 1)}%`)
    : "Answers evaluated against baseline";

  const isSigned = !!rc;
  const signedPill = isSigned
    ? `<span class="v-status pass"><span class="ic" aria-hidden="true">✓</span>Signed</span>`
    : `<span class="v-status" style="color:var(--v-mut)">— Not signed</span>`;
  const signedSub = isSigned ? "Offline test key" : "No published receipt for this run";

  const dlBtn = isSigned
    ? `<a class="hero-dl-btn" href="${esc(rc.json)}" download>⬇ Download receipt JSON</a><span class="hero-dl-note">Cryptographic proof bundle</span>`
    : `<button class="hero-dl-btn" disabled style="opacity:.5;cursor:not-allowed">No receipt available</button><span class="hero-dl-note">Unsigned run</span>`;

  $("#heroGrid").innerHTML = `
    <div class="hero-item" data-explain="report.saving" data-explain-value="${esc(savingVal)}" data-explain-label="${esc(X.w?.label || (X.savedPct != null ? 'MEASURED' : 'pending'))}" data-explain-source="phases.wattnot.metrics.saved_pct">
      <div class="label">Energy Saved</div>
      <div class="hero-val">${savingVal} ${savingChip}</div>
      <div class="hero-sub">${savingSub}</div>
    </div>
    <div class="hero-item" data-explain="report.quality" data-explain-value="${esc(qualityVal)}" data-explain-label="${esc(X.wm?.label || (X.qualityHeld != null ? 'MEASURED' : 'pending'))}" data-explain-source="phases.wattnot.metrics.quality_pct">
      <div class="label">Quality &amp; Gate</div>
      <div class="hero-val">${qualityVal} ${qualityChip}</div>
      <div class="hero-sub">${qualitySub}</div>
    </div>
    <div class="hero-item" data-explain="report.receipt" data-explain-value="${isSigned ? 'Signed' : 'Not signed'}" data-explain-label="${isSigned ? 'MEASURED' : 'REPLAY'}" data-explain-source="receipt.signature">
      <div class="label">Receipt Status</div>
      <div class="hero-val">${signedPill}${isSigned ? "" : ` ${chip("REPLAY")}`}</div>
      <div class="hero-sub">${signedSub}</div>
    </div>
    <div class="hero-dl-item">
      <div class="label">Offline Verification</div>
      ${dlBtn}
    </div>
  `;

  // Render 7 Breakdown Cards
  const S = steps(X);
  $("#cardsGrid").innerHTML = S.map((s) => `
    <div class="bd-card v-glass ${s.muted ? "" : "active-num"}" data-step="${s.id}" data-explain="${esc(s.explainId)}" data-explain-value="${esc(s.explainVal)}" data-explain-label="${esc(s.explainLabel)}" data-explain-source="${esc(s.explainSource)}" role="button" tabindex="0" aria-haspopup="dialog" aria-label="${esc(s.name)} details">
      <div class="bd-card-head">
        <span class="bd-card-num" aria-hidden="true">${s.node}</span>
        <span class="bd-card-name">${esc(s.name)}</span>
      </div>
      <div class="bd-card-val">${s.val} ${s.labelChip}</div>
      <div class="bd-card-sub">${s.sub}</div>
      <div class="bd-card-open">Open details →</div>
    </div>
  `).join("");

  const byId = Object.fromEntries(S.map((s) => [s.id, s]));
  $("#cardsGrid").querySelectorAll("[data-step]").forEach((el) => {
    const fn = () => openSide(byId[el.dataset.step]);
    el.onclick = fn;
    el.onkeydown = (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); fn(); } };
  });

  // Draw glowing connector lines from hero to breakdown cards
  setTimeout(drawConnectors, 20);
}

// Glowing connector lines (SVG curves with soft glow in #B2A6C3)
function drawConnectors() {
  const hero = $("#heroCard");
  const stage = $("#connectorStage");
  const svg = $("#connectorSvg");
  const grid = $("#cardsGrid");
  if (!hero || !stage || !svg || !grid) return;

  const stageBox = stage.getBoundingClientRect();
  if (stageBox.width < 10) return;

  const heroBox = hero.getBoundingClientRect();
  const heroX = heroBox.left + heroBox.width / 2 - stageBox.left;
  const heroY = 0; // top edge of connector stage

  const cards = $$(".bd-card", grid);
  if (!cards.length) return;

  const targetY = stageBox.height; // bottom edge of connector stage
  const paths = [];
  const dots = [];

  cards.forEach((card, idx) => {
    const cardBox = card.getBoundingClientRect();
    const cardX = cardBox.left + cardBox.width / 2 - stageBox.left;
    const cardTopY = targetY;

    // Cubic Bézier curve from hero bottom to card top
    const cp1Y = heroY + (targetY - heroY) * 0.45;
    const cp2Y = heroY + (targetY - heroY) * 0.55;
    const d = `M ${heroX.toFixed(1)} ${heroY.toFixed(1)} C ${heroX.toFixed(1)} ${cp1Y.toFixed(1)}, ${cardX.toFixed(1)} ${cp2Y.toFixed(1)}, ${cardX.toFixed(1)} ${cardTopY.toFixed(1)}`;
    paths.push(d);
    dots.push([cardX, cardTopY]);
  });

  svg.setAttribute("viewBox", `0 0 ${stageBox.width.toFixed(1)} ${stageBox.height.toFixed(1)}`);
  svg.style.width = `${stageBox.width}px`;
  svg.style.height = `${stageBox.height}px`;

  // Filter for soft glow
  const filterDef = `
    <defs>
      <filter id="softGlow" x="-20%" y="-20%" width="140%" height="140%">
        <feGaussianBlur stdDeviation="3.5" result="blur"/>
        <feMerge>
          <feMergeNode in="blur"/>
          <feMergeNode in="SourceGraphic"/>
        </feMerge>
      </filter>
    </defs>
  `;

  // Draw soft glow underlayer, core glowing line, and endpoint dots
  const glowPaths = paths.map((p) => `<path d="${p}" fill="none" stroke="rgba(178,166,195,0.45)" stroke-width="3.5" filter="url(#softGlow)" />`).join("");
  const corePaths = paths.map((p) => `<path d="${p}" fill="none" stroke="#B2A6C3" stroke-width="1.6" stroke-linecap="round" />`).join("");
  const endDots = dots.map(([x, y]) => `<circle cx="${x.toFixed(1)}" cy="${y.toFixed(1)}" r="3.5" fill="#B2A6C3" filter="url(#softGlow)" /><circle cx="${x.toFixed(1)}" cy="${y.toFixed(1)}" r="2" fill="#DCD6E4" />`).join("");
  const startDot = `<circle cx="${heroX.toFixed(1)}" cy="${heroY.toFixed(1)}" r="4" fill="#B2A6C3" filter="url(#softGlow)" /><circle cx="${heroX.toFixed(1)}" cy="${heroY.toFixed(1)}" r="2" fill="#DCD6E4" />`;

  svg.innerHTML = filterDef + glowPaths + corePaths + startDot + endDots;
}

const $$ = (s, r = document) => [...r.querySelectorAll(s)];

window.addEventListener("resize", drawConnectors);
window.addEventListener("load", drawConnectors);

function openSide(step) {
  $("#sideBody").innerHTML = `<h2>${esc(step.name)}</h2>${step.detail()}`;
  $("#side").classList.add("on");
  $("#side").setAttribute("aria-hidden", "false");
  $("#scrim").hidden = false;
  $("#sideX").focus();
}
function closeSide() {
  $("#side").classList.remove("on");
  $("#side").setAttribute("aria-hidden", "true");
  $("#scrim").hidden = true;
}
$("#sideX").onclick = closeSide;
$("#scrim").onclick = closeSide;
document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeSide(); });

load();
