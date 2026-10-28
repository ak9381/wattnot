// Per-run report: one run told as a stepped story — room scan, baseline,
// quality gate, change tried, kept or rolled back, signed receipt. Cards show
// a name and one value; the full breakdown opens in the side panel. Every
// number carries its data label; anything the run did not measure shows "—".
// Works under a GitHub Pages subpath: data is fetched from absolute /api/...
// paths (the static-api shim rewrites them) and the receipt download is a
// relative ../receipts/ link.

const $ = (s, r = document) => r.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const LABELS = ["MEASURED", "ESTIMATE", "SIMULATED", "MOCK", "REPLAY", "ADVICE", "SAMPLE"];
const lab = (l) => (l && LABELS.includes(l) ? `<span class="v-lab ${l}" title="${esc(l)}" aria-label="label ${esc(l)}"></span>` : "");
const chip = (l) => (l ? `<span class="v-chip"><i class="v-lab ${LABELS.includes(l) ? l : "MOCK"}" style="margin:0"></i>${esc(l)}</span>` : "");
// Number formatter: null/NaN -> em dash, never a fabricated value.
const num = (v, d = 2) => {
  if (v == null || Number.isNaN(+v)) return "—";
  const a = Math.abs(+v);
  return (+v).toLocaleString("en-US", { maximumFractionDigits: a >= 100 ? 0 : a >= 10 ? 1 : d });
};
const nz = (v) => v != null && !Number.isNaN(+v);

// Receipts copied into the built site by scripts/build_pages.py. A run with no
// entry here honestly says it has no signed receipt.
const RECEIPTS = {
  "run2-llama-350w": {
    json: "../receipts/run2/rcpt_20261027_run2_cap350.json",
    sig: "../receipts/run2/rcpt_20261027_run2_cap350.sigstore.json",
  },
};
const LEVER = { factor_l_per_kwh: 0.36, usd_per_kwh: 0.1387 };

// The context saver's recorded numbers, read defensively from wherever a later
// build writes them. Returns the object or null (then the card is pending).
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

// Derive every headline the cards need, honestly (null when not measured).
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

