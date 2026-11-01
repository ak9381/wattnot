// Launch steps for the v2 "Configure a new run" wizard: where it runs, datasets, limits and caps,
// approvals, eligibility, pre-launch checks, cost confirm and live progress. One topic per step.
// Talks to /api/launch on the local server; keys stay on the server and never reach the page.
import { loadConfig, saveConfig, lab } from "./elig.js";

const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const post = (p, b) => fetch(p, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(b) }).then(async (r) => ({ ok: r.ok, status: r.status, body: await r.json() }));
const q = (s, el = document) => el.querySelector(s);
const qa = (s, el = document) => [...el.querySelectorAll(s)];
export const CAP_NAMES = { baseten_round_usd: "Baseten, per round (hard stop)", modal_round_usd: "Modal, per round worst case", modal_stage_usd: "Modal, whole stage", modal_smoke_usd: "Modal GPU smoke", meta_hard_usd: "Meta API, hard stop", meta_soft_usd: "Meta API, warning" };

export const L = { opt: null, cfg: loadConfig(), elig: null, checks: null, est: null, filt: "all", job: null };
const DEF = { provider: "baseten", datasets: ["ours", "gsm8k", "humaneval"], p99_pct: 10, rounds: 3, open_pr: true, smoke: null };
for (const [k, v] of Object.entries(DEF)) L.cfg[k] ??= v;

export const info = (k, label) => `<button type="button" class="info" data-info="L:${k}" aria-label="What is ${esc(label)}?">i</button>`;

export const BANNER = `<div class="wn-twice glass" role="note"><b>Every run executes twice.</b> Once <b>without WattNot</b> (the baseline) and once <b>with WattNot</b> (every eligible feature on), on the same items, so the results sit side by side. Cost estimates cover both arms.</div>`;

