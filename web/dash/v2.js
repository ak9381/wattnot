import { esc, badge, num, stageState, ledgerRows, run2Rows, verifySummary } from "./v2lib.js";

const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const S = { opts: null, choice: {}, run: null, n: 0, playing: false, speed: 2, timer: null, story: null, verify: null, partners: null, lastActive: null };
const ICON = { room: "▦", baseline: "◔", gate: "✓", sweep: "⚡", topk: "◇", effort: "◎", waste: "♻", approve: "✋", keep: "↺", receipt: "✎", hosted: "☁" };

// ---------- cards (drill-down) ----------
function card(id, icon, title, sub, body, { open = false, cls = "" } = {}) {
  return `<div class="card glass ${cls} ${open ? "open" : ""}" data-card="${esc(id)}">
    <div class="hd" tabindex="0" role="button" aria-expanded="${open}"><span class="ic">${icon}</span><span class="t"><h3>${title}</h3>${sub ? `<small>${sub}</small>` : ""}</span><span class="chev">›</span></div>
    <div class="bd">${body}</div></div>`;
}
const raw = (title, obj) => card(`raw-${title}`, "{}", "Raw data", esc(title), `<pre>${esc(JSON.stringify(obj, null, 2))}</pre>`);
function setOpen(el, on) { el.classList.toggle("open", on); $(".hd", el)?.setAttribute("aria-expanded", on); }
document.addEventListener("click", (e) => {
  const hd = e.target.closest(".card>.hd");
  if (hd) { const c = hd.parentElement; setOpen(c, !c.classList.contains("open")); c.dataset.pinned = c.classList.contains("open") ? "1" : ""; return; }
  const ex = e.target.closest("[data-all]");
  if (ex) { const scope = ex.closest(".page"); $$(".card", scope).forEach((c) => setOpen(c, ex.dataset.all === "open")); }
  const nd = e.target.closest(".node[data-stage]");
  if (nd) { const c = $(`[data-card="stage-${nd.dataset.stage}"]`); if (c) { setOpen(c, true); c.scrollIntoView({ behavior: "smooth", block: "center" }); } }
  const inf = e.target.closest("[data-info]"); if (inf) openInfo(inf.dataset.info);
});
document.addEventListener("keydown", (e) => {
  if ((e.key === "Enter" || e.key === " ") && e.target.matches(".card>.hd")) { e.preventDefault(); e.target.click(); }
  if (e.key === "Escape") closeInfo();
});
const ctrl = () => `<div class="ctrl noprint"><button class="btn" data-all="open">Expand all</button><button class="btn" data-all="close">Collapse all</button><span style="color:var(--mu);font-size:13px">Click any card to open it. Each card opens a level deeper, down to the raw data.</span></div>`;

// ---------- tabs ----------
function show(p) {
  $$("#tabs button").forEach((b) => b.classList.toggle("on", b.dataset.p === p));
  $$(".page").forEach((s) => s.classList.toggle("on", s.id === p));
  history.replaceState(null, "", `#${p}`);
  if (p === "pReport") renderReport();
}
$$("#tabs button").forEach((b) => (b.onclick = () => show(b.dataset.p)));

// ---------- info side panel ----------
function openInfo(id) {
  const o = S.opts.options.find((x) => x.id === id) || LEARN_INFO[id]; if (!o) return;
  $("#sideBody").innerHTML = `<h2>${esc(o.label)}</h2>
    <h4>What it is</h4><p>${esc(o.help.what)}</p><h4>Why it matters here</h4><p>${esc(o.help.why)}</p><h4>Example</h4><p>${esc(o.help.example)}</p>
    ${o.flag ? `<h4>In the code</h4><p><code>${esc(o.flag)}</code> · ${o.applies === "replay" ? "changes this replay now" : "saved for a live run; the replay uses the recorded value"}</p>` : ""}`;
  $("#side").classList.add("on"); $("#side").setAttribute("aria-hidden", "false"); $("#scrim").hidden = false; $("#sideX").focus();
}
function closeInfo() { $("#side").classList.remove("on"); $("#side").setAttribute("aria-hidden", "true"); $("#scrim").hidden = true; }
$("#sideX").onclick = closeInfo; $("#scrim").onclick = closeInfo;
const LEARN_INFO = {};