// ---- the six story steps ----
function steps(X) {
  const d = STATE.run;
  return [
    {
      id: "room", node: "1", name: "Room scan", muted: true,
      val: "—", labelDot: "", sub: "Rack map not captured in this recorded run",
      detail: () => `<h4>What this step is</h4>
        <p>Before tuning, WattNot can scan the server room with SAM 3.1 to map the racks, then model the heat each one gives off.</p>
        <p>This recorded run is a GPU energy run; it has no room capture, so there are no rack numbers to show here ${chip("MEASURED")} racks / ${chip("SIMULATED")} heat would appear on a run that has them.</p>
        <p>You can see a live rack map on the <a href="v2.html">dashboard</a> under “How it works”.</p>`,
    },
    {
      id: "baseline", node: "2", name: "Baseline", muted: false,
      val: X.have ? `${num(X.bm.j_per_req)} J` : "—",
      labelDot: X.have ? lab(X.bm.label || "MEASURED") : "",
      sub: X.b ? `Stock ${num(X.b.cap_w, 0)} W cap · ${num(X.bm.requests, 0)} requests` : "No baseline phase",
      detail: () => X.b ? `<h4>Measured, stock settings</h4>
        <dl class="kv">
          <dt>Energy / request</dt><dd>${num(X.bm.j_per_req)} J ${lab(X.bm.label)}</dd>
          <dt>Power cap</dt><dd>${num(X.b.cap_w, 0)} W ${lab(X.b.label)}</dd>
          <dt>Mean power</dt><dd>${num(X.bm.mean_w)} W ${lab(X.bm.label)}</dd>
          <dt>p99 latency</dt><dd>${num(X.bm.p99_s)} s ${lab(X.bm.label)}</dd>
          <dt>Quality (pass)</dt><dd>${num(X.bm.quality_pct, 1)}% ${lab(X.bm.label)}</dd>
          <dt>Requests</dt><dd>${num(X.bm.requests, 0)} · ${num(X.bm.repeats, 0)} repeats</dd>
        </dl>
        <p>Read from the card with NVIDIA's NVML counter over ${num(X.bm.repeats, 0)} repeats. This is the “before” every saving is measured against.</p>`
        : `<p>This run has no recorded baseline phase.</p>`,
    },
    contextStep(),
    {
      id: "gate", node: "4", name: "Quality gate", muted: false,
      val: statusPill(X.gatePass, "Passed", "Failed"),
      labelDot: X.gatePass == null ? "" : lab("MEASURED"),
      sub: X.gatePass == null ? "Not evaluated for this run"
        : `Answers held · p99 ${X.p99Chg >= 0 ? "+" : ""}${num(X.p99Chg, 1)}% ${X.p99Ok ? "within" : "over"} the 10% limit`,
      detail: () => `<h4>Did it stay safe?</h4>
        <p>A change is only kept if answer quality holds and the slowest 1% of requests (p99) does not rise past the limit.</p>
        <dl class="kv">
          <dt>Quality before</dt><dd>${num(X.bm.quality_pct, 1)}% ${lab(X.bm.label)}</dd>
          <dt>Quality after</dt><dd>${num(X.wm.quality_pct, 1)}% ${lab(X.wm.label)}</dd>
          <dt>Quality check</dt><dd>${statusPill(X.qualityHeld, "held", "dropped")}</dd>
          <dt>p99 change</dt><dd>${X.p99Chg == null ? "—" : `${X.p99Chg >= 0 ? "+" : ""}${num(X.p99Chg, 1)}%`} ${X.p99Chg == null ? "" : lab("MEASURED")}</dd>
          <dt>p99 limit</dt><dd>+10% ${statusPill(X.p99Ok, "within", "over")}</dd>
        </dl>
        <p>${X.gatePass == null ? "This run did not record a tuned phase, so the gate could not be evaluated." : X.gatePass ? "Both checks passed, so the change is eligible to keep." : "A check failed, so the change is rejected."}</p>`,
    },
    {
      id: "change", node: "5", name: "Change tried", muted: !X.have,
      val: X.savedPct != null ? `${num(X.savedPct, 1)}%` : "—",
      labelDot: X.savedPct != null ? lab(X.wm.label || "MEASURED") : "",
      sub: X.w ? `less energy · ${num(X.b?.cap_w, 0)} W → ${num(X.w.cap_w, 0)} W cap` : "No change recorded",
      detail: () => X.have ? `<h4>What WattNot changed</h4>
        <dl class="kv">
          <dt>Power cap</dt><dd>${num(X.b.cap_w, 0)} W → ${num(X.w.cap_w, 0)} W</dd>
          <dt>Energy / request</dt><dd>${num(X.bm.j_per_req)} → ${num(X.wm.j_per_req)} J ${lab("MEASURED")}</dd>
          <dt>Energy saved</dt><dd>${num(X.savedPct, 1)}% · ${num(X.jSavedTot)} J over the run ${lab("MEASURED")}</dd>
          <dt>Cost saved</dt><dd>$${num(X.costSaved, 4)} ${lab("ESTIMATE")}</dd>
          <dt>Water saved</dt><dd>${num(X.waterSaved, 4)} L ${lab("ESTIMATE")}</dd>
        </dl>
        <p>Energy is measured joules (${num(X.reqs, 0)} requests). Cost uses $${X.usd}/kWh and water uses ${X.wue} L/kWh — both ${chip("ESTIMATE")} derived from the measured joules.</p>`
        : `<p>This run has no tuned phase to compare against the baseline.</p>`,
    },
    {
      id: "decision", node: "6", name: "Kept or rolled back", muted: !X.have,
      val: statusPill(X.gatePass, "Kept", "Rolled back"),
      labelDot: X.gatePass == null ? "" : lab("MEASURED"),
      sub: X.gatePass == null ? "No decision recorded"
        : X.gatePass ? "Change kept; one click would undo it" : "Change undone automatically",
      detail: () => `<h4>The decision</h4>
        <p>${X.gatePass == null ? "No tuned phase was recorded, so there is no decision for this run."
          : X.gatePass ? `The ${num(X.w.cap_w, 0)} W cap passed every gate, so it was kept. Every applied change stays undoable in one click and is written to the audit ledger.`
          : "A gate failed, so the change was rolled back and the stock setting kept."}</p>
        <dl class="kv"><dt>Outcome</dt><dd>${statusPill(X.gatePass, "Kept", "Rolled back")}</dd>
        <dt>Undo</dt><dd>one click, logged ${lab("MOCK")}</dd></dl>`,
    },
    receiptStep(X),
  ];
}