// Drawer copy: what the field means, the options, the default, and what it changes in cost or eligibility.
export const INFO = {
  goal: ["What to save on", "The main thing WattNot should cut: energy, money, or water. It sets which saving the planner ranks first.", "Energy, money, or water and cooling.", "Energy.", "No direct cost. It changes which proposal wins when two settings trade off."],
  model: ["Model", "The AI model that answers. For the free replay it picks which recorded run is shown. For a real run the platform's served model is used (Spark Open on Baseten, Glimmer 30B on Modal).", "The models with recorded runs.", "Muse Glimmer 30B.", "Bigger models cost more per answer and use more energy; some features only exist on models you serve yourself."],
  traffic: ["Traffic", "The shape of the requests: short chat, long documents, agents or batch jobs. WattNot tunes differently for each.", "Chat, long context, agents, batch.", "Chat.", "Long context and agents use more tokens per item, so a real run costs more."],
  stack: ["Your stack", "The client library your app uses. WattNot sits in front of the model as an OpenAI-compatible proxy, so the change is one base_url line.", "openai-python, LangChain, LlamaIndex, plain HTTP.", "openai-python.", "No cost effect. Some framework-specific features (for example LangChain agent-waste checks) only apply to that framework."],
  care: ["How careful", "How much quality and speed WattNot may trade for savings. Careful keeps answers nearly identical; bold saves more.", "Careful, balanced, bold.", "Balanced: p99 may rise 10%, answers must agree 90%.", "Bolder settings save more but more changes get held for review."],
  name: ["Run name", "A label for this run so you can find it later in Runs, Reports and the receipts.", "Any text up to 80 characters.", "None: you must type one.", "No effect on cost or eligibility. It is written into the run folder and the receipt."],
  desc: ["Short description", "One or two lines on why you are running this, for whoever reviews it later.", "Free text, optional.", "Empty.", "No effect on cost or eligibility. Shown next to the run in Runs and on the approval card."],
  provider: ["Where it runs", "The platform that serves the model during both arms. It decides which WattNot features can act: a hosted API has no power meter or GPU knobs; your own vLLM server has all of them.", "Baseten (hosted Spark Open, pay per token) · Modal A100 (own vLLM, hackathon credits) · E2E H100 (own GPU) · Combined (Baseten + Modal).", "Baseten.", "Changes the feature list (see Eligibility), the energy label (ESTIMATE on hosted, MEASURED from NVML on a GPU), and the price: Baseten about $3–4 a round with an $8 hard stop; Modal about $3–3.6 a round, $10 worst case per round, $60 for the whole stage. E2E and Combined have no runner wired to the UI yet."],
  datasets: ["Datasets", "The question sets both arms answer. The same items go to both arms so the comparison is fair.", "ours (house questions), gsm8k (maths), humaneval (code), mmlu, truthfulqa, and anything you uploaded.", "ours, gsm8k, humaneval.", "More datasets means more items, so more tokens or GPU minutes. Accuracy is graded per dataset."],
  p99: ["p99 latency limit", "How much slower the slowest 1% of answers may get with WattNot on. If the with-WattNot arm is slower than this, the change is not applied.", "0–50 % over the without-WattNot arm.", "10 %.", "A tighter limit makes WattNot hold more changes for review. No direct cost effect."],
  rounds: ["Rounds", "How many times the full with/without pair runs. Each round lets WattNot propose one more change from the last round's results.", "1, 2 or 3.", "3.", "Cost scales with rounds: the estimate on the confirm step multiplies the per-round cost."],
  smoke: ["GPU smoke", "A tiny real run on the Modal A100 that only tests the parts never run on a GPU before (FP8 on A100, the batch sweep, the thinking-budget enforcer). It is not a full comparison.", "Off, or 1–3 items per dataset.", "Off.", "Worst case $0.92: the container is killed at 22 minutes. Hard cap $1.50, billed to Modal credits. Spark grading and the PR are off."],
  caps: ["Spend caps", "Hard limits the runners enforce on themselves. They stop and tear down when a cap is reached.", "Each cap can be lowered here but never raised past the approved value.", "Baseten $8 per round · Modal $10 per round worst case · Modal $60 whole stage · smoke $1.50 · Meta API $120 hard / $100 warning.", "Lower caps can stop a run early, which leaves the comparison incomplete for that round."],
  approvals: ["Approvals", "Every change WattNot wants to make is scored for risk. Low-risk changes apply after their checks pass. High-risk ones are held for a person.", "Fixed: high-risk changes are never auto-approved.", "Held for a reviewer.", "No cost effect. Held changes wait on the Approvals page with their evidence."],
  pr: ["Muse Code PR", "After a round, Muse Code writes the winning setting as a code change and opens a pull request so a person can review it.", "On or off. Only ever on the demo repo ak9381/wattnot-demo-deploy.", "On.", "A few cents of Meta API per round. Turning it off removes the PR feature from eligibility."],
  eligibility: ["Eligibility", "Which of the 110 WattNot features and which Meta models can actually work on this setup, each with a one-line reason. The with-WattNot arm turns on everything eligible.", "Read only: it follows from where it runs and the PR toggle.", "Depends on the provider.", "Ineligible features are greyed out on every page with the same reason."],
  checks: ["Pre-launch checks", "Free checks that run before anything is spent: keys present on the server, the model reachable, caps and ledger headroom, no recent billing refusals, servers, code tree, recorder.", "Pass, warn or fail, each with a reason and a fix.", "All required items must pass.", "No cost: none of these calls a paid model. Run stays disabled until every required item passes."],
  confirm: ["Confirm the cost", "The estimate for both arms of every round, the hard stop, and who is billed. Ticking the box approves spending up to the hard stop.", "Run (paid) or a MOCK rehearsal that spends nothing.", "Not approved.", "The confirm is tied to this exact configuration and expires after 10 minutes."],
};
export function infoHtml(k) {
  const v = INFO[k]; if (!v) return null;
  const [t, what, opts, def, impact] = v;
  return { title: t, html: `<h4>What it is</h4><p>${esc(what)}</p><h4>Options</h4><p>${esc(opts)}</p><h4>Default</h4><p>${esc(def)}</p><h4>Cost and eligibility</h4><p>${esc(impact)}</p>` };
}

export async function init() {
  if (!L.opt) {
    try { L.opt = await fetch("/api/launch/options").then((r) => r.json()); }
    catch { L.opt = null; }
  }
  return L.opt;
}
const save = () => { if (L.cfg.provider !== "modal_a100") L.cfg.smoke = null; saveConfig(L.cfg); L.checks = L.est = null; };
const needServer = `<p class="lock">This step needs the local WattNot server (<code>python -m wattnot.dash</code>).</p>`;