// ---------- overview ----------
function renderHome() {
  const led = S.story?.ledger || {};
  $("#pHome").innerHTML = `
  <div class="intro"><div><h1>WattNot makes AI servers use less power, and shows its work.</h1>
  <p>It measures a GPU, tries a safer setting, checks the answers did not get worse, asks a person, applies it, and signs a receipt. Every one of those steps is on this page. Nothing is hidden.</p></div>
  <button class="btn pri noprint" id="goCfg">Configure a run →</button></div>
  <div class="kpis">
    <div class="kpi glass"><div class="v cy">21%</div><div class="c">less energy per request, Muse Glimmer 30B at a 400 W cap ${badge("MEASURED")}</div></div>
    <div class="kpi glass"><div class="v tl">+3.7%</div><div class="c">slowest 1% got slower, inside the 10% limit ${badge("MEASURED")}</div></div>
    <div class="kpi glass"><div class="v pk">67%</div><div class="c">fewer agent tokens when a harness's stop rule misses ${badge("MEASURED")}</div></div>
    <div class="kpi glass"><div class="v am">$${num(led.total_usd)}</div><div class="c">Meta API spend recorded in this bundle, ${num(led.calls, 0)} calls ${badge("MEASURED")}</div></div>
  </div>
  ${ctrl()}
  ${card("h-what", "?", "New here? Start with this", "What problem WattNot solves, in three sentences", `
    <p>AI models run on GPUs that draw up to 700 watts each, often more than the work needs. Lowering the limit a little can save a lot of energy with almost no slowdown, but nobody wants to risk worse answers.</p>
    <p>WattNot tests a lower setting on real prompts, measures power from the card itself, and keeps the change only if speed and answer quality stay inside limits you set. A person approves risky changes, and any change can be undone in one click.</p>
    ${card("h-what2", "→", "Go one level deeper: what is measured, and how", "", `<dl class="kv"><dt>Power</dt><dd>Read from the card 10 times a second with NVIDIA's NVML counter ${badge("MEASURED")}</dd><dt>Speed</dt><dd>p99: the time 99 of 100 requests beat ${badge("MEASURED")}</dd><dt>Quality</dt><dd>Answers compared with the stock setting on a fixed test set</dd><dt>Cost and water</dt><dd>Energy times a published price or water factor ${badge("ESTIMATE")}</dd><dt>Room heat</dt><dd>Drawn from the measured power change ${badge("SIMULATED")}</dd></dl>`)}`, { open: true })}
  ${card("h-steps", "1→8", "What happens in a run", "Eight steps, each one visible while it runs", `<ol>${(S.pipeline || []).map((p) => `<li><b>${esc(p.title)}</b>: ${esc(p.desc)}</li>`).join("")}</ol>
    <p>Open the <a href="#pRun" data-go="pRun">Run</a> tab to watch one. The active step opens by itself and folds away when the run moves on.</p>`)}
  ${card("h-labels", "◈", "Where every number comes from", "Each figure carries one of these tags", `<dl class="kv">
    <dt>${badge("MEASURED")}</dt><dd>Read from a recorded file or a live counter.</dd><dt>${badge("ESTIMATE")}</dt><dd>Computed from a measurement with a stated formula, for example energy × electricity price.</dd>
    <dt>${badge("SIMULATED")}</dt><dd>From a model of the room or system, not the real thing.</dd><dt>${badge("MOCK")}</dt><dd>A stand-in that behaves like the real part, for example applying a cap without touching a GPU.</dd>
    <dt>${badge("REPLAY")}</dt><dd>A recorded run played back. Nothing is running now.</dd><dt>${badge("ADVICE")}</dt><dd>A model's suggestion. It is never applied without the gates and approval.</dd></dl>`)}
  ${card("h-cost", "$", "What this cost to build and test", "Every paid call is counted", `<dl class="kv"><dt>Meta API (Muse Spark)</dt><dd>$${num(led.total_usd)} over ${num(led.calls, 0)} calls; code cap $${num(led.cap_usd)} ${badge("MEASURED")}</dd><dt>This page</dt><dd>$0. Replay only. No GPU, no model call.</dd></dl>${partnersHtml()}`)}`;
  $("#goCfg").onclick = () => show("pCfg");
  $$("[data-go]").forEach((a) => (a.onclick = (e) => { e.preventDefault(); show(a.dataset.go); }));
}
function partnersHtml() {
  const ps = S.partners?.partners || []; if (!ps.length) return "";
  return `<div class="lvl">Partners and their calls</div><dl class="kv">${ps.map((p) => `<dt>${esc(p.name)}</dt><dd>${esc(p.what)}${p.calls != null ? ` · ${num(p.calls, 0)} calls` : ""}${p.cost?.value != null ? ` · ${esc(p.cost.unit === "INR" ? "₹" : "$")}${num(p.cost.value)} ${badge(p.cost.label)}` : ""}</dd>`).join("")}</dl>`;
}

// ---------- configure: one question per screen ----------
// Every answer maps onto an EXISTING option key (see configure.py
// STEPPER_SAVE / STEPPER_TRAFFIC / STEPPER_CARE), so the replay path needs
// no change. The stepper never invents a new knob.
const ESSENTIAL_IDS = ["model", "workload", "goal", "gpu", "room_size"];
const GOAL_PRESETS = { save: { p99_slack_pct: 15, min_quality_pct: 94 }, balanced: { p99_slack_pct: 10, min_quality_pct: 95 }, fast: { p99_slack_pct: 5, min_quality_pct: 97 } };
const SAVE_MAP = { power: "save", cost: "save", water: "save", balanced: "balanced" };
const TRAFFIC_MAP = { chat: "coding", batch: "moe", agents: "classes" };
const CARE_MAP = { gentle: { p99_slack_pct: 5, min_quality_pct: 97 }, balanced: { p99_slack_pct: 10, min_quality_pct: 95 }, bold: { p99_slack_pct: 15, min_quality_pct: 94 } };
const SVG = {
  bolt: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M13 2 4 14h6l-1 8 9-12h-6z"/></svg>',
  coin: '<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="9"/><path d="M12 7v10M9 9.5c0-1 1.3-1.8 3-1.8s3 .8 3 1.8-1 1.6-3 2.2-3 1.2-3 2.3 1.3 1.8 3 1.8 3-.8 3-1.8"/></svg>',
  drop: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3c3.5 4.5 6 7.6 6 11a6 6 0 0 1-12 0c0-3.4 2.5-6.5 6-11z"/></svg>',
  scale: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 4v16M5 7l7-3 7 3M5 7l-2.5 6a3.5 3.5 0 0 0 7 0L7 7M19 7l-2.5 6a3.5 3.5 0 0 0 7 0L21 7M8 20h8"/></svg>',
  chip: '<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="7" y="7" width="10" height="10" rx="2"/><path d="M10 2v4M14 2v4M10 18v4M14 18v4M2 10h4M2 14h4M18 10h4M18 14h4"/></svg>',
  chat: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 5h16v11H9l-5 4z"/></svg>',
  batch: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 8l8-4 8 4-8 4zM4 12l8 4 8-4M4 16l8 4 8-4"/></svg>',
  agent: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 12a8 8 0 0 1 14-5l3 3M20 12a8 8 0 0 1-14 5l-3-3M17 4v4h-4M7 20v-4h4"/></svg>',
  shield: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3l7 3v5c0 5-3.5 8-7 10-3.5-2-7-5-7-10V6z"/></svg>',
  play: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M7 4l13 8-13 8z"/></svg>',
  base: '<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="8"/><path d="M12 4v16M4 12h16"/></svg>',
  both: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M8 4H4v16h4M16 4h4v16h-4M8 4h8v16H8z"/></svg>',
};
const SAVE_CARDS = [
  { v: "power", t: "Power", d: "Use less electricity on your own GPU.", ex: "Example: a lower watt cap on your card.", icon: "bolt" },
  { v: "cost", t: "Cost", d: "Spend less per answer, on your card or a host.", ex: "Example: a cheaper setting that still passes.", icon: "coin" },
  { v: "water", t: "Water", d: "Use less cooling water at the data center.", ex: "Example: less heat means less water to cool it.", icon: "drop" },
  { v: "balanced", t: "Balanced", d: "Save what you can without pushing it.", ex: "Example: the default limits.", icon: "scale" },
];
const TRAFFIC_CARDS = [
  { v: "chat", t: "Chat", d: "People waiting for replies.", ex: "Example: a support bot answering live.", icon: "chat" },
  { v: "batch", t: "Batch", d: "Big jobs that can wait.", ex: "Example: overnight scoring of a backlog.", icon: "batch" },
  { v: "agents", t: "Agents", d: "AI calling tools in loops.", ex: "Example: an agent retrying calls until done.", icon: "agent" },
];
const CARE_CARDS = [
  { v: "gentle", t: "Gentle", d: "Stays well inside the limits; keeps less.", ex: "Slowest 1% may rise 5%, answers must agree 97%.", icon: "shield" },
  { v: "balanced", t: "Balanced", d: "The default limits.", ex: "Slowest 1% may rise 10%, answers must agree 95%.", icon: "shield" },
  { v: "bold", t: "Bold", d: "Pushes the limits to save the most.", ex: "Slowest 1% may rise 15%, answers must agree 94%.", icon: "shield" },
];
const COMPARE_CARDS = [
  { v: "with", t: "Run with WattNot", d: "Apply the saving and show the result.", ex: "The tuned run.", icon: "play" },
  { v: "without", t: "Run without (baseline)", d: "Stock settings, no changes applied.", ex: "The before picture.", icon: "base" },
  { v: "both", t: "Compare both", d: "Show the stock and tuned runs side by side.", ex: "The default: before and after.", icon: "both" },
];
const MODEL_BLURB = {
  glimmer: "30B dense model · runs on one H100",
  llama: "8B dense model · runs on one H100",
  qwen3: "30B mixture-of-experts · runs on one H100",
  granite: "3B mixture-of-experts · runs on one H100",
  spark_open: "Open model · runs hosted on Baseten",
};
const STEP_Q = ["What do you want to save on?", "Which AI are you running?", "What kind of traffic?", "How careful should we be?", "How do you want to run it?"];
const CONN_ITEMS = [
  { id: "nvidia", t: "GPU telemetry", d: "NVIDIA NVML power counter" },
  { id: "e2e", t: "E2E Networks H100", d: "the rented GPU" },
  { id: "meta", t: "Meta Muse Spark planner", d: "proposes the next setting" },
  { id: "baseten", t: "Baseten", d: "hosted model runs" },
  { id: "huggingface", t: "Hugging Face", d: "model weights" },
  { id: "sigstore", t: "Sigstore", d: "receipt signing" },
];
function connStatus(id) {
  const p = S.partners?.partners?.find((x) => x.id === id);
  return p && (p.status === "used" || p.status === "free") ? "ok" : "off";
}
function renderConn() {
  const el = $("#connList"); if (!el) return;
  el.innerHTML = CONN_ITEMS.map((c) => {
    const ok = connStatus(c.id) === "ok";
    return `<li class="${ok ? "ok" : "off"}"><span class="dot" aria-hidden="true"></span><span class="ct"><b>${esc(c.t)}</b><small>${ok ? esc(c.d) : "not set up"}</small></span></li>`;
  }).join("");
}
function pickCard(name, c, checked, extra) {
  return `<label class="pick"><input type="radio" name="${name}" value="${esc(JSON.stringify(c.v))}" ${checked ? "checked" : ""}>
    <span class="pick-c"><span class="pick-ic" aria-hidden="true">${SVG[c.icon] || ""}</span>
    <span class="pick-t"><b>${esc(c.t)}</b><small>${esc(c.d)}</small><small class="ex">${esc(c.ex)}</small>${extra || ""}</span></span></label>`;
}
function renderCfg() {
  const O = S.opts;
  for (const o of O.options) S.choice[o.id] ??= o.default;
  S.choice.compare ??= "both"; S.step ??= 1;
  const byId = Object.fromEntries(O.options.map((o) => [o.id, o]));
  const adv = O.options.filter((o) => !ESSENTIAL_IDS.includes(o.id)).map((o) => fld(o)).join("");
  $("#pCfg").innerHTML = `<div class="intro"><div><h1>Configure a new run</h1><p>One question per screen, five screens. Everything else keeps a safe default. Use Tab and the arrow keys to move between cards, Enter to continue. Live GPU runs are locked on this screen; it only replays recorded runs, so it costs nothing.</p></div></div>
    <div class="cfg-step"><div class="glass step-main">
      <div class="step-prog"><span id="stepCount" role="status"></span><ol id="stepDots" aria-label="Progress"></ol></div>
      <div id="stepBody" aria-live="polite"></div>
      <div class="step-nav"><button class="btn" id="stepBack">Back</button><button class="btn pri" id="stepNext">Next</button></div>
    </div>
    <div class="step-side"><div class="glass conn"><h3>Already connected</h3><p>Wired by default, read from the partners list.</p><ul id="connList"></ul></div>
      <div class="grp glass"><h3>What will happen</h3><div id="cfgSum"></div>
      <p class="lock">Live mode needs the owner's okay and the command line. It is never started from here.</p></div></div></div>
    <details class="adv glass" id="advDis"><summary id="advSum" aria-expanded="false"><span class="adv-t">Advanced settings</span>
      <span class="adv-line">Safe defaults are set; open this to change limits, gates and budgets.</span><span class="chev" aria-hidden="true">›</span></summary>
      <div class="adv-defaults" id="advDef"><span class="lvl">Defaults in use</span><div id="advDefList"></div></div>
      <div class="adv-fields">${adv}</div></details>`;
  function fld(o) {
    return `<div class="fld"><label for="f-${o.id}">${esc(o.label)}<small>${o.applies === "replay" ? "changes this replay" : "used by a live run"}</small></label>
    <select id="f-${o.id}" data-k="${o.id}">${o.choices.map((c) => `<option value="${esc(JSON.stringify(c.v))}" ${JSON.stringify(c.v) === JSON.stringify(S.choice[o.id]) ? "selected" : ""} ${o.id === "mode" && c.v === "live" ? "disabled" : ""}>${esc(c.label)}</option>`).join("")}</select>
    <button class="info" data-info="${o.id}" aria-label="What is ${esc(o.label)}?">i</button></div>`;
  }
  $$("#pCfg select").forEach((s) => (s.onchange = () => { S.choice[s.dataset.k] = JSON.parse(s.value); sumCfg(); syncAdv(); }));
  $("#advDis").addEventListener("toggle", syncAdv);
  $("#stepBack").onclick = () => { if (S.step > 1) { S.step--; renderStep(); } };
  $("#stepNext").onclick = () => { if (S.step < 5) { S.step++; renderStep(); } };
  document.onkeydown = (e) => {
    if (!$("#pCfg").classList.contains("on")) return;
    if (e.key === "ArrowRight" && S.step < 5 && e.target.tagName !== "SELECT" && e.target.tagName !== "INPUT") { S.step++; renderStep(); }
    if (e.key === "ArrowLeft" && S.step > 1 && e.target.tagName !== "SELECT" && e.target.tagName !== "INPUT") { S.step--; renderStep(); }
  };
  renderStep(); renderConn(); sumCfg(); syncAdv();
}
function workloadLabel(id) {
  const o = S.opts.options.find((x) => x.id === "workload");
  return o?.choices.find((c) => c.v === id)?.label || id;
}
function renderStep() {
  const n = S.step, body = $("#stepBody");
  $("#stepCount").textContent = `Step ${n} of 5`;
  $("#stepDots").innerHTML = STEP_Q.map((q, i) => `<li class="${i + 1 === n ? "now" : i + 1 < n ? "done" : ""}" ${i + 1 === n ? 'aria-current="step"' : ""} title="${esc(q)}"><span></span></li>`).join("");
  $("#stepBack").disabled = n === 1;
  $("#stepNext").hidden = n === 5;
  if (n === 1) body.innerHTML = `<h2>${esc(STEP_Q[0])}</h2><div class="picks" role="radiogroup" aria-label="${esc(STEP_Q[0])}">
    ${SAVE_CARDS.map((c) => pickCard("save", c, S.choice.save_target === c.v)).join("")}</div>`;
  else if (n === 2) {
    const o = S.opts.options.find((x) => x.id === "model");
    body.innerHTML = `<h2>${esc(STEP_Q[1])}</h2><p class="step-note">A model is the AI program that answers your users' prompts.</p>
      <div class="picks" role="radiogroup" aria-label="${esc(STEP_Q[1])}">${o.choices.map((c) => pickCard("model",
        { v: c.v, t: c.label, d: MODEL_BLURB[c.v] || c.label, icon: "chip" }, S.choice.model === c.v)).join("")}</div>`;
  } else if (n === 3) body.innerHTML = `<h2>${esc(STEP_Q[2])}</h2><div class="picks" role="radiogroup" aria-label="${esc(STEP_Q[2])}">
    ${TRAFFIC_CARDS.map((c) => pickCard("traffic", c, S.choice.traffic === c.v, `<small class="ex">Runs as: ${esc(workloadLabel(TRAFFIC_MAP[c.v]))}</small>`)).join("")}</div>`;
  else if (n === 4) body.innerHTML = `<h2>${esc(STEP_Q[3])}</h2><div class="picks" role="radiogroup" aria-label="${esc(STEP_Q[3])}">
    ${CARE_CARDS.map((c) => pickCard("care", c, S.choice.care === c.v)).join("")}</div>`;
  else body.innerHTML = `<h2>${esc(STEP_Q[4])}</h2><div class="picks" role="radiogroup" aria-label="${esc(STEP_Q[4])}">
    ${COMPARE_CARDS.map((c) => pickCard("compare", c, (S.choice.compare || "both") === c.v)).join("")}</div>
    <div class="fld namerow"><label for="f-name">Run name<small>free text</small></label><input type="text" id="f-name" value="${esc(S.choice.name || "My first replay")}"></div>
    <button class="btn pri big" id="startRun">Start replay</button>
    <p class="lock">Replay only, no GPU. Live mode needs the owner's okay and the command line.</p>`;
  $$('input[name="save"]', body).forEach((r) => (r.onchange = () => { const v = JSON.parse(r.value); S.choice.goal = SAVE_MAP[v]; S.choice.save_target = v; sumCfg(); }));
  $$('input[name="model"]', body).forEach((r) => (r.onchange = () => { S.choice.model = JSON.parse(r.value); sumCfg(); }));
  $$('input[name="traffic"]', body).forEach((r) => (r.onchange = () => { const v = JSON.parse(r.value); S.choice.workload = TRAFFIC_MAP[v]; S.choice.traffic = v; sumCfg(); }));
  $$('input[name="care"]', body).forEach((r) => (r.onchange = () => { const v = JSON.parse(r.value); Object.assign(S.choice, CARE_MAP[v]); S.choice.care = v; sumCfg(); syncAdv(); }));
  $$('input[name="compare"]', body).forEach((r) => (r.onchange = () => { S.choice.compare = JSON.parse(r.value); S.view = S.choice.compare; }));
  const fn = $("#f-name"); if (fn) fn.oninput = (e) => (S.choice.name = e.target.value);
  const st = $("#startRun"); if (st) st.onclick = startRun;
}
function advChoiceLabel(o) {
  const hit = o.choices.find((c) => JSON.stringify(c.v) === JSON.stringify(S.choice[o.id]));
  return hit ? hit.label : JSON.stringify(S.choice[o.id] ?? o.default);
}
function syncAdv() {
  const d = $("#advDis"); if (!d) return;
  $("#advSum")?.setAttribute("aria-expanded", String(d.open));
  const list = $("#advDefList"); if (!list) return;
  list.innerHTML = S.opts.options.filter((o) => !ESSENTIAL_IDS.includes(o.id))
    .map((o) => `<span class="chip">${esc(o.label)}: <b style="color:#fff">${esc(advChoiceLabel(o))}</b></span>`).join("");
}
function sumCfg() {
  const c = S.choice, m = S.opts.options.find((o) => o.id === "model").choices.find((x) => x.v === c.model);
  const g = S.opts.options.find((o) => o.id === "goal")?.choices.find((x) => x.v === c.goal)?.label;
  $("#cfgSum").innerHTML = `<dl class="kv" style="grid-template-columns:110px 1fr"><dt>Model</dt><dd>${esc(m?.label)}</dd>${g ? `<dt>Goal</dt><dd>${esc(g)}</dd>` : ""}<dt>Speed limit</dt><dd>p99 may rise ${esc(c.p99_slack_pct)}%</dd><dt>Quality bar</dt><dd>${esc(c.min_quality_pct)}% agreement</dd><dt>Approval</dt><dd>${esc(S.opts.options.find((o) => o.id === "approval").choices.find((x) => x.v === c.approval)?.label)}</dd><dt>Cost</dt><dd>$0 ${badge("REPLAY")}</dd></dl>`;
}
async function startRun() {
  const btn = $("#startRun"); btn.disabled = true; btn.textContent = "Loading the recorded run…";
  try {
    const r = await fetch("/api/dash/v2/run", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(S.choice) });
    if (!r.ok) throw new Error((await r.json()).detail || r.status);
    S.run = await r.json(); S.n = 0; renderRun(); show("pRun"); play();
  } catch (e) { alert(`Could not start: ${e.message}`); }
  btn.disabled = false; btn.textContent = "Start replay";
}