// Context saver: tokens cut per request, ESTIMATE or MOCK until measured. The
// card shows only the name + the tokens-cut value; clicking opens the three
// sub-parts with tokens/joules before→after and each one's gate pass rate.
function contextStep() {
  const cs = contextSaver();
  const tc = cs ? (cs.tokens_cut_pct || cs.tokens_saved_pct) : null;
  const val = tc?.value != null ? `${num(tc.value, 1)}%` : (typeof tc === "number" ? `${num(tc, 1)}%` : "—");
  const tag = tc?.label || (cs ? "MOCK" : null);
  const shown = tc?.value != null || typeof tc === "number";
  return {
    id: "context", node: "3", name: "Context saver", muted: !cs,
    val, labelDot: shown ? lab(tag) : "",
    sub: cs ? "tokens cut from each request" : "pending — not measured yet",
    detail: () => {
      const cell = (m) => (m?.value != null ? `${num(m.value)} ${lab(m.label || "ESTIMATE")}` : "—");
      const rows = CONTEXT_PARTS.map(([id, name]) => {
        const p = cs ? (cs.parts?.[id] || cs[id]) : null;
        return `<h4>${esc(name)}</h4><dl class="kv">
          <dt>Tokens</dt><dd>${cell(p?.tokens_before)} → ${cell(p?.tokens_after)}</dd>
          <dt>Joules</dt><dd>${cell(p?.joules_before)} → ${cell(p?.joules_after)}</dd>
          <dt>Gate pass rate</dt><dd>${p?.pass_rate?.value != null ? `${num(p.pass_rate.value, 1)}% ${lab(p.pass_rate.label || "MEASURED")}` : `— ${chip("PENDING")}`}</dd></dl>`;
      }).join("");
      return `<h4>What this step is</h4>
        <p>Before the quality gate, WattNot trims the tokens each request carries — reused prompt prefixes, compressed memory and dropped duplicate tool output — so fewer tokens run for the same answer.</p>
        <p>Tokens cut: <b>${val}</b> ${shown ? lab(tag) : `${chip("PENDING")}`}. ${cs ? "" : "No context-saver numbers are in this run yet, so each sub-part is pending (ESTIMATE or MOCK until measured)."}</p>
        ${rows}`;
    },
  };
}
function receiptStep(X) {
  const rc = RECEIPTS[RUN_ID], r = STATE.receipt;
  const savePct = r?.result?.saving_pct?.mean ?? X.savedPct;
  return {
    id: "receipt", node: "7", name: "Signed receipt", muted: !rc,
    val: rc && savePct != null ? `${num(savePct, 1)}%` : "—",
    labelDot: rc && savePct != null ? lab("MEASURED") : "",
    sub: rc ? "saving, signed offline · grade ESTIMATE" : "No signed receipt for this run",
    detail: () => {
      if (!rc) return `<h4>No receipt</h4><p>This run was not signed into a receipt. Only runs with a published receipt can be re-checked offline.</p>`;
      const bl = r?.baseline, ca = r?.candidate;
      const whRow = bl && ca
        ? `<dt>Energy / request</dt><dd>${num(bl.wh_per_req?.mean * 1000, 2)} → ${num(ca.wh_per_req?.mean * 1000, 2)} mWh ${lab("MEASURED")}</dd>`
        : "";
      const capRow = bl && ca
        ? `<dt>Power cap</dt><dd>${num(bl.params?.power_cap_w, 0)} W → ${num(ca.params?.power_cap_w, 0)} W</dd>`
        : "";
      return `<h4>What the receipt proves</h4>
        <dl class="kv">
          <dt>Receipt</dt><dd>${esc(r?.receipt_id || RUN_ID)}</dd>
          ${capRow}${whRow}
          <dt>Saving</dt><dd>${num(savePct, 1)}% ${lab("MEASURED")}${r?.result?.saving_pct ? ` (CI ${num(r.result.saving_pct.ci_low, 1)}–${num(r.result.saving_pct.ci_high, 1)}%)` : ""}</dd>
          <dt>Meter</dt><dd>${esc(r?.measurement?.meter || "nvml")} @ ${num(r?.measurement?.hz, 0)} Hz ${lab(r?.measurement?.label || "MEASURED")}</dd>
        </dl>
        <h4>Signature, honestly</h4>
        <dl class="kv">
          <dt>Hash + schema</dt><dd>${statusPill(true, "checks out", "")}</dd>
          <dt>Signed</dt><dd>${statusPill(true, "offline, repo test key", "")}</dd>
          <dt>Rekor log</dt><dd>${statusPill(false, "", "not logged")}</dd>
          <dt>Grade</dt><dd>capped at <b>ESTIMATE</b> ${lab("ESTIMATE")}</dd>
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
  const d = STATE.run, X = derive();
  $("#lede").innerHTML = `<h1>${esc(d.run_name || d.id)}</h1>
    <div class="meta-line"><span>${esc(d.model || "—")}</span><span>·</span><span>${esc(d.date || "—")}</span>
    <span>·</span><span>run <code>${esc(d.id)}</code></span>${chip(d.label || "REPLAY")}</div>`;
  $("#legend").innerHTML = `<span><i class="v-lab MEASURED" style="margin:0"></i>measured</span>
    <span><i class="v-lab ESTIMATE" style="margin:0"></i>estimate</span>
    <span><i class="v-lab SIMULATED" style="margin:0"></i>simulated</span>
    <span><i class="v-lab MOCK" style="margin:0"></i>mock</span>
    <span><i class="v-lab REPLAY" style="margin:0"></i>replay</span>`;
  const S = steps(X);
  $("#flow").innerHTML = S.map((s) => `
    <div class="step">
      <span class="node ${s.muted ? "muted" : ""}" aria-hidden="true">${s.node}</span>
      <button class="card" data-step="${s.id}" aria-haspopup="dialog">
        <span class="k">${esc(s.name)}</span>
        <div class="row"><span class="val ${s.id === "change" || s.id === "receipt" ? "peach" : ""}">${s.val}${s.labelDot}</span></div>
        <div class="sub">${s.sub}</div>
        <div class="open">Open the full breakdown →</div>
      </button>
    </div>`).join("");
  const byId = Object.fromEntries(S.map((s) => [s.id, s]));
  $("#flow").querySelectorAll("[data-step]").forEach((el) => {
    el.onclick = () => openSide(byId[el.dataset.step]);
  });
}

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