export function where(el) {
  if (!L.opt) { el.innerHTML = needServer; return; }
  el.innerHTML = `<div class="picks" role="radiogroup" aria-label="Where it runs">${Object.entries(L.opt.providers).map(([k, p]) => `<label class="pick"><input type="radio" name="lprov" value="${k}" ${L.cfg.provider === k ? "checked" : ""}>
      <span class="pick-c"><span class="pick-t"><b>${esc(p.label)}</b><small>${esc(p.spend)} · energy ${lab(p.energy_label)}</small>${p.wired ? "" : '<small class="ex">No runner wired to the UI yet: eligibility only.</small>'}</span></span></label>`).join("")}</div>`;
  qa("input[name=lprov]", el).forEach((r) => (r.onchange = () => { L.cfg.provider = r.value; L.cfg.model = L.opt.providers[r.value].default_model; save(); }));
}

export function datasets(el) {
  if (!L.opt) { el.innerHTML = ""; return; }
  el.innerHTML = `<fieldset class="lfs"><legend>Datasets ${info("datasets", "Datasets")}</legend><div class="lchecks">${L.opt.datasets.map((d) => `<label><input type="checkbox" value="${esc(d)}" ${L.cfg.datasets.includes(d) ? "checked" : ""}> ${esc(d)}</label>`).join("")}</div></fieldset>`;
  qa("input", el).forEach((i) => (i.onchange = () => { L.cfg.datasets = qa("input:checked", el).map((x) => x.value); save(); }));
}

export function limits(el) {
  if (!L.opt) { el.innerHTML = needServer; return; }
  const caps = L.cfg.caps || {};
  el.innerHTML = `<div class="lgrid">
    <div class="fld"><label for="l-p99">p99 latency limit<small>% over without-WattNot</small></label><input id="l-p99" type="number" min="0" max="50" step="1" value="${L.cfg.p99_pct}">${info("p99", "p99 latency limit")}</div>
    <div class="fld"><label for="l-rounds">Rounds<small>with/without pairs</small></label><select id="l-rounds">${[1, 2, 3].map((n) => `<option ${n === L.cfg.rounds ? "selected" : ""}>${n}</option>`).join("")}</select>${info("rounds", "Rounds")}</div>
    ${L.cfg.provider === "modal_a100" ? `<div class="fld"><label for="l-smoke">GPU smoke<small>items per dataset, 0 = off</small></label><input id="l-smoke" type="number" min="0" max="3" value="${L.cfg.smoke?.limit || 0}">${info("smoke", "GPU smoke")}</div>` : ""}
    </div>
    <fieldset class="lfs"><legend>Spend caps (can only be lowered) ${info("caps", "Spend caps")}</legend><div class="lcaps">${Object.entries(L.opt.caps).map(([k, v]) => `<label>${esc(CAP_NAMES[k] || k)} <span>$<input type="number" name="${k}" min="0" max="${v}" step="0.5" value="${caps[k] ?? v}"></span></label>`).join("")}</div></fieldset>`;
  const sync = () => {
    L.cfg.p99_pct = +q("#l-p99", el).value; L.cfg.rounds = +q("#l-rounds", el).value;
    const s = q("#l-smoke", el); L.cfg.smoke = s && +s.value > 0 ? { limit: Math.min(3, +s.value) } : null;
    L.cfg.caps = Object.fromEntries(qa(".lcaps input", el).map((i) => [i.name, Math.min(+i.value, L.opt.caps[i.name])]));
    save();
  };
  qa("input,select", el).forEach((i) => (i.onchange = sync));
}

export function approvals(el) {
  el.innerHTML = `<div class="fld"><label>Approvals${info("approvals", "Approvals")}</label><p class="lock">High-risk changes are <b>held for a reviewer</b> and never auto-approved. The reviewer sees the evidence on the <a href="approvals.html">Approvals</a> page.</p></div>
    <div class="fld"><label class="ltog"><input id="l-pr" type="checkbox" ${L.cfg.open_pr !== false ? "checked" : ""}> Muse Code opens the change as a PR on <code>ak9381/wattnot-demo-deploy</code> (demo repo only)</label>${info("pr", "Muse Code PR")}</div>`;
  q("#l-pr", el).onchange = (e) => { L.cfg.open_pr = e.target.checked; save(); };
}