// ---------- run: Without WattNot vs With WattNot ----------
function run2BlockHtml(view, withToggle) {
  view = view || S.view || "both";
  const R = S.run?.results, rows = run2Rows(S.run2, R?.price, R?.water);
  if (!rows.length) return `<h3>Without WattNot vs With WattNot</h3><p>Headline numbers are still loading.</p>`;
  const show = (side) => view === "both" || view === (side === "w" ? "with" : "without");
  const cell = (r, side) => (show(side) ? `<div class="cell ${r.win === side ? "win" : ""}"><span class="v num">${esc(side === "w" ? r.wattnot : r.stock)}</span>${side === "w" && r.delta && view === "both" ? `<span class="d">${esc(r.delta)}</span>` : ""}</div>` : `<div></div>`);
  return `<div class="r2head"><div><h3>Without WattNot vs With WattNot</h3><p>Llama 3.1 8B at a 350 W cap, RUN2 ${badge("REPLAY")}. Energy, p99 and quality are measured; cost and water are estimates from measured joules.</p></div>
    ${withToggle ? `<div class="seg" role="group" aria-label="Which run to show">${[["without", "Without"], ["both", "Compare"], ["with", "With"]].map(([v, t]) => `<button class="btn ${view === v ? "pri" : ""}" data-view="${v}" aria-pressed="${view === v}">${t}</button>`).join("")}</div>` : ""}</div>
  ${rows.map((r) => `<div class="rows">${cell(r, "s")}<div class="dimlab">${esc(r.dim)}<br>${r.tag ? badge(r.tag) : ""}</div>${cell(r, "w")}</div>${r.note ? `<p class="r2note">${esc(r.note)}</p>` : ""}`).join("")}`;
}
function renderRun2() {
  const el = $("#run2block"); if (!el) return;
  el.innerHTML = run2BlockHtml(S.view, true);
  $$("#run2block [data-view]").forEach((b) => (b.onclick = () => { S.view = b.dataset.view; renderRun2(); }));
}