export async function eligibility(el) {
  if (!L.opt) { el.innerHTML = needServer; return; }
  el.innerHTML = `<p class="lock">Checking…</p>`;
  const { body: e } = await post("/api/launch/eligibility", { config: L.cfg });
  L.elig = e; window.__wnElig = e; window.dispatchEvent(new CustomEvent("wn:eligibility", { detail: e }));
  const draw = () => {
    el.innerHTML = `<p>${esc(e.provider_label)}: <b>${e.counts.eligible}</b> of ${e.counts.total} features eligible, ${e.counts.ineligible} greyed out. Energy here is ${lab(e.energy_label)}. ${info("eligibility", "Eligibility")}</p>
      ${e.problems.length ? `<p class="lock"><span class="pill no">problem</span> ${e.problems.map(esc).join("; ")}</p>` : ""}
      <details class="lfold"><summary>Meta models (${e.models.filter((m) => m.eligible).length} of ${e.models.length} eligible)</summary><div class="lmodels">${e.models.map((m) => `<div class="mchip ${m.eligible ? "" : "wn-inel"}" data-model="${m.id}" title="${esc(m.reason)}"><span class="pill ${m.eligible ? "yes" : "no"}">${m.eligible ? "Eligible" : "Ineligible"}</span> ${m.label ? lab(m.label) : ""}<b>${esc(m.name)}</b> <small>${esc(m.reason)}</small></div>`).join("")}</div></details>
      <details class="lfold" open><summary>Features</summary><div class="lfilt" role="group" aria-label="Filter"><button type="button" class="btn sm" data-f="all" aria-pressed="${L.filt === "all"}">All</button><button type="button" class="btn sm" data-f="no" aria-pressed="${L.filt === "no"}">Greyed out only</button></div>
      <div class="ltab"><table><thead><tr><th>#</th><th>Feature</th><th>Status</th><th>Why</th></tr></thead><tbody>${e.features.filter((f) => L.filt === "all" || !f.eligible).map((f) => `<tr data-feature-row="${f.n}" class="${f.eligible ? "" : "wn-inel"}" title="${esc(f.reason)}"><td>${f.n}</td><td>${esc(f.name)}</td><td><span class="pill ${f.eligible ? "yes" : "no"}">${f.eligible ? "Eligible" : "Ineligible"}</span></td><td>${esc(f.reason)}</td></tr>`).join("")}</tbody></table></div></details>`;
    qa("[data-f]", el).forEach((b) => (b.onclick = () => { L.filt = b.dataset.f; draw(); }));
  };
  draw();
}

export async function checks(el) {
  if (!L.opt) { el.innerHTML = needServer; return; }
  el.innerHTML = `<p>Free checks only: nothing here calls a paid model. ${info("checks", "Pre-launch checks")} <button type="button" class="btn sm" id="l-recheck">Run checks again</button></p><ul class="lchk" aria-live="polite"><li><span class="st wait">…</span><span class="t">Running free checks</span></li></ul>`;
  q("#l-recheck", el).onclick = () => checks(el);
  const { body } = await post("/api/launch/checklist", { config: L.cfg });
  L.checks = body;
  q(".lchk", el).innerHTML = body.items.map((i) => `<li data-check="${i.id}"><span class="st ${i.state}">${i.state.toUpperCase()}</span><span class="t">${esc(i.title)}${i.required ? "" : " <small>(advisory)</small>"}</span><span class="r">${esc(i.reason)}</span>${i.fix ? `<span class="f">Fix: ${esc(i.fix)}</span>` : ""}</li>`).join("")
    + `<li class="lsum"><b>${body.ready ? "All required checks pass." : "Run stays disabled until every required item passes."}</b></li>`;
}