// ---------- run ----------
function stageBody(p, evs) {
  const mine = evs.filter((f) => (f.event || f).data?.node === p.id).map((f) => f.event || f);
  const fin = Object.fromEntries(mine.filter((e) => e.type === "step.finished").map((e) => [e.data.sub, e]));
  const say = mine.filter((e) => e.data?.narration).map((e) => e.data.narration).slice(-1)[0];
  const subs = p.subs.map((s) => { const f = fin[s.id]; const st = f ? (f.data.status === "failed" ? "fail" : "done") : mine.some((e) => e.data.sub === s.id) ? "run" : "todo";
    return `<div class="sub"><span class="st ${st}" style="margin-top:6px"></span><div style="flex:1"><b>${esc(s.title)}</b>${s.partner ? ` <span class="chip" style="padding:1px 7px">${esc(s.partner)}</span>` : ""}<p>${esc(f?.data.summary || s.desc)}</p>
      <div class="io">in: ${esc(s.inputs)} → out: ${esc(s.outputs)} · <code>${esc(s.code || "")}</code></div>
      ${(f?.data.numbers || []).map((x) => `<span class="chip" style="margin:4px 4px 0 0">${esc(x.label)} <b style="color:#fff">${num(x.v)} ${esc(x.unit)}</b>${badge(x.tag)}</span>`).join("")}</div></div>`; }).join("");
  return `<p>${esc(p.desc)}</p>${say ? `<div class="say">${esc(say.text)} <small style="color:var(--mu)">(${esc(say.source)})</small></div>` : ""}
    <div class="lvl">Steps inside</div>${subs}${mine.length ? raw(`${p.id}: ${mine.length} events`, mine) : ""}`;
}
function renderRun() {
  if (!S.run) { $("#pRun").innerHTML = `<div class="intro"><div><h1>Run</h1><p>No run yet. <a href="#pCfg" id="toCfg">Configure one</a>, or start the default replay.</p></div><button class="btn pri" id="defRun">Start default replay</button></div>`; $("#defRun").onclick = startRun; $("#toCfg").onclick = (e) => { e.preventDefault(); show("pCfg"); }; return; }
  const R = S.run;
  S.view ??= S.choice.compare || "both";
  $("#pRun").innerHTML = `<div class="glass run2block" id="run2block" aria-live="polite"></div>
  <div class="intro"><div><h1>${esc(S.choice.name || "Run")} <span style="font-size:14px">${badge("REPLAY")}</span></h1><p>${esc(R.results.model)} · ${esc(R.results.workload)} · run id <code>${esc(R.run_id)}</code>. A recorded run, re-checked against your limits. Nothing is running on a GPU now.</p></div>
    <div class="noprint" style="display:flex;gap:8px"><button class="btn" id="pp">Pause</button><button class="btn" id="rs">Restart</button><select id="spd" style="width:auto"><option value="1">1×</option><option value="2" selected>2×</option><option value="4">4×</option><option value="20">20×</option></select></div></div>
    <div class="glass" style="padding:10px 16px"><div style="display:flex;justify-content:space-between;font-size:13px"><span id="progT"></span><span id="progN" style="color:var(--mu)"></span></div><div class="prog"><i id="progB"></i></div>
    <div class="rail" id="rail">${R.pipeline.map((p, i) => `${i ? '<span class="edge"></span>' : ""}<button class="node" data-stage="${p.id}"><b>${esc(p.title)}</b><span id="ns-${p.id}">waiting</span></button>`).join("")}</div></div>
    ${ctrl()}<div id="stages">${R.pipeline.map((p) => card(`stage-${p.id}`, ICON[p.id] || "•", esc(p.title), `<span id="sm-${p.id}">${esc(p.desc)}</span>`, `<div id="sb-${p.id}"></div>`)).join("")}</div>
    ${card("planner", "✦", "The planner: Muse Spark's prompts and replies", "Every round it proposed, why, whether it passed, and its token use", plannerBody())}
    ${card("ledger", "≡", "Every backend action, in order", "Telemetry reads, gates, decisions, apply and undo. The audit trail.", `<div class="log" id="logBox"></div>`)}
    ${card("results", "▣", "Results table", "Every setting tried, with its numbers", resultsBody())}`;
  $("#pp").onclick = () => (S.playing ? pause() : play()); $("#rs").onclick = () => { S.n = 0; $$("#stages .card").forEach((c) => { setOpen(c, false); c.dataset.pinned = ""; }); tick(true); play(); };
  $("#spd").onchange = (e) => (S.speed = +e.target.value);
  S.lastActive = null; renderRun2(); tick(true);
}
function plannerBody() {
  const a = S.story?.agent; if (!a) return "<p>No planner rounds in this bundle.</p>";
  return `<p>${esc(a.note)} ${badge("ADVICE")} ${badge("SIMULATED")}</p>${a.rounds.map((r) => card(`pr-${r.round}`, r.passed ? "✓" : "✗", `Round ${r.round}: try ${esc(r.knob)} = ${esc(r.value)}`, `${r.passed ? "passed the gates" : "failed a gate"} · ${num(r.saving_pct, 1)}% · ${num(r.tokens.input + r.tokens.output, 0)} tokens`,
    `<div class="lvl">The model's reason</div><div class="say">${esc(r.reason)}</div><div class="lvl">Tokens</div><p>in ${r.tokens.input}, out ${r.tokens.output} (of which reasoning ${r.tokens.reasoning}) ${badge("MEASURED")}</p>${raw(`round ${r.round}`, r)}`)).join("")}`;
}
function resultsBody() {
  const lv = S.run.results.levers[0]; if (!lv?.tried) return raw("results", S.run.results);
  return `<p>${esc(lv.src || "")} ${badge("MEASURED")}</p><table style="width:100%;border-collapse:collapse;font-size:13.5px"><tr style="color:var(--mu);text-align:left"><th>Cap</th><th>mWh / request</th><th>Saved</th><th>p99</th><th>p99 ok</th><th>Quality</th></tr>
  ${lv.tried.map((t) => `<tr style="border-top:1px solid var(--ln)"><td>${t.cap} W</td><td>${num(t.wh * 1000)}</td><td>${num(t.saved_pct, 1)}%</td><td>${num(t.p99)} s</td><td>${t.p99_ok ? '<span class="ok">yes</span>' : '<span class="no">no</span>'}</td><td>${esc(t.quality)}</td></tr>`).join("")}</table>${raw("results", S.run.results)}`;
}
function tick(force) {
  const R = S.run; if (!R || !$("#rail")) return;
  const { st, active } = stageState(R.pipeline, R.events, S.n);
  const done = Object.values(st).filter((x) => x === "done").length;
  $("#progB").style.width = `${(S.n / R.events.length) * 100}%`;
  $("#progT").textContent = active ? `Now: ${R.pipeline.find((p) => p.id === active).title}` : S.n >= R.events.length ? "Finished. Open the Signed report tab for the result." : "Ready";
  $("#progN").textContent = `${done} of ${R.pipeline.length} steps done · event ${S.n} of ${R.events.length}`;
  for (const p of R.pipeline) {
    const n = $(`.node[data-stage="${p.id}"]`); n.className = `node ${st[p.id]}`; $(`#ns-${p.id}`).textContent = { todo: "waiting", run: "running", done: "done", fail: "check" }[st[p.id]];
    const c = $(`[data-card="stage-${p.id}"]`);
    if (st[p.id] !== "todo" || force) $(`#sb-${p.id}`).innerHTML = stageBody(p, R.events.slice(0, S.n));
    $(".st", c)?.remove(); $(".hd", c).insertAdjacentHTML("afterbegin", `<span class="st ${st[p.id]}"></span>`);
  }
  if (active !== S.lastActive) {
    if (S.lastActive) { const prev = $(`[data-card="stage-${S.lastActive}"]`); if (prev && !prev.dataset.pinned) setOpen(prev, false); }
    if (active) setOpen($(`[data-card="stage-${active}"]`), true);
    S.lastActive = active;
  }
  $("#logBox").innerHTML = ledgerRows(R.events.slice(0, S.n)).map((r) => `<div><span class="k">${esc(r.node)}</span><span class="st ${r.ok ? "done" : "fail"}" style="margin-top:6px"></span><span style="flex:1">${esc(r.kind)}: ${esc(r.text)}</span>${badge(r.label)}</div>`).join("") || "<p>Nothing yet.</p>";
}
function play() {
  pause(); S.playing = true; $("#pp") && ($("#pp").textContent = "Pause");
  const R = S.run;
  const step = () => {
    if (S.n >= R.events.length) { S.playing = false; tick(); $("#pp") && ($("#pp").textContent = "Play"); return; }
    S.n++; tick();
    const dt = (R.events[S.n]?.t_ms ?? 0) - (R.events[S.n - 1]?.t_ms ?? 0);
    S.timer = setTimeout(step, Math.max(30, Math.min(1500, dt)) / S.speed);
  };
  step();
}
function pause() { clearTimeout(S.timer); S.playing = false; $("#pp") && ($("#pp").textContent = "Play"); }

// ---------- signed report ----------
// ---------- signed report: a mind map ----------
// Static. No scroll animation, no counting, no scrambling: numbers render
// as plain text, and cards open with a simple expand toggle.
function stableStringify(v) {
  if (v === null || typeof v !== "object") return JSON.stringify(v);
  if (Array.isArray(v)) return `[${v.map(stableStringify).join(",")}]`;
  return `{${Object.keys(v).sort().map((k) => `${JSON.stringify(k)}:${stableStringify(v[k])}`).join(",")}}`;
}
// Only headline report data goes into the fingerprint: sorted keys, and no
// run id, timestamps, or randomness. Same data, same fingerprint, every load.
function reportFingerprintData() {
  const r = S.run2 || {};
  return {
    cap_w: r.cap_w, stock_w: r.stock_w, model: r.model,
    j_per_req_stock: r.j_per_req_stock, j_per_req_cap: r.j_per_req_cap,
    saved_pct: r.saved_pct, p99_s_stock: r.p99_s_stock, p99_s_cap: r.p99_s_cap,
    p99_change_pct: r.p99_change_pct, quality_stock_pct: r.quality_stock_pct,
    quality_cap_pct: r.quality_cap_pct, label: r.label, source: r.source,
    rejected_325w_p99_change_pct: 10.9,
    rejected_granite_agreement_pct: 94.53,
  };
}
const MAP_ICON = {
  bolt: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M13 2 4 14h6l-1 8 9-12h-6z"/></svg>',
  coin: '<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="9"/><path d="M12 7v10M9 9.5c0-1 1.3-1.8 3-1.8s3 .8 3 1.8-1 1.6-3 2.2-3 1.2-3 2.3 1.3 1.8 3 1.8 3-.8 3-1.8"/></svg>',
  drop: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3c3.5 4.5 6 7.6 6 11a6 6 0 0 1-12 0c0-3.4 2.5-6.5 6-11z"/></svg>',
  clock: '<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 3"/></svg>',
  check: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 12.5l5 5L20 6.5"/></svg>',
  shield: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3l7 3v5c0 5-3.5 8-7 10-3.5-2-7-5-7-10V6z"/></svg>',
  x: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 6l12 12M18 6L6 18"/></svg>',
  doc: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 3h8l4 4v14H6zM14 3v4h4"/></svg>',
};
function mapTopics() {
  const R = S.run?.results || {}, rows = run2Rows(S.run2, R.price, R.water);
  const by = Object.fromEntries(rows.map((x) => [x.id, x]));
  const leaf = (id, icon, label, text) => ({ id, icon, label, text });
  const row = (r) => (r ? { stock: r.stock, wattnot: r.wattnot, delta: r.delta, tag: r.tag } : null);
  const e = row(by.energy), c = row(by.cost), w = row(by.water), p = row(by.p99), q = row(by.quality);
  return [
    { id: "power", color: "#2ee6f0", name: "Power", sub: "Energy per request",
      kids: [leaf("pw-s", "bolt", "Without WattNot", e ? `700 W stock · <b>${esc(e.stock)}</b>/req ${badge(e.tag)}` : "700 W stock · not measured"),
             leaf("pw-w", "bolt", "With WattNot", e ? `350 W cap · <b>${esc(e.wattnot)}</b>/req · <b>${esc(e.delta)}</b> ${badge(e.tag)}` : "350 W cap · not measured")],
      detail: `<b>What went right.</b> RUN2 on Llama 3.1 8B at a 350 W cap: <b>−27.1%</b> joules per request ${badge("MEASURED")} (data/h100-run2/vm/steps/cap350.json). Earlier sweep on Muse Glimmer 30B at 400 W: <b>−21.0%</b> with p99 +3.7% ${badge("MEASURED")} (H100 run sweep.csv, NVML). Qwen3 adaptive top-k saved 7.0% and passed all three gates at 95.7% agreement ${badge("MEASURED")}.` },
    { id: "cost", color: "#ffc857", name: "Cost", sub: "Electricity per 1M requests",
      kids: [leaf("c-s", "coin", "Without WattNot", c ? `Stock: <b>${esc(c.stock)}</b> ${badge(c.tag)}` : "Stock cost: not measured"),
             leaf("c-w", "coin", "With WattNot", c ? `Capped: <b>${esc(c.wattnot)}</b> (<b>${esc(c.delta)}</b>) ${badge(c.tag)}` : "Capped cost: not measured")],
      detail: `Measured joules times <b>$0.1387</b>/kWh, the US commercial electricity price (EIA). The meter reading is ${badge("MEASURED")}; the bill math is ${badge("ESTIMATE")}.` },
    { id: "water", color: "#4aa8ff", name: "Water", sub: "Cooling water per 1M requests",
      kids: [leaf("w-s", "drop", "Without WattNot", w ? `Stock: <b>${esc(w.stock)}</b> ${badge(w.tag)}` : "Stock water: not measured"),
             leaf("w-w", "drop", "With WattNot", w ? `Capped: <b>${esc(w.wattnot)}</b> (<b>${esc(w.delta)}</b>) ${badge(w.tag)}` : "Capped water: not measured")],
      detail: `Measured joules times <b>0.36 L</b>/kWh, a US fleet-average site water factor (LBNL). On-site cooling water only; making the electricity is not counted. ${badge("ESTIMATE")}.` },
    { id: "latency", color: "#a78bfa", name: "Latency", sub: "Slowest 1% of requests (p99)",
      kids: [leaf("l-s", "clock", "Without WattNot", p ? `Stock p99 <b>${esc(p.stock)}</b> ${badge(p.tag)}` : "Stock p99: not measured"),
             leaf("l-w", "clock", "With WattNot", p ? `Capped p99 <b>${esc(p.wattnot)}</b> (<b>${esc(p.delta)}</b>) ${badge(p.tag)}` : "Capped p99: not measured")],
      detail: `p99 is the time 99 of 100 requests beat. The 350 W cap added <b>+7.7%</b> ${badge("MEASURED")}, inside the <b>10%</b> gate, so it passed. A slower cap would not.` },
    { id: "quality", color: "#2ff5b4", name: "Quality", sub: "Answer quality, stock vs capped",
      kids: [leaf("q-s", "check", "Without WattNot", q ? `Stock: <b>${esc(q.stock)}</b> pass ${badge(q.tag)}` : "Stock quality: not measured"),
             leaf("q-w", "check", "With WattNot", q ? `Capped: <b>${esc(q.wattnot)}</b> · <b>${esc(q.delta)}</b> ${badge(q.tag)}` : "Capped quality: not measured")],
      detail: `<b>666</b> answers checked: <b>93.7%</b> pass at stock and <b>93.7%</b> capped ${badge("MEASURED")}. The same pass rate means quality held.` },
    { id: "safety", color: "#ff8a5c", name: "Safety/Approve", sub: "A person stays in charge",
      kids: [leaf("s-s", "shield", "Without WattNot", `No review: a change would just apply`),
             leaf("s-w", "shield", "With WattNot", `A person approves high-risk changes ${badge("MOCK")}`)],
      detail: `<b>Why choose WattNot.</b> It is a second layer, not a claim to beat everything. Every change is measured, gated, approved by a person, logged, undoable, and signed. If a cap fails a gate, it is rejected, as below.` },
    { id: "rejected", color: "#ff6b6b", name: "Rejected options", sub: "What we refused, and why",
      kids: [leaf("r-s", "x", "Without WattNot", `No gate: 325 W would just run slower at p99 <b>+10.9%</b>`),
             leaf("r-w", "x", "With WattNot", `Gate rejects 325 W: p99 <b>+10.9%</b> is over the <b>10%</b> limit ${badge("MEASURED")}`)],
      detail: `<b>325 W rejected:</b> p99 rose <b>+10.9%</b>, over the 10% limit ${badge("MEASURED")} (data/h100-run2/vm/steps/cap325.json). <b>Granite adaptive top-k rejected:</b> 94.53% agreement, under the 95% gate ${badge("MEASURED")} (story bundle adaptive stats). Rejections are the system working.` },
    { id: "proof", color: "#e6edf5", name: "Proof", sub: "Fingerprint, verifier, raw files",
      kids: [leaf("f-s", "doc", "Without WattNot", `A chart you cannot re-check`),
             leaf("f-w", "doc", "With WattNot", `Fingerprint plus verifier plus raw files ${badge("SAMPLE")}`)],
      detail: `The fingerprint covers the headline numbers only, with sorted keys and no timestamps. The verifier re-checks sample receipts ${badge("SAMPLE")}. Raw data: data/h100-run2/vm/steps/cap350.json, data/h100-run2/vm/steps/cap325.json, data/h100-run2/vm/adaptive_topk/, verifier/samples/.` },
  ];
}
let mapWired = false;
function drawWires() {
  const wrap = $("#mapWrap"), svg = $("#mapWires");
  if (!wrap || !svg) return;
  const wr = wrap.getBoundingClientRect();
  if (!wr.width) return;
  const pt = (el, edge) => { const b = el.getBoundingClientRect(); return edge === "top" ? [b.left + b.width / 2 - wr.left, b.top - wr.top] : [b.left + b.width / 2 - wr.left, b.bottom - wr.top]; };
  const curve = (a, b) => { const my = (a[1] + b[1]) / 2; return `M ${a[0].toFixed(1)} ${a[1].toFixed(1)} C ${a[0].toFixed(1)} ${my.toFixed(1)}, ${b[0].toFixed(1)} ${my.toFixed(1)}, ${b[0].toFixed(1)} ${b[1].toFixed(1)}`; };
  const hub = $("#mapHub"), d = [];
  if (hub) $$(".topic", wrap).forEach((t) => {
    const head = $(".topic-head", t);
    if (!head) return;
    d.push(curve(pt(hub, "bot"), pt(head, "top")));
    if (t.dataset.open === "1") $$(".leaf", t).forEach((l) => d.push(curve(pt(head, "bot"), pt(l, "top"))));
  });
  svg.setAttribute("viewBox", `0 0 ${wr.width} ${wr.height}`);
  svg.innerHTML = d.map((x) => `<path d="${x}"/>`).join("");
}
function wireMap() {
  drawWires();
  setTimeout(drawWires, 0);
  if (!mapWired) { mapWired = true; window.addEventListener("resize", drawWires); }
}
async function renderReport() {
  if (!S.run) { $("#pReport").innerHTML = `<p>Loading the default replay…</p>`; await startQuiet(); }
  S.verify ??= await fetch("/api/dash/v2/verify").then((r) => r.json()).catch(() => null);
  const topics = mapTopics();
  const left = topics.slice(0, 4), right = topics.slice(4);
  const sha = await sha256(stableStringify(reportFingerprintData()));
  const vs = verifySummary(S.verify);
  const r2 = S.run2 || {};
  const topicHtml = (t) => `<div class="topic" data-topic="${t.id}" data-open="0">
    <button class="topic-head" aria-expanded="false"><span class="tdot" style="--tc:${t.color}" aria-hidden="true"></span><span class="tt"><b>${esc(t.name)}</b><small>${esc(t.sub)}</small></span><span class="chev" aria-hidden="true">›</span></button>
    <div class="topic-kids" hidden>
      ${t.kids.map((k) => `<div class="leaf"><span class="leaf-ic" aria-hidden="true">${MAP_ICON[k.icon] || ""}</span><div><b>${esc(k.label)}</b><span>${k.text}</span></div></div>`).join("")}
      <div class="topic-detail">${t.detail}</div>
    </div></div>`;
  $("#pReport").innerHTML = `<div class="ctrl noprint"><button class="btn pri" onclick="print()">Export PDF / PNG (print)</button><span style="color:var(--mu);font-size:13px">Topics start closed so the map stays readable. Open any circle for its two paths and the detail.</span><span style="flex:1"></span><button class="btn" data-map="open">Expand all</button><button class="btn" data-map="close">Collapse all</button></div>
  <div class="map-wrap glass" id="mapWrap"><svg class="map-wires" id="mapWires" aria-hidden="true"></svg>
    <div class="map-hub" id="mapHub" data-open="0">
      <p class="kicker">WattNot signed report · ${badge("REPLAY")} ${badge("MEASURED")}</p>
      <h1>${esc(r2.model || "Llama 3.1 8B Instruct")} · ${r2.cap_w || 350} W cap</h1>
      <p class="q">Can we cut GPU power without hurting quality?</p>
      <button class="btn sm" id="hubToggle" aria-expanded="false">Show the question</button>
      <div class="hub-detail" hidden><p>We capped an H100 at <b>350 W</b> instead of 700 W on Llama 3.1 8B and measured energy per request, the slowest 1% of requests, and answer quality. Energy fell <b>−27.1%</b> ${badge("MEASURED")}, the slowest 1% rose <b>+7.7%</b> ${badge("MEASURED")} inside the 10% limit, and quality stayed at <b>93.7%</b> ${badge("MEASURED")}.</p></div>
    </div>
    <div class="map-cols"><div class="map-col">${left.map(topicHtml).join("")}</div><div class="map-col">${right.map(topicHtml).join("")}</div></div>
  </div>
  <div class="who-banner">Who should pick which?</div>
  <div class="who"><div class="glass"><h3 style="color:var(--pk)">Pick the baseline if</h3><p>Every millisecond of the slowest requests matters more than the power bill, or you cannot run a quality check on your own prompts yet.</p></div>
  <div class="glass"><h3 style="color:var(--cy)">Pick WattNot if</h3><p>You pay for power and cooling, can accept the p99 change shown above, and want each change approved, logged, undoable and signed.</p></div></div>
  <div class="sig glass"><div><div class="lvl">This report's fingerprint</div><code class="fp" id="fpCode">sha256 ${esc(sha)}</code><p class="fp-note">Same data, same fingerprint, every load. Sorted keys, no timestamps.</p></div>
   <div><div class="lvl">Verifier check on sample receipts ${badge("SAMPLE")}</div>${vs.map((v) => `<div>${esc(v.sample)}: hash ${v.hash ? '<span class="ok">✓</span>' : '<span class="no">✗</span>'} · signature ${v.sig ? '<span class="ok">✓ verified</span>' : '<span class="no">✗ rejected</span>'} · grade ${esc(v.grade)}</div>`).join("")}</div></div>
  <p style="font-size:12px;color:var(--mu);margin-top:10px">${esc(S.verify?.note || "")} Cost uses ${esc(S.run.results.price?.name || "")}; water uses ${esc(S.run.results.water?.name || "")}. Source: ${esc(r2.source || "data/h100-run2/vm/steps/cap350.json")}.</p>`;
  const setTopic = (t, on) => {
    t.dataset.open = on ? "1" : "0";
    $(".topic-head", t).setAttribute("aria-expanded", String(on));
    $(".topic-kids", t).hidden = !on;
  };
  $$("#pReport .topic-head").forEach((b) => (b.onclick = () => {
    const t = b.closest(".topic"); setTopic(t, t.dataset.open !== "1"); wireMap();
  }));
  $$("#pReport [data-map]").forEach((b) => (b.onclick = () => {
    const on = b.dataset.map === "open";
    $$("#pReport .topic").forEach((t) => setTopic(t, on));
    const hub = $("#mapHub"), hd = $(".hub-detail", hub);
    hub.dataset.open = on ? "1" : "0"; hd.hidden = !on;
    $("#hubToggle").textContent = on ? "Hide the question" : "Show the question";
    $("#hubToggle").setAttribute("aria-expanded", String(on));
    wireMap();
  }));
  $("#hubToggle").onclick = () => {
    const hub = $("#mapHub"), on = hub.dataset.open !== "1", hd = $(".hub-detail", hub);
    hub.dataset.open = on ? "1" : "0"; hd.hidden = !on;
    $("#hubToggle").textContent = on ? "Hide the question" : "Show the question";
    $("#hubToggle").setAttribute("aria-expanded", String(on));
  };
  wireMap();
}
async function sha256(s) { const b = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(s)); return [...new Uint8Array(b)].map((x) => x.toString(16).padStart(2, "0")).join(""); }
async function startQuiet() { const r = await fetch("/api/dash/v2/run", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(S.choice) }); S.run = await r.json(); S.n = S.run.events.length; renderRun(); }