export async function confirm(el, { replayHtml = "", onReplay } = {}) {
  if (!L.opt) { el.innerHTML = BANNER + replayHtml + needServer; onReplay?.(); return; }
  el.innerHTML = `${BANNER}${replayHtml}<h3>Real run ${info("confirm", "Confirm the cost")}</h3><div class="lest"><p class="lock">Estimating…</p></div>
    <label class="ltog"><input id="l-ok" type="checkbox"> I approve spending up to the hard stop shown above</label>
    <div class="lact"><button type="button" class="btn pri" id="l-live" disabled>Run (paid)</button><button type="button" class="btn" id="l-mock">Run a MOCK rehearsal (no spend)</button><span id="lwhy" class="lwhy" role="status"></span></div>
    <div class="lprog" hidden><h3>Live progress <span class="ljl"></span></h3><p class="ljm"></p><p><a href="graph.html?live=1">Open the live graph</a> · <a href="runs.html">Runs</a> · <a href="approvals.html">Approvals</a></p><pre class="llog" tabindex="0" aria-label="Runner output"></pre></div>`;
  onReplay?.();
  if (!L.checks) { const { body } = await post("/api/launch/checklist", { config: L.cfg }); L.checks = body; }
  const { body: e } = await post("/api/launch/estimate", { config: L.cfg });
  L.est = e;
  q(".lest", el).innerHTML = e.available ? `<div class="lestg">
      <div>Estimated cost, both arms ${lab(e.label)}<b>$${e.total_usd[0].toFixed(2)}–$${e.total_usd[1].toFixed(2)}</b>${e.rounds} round(s), billed to ${esc(e.billed_to)}</div>
      <div>Hard stop<b>$${e.hard_stop_usd.toFixed(2)}</b>the runner stops itself here</div>
      <div>Meta API ${lab("ESTIMATE")}<b>$${e.meta_usd[0].toFixed(2)}–$${e.meta_usd[1].toFixed(2)}</b>inside the $120 / $100 Meta caps</div></div><p class="lock">${esc(e.basis)}</p>`
    : `<p class="lock"><span class="pill no">not available</span> ${esc(e.why || "")}</p>`;
  const gate = () => {
    const ready = L.checks?.ready, can = e.available, ok = q("#l-ok", el).checked;
    q("#l-live", el).disabled = !(ready && can && ok);
    q("#l-mock", el).disabled = !can;
    const fails = (L.checks?.items || []).filter((i) => i.required && i.state !== "pass").map((i) => i.title);
    q("#lwhy", el).textContent = !can ? "No runner for this setup yet." : !ready ? `Blocked until these pass: ${fails.join("; ")}.` : !ok ? "Tick the approval box to enable Run." : "Ready.";
  };
  q("#l-ok", el).onchange = gate; gate();
  const start = async (mode) => {
    const b = { config: { ...L.cfg, name: L.cfg.name, description: L.cfg.description }, mode };
    if (mode === "live") b.confirm_token = e.confirm_token;
    const r = await post("/api/launch/start", b);
    if (!r.ok) { q("#lwhy", el).textContent = r.body.error || `failed (${r.status})`; return; }
    follow(el, r.body);
  };
  q("#l-live", el).onclick = () => start("live");
  q("#l-mock", el).onclick = () => start("mock");
  const jobs = await fetch("/api/launch/jobs").then((r) => r.json()).catch(() => ({ jobs: [] }));
  const running = jobs.jobs.find((j) => j.state === "running"); if (running) follow(el, running);
}

function follow(el, job) {
  const p = q(".lprog", el); p.hidden = false; p.scrollIntoView({ behavior: "smooth", block: "start" });
  q(".ljl", el).innerHTML = lab(job.label);
  q(".ljm", el).textContent = `Job ${job.id} · ${job.mode === "live" ? "paid run" : "rehearsal, no spend"} · started ${new Date(job.started).toLocaleTimeString()}`;
  const log = q(".llog", el); log.textContent = "";
  const es = new EventSource(`/api/launch/jobs/${job.id}/stream`);
  const add = (t) => { log.textContent += t + "\n"; log.scrollTop = log.scrollHeight; };
  es.addEventListener("log", (ev) => add(JSON.parse(ev.data).line));
  es.addEventListener("event", (ev) => { const { line, label } = JSON.parse(ev.data); try { const x = JSON.parse(line); add(`[${label}] ${x.type}${x.data?.node ? " · " + x.data.node : ""}${x.data?.summary ? " · " + x.data.summary : ""}`); } catch { add(line); } });
  es.addEventListener("done", (ev) => { const j = JSON.parse(ev.data); add(`— ${j.state} (exit ${j.rc ?? 0})`); es.close(); });
}