// ---------- how it works ----------
const FEATURES = [
  ["cap", "⚡", "Power caps", "Lower the GPU's watt limit and keep the lowest one that stays fast enough.", "An H100 ships at 700 W. Many workloads do not need all of it. WattNot tries 650, 600 … 400 W, measures energy per request and the p99 at each, and keeps the lowest cap that passes. On Muse Glimmer 30B, 400 W used 21% less energy per request, and p99 rose 3.7%.", "MEASURED"],
  ["topk", "◇", "Adaptive top-k", "Let each word use fewer of the model's 'experts' when it does not need them.", "Mixture-of-Experts models send every token to a fixed number of experts. Often the first one or two carry almost all the weight. Adaptive top-k drops the rest. It saved 6 to 7% energy on Granite and Qwen3-30B in RUN1. At the safer setting that passes the 95% quality bar, it saves about 1.5%.", "MEASURED"],
  ["waste", "♻", "Agent waste saver", "Stop AI agents that keep calling the model after the job is done.", "Agents loop: they retry broken tools, repeat searches, or never print the 'done' word the harness waits for. The saver watches for repetition and no new information and stops the loop. On 12 failure cases with real Muse Spark calls, it cut tokens 67% and calls 57%. On normal tasks it is a tie.", "MEASURED"],
  ["approve", "✋", "Approve and undo", "Nothing risky touches a GPU without a person saying yes.", "Each proposed change gets a risk level. High-risk changes wait for a click; no answer in 10 minutes counts as no. Every decision records who and when. Any applied change can be rolled back in one click, and the rollback is logged too.", "MOCK"],
  ["spark", "☁", "Muse Spark Open vs Baseten", "Run the open model on our own H100, or pay a host per answer.", "Same model, two ways. On our own GPU we can measure and cap power. On Baseten we get a bill and an estimated energy figure. A small live check on Baseten passed 6 of 6 prompts at $0.79 per 1,000 answers. The own-GPU side runs in the next long H100 session.", "MEASURED"],
  ["partners", "⬡", "Partners", "Who does what, and what each call cost.", "Meta: Muse Spark plans the next try, SAM 3.1 finds the racks. E2E Networks: the rented H100. NVIDIA NVML: power readings and caps. Baseten: hosted open-weights model. Sigstore: receipt signatures. Every call is counted with its cost.", null],
  ["room", "▦", "SAM 3.1 room scan", "Turn a room photo into one shape per rack, so savings have a place.", "SAM 3.1 outlines each rack in a photo or thermal still. WattNot marks the rack it can control and redraws its heat after the change. The outlines come from the scan; the heat change is simulated from the measured power drop.", "SIMULATED"],
  ["eu", "€", "EU efficiency report", "One click to the yearly numbers EU data centers must file.", "EU Delegated Regulation 2024/1364 asks data centers of 500 kW or more for yearly energy, PUE, water and waste-heat figures. WattNot fills the energy and saving rows from its receipts and leaves the rest clearly marked for the operator.", "ESTIMATE"],
];
function renderLearn() {
  $("#pLearn").innerHTML = `<div class="intro"><div><h1>How it works, one feature at a time</h1><p>Each card is a feature we built. The first line says what it does. Open it for how, the numbers, and where they come from.</p></div></div>${ctrl()}
  <div class="feat">${FEATURES.map(([id, ic, t, s, body, tag]) => `<div>${card(`f-${id}`, ic, t, s, `<p>${esc(body)} ${badge(tag)}</p>`)}</div>`).join("")}</div>`;
}

// ---------- boot ----------
(async () => {
  const [opts, story, partners, run2] = await Promise.all([fetch("/api/dash/v2/options").then((r) => r.json()), fetch("/api/dash/story").then((r) => r.json()).catch(() => null), fetch("/api/dash/partners").then((r) => r.json()).catch(() => null), fetch("/api/dash/v2/run2").then((r) => r.json()).catch(() => null)]);
  S.opts = opts; S.story = story; S.partners = partners; S.run2 = run2; S.pipeline = story?.config?.pipeline || [];
  renderCfg(); renderHome(); renderRun(); renderLearn();
  const h = location.hash.slice(1); if (h && $(`#${h}`)) show(h);
  if (new URLSearchParams(location.search).get("autorun")) { await startRun(); }
})();
