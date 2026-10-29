import { esc, badge, num, cnum, legendHtml, stageState, ledgerRows, run2Rows, verifySummary, phaseLabel } from "./v2lib.js";
import { openThermal } from "./thermal.js";
import { renderProof, wireIllus } from "./proof.js";
import { init as initPanCanvas } from "./pancanvas.js";
import { overviewHtml, openPowerGraph, wireR7, metaInRun, branchPopup, cmpHeadHtml, openWhere, methodPopup, branchInfo, savedPct, childSaved, CHILDREN, CHILD_ONLY, DERIVED_HINT, BUNDLED_ID, compareState, bundledRecord, emptyStateHtml, bundledBanner, TREE, treeChildren, treeFind, treeValue, contextSaver } from "./v2r7.js";
import { compareRows } from "./metatools.js";

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
  window.scrollTo(0, 0);
  if (p === "pReport") renderReport();
  if (p === "pProof") renderProof($("#pProof"));
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
function closeInfo() {
  $("#side").classList.remove("on"); $("#side").setAttribute("aria-hidden", "true");
  document.body.classList.remove("drawer-open");
  $("#scrim").hidden = true;
  setTimeout(() => S.radPan?.fit(), 310);
}
// Open arbitrary detail HTML in the shared side panel (used by the Compare tree
// info buttons: detail opens here, never in a pop-up).
function openSidePanel(title, html) {
  $("#sideBody").innerHTML = `<h2>${esc(title)}</h2>${html}`;
  $("#side").classList.add("on"); $("#side").setAttribute("aria-hidden", "false");
  document.body.classList.add("drawer-open");
  $("#scrim").hidden = false; $("#sideX").focus();
  setTimeout(() => S.radPan?.fit(), 310);
}
$("#sideX").onclick = closeInfo; $("#scrim").onclick = closeInfo;
const LEARN_INFO = {};

// ---------- overview ----------
// Round 7 overview: sidebar, KPI cards, trend, donut, gauge and table, all from run data.
function renderHome() {
  wireR7();
  $("#pHome").innerHTML = overviewHtml(S);
  $$("#pHome [data-go]").forEach((a) => (a.onclick = (e) => { e.preventDefault(); show(a.dataset.go); }));
  $$("#pHome [data-pg]").forEach((b) => (b.onclick = () => openPowerGraph(S.radRecs?.[S.runs?.runs?.[0]?.id])));
}
// The round 6 overview, kept for reference; no longer mounted.
function renderHomeClassic() {
  const led = S.story?.ledger || {};
  $("#pHome").innerHTML = `
  <div class="intro"><div><h1>WattNot makes AI servers use less power, and shows its work.</h1>
  <p>It measures a GPU, tries a safer setting, checks the answers did not get worse, asks a person, applies it, and signs a receipt. Every one of those steps is on this page. Nothing is hidden.</p></div>
  <button class="btn pri noprint" id="goCfg">Configure a run →</button></div>
  <div class="kpis">
    <div class="kpi glass kpi-headline" data-explain="dash.kpi.jpc" data-explain-value="−27.1%" data-explain-label="MEASURED" data-explain-source="receipts/run2/rcpt_20261027_run2_cap350.json joules.change_pct.j_per_good_answer"><div class="v">${cnum("−27.1%", "MEASURED")}</div><div class="c"><b>joules per correct answer</b>, Llama 3.1 8B on one H100 at a 350 W cap (17.03 → 12.42 J). Fixed-check pass rate held at 93.7% ${badge("MEASURED")}. Cost and water follow from these joules ${badge("ESTIMATE")}</div></div>
    <div class="kpi glass"><div class="v">${cnum("+7.7%", "MEASURED")}</div><div class="c">slowest 1% (p99) got slower at 350 W, inside the 10% limit; 325 W was rejected at +10.9% ${badge("MEASURED")}</div></div>
    <div class="kpi glass"><div class="v">${cnum("67%", "MEASURED")}</div><div class="c">fewer agent tokens when a harness's stop rule misses ${badge("MEASURED")}</div></div>
    <div class="kpi glass"><div class="v">${cnum(`$${num(led.total_usd)}`, "MEASURED")}</div><div class="c">Meta API spend recorded in this bundle, ${num(led.calls, 0)} calls ${badge("MEASURED")}</div></div>
  </div>
  ${legendHtml()}
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
  return `<div class="lvl">Partners and their calls</div><dl class="kv">${ps.map((p) => `<dt><span class="ptag">${esc(p.name)}</span></dt><dd>${esc(p.what)}${p.calls != null ? ` · ${num(p.calls, 0)} calls` : ""}${p.cost?.value != null ? ` · ${cnum(`$${num(p.cost.value)}`, p.cost.label)}` : ""}</dd>`).join("")}</dl>`;
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
  { v: "balanced", t: "Balanced", d: "The default limits.", ex: "Slowest 1% may rise 10%. Power cap: fixed-check pass rate may drop at most 1 point. Routing: 95% agreement.", icon: "shield" },
  { v: "bold", t: "Bold", d: "Pushes the limits to save the most.", ex: "Slowest 1% may rise 15%, answers must agree 94%.", icon: "shield" },
];
const MODEL_BLURB = {
  glimmer: "30B dense model · runs on one H100",
  llama: "8B dense model · runs on one H100",
  qwen3: "30B mixture-of-experts · runs on one H100",
  granite: "3B mixture-of-experts · runs on one H100",
  spark_open: "Open model · runs hosted on Baseten",
};
const STACK_CARDS = [
  { v: "openai-python", t: "openai-python", d: "Standard OpenAI Python SDK client", snippet: 'OpenAI(base_url="http://localhost:8000/v1")', icon: "chip", ex: 'client = OpenAI(base_url="http://localhost:8000/v1")' },
  { v: "langchain", t: "LangChain", d: "LangChain ChatOpenAI LLM module", snippet: 'ChatOpenAI(base_url="http://localhost:8000/v1")', icon: "agent", ex: 'llm = ChatOpenAI(base_url="http://localhost:8000/v1")' },
  { v: "llamaindex", t: "LlamaIndex", d: "LlamaIndex OpenAILike integration", snippet: 'OpenAILike(api_base="http://localhost:8000/v1")', icon: "batch", ex: 'llm = OpenAILike(api_base="http://localhost:8000/v1")' },
  { v: "plain", t: "plain API", d: "Direct HTTP completions endpoint", snippet: 'curl http://localhost:8000/v1/chat/completions', icon: "chat", ex: 'curl http://localhost:8000/v1/chat/completions' },
];
const STEP_Q = ["What do you want to save on?", "Which AI are you running?", "What kind of traffic?", "Your stack", "How careful should we be?", "How do you want to run it?"];
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
  S.step ??= 1;
  const byId = Object.fromEntries(O.options.map((o) => [o.id, o]));
  const adv = O.options.filter((o) => !ESSENTIAL_IDS.includes(o.id)).map((o) => fld(o)).join("");
  $("#pCfg").innerHTML = `<div class="intro"><div><h1>Configure a new run</h1><p>Six short questions. Edit any answer on the right. Replays recorded runs only: no GPU, no cost.</p></div></div>
    <div class="cfg-step bento"><div class="glass tile span4 lift step-main">
      <div class="step-prog"><span id="stepCount" role="status"></span><ol id="stepDots" aria-label="Progress"></ol></div>
      <div id="stepBody" aria-live="polite"></div>
      <div class="step-nav"><button class="btn" id="stepBack">Back</button><button class="btn pri" id="stepNext">Next</button></div>
    </div>
    <div class="step-side span2"><div class="glass tile low picked" id="pickSum" aria-live="polite"><h3>Your choices</h3><ol class="picked-list" id="pickList"></ol></div>
      <div class="glass tile low conn"><h3>Already connected</h3><p>Wired by default, read from the partners list.</p><ul id="connList"></ul></div>
      <div class="grp glass tile low"><h3>What will happen</h3><div id="cfgSum"></div>${legendHtml()}
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
  $("#stepNext").onclick = () => { if (S.step < 6) { markDone(S.step); S.step++; renderStep(); } };
  document.onkeydown = (e) => {
    if (!$("#pCfg").classList.contains("on")) return;
    if (e.key === "ArrowRight" && S.step < 6 && e.target.tagName !== "SELECT" && e.target.tagName !== "INPUT") { markDone(S.step); S.step++; renderStep(); }
    if (e.key === "ArrowLeft" && S.step > 1 && e.target.tagName !== "SELECT" && e.target.tagName !== "INPUT") { S.step--; renderStep(); }
  };
  renderStep(); renderConn(); sumCfg(); syncAdv();
}
function workloadLabel(id) {
  const o = S.opts.options.find((x) => x.id === "workload");
  return o?.choices.find((c) => c.v === id)?.label || id;
}
// Right-hand summary: one entry per page, plain words, Edit jumps back.
function markDone(n) { (S.answered ??= {})[n] = true; renderPicked(); }
function cardLabel(cards, v) { return cards.find((c) => c.v === v)?.t; }
function pickedAnswer(n) {
  const c = S.choice;
  if (n === 1) return cardLabel(SAVE_CARDS, c.save_target);
  if (n === 2) return S.opts.options.find((o) => o.id === "model")?.choices.find((x) => x.v === c.model)?.label;
  if (n === 3) return cardLabel(TRAFFIC_CARDS, c.traffic);
  if (n === 4) return STACK_CARDS.find((s) => s.v === (c.framework || "openai-python"))?.t || "openai-python";
  if (n === 5) { const t = cardLabel(CARE_CARDS, c.care); return t ? `${t} (slowest 1% may rise ${c.p99_slack_pct}%, answers must agree ${c.min_quality_pct}%)` : null; }
  return S.answered?.[6] ? `Replay: ${c.name || "My first replay"}` : null;
}
function renderPicked() {
  const el = $("#pickList"); if (!el) return;
  el.innerHTML = STEP_Q.map((q, i) => {
    const n = i + 1, a = S.answered?.[n] ? pickedAnswer(n) : null, now = n === S.step;
    return `<li class="${a ? "done" : "pending"}${now ? " now" : ""}"><span class="pq">${n}. ${esc(q)}</span>
      <span class="pa">${a ? esc(a) : now ? "Answering now" : "Pending"}</span>
      ${a && !now ? `<button class="btn sm edit" data-edit="${n}" aria-label="Edit: ${esc(q)}">Edit</button>` : ""}</li>`;
  }).join("");
  $$("#pickList [data-edit]").forEach((b) => (b.onclick = () => { S.step = +b.dataset.edit; renderStep(); $("#stepBody")?.querySelector("input")?.focus(); }));
}
function renderStep() {
  const n = S.step, body = $("#stepBody");
  $("#stepCount").textContent = `Step ${n} of 6`;
  $("#stepDots").innerHTML = STEP_Q.map((q, i) => `<li class="${i + 1 === n ? "now" : i + 1 < n ? "done" : ""}" ${i + 1 === n ? 'aria-current="step"' : ""} title="${esc(q)}"><span></span></li>`).join("");
  $("#stepBack").disabled = n === 1;
  $("#stepNext").hidden = n === 6;
  if (n === 1) body.innerHTML = `<h2>${esc(STEP_Q[0])}</h2><div class="picks" role="radiogroup" aria-label="${esc(STEP_Q[0])}">
    ${SAVE_CARDS.map((c) => pickCard("save", c, S.choice.save_target === c.v)).join("")}</div>`;
  else if (n === 2) {
    const o = S.opts.options.find((x) => x.id === "model");
    body.innerHTML = `<h2>${esc(STEP_Q[1])}</h2><p class="step-note">A model is the AI program that answers your users' prompts.</p>
      <div class="picks" role="radiogroup" aria-label="${esc(STEP_Q[1])}">${o.choices.map((c) => pickCard("model",
        { v: c.v, t: c.label, d: MODEL_BLURB[c.v] || c.label, icon: "chip" }, S.choice.model === c.v)).join("")}</div>`;
  } else if (n === 3) body.innerHTML = `<h2>${esc(STEP_Q[2])}</h2><div class="picks" role="radiogroup" aria-label="${esc(STEP_Q[2])}">
    ${TRAFFIC_CARDS.map((c) => pickCard("traffic", c, S.choice.traffic === c.v, `<small class="ex">Runs as: ${esc(workloadLabel(TRAFFIC_MAP[c.v]))}</small>`)).join("")}</div>`;
  else if (n === 4) {
    const curFw = S.choice.framework || "openai-python";
    const curCard = STACK_CARDS.find((c) => c.v === curFw) || STACK_CARDS[0];
    body.innerHTML = `<h2>${esc(STEP_Q[3])}</h2><p class="step-note">WattNot is a drop-in proxy. Connect your framework by updating the <code>base_url</code> to point to WattNot.</p>
      <div class="picks" role="radiogroup" aria-label="${esc(STEP_Q[3])}">${STACK_CARDS.map((c) => pickCard("stack", c, curFw === c.v)).join("")}</div>
      <div class="stack-box glass" style="margin-top:14px;padding:12px 14px;border-radius:14px;background:rgba(31,26,47,.75);border:1px solid rgba(220,214,228,.14)">
        <div style="font-size:12px;color:var(--mu);margin-bottom:6px">One-line change for <b>${esc(curCard.t)}</b>:</div>
        <pre style="margin:0;font-family:var(--mo);color:#DCD6E4;font-size:13px;padding:8px 12px;background:rgba(0,0,0,.35);border-radius:8px;overflow-x:auto"><code>${esc(curCard.snippet)}</code></pre>
      </div>`;
  } else if (n === 5) body.innerHTML = `<h2>${esc(STEP_Q[4])}</h2><div class="picks" role="radiogroup" aria-label="${esc(STEP_Q[4])}">
    ${CARE_CARDS.map((c) => pickCard("care", c, S.choice.care === c.v)).join("")}</div>`;
  else body.innerHTML = `<h2>${esc(STEP_Q[5])}</h2>
    <p class="lock">This run measures both: baseline first, then WattNot.</p>
    <div class="where3"><h3>Where it runs</h3><p class="lock">All three are compared in this run.</p><ul>${["Your GPU · Muse Glimmer 30B (Meta) · power caps + FP8 · thinking budget and prefix caching pending", "Baseten hosted", "Modal hosted"].map((t) => `<li><span class="chk" aria-hidden="true">✓</span>${esc(t)}</li>`).join("")}</ul></div>
    <div class="fld namerow"><label for="f-name">Run name<small>free text</small></label><input type="text" id="f-name" value="${esc(S.choice.name || "My first replay")}"></div>
    <button class="btn pri big" id="startRun">Start replay</button>
    <p class="lock">Replay only, no GPU. Live mode needs the owner's okay and the command line.</p>`;
  $$('input[name="save"]', body).forEach((r) => (r.onchange = () => { const v = JSON.parse(r.value); S.choice.goal = SAVE_MAP[v]; S.choice.save_target = v; sumCfg(); markDone(1); }));
  $$('input[name="model"]', body).forEach((r) => (r.onchange = () => { S.choice.model = JSON.parse(r.value); sumCfg(); markDone(2); }));
  $$('input[name="traffic"]', body).forEach((r) => (r.onchange = () => { const v = JSON.parse(r.value); S.choice.workload = TRAFFIC_MAP[v]; S.choice.traffic = v; sumCfg(); markDone(3); }));
  $$('input[name="stack"]', body).forEach((r) => (r.onchange = () => { const v = JSON.parse(r.value); S.choice.framework = v; sumCfg(); markDone(4); renderStep(); }));
  $$('input[name="care"]', body).forEach((r) => (r.onchange = () => { const v = JSON.parse(r.value); Object.assign(S.choice, CARE_MAP[v]); S.choice.care = v; sumCfg(); syncAdv(); markDone(5); }));
  const fn = $("#f-name"); if (fn) fn.oninput = (e) => { S.choice.name = e.target.value; markDone(6); };
  renderPicked();
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
    .map((o) => `<span class="chip">${esc(o.label)}: <b style="color:#DCD6E4">${esc(advChoiceLabel(o))}</b></span>`).join("");
}
function sumCfg() {
  const c = S.choice, m = S.opts.options.find((o) => o.id === "model").choices.find((x) => x.v === c.model);
  const g = S.opts.options.find((o) => o.id === "goal")?.choices.find((x) => x.v === c.goal)?.label;
  const fw = STACK_CARDS.find((s) => s.v === (c.framework || "openai-python"))?.t || "openai-python";
  $("#cfgSum").innerHTML = `<dl class="kv" style="grid-template-columns:110px 1fr"><dt>Model</dt><dd>${esc(m?.label)}</dd>${g ? `<dt>Goal</dt><dd>${esc(g)}</dd>` : ""}<dt>Framework</dt><dd>${esc(fw)}</dd><dt>Speed limit</dt><dd>p99 may rise ${esc(c.p99_slack_pct)}%</dd><dt>Quality bar</dt><dd>${esc(c.min_quality_pct)}% agreement</dd><dt>Approval</dt><dd>${esc(S.opts.options.find((o) => o.id === "approval").choices.find((x) => x.v === c.approval)?.label)}</dd><dt>Cost</dt><dd>$0 ${badge("REPLAY")}</dd></dl>`;
}
async function startRun() {
  markDone(6);
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
  // The model line reads the run data being shown, never a hard-coded name.
  const m = S.run2 || {};
  const where = [m.model, m.cap_w != null ? `at a ${m.cap_w} W cap` : null].filter(Boolean).join(" ");
  const shown = S.run?.results?.model;
  const other = m.model && shown && m.model !== shown ? ` (the separate RUN2 recording, not the ${shown} run shown above)` : "";
  const show = (side) => view === "both" || view === (side === "w" ? "with" : "without");
  const cell = (r, side) => (show(side) ? `<div class="cell ${r.win === side ? "win" : ""}"><span class="v num">${cnum(esc(side === "w" ? r.wattnot : r.stock), r.tag)}</span>${side === "w" && r.delta && view === "both" ? `<span class="d">${cnum(esc(r.delta), r.tag)}</span>` : ""}</div>` : `<div></div>`);
  return `<div class="r2head"><div><h3>Without WattNot vs With WattNot</h3><p>${esc(where)}${esc(other)}${where ? ", " : ""}replay ${badge("REPLAY")}. Energy, p99 and quality are measured; cost and water are estimates from measured joules.</p></div>
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
    return `<div class="sub"><span class="st ${st}" style="margin-top:6px"></span><div style="flex:1"><b>${esc(s.title)}</b>${s.partner ? ` <span class="ptag">${esc(s.partner)}</span>` : ""}<p>${esc(f?.data.summary || s.desc)}</p>
      <div class="io">in: ${esc(s.inputs)} → out: ${esc(s.outputs)} · <code>${esc(s.code || "")}</code></div>
      ${(f?.data.numbers || []).map((x) => `<span class="chip" style="margin:4px 4px 0 0">${esc(x.label)} <b style="color:#DCD6E4">${num(x.v)} ${esc(x.unit)}</b>${badge(x.tag)}</span>`).join("")}</div></div>`; }).join("");
  return `<p>${esc(p.desc)}</p>${say ? `<div class="say">${esc(say.text)} <small style="color:var(--mu)">(${esc(say.source)})</small></div>` : ""}
    <div class="lvl">Steps inside</div>${subs}${mine.length ? raw(`${p.id}: ${mine.length} events`, mine) : ""}`;
}
function renderRun() {
  if (!S.run) { $("#pRun").innerHTML = `<div class="intro"><div><h1>Run</h1><p>No run yet. <a href="#pCfg" id="toCfg">Configure one</a>, or start the default replay.</p></div><button class="btn pri" id="defRun">Start default replay</button></div>`; $("#defRun").onclick = startRun; $("#toCfg").onclick = (e) => { e.preventDefault(); show("pCfg"); }; return; }
  const R = S.run;
  S.view ??= "both";
  $("#pRun").innerHTML = `<div class="bento"><div class="tile glass span4 lift run2block" id="run2block" aria-live="polite"></div>
  <div class="tile glass span2 low"><div class="lvl">Run</div><h1>${esc(S.choice.name || "Run")}</h1><p>${esc(R.results.model)} · ${esc(R.results.workload)} · run id <code>${esc(R.run_id)}</code> ${badge("REPLAY")}</p><p class="lock" id="phaseInd" role="status"></p>
    <div class="noprint" style="display:flex;gap:8px;flex-wrap:wrap"><button class="btn" id="pp">Pause</button><button class="btn" id="rs">Restart</button><select id="spd" style="width:auto"><option value="1">1×</option><option value="2" selected>2×</option><option value="4">4×</option><option value="20">20×</option></select></div>${legendHtml()}</div>
    <div class="tile glass span6" style="padding:10px 16px"><div style="display:flex;justify-content:space-between;font-size:13px"><span id="progT"></span><span id="progN" style="color:var(--mu)"></span></div><div class="prog"><i id="progB"></i></div>
    <div class="rail" id="rail">${R.pipeline.map((p, i) => `${i ? '<span class="edge"></span>' : ""}<button class="node" data-stage="${p.id}"><b>${esc(p.title)}</b><span id="ns-${p.id}">waiting</span></button>`).join("")}</div></div></div>
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
  const brk = R.phase_break ?? Math.floor(R.events.length / 2);
  const ind = $("#phaseInd"); if (ind) ind.textContent = phaseLabel(S.n, brk);
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
// One run record's two phases as the headline the mind map reads. Returns
// null when the run has no measured joules pair, so the leaves honestly say
// "not measured" instead of borrowing another run's numbers.
function headlineFromRecord(rec) {
  const b = rec?.phases?.baseline, w = rec?.phases?.wattnot;
  if (b?.metrics?.j_per_req == null || w?.metrics?.j_per_req == null) return null;
  return {
    model: rec.model, cap_w: w.cap_w, stock_w: b.cap_w,
    j_per_req_stock: b.metrics.j_per_req, j_per_req_cap: w.metrics.j_per_req,
    saved_pct: w.metrics.saved_pct, p99_s_stock: b.metrics.p99_s, p99_s_cap: w.metrics.p99_s,
    p99_change_pct: w.metrics.p99_change_pct, quality_stock_pct: b.metrics.quality_pct,
    quality_cap_pct: w.metrics.quality_pct, label: "MEASURED",
    source: (rec.source || []).join(", "),
  };
}
// Only headline report data goes into the fingerprint: sorted keys, the run
// id, and no timestamps or randomness. Same run, same fingerprint, every load.
function reportFingerprintData() {
  const r = S.selHeadline || S.run2 || {};
  return {
    run: S.selRun?.id || "run2",
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
  const R = S.run?.results || {}, rows = run2Rows(S.selHeadline || S.run2, R.price, R.water);
  const by = Object.fromEntries(rows.map((x) => [x.id, x]));
  const leaf = (id, icon, label, text) => ({ id, icon, label, text });
  const row = (r) => (r ? { stock: r.stock, wattnot: r.wattnot, delta: r.delta, tag: r.tag } : null);
  const e = row(by.energy), c = row(by.cost), w = row(by.water), p = row(by.p99), q = row(by.quality);
  return [
    { id: "power", color: "#B2A6C3", name: "Power", sub: "Energy per request",
      kids: [leaf("pw-s", "bolt", "Without WattNot", e ? `700 W stock · <b>${esc(e.stock)}</b>/req ${badge(e.tag)}` : "700 W stock · not measured"),
             leaf("pw-w", "bolt", "With WattNot", e ? `350 W cap · <b>${esc(e.wattnot)}</b>/req · <b>${esc(e.delta)}</b> ${badge(e.tag)}` : "350 W cap · not measured")],
      detail: `<b>What went right.</b> RUN2 on Llama 3.1 8B at a 350 W cap: <b>−27.1%</b> joules per request ${badge("MEASURED")} (data/h100-run2/vm/steps/cap350.json). Earlier sweep on Muse Glimmer 30B at 400 W: <b>−21.0%</b> with p99 +3.7% ${badge("MEASURED")} (H100 run sweep.csv, NVML). Next on the H100: a thinking budget per tier and prefix caching <span class="rad-pending">pending</span>.` },
    { id: "cost", color: "#C4B4D6", name: "Cost", sub: "Electricity per 1M requests",
      kids: [leaf("c-s", "coin", "Without WattNot", c ? `Stock: <b>${esc(c.stock)}</b>` : "Stock cost: not measured"),
             leaf("c-w", "coin", "With WattNot", c ? `Capped: <b>${esc(c.wattnot)}</b> (<b>${esc(c.delta)}</b>)` : "Capped cost: not measured")],
      detail: `Measured joules times <b>$0.1387</b>/kWh, the US commercial electricity price (EIA). The meter reading is ${badge("MEASURED")}; the bill math is ${badge("ESTIMATE")}.` },
    { id: "water", color: "#8E7A9E", name: "Water", sub: "Cooling water per 1M requests",
      kids: [leaf("w-s", "drop", "Without WattNot", w ? `Stock: <b>${esc(w.stock)}</b>` : "Stock water: not measured"),
             leaf("w-w", "drop", "With WattNot", w ? `Capped: <b>${esc(w.wattnot)}</b> (<b>${esc(w.delta)}</b>)` : "Capped water: not measured")],
      detail: `Measured joules times <b>0.36 L</b>/kWh, a US fleet-average site water factor (LBNL). On-site cooling water only; making the electricity is not counted. ${badge("ESTIMATE")}.` },
    { id: "latency", color: "#B49BDB", name: "Latency", sub: "Slowest 1% of requests (p99)",
      kids: [leaf("l-s", "clock", "Without WattNot", p ? `Stock p99 <b>${esc(p.stock)}</b> ${badge(p.tag)}` : "Stock p99: not measured"),
             leaf("l-w", "clock", "With WattNot", p ? `Capped p99 <b>${esc(p.wattnot)}</b> (<b>${esc(p.delta)}</b>) ${badge(p.tag)}` : "Capped p99: not measured")],
      detail: `p99 is the time 99 of 100 requests beat. The 350 W cap added <b>+7.7%</b> ${badge("MEASURED")}, inside the <b>10%</b> gate, so it passed. A slower cap would not.` },
    { id: "quality", color: "#8FBBA0", name: "Quality", sub: "Answer quality, stock vs capped",
      kids: [leaf("q-s", "check", "Without WattNot", q ? `Stock: <b>${esc(q.stock)}</b> pass ${badge(q.tag)}` : "Stock quality: not measured"),
             leaf("q-w", "check", "With WattNot", q ? `Capped: <b>${esc(q.wattnot)}</b> · <b>${esc(q.delta)}</b> ${badge(q.tag)}` : "Capped quality: not measured")],
      detail: `<b>666</b> answers checked: <b>93.7%</b> pass at stock and <b>93.7%</b> capped ${badge("MEASURED")}. The same pass rate means quality held.` },
    { id: "safety", color: "#8E7A9E", name: "Safety/Approve", sub: "A person stays in charge",
      kids: [leaf("s-s", "shield", "Without WattNot", `No review: a change would just apply`),
             leaf("s-w", "shield", "With WattNot", `A person approves high-risk changes ${badge("MOCK")}`)],
      detail: `<b>Why choose WattNot.</b> It is a second layer, not a claim to beat everything. Every change is measured, gated, approved by a person, logged, undoable, and signed. If a cap fails a gate, it is rejected, as below.` },
    { id: "rejected", color: "#D48389", name: "Rejected options", sub: "What we refused, and why",
      kids: [leaf("r-s", "x", "Without WattNot", `No gate: 325 W would just run slower at p99 <b>+10.9%</b>`),
             leaf("r-w", "x", "With WattNot", `Gate rejects 325 W: p99 <b>+10.9%</b> is over the <b>10%</b> limit ${badge("MEASURED")}`)],
      detail: `<b>325 W rejected:</b> p99 rose <b>+10.9%</b>, over the 10% limit ${badge("MEASURED")} (data/h100-run2/vm/steps/cap325.json). Rejections are the system working.` },
    { id: "proof", color: "#DCD6E4", name: "Proof", sub: "Fingerprint, verifier, raw files",
      kids: [leaf("f-s", "doc", "Without WattNot", `A chart you cannot re-check`),
             leaf("f-w", "doc", "With WattNot", `Fingerprint plus verifier plus raw files ${badge("SAMPLE")}`)],
      detail: `The fingerprint covers the headline numbers only, with sorted keys and no timestamps. The verifier re-checks sample receipts ${badge("SAMPLE")}. Raw data: data/h100-run2/vm/steps/cap350.json, data/h100-run2/vm/steps/cap325.json, verifier/samples/.` },
  ];
}
let mapWired = false;
// Radial layout: the hub sits in the middle and the 8 topics sit on an
// ellipse around it. Angles come from trig (Math.cos/Math.sin) so the
// nodes spread evenly; the radii derive from the container size so the map
// responds to width, and the stage grows taller when open topics need room.
function mapLayout() {
  const wrap = $("#mapWrap"), stage = $("#mapStage"), svg = $("#mapWires"), hub = $("#mapHub");
  if (!wrap || !stage || !svg || !hub) return;
  const W = wrap.clientWidth || 0;
  const topics = $$(".topic", stage);
  if (!W || W < 900) {
    // Narrow screens: fall back to the stacked layout (static flow, no wires).
    stage.style.height = "";
    hub.style.left = ""; hub.style.top = "";
    topics.forEach((t) => { t.style.left = ""; t.style.top = ""; });
    svg.innerHTML = "";
    return;
  }
  const n = topics.length || 8;
  const openN = topics.filter((t) => t.dataset.open === "1").length;
  let H = Math.max(660, 560 + openN * 120);
  stage.style.height = `${H}px`;
  const hubW = hub.offsetWidth || 360, hubH = hub.offsetHeight || 240;
  const cx = W / 2, cy = H / 2;
  const rx = Math.max(150, W / 2 - 160, hubW / 2 + 150);
  const ry = Math.max(160, H / 2 - 130, hubH / 2 + 90);
  hub.style.left = `${cx.toFixed(1)}px`; hub.style.top = `${cy.toFixed(1)}px`;
  topics.forEach((t, i) => {
    const a = -Math.PI / 2 + (i * 2 * Math.PI) / n;
    t.style.left = `${(cx + rx * Math.cos(a)).toFixed(1)}px`;
    t.style.top = `${(cy + ry * Math.sin(a)).toFixed(1)}px`;
  });
  drawWires();
  // Widen the canvas when an open topic spills past the bottom edge.
  const need = stage.scrollHeight || 0;
  if (need > H + 4) { stage.style.height = `${need + 24}px`; drawWires(); }
}
function drawWires() {
  const wrap = $("#mapWrap"), stage = $("#mapStage"), svg = $("#mapWires"), hub = $("#mapHub");
  if (!wrap || !stage || !svg || !hub) return;
  if ((wrap.clientWidth || 0) < 900) return;
  const sr = stage.getBoundingClientRect();
  if (!sr.width || !sr.height) return;
  // stage coordinates (unscaled), so wires stay on the cards at any pan/zoom
  const pos = (el) => { const c = el.closest(".rad-card, .rad-child, .radial-centre") || el; return [parseFloat(c.style.left) || 0, parseFloat(c.style.top) || 0, c.offsetHeight || 0]; };
  const ctr = (el) => { const [x, y] = pos(el); return [x, y]; };
  const top = (el) => { const [x, y, h] = pos(el); return [x, y - h / 2]; };
  const bot = (el) => { const [x, y, h] = pos(el); return [x, y + h / 2]; };
  // Curved radial connector: leaves the hub toward the topic and arrives
  // along the same direction, bowed with a cubic Bezier.
  const radial = (a, b) => {
    const dx = b[0] - a[0], dy = b[1] - a[1];
    const len = Math.hypot(dx, dy) || 1;
    const sx = a[0] + (dx / len) * 40, sy = a[1] + (dy / len) * 40;
    const ex = b[0] - (dx / len) * 46, ey = b[1] - (dy / len) * 30;
    const c1 = [sx + dx * 0.3, sy + dy * 0.3], c2 = [sx + dx * 0.7, sy + dy * 0.7];
    return `M ${sx.toFixed(1)} ${sy.toFixed(1)} C ${c1[0].toFixed(1)} ${c1[1].toFixed(1)}, ${c2[0].toFixed(1)} ${c2[1].toFixed(1)}, ${ex.toFixed(1)} ${ey.toFixed(1)}`;
  };
  const drop = (a, b) => { const my = (a[1] + b[1]) / 2; return `M ${a[0].toFixed(1)} ${a[1].toFixed(1)} C ${a[0].toFixed(1)} ${my.toFixed(1)}, ${b[0].toFixed(1)} ${my.toFixed(1)}, ${b[0].toFixed(1)} ${b[1].toFixed(1)}`; };
  const hc = ctr(hub), d = [];
  $$(".topic", stage).forEach((t) => {
    const head = $(".topic-head", t);
    if (!head) return;
    d.push(radial(hc, ctr(head)));
    if (t.dataset.open === "1") $$(".leaf", t).forEach((l) => d.push(drop(bot(head), top(l))));
  });
  svg.setAttribute("viewBox", `0 0 ${stage.offsetWidth} ${stage.offsetHeight}`);
  svg.innerHTML = d.map((x) => `<path d="${x}"/>`).join("");
}
function wireMap() {
  mapLayout();
  setTimeout(mapLayout, 0);
  if (!mapWired) { mapWired = true; window.addEventListener("resize", mapLayout); }
}
// ---------- signed report: radial comparison canvas ----------
// A pan/zoom radial view of ONE run record: WattNot in the centre (with a
// run picker), three branch cards 120° apart (Power/Cost/Water savings from
// that run's own baseline vs WattNot phases), and an outer sub-ring of
// per-method cards per branch. Numbers come only from the run record (or a
// stated derivation from it); anything else reads "not measured in this
// run". Totals are never split across methods.
const RAD_METHODS = [
  { id: "caps", name: "Power caps", hint: "Lower watt limit; keep the lowest cap that passes the gates." },
  { id: "think", name: "Thinking budget per tier", hint: "Pending: each tier gets the cheapest thinking budget that keeps the pass rate (H100 long run)." },
  { id: "prefix", name: "Prefix caching", hint: "Pending: reuse the shared prompt prefix of Complex agent turns (H100 long run)." },
  { id: "topk", name: "Adaptive top-k (Spark on Modal)", hint: "Pending: fewer experts per word on Muse Spark Open, tested on Modal 8x B200, not on the H100." },
  { id: "gate", name: "Quality-gate effect", hint: "Caps the gates refused, and what they would have cost." },
  { id: "waste", name: "Agent-waste saver", hint: "Stops tool-call loops that repeat without new information." },
  { id: "router", name: "Hosted-router choice", hint: "Own GPU with a cap, or paying a host per answer." },
  { id: "cache", name: "Prompt cache", hint: "Reuse of repeated prompt prefixes." },
];
const RAD_FALLBACK = "not measured in this run";

// Saved block for one run record: each line is that run's WattNot phase vs
// its own baseline phase. Cost/water are derived from measured joules with
// the bundle's price/water factors; the note states the derivation.
function radSavedLines(rec) {
  const b = rec?.phases?.baseline, w = rec?.phases?.wattnot;
  const bj = b?.metrics?.j_per_req, wj = w?.metrics?.j_per_req;
  const price = S.run?.results?.price, water = S.run?.results?.water;
  const out = [];
  if (bj != null && wj != null) {
    const pct = ((wj - bj) / bj) * 100;
    out.push({ id: "power", name: "Power", text: `${num(pct, 1)}% · ${num(bj)} → ${num(wj)} J/req`, tag: b.label || "MEASURED",
      note: "This run's WattNot j_per_req vs its own baseline j_per_req: (wattnot − baseline) / baseline." });
  } else out.push({ id: "power", name: "Power", text: null });
  if (bj != null && wj != null && price?.usd_per_kwh != null) {
    const kwh = (j) => (j * 1e6) / 3.6e6;
    const s = kwh(bj) * price.usd_per_kwh, c = kwh(wj) * price.usd_per_kwh, d = s - c;
    out.push({ id: "cost", name: "Cost", text: `$${num(s)} → $${num(c)} per 1M (${d >= 0 ? "saves" : "costs"} $${num(Math.abs(d))})`, tag: "ESTIMATE",
      note: `Measured joules × $${price.usd_per_kwh}/kWh (${price.name || "bundle price"}). The meter reading is measured; the bill math is an estimate.` });
  } else out.push({ id: "cost", name: "Cost", text: null });
  if (bj != null && wj != null && water?.factor_l_per_kwh != null) {
    const kwh = (j) => (j * 1e6) / 3.6e6;
    const s = kwh(bj) * water.factor_l_per_kwh, c = kwh(wj) * water.factor_l_per_kwh, d = s - c;
    out.push({ id: "water", name: "Water", text: `${num(s)} → ${num(c)} L per 1M (${d >= 0 ? "saves" : "uses"} ${num(Math.abs(d))} L)`, tag: "ESTIMATE",
      note: `Measured joules × ${water.factor_l_per_kwh} L/kWh (${water.name || "bundle water factor"}), on-site cooling water only. The meter reading is measured; the water math is an estimate.` });
  } else out.push({ id: "water", name: "Water", text: null });
  return out;
}

// One method's saving for one metric branch within a run record, or null
// when the record does not attribute anything to that method (the caller
// renders RAD_FALLBACK). Totals are never split: only power caps may show
// the whole-run figure, with a note that it is not a per-method split, and
// cost/water for caps are derived from that whole-run figure with the same
// note. Every other method reads "not measured in this run".
function radMethodSaving(rec, branch, id) {
  const b = rec?.phases?.baseline, w = rec?.phases?.wattnot;
  const bj = b?.metrics?.j_per_req, wj = w?.metrics?.j_per_req;
  if (id !== "caps" || bj == null || wj == null || w?.metrics?.saved_pct == null) return null;
  const splitNote = `Whole-run WattNot-vs-baseline difference from this run's record (${w.cap_w} W vs ${b.cap_w} W). Not a per-method split: the record does not attribute shares to methods.`;
  if (branch === "power") {
    return { text: `${num(w.metrics.saved_pct, 1)}%`, tag: w.label || "MEASURED", note: splitNote };
  }
  const kwh = (j) => (j * 1e6) / 3.6e6;
  if (branch === "cost") {
    const price = S.run?.results?.price;
    if (price?.usd_per_kwh == null) return null;
    const d = (kwh(bj) - kwh(wj)) * price.usd_per_kwh;
    return { text: `${d >= 0 ? "saves" : "costs"} $${num(Math.abs(d))} per 1M`, tag: "ESTIMATE",
      note: `Derived from the whole-run joules difference above × $${price.usd_per_kwh}/kWh. ${splitNote}` };
  }
  if (branch === "water") {
    const water = S.run?.results?.water;
    if (water?.factor_l_per_kwh == null) return null;
    const d = (kwh(bj) - kwh(wj)) * water.factor_l_per_kwh;
    return { text: `${d >= 0 ? "saves" : "uses"} ${num(Math.abs(d))} L per 1M`, tag: "ESTIMATE",
      note: `Derived from the whole-run joules difference above × ${water.factor_l_per_kwh} L/kWh. ${splitNote}` };
  }
  return null;
}

// Power over time for the Power branch: per-repeat energy per request from
// the run record itself (baseline repeats, then WattNot repeats) as a small
// SVG step chart. Null when the record has no such series, so the branch
// honestly says "not measured in this run".
function radPowerSeries(rec) {
  const b = rec?.phases?.baseline?.events || [], w = rec?.phases?.wattnot?.events || [];
  const bj = b.map((e) => e.j_per_req).filter((v) => v != null);
  const wj = w.map((e) => e.j_per_req).filter((v) => v != null);
  if (!bj.length || !wj.length) return null;
  return { base: bj, tuned: wj };
}
function radPowerSvg(rec) {
  const s = radPowerSeries(rec);
  if (!s) return `<span class="rad-miss">${RAD_FALLBACK}</span>`;
  const pts = [...s.base.map((v) => ({ v, ph: 0 })), ...s.tuned.map((v) => ({ v, ph: 1 }))];
  const W = 180, H = 56, P = 6;
  const vs = pts.map((p) => p.v), lo = Math.min(...vs), hi = Math.max(...vs), rg = (hi - lo) || 1;
  const X = (i) => (P + (i * (W - 2 * P)) / (pts.length - 1 || 1)).toFixed(1);
  const Y = (v) => (H - P - ((v - lo) / rg) * (H - 2 * P)).toFixed(1);
  const line = (a, z, cls) => `<polyline class="${cls}" points="${pts.slice(a, z).map((p, j) => `${X(a + j)},${Y(p.v)}`).join(" ")}"/>`;
  const cut = (P + ((s.base.length - 0.5) * (W - 2 * P)) / (pts.length - 1 || 1)).toFixed(1);
  return `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Energy per request over repeats: baseline then WattNot">`
    + `<line x1="${cut}" y1="2" x2="${cut}" y2="${H - 2}" class="cut"/>`
    + `${line(0, s.base.length, "s")}${line(s.base.length - 1, pts.length, "w")}`
    + `<text x="${P}" y="${H - 1}" class="t">without</text><text x="${W - P}" y="${H - 1}" class="t e">with</text></svg>`;
}

// Radial comparison for ONE run: the centre WattNot node opens a dropdown
// to pick the run (kept in sync with #runSel). The centre then has exactly
// three branch cards — Power, Cost, Water — each showing that run's
// WattNot-vs-own-baseline saving from radSavedLines, coloured by label.
// Each branch opens its own outer sub-ring of method cards; a method shows
// that metric's saving only when the record attributes it, else
// "not measured in this run". The Power branch also carries a small
// with-vs-without power-over-time graph from the run record.
const RAD_BRANCHES = ["power", "cost", "water"];
function radialHtml(runs, featuredId, rec) {
  const lines = radSavedLines(rec);
  const byId = Object.fromEntries(lines.map((l) => [l.id, l]));
  const cards = [], kids = [];
  for (const id of RAD_BRANCHES) {
    const l = byId[id];
    for (const m of RAD_METHODS) {
      const s = radMethodSaving(rec, id, m.id);
      kids.push(`<div class="rad-child" data-branch="${id}" data-m="${m.id}" hidden><b>${esc(m.name)}</b>`
        + (s ? `<span class="rad-save">${cnum(esc(s.text), s.tag)}</span><small class="rad-note" title="${esc(s.note)}">${esc(s.note)}</small>`
          : `<span class="rad-miss">${RAD_FALLBACK}</span><small class="rad-note">${esc(m.hint)} The record has no per-method numbers, so nothing is split out.</small>`)
        + `</div>`);
    }
    const saving = l?.text
      ? `<span class="rad-save">${cnum(esc(l.text), l.tag)}</span>`
      : `<span class="rad-miss">${RAD_FALLBACK}</span>`;
    const graph = id === "power" ? `<div class="rad-power">${radPowerSvg(rec)}</div>` : "";
    cards.push(`<div class="rad-card" data-branch="${id}" data-open="0">`
      + `<button class="rad-head" aria-expanded="false"><b>${esc(l?.name || id)}</b><small>saving vs this run's own baseline</small></button>`
      + `<div class="rad-saved"><div class="lvl">Saved</div>`
      + `<button class="rad-line" data-branch="${id}" title="${esc(l?.note || "Not in this run's record.")}"><span class="rad-dim">${esc(l?.name || id)}</span>${saving}</button>`
      + `${graph}</div></div>`);
  }
  const drop = runs.map((r) => `<button class="centre-opt${r.id === featuredId ? " now" : ""}" data-run="${esc(r.id)}" role="option" aria-selected="${r.id === featuredId}">${esc(r.name)}</button>`).join("")
    || `<span class="rad-miss">No runs listed.</span>`;
  const feat = runs.find((r) => r.id === featuredId) || runs[0];
  return `<div class="bento"><div class="radial-bar noprint span6">`
    + `<span style="flex:1"></span><button class="btn" data-radial="open">Expand all</button><button class="btn" data-radial="close">Collapse all</button></div>`
    + `<div class="radial-wrap glass tile span6 low" id="radialWrap"><svg class="radial-wires" id="radialWires" aria-hidden="true"></svg>`
    + `<div class="radial-stage" id="radialStage">`
    + `<div class="radial-centre" id="radCentre"><button class="centre-node" id="centreNode" aria-expanded="false" aria-haspopup="listbox" aria-label="WattNot: choose a run">WattNot</button>`
    + `<div class="centre-sub">${esc(feat ? feat.name : "No run chosen")}${rec?.model ? ` · ${esc(rec.model)}` : ""}</div>`
    + `<div class="centre-drop" id="centreDrop" role="listbox" aria-label="Runs" hidden>${drop}</div></div>`
    + cards.join("") + kids.join("") + `</div></div></div>`;
}

// Radial layout: three branch cards sit 120° apart on a compact inner ring
// around the centre; each expanded branch's method cards fan out on an outer
// arc inside that branch's own sector, so families never overlap. Radii stay
// as small as the card sizes allow; the user pans for the rest at fit zoom.
function layoutRadial() {
  // Compare runs the nested-tree layout (round 8); fall through to it so every
  // caller (resize, pancanvas, Expand all) positions the drill rings too.
  if (S.cmpTreeMode) return layoutTree();
  const wrap = $("#radialWrap"), stage = $("#radialStage"), svg = $("#radialWires"), centre = $("#radCentre");
  if (!wrap || !stage || !svg || !centre) return;
  const W = wrap.clientWidth || 0;
  const cards = $$(".rad-card", stage);
  const kids = $$(".rad-child", stage);
  const visKids = kids.filter((k) => !k.hidden);
  if (!W || W < 900) {
    stage.style.width = ""; stage.style.height = "";
    centre.style.left = ""; centre.style.top = "";
    cards.forEach((c) => { c.style.left = ""; c.style.top = ""; });
    kids.forEach((k) => { k.style.left = ""; k.style.top = ""; });
    svg.innerHTML = "";
    return;
  }
  const n = 3, sector = (2 * Math.PI) / n;
  const R1 = 240;
  const byBranch = cards.map((c) => visKids.filter((k) => k.dataset.branch === c.dataset.branch));
  let R2 = 0;
  byBranch.forEach((ks) => { if (ks.length) R2 = Math.max(R2, (ks.length * 175) / (0.8 * sector)); });
  if (R2) R2 = Math.max(R2, R1 + 300);
  const maxR = (R2 || R1) + 130;
  const w = Math.ceil(maxR * 2 + 40), h = Math.ceil(maxR * 2 + 40);
  const cx = w / 2, cy = h / 2;
  stage.style.width = `${w}px`; stage.style.height = `${h}px`;
  centre.style.left = `${cx.toFixed(1)}px`; centre.style.top = `${cy.toFixed(1)}px`;
  cards.forEach((c, i) => {
    const a = -Math.PI / 2 + (i * 2 * Math.PI) / n;
    c.style.left = `${(cx + R1 * Math.cos(a)).toFixed(1)}px`;
    c.style.top = `${(cy + R1 * Math.sin(a)).toFixed(1)}px`;
  });
  byBranch.forEach((ks, i) => {
    if (!ks.length) return;
    const a = -Math.PI / 2 + (i * 2 * Math.PI) / n;
    const k = ks.length, step = k > 1 ? (0.8 * sector) / (k - 1) : 0;
    ks.forEach((el, j) => {
      const aj = a + (j - (k - 1) / 2) * step;
      el.style.left = `${(cx + R2 * Math.cos(aj)).toFixed(1)}px`;
      el.style.top = `${(cy + R2 * Math.sin(aj)).toFixed(1)}px`;
    });
  });
  drawRadialWires();
}
function drawRadialWires() {
  const wrap = $("#radialWrap"), stage = $("#radialStage"), svg = $("#radialWires"), centre = $("#radCentre");
  if (!wrap || !stage || !svg || !centre) return;
  if ((wrap.clientWidth || 0) < 900) return;
  const sr = stage.getBoundingClientRect();
  if (!sr.width || !sr.height) return;
  // stage coordinates (unscaled), so wires stay on the cards at any pan/zoom
  const pos = (el) => { const c = el.closest(".rad-card, .rad-child, .radial-centre") || el; return [parseFloat(c.style.left) || 0, parseFloat(c.style.top) || 0, c.offsetHeight || 0]; };
  const ctr = (el) => { const [x, y] = pos(el); return [x, y]; };
  const top = (el) => { const [x, y, h] = pos(el); return [x, y - h / 2]; };
  const bot = (el) => { const [x, y, h] = pos(el); return [x, y + h / 2]; };
  const radial = (a, b) => {
    const dx = b[0] - a[0], dy = b[1] - a[1];
    const len = Math.hypot(dx, dy) || 1;
    const sx = a[0] + (dx / len) * 40, sy = a[1] + (dy / len) * 40;
    const ex = b[0] - (dx / len) * 46, ey = b[1] - (dy / len) * 30;
    const c1 = [sx + dx * 0.3, sy + dy * 0.3], c2 = [sx + dx * 0.7, sy + dy * 0.7];
    return `M ${sx.toFixed(1)} ${sy.toFixed(1)} C ${c1[0].toFixed(1)} ${c1[1].toFixed(1)}, ${c2[0].toFixed(1)} ${c2[1].toFixed(1)}, ${ex.toFixed(1)} ${ey.toFixed(1)}`;
  };
  const drop = (a, b) => { const my = (a[1] + b[1]) / 2; return `M ${a[0].toFixed(1)} ${a[1].toFixed(1)} C ${a[0].toFixed(1)} ${my.toFixed(1)}, ${b[0].toFixed(1)} ${my.toFixed(1)}, ${b[0].toFixed(1)} ${b[1].toFixed(1)}`; };
  const hc = ctr(centre), d = [];
  $$(".rad-card", stage).forEach((c) => {
    const head = $(".rad-head", c);
    if (!head) return;
    d.push(radial(hc, ctr(head)));
    if (c.dataset.open === "1") $$(".rad-child", stage).filter((l) => l.dataset.branch === c.dataset.branch && !l.hidden).forEach((l) => d.push(drop(bot(c), top(l))));
  });
  svg.setAttribute("viewBox", `0 0 ${stage.offsetWidth} ${stage.offsetHeight}`);
  svg.innerHTML = d.map((x) => `<path d="${x}"/>`).join("");
}
let radWired = false;
function wireRadial() {
  const wrap = $("#radialWrap"), stage = $("#radialStage");
  if (!wrap || !stage) return;
  const setCard = (c, on) => {
    c.dataset.open = on ? "1" : "0";
    $(".rad-head", c).setAttribute("aria-expanded", String(on));
    $$(".rad-child", stage).filter((k) => k.dataset.branch === c.dataset.branch).forEach((k) => { k.hidden = !on; });
  };
  const radFit = () => { S.radPan?.update(); S.radPan?.fit(); if (S.radPan && S.radPan.state.scale < 0.6) S.radPan.setZoom(0.6); };
  $$("#radialStage .rad-head").forEach((b) => (b.onclick = () => {
    const c = b.closest(".rad-card"); setCard(c, c.dataset.open !== "1"); layoutRadial(); radFit();
  }));
  $$("#radialStage .rad-line").forEach((b) => (b.onclick = () => {
    const c = b.closest(".rad-card"); setCard(c, true); layoutRadial(); radFit();
  }));
  $$("#pReport [data-radial]").forEach((b) => (b.onclick = () => {
    const on = b.dataset.radial === "open";
    $$("#radialStage .rad-card").forEach((c) => setCard(c, on));
    layoutRadial(); radFit();
  }));
  const node = $("#centreNode"), cd = $("#centreDrop");
  if (node && cd) node.onclick = () => {
    const on = cd.hidden; cd.hidden = !on; node.setAttribute("aria-expanded", String(on));
  };
  $$("#centreDrop .centre-opt").forEach((b) => (b.onclick = () => selectRun(b.dataset.run)));
  layoutRadial();
  if (S.radPan) { try { S.radPan.destroy(); } catch {} S.radPan = null; }
  try {
    S.radPan = initPanCanvas(wrap, stage, { goto: () => {
      const feat = $(".rad-card", stage) || $("#radCentre");
      if (feat) S.radPan.focus(feat);
    } });
    S.radPan.fit();
    if (S.radPan.state.scale < 0.6) S.radPan.setZoom(0.6);
  } catch {}
  wrap.ondblclick = (e) => { if (e.target === wrap || e.target === stage) S.radPan?.setZoom(1); };
  setTimeout(() => { layoutRadial(); S.radPan?.update(); }, 0);
  if (!radWired) { radWired = true; window.addEventListener("resize", () => { layoutRadial(); }); }
}
async function selectRun(id) {
  S.selRunId = id;
  const sel = $("#runSel"); if (sel && id) sel.value = id;
  if (!id || id === "default") { S.selRun = null; S.selHeadline = null; }
  else if (id === BUNDLED_ID) { S.selRun = bundledRecord(S.run2); S.selHeadline = null; }
  else {
    try {
      const rec = await fetch(`/api/dash/v2/runs/${encodeURIComponent(id)}`).then((r) => { if (!r.ok) throw new Error(r.status); return r.json(); });
      S.selRun = rec; S.selHeadline = headlineFromRecord(rec);
    } catch { S.selRun = null; S.selHeadline = null; }
  }
  renderReport();
}
async function renderReport() {
  if (S.radPan) { try { S.radPan.destroy(); } catch {} S.radPan = null; }
  if (!S.run) { $("#pReport").innerHTML = `<p>Loading the default replay…</p>`; await startQuiet(); }
  S.verify ??= await fetch("/api/dash/v2/verify").then((r) => r.json()).catch(() => null);
  S.runs ??= await fetch("/api/dash/v2/runs").then((r) => r.json()).catch(() => null);
  S.radRecs ??= {};
  await Promise.all(((S.runs && S.runs.runs) || []).map(async (r) => {
    if (S.radRecs[r.id]) return;
    try {
      const rec = await fetch(`/api/dash/v2/runs/${encodeURIComponent(r.id)}`).then((x) => { if (!x.ok) throw new Error(x.status); return x.json(); });
      S.radRecs[r.id] = rec;
    } catch { /* the card renders Saved fallbacks */ }
  }));
  const topics = mapTopics();
  const sha = await sha256(stableStringify(reportFingerprintData()));
  const vs = verifySummary(S.verify);
  const r2 = S.selHeadline || S.run2 || {};
  const runs = S.runs?.runs || [];
  const selId = S.selRunId || (runs[0] && runs[0].id) || "default";
  const hubTitle = S.selRun?.run_name || `${r2.model || "Llama 3.1 8B Instruct"} · ${r2.cap_w || 350} W cap`;
  const hubDetail = r2.j_per_req_stock != null
    ? `<p>We capped an H100 at <b>${r2.cap_w} W</b> instead of ${r2.stock_w} W on ${esc(r2.model)} and measured energy per request, the slowest 1% of requests, and answer quality. Energy fell <b>${num(r2.saved_pct, 1)}%</b> ${badge("MEASURED")}, the slowest 1% changed by <b>${num(r2.p99_change_pct, 1)}%</b> ${badge("MEASURED")}, and quality went from <b>${num(r2.quality_stock_pct, 1)}%</b> to <b>${num(r2.quality_cap_pct, 1)}%</b> ${badge("MEASURED")}.</p>`
    : `<p>${esc(S.selRun?.phases?.wattnot?.note || "This run has no measured headline numbers.")}</p>`;
  const topicHtml = (t) => `<div class="topic" data-topic="${t.id}" data-open="0">
    <button class="topic-head" aria-expanded="false"><span class="tdot" style="--tc:${t.color}" aria-hidden="true"></span><span class="tt"><b>${esc(t.name)}</b><small>${esc(t.sub)}</small></span><span class="chev" aria-hidden="true">›</span></button>
    <div class="topic-kids" hidden>
      ${t.kids.map((k) => `<div class="leaf"><span class="leaf-ic" aria-hidden="true">${MAP_ICON[k.icon] || ""}</span><div><b>${esc(k.label)}</b><span>${k.text}</span></div></div>`).join("")}
      <div class="topic-detail">${t.detail}</div>
    </div></div>`;
  const featRec = S.selRunId === BUNDLED_ID ? bundledRecord(S.run2) : (S.selRun || (S.radRecs && S.radRecs[selId]) || null);
  const cst = compareState(runs, S.selRunId === BUNDLED_ID ? BUNDLED_ID : selId, featRec);
  S.cmpState = cst; S.cmpRec = cst.kind === "ok" || cst.kind === "bundled" ? featRec : null;
  const radOrEmpty = S.cmpRec ? `${cst.kind === "bundled" ? bundledBanner() : ""}${radialHtml(runs, selId, featRec)}` : emptyStateHtml(cst, !!bundledRecord(S.run2));
  $("#pReport").innerHTML = `${radOrEmpty}<div class="ctrl noprint"><label for="runSel">Run</label><select id="runSel" style="width:auto;max-width:320px" aria-label="Run">${runs.length ? runs.map((r) => `<option value="${esc(r.id)}" ${r.id === selId ? "selected" : ""}>${esc(r.name)}</option>`).join("") : `<option value="default">Default replay</option>`}${bundledRecord(S.run2) ? `<option value="${BUNDLED_ID}" ${S.selRunId === BUNDLED_ID ? "selected" : ""}>Bundled sample · RUN2 (built-in, not your run)</option>` : ""}</select>${legendHtml()}<button class="btn pri" onclick="print()">Export PDF / PNG (print)</button><span style="color:var(--mu);font-size:13px">Topics start closed so the map stays readable. Open any circle for its two paths and the detail.</span><span style="flex:1"></span><button class="btn" data-map="open">Expand all</button><button class="btn" data-map="close">Collapse all</button></div>
  <div class="map-wrap glass" id="mapWrap"><svg class="map-wires" id="mapWires" aria-hidden="true"></svg>
    <div class="map-stage" id="mapStage">
      <div class="map-hub" id="mapHub" data-open="0">
        <p class="kicker">WattNot signed report · ${badge("REPLAY")} ${badge("MEASURED")}</p>
        <h1>${esc(hubTitle)}</h1>
        <p class="q">Can we cut GPU power without hurting quality?</p>
        <button class="btn sm" id="hubToggle" aria-expanded="false">Show the question</button>
        <div class="hub-detail" hidden>${hubDetail}</div>
      </div>
      ${topics.map(topicHtml).join("")}
    </div>
  </div>
  <div class="who-banner">Who should pick which?</div>
  <div class="who bento"><div class="glass tile span3 low"><h3 style="color:var(--pk)">Pick the baseline if</h3><p>Every millisecond of the slowest requests matters more than the power bill, or you cannot run a quality check on your own prompts yet.</p></div>
  <div class="glass tile span3 lift"><h3 style="color:var(--cy)">Pick WattNot if</h3><p>You pay for power and cooling, can accept the p99 change shown above, and want each change approved, logged, undoable and signed.</p></div></div>
  <div class="sig glass"><div><div class="lvl">This report's fingerprint</div><code class="fp" id="fpCode">sha256 ${esc(sha ?? "unavailable on a non-https page")}</code><p class="fp-note">Same data, same fingerprint, every load. Sorted keys, no timestamps.</p></div>
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
    mapLayout();
  };
  $("#runSel").onchange = (e) => selectRun(e.target.value);
  wireMap();
  wireRadial();
  mountCompare();
}
// Round 8: the Power/Cost/Water branches open into a NESTED TREE (v2r7.TREE).
// Each card click opens one more ring on the same circle (no pop-ups); a
// breadcrumb walks back. Cost and Water reuse the tree, values derived from
// Power. Cards show only name + one value + an info button; info and the detail
// open in the shared side panel.
const TREE_DESC = {
  owngpu: "Levers that need our own GPU: caps, expert routing and quantization. Hosted endpoints give no GPU access.",
  caps: "Lower the GPU's watt limit and keep the lowest cap that passes the quality and speed gates.",
  capsweep: "Try 650, 600 … 400 W and keep the lowest cap that passes.",
  clocks: "Set clocks per phase (prefill vs decode) instead of one fixed cap.",
  topk: "Send each word to fewer experts when one or two carry almost all the weight.",
  quant: "Store experts in fewer bits; tiered keeps busy experts at higher precision.",
  waste: "Stop agent loops and wasted tokens that add no new information.",
  loopstop: "Stop an agent that calls the model again with nothing new.",
  smallfirst: "Answer with a small model first (Llama 8B, Muse Glimmer, Muse Spark) and only escalate on a failed check.",
  context: "Cut the tokens a request carries: reuse cached prefixes, compress memory, drop duplicates.",
  prefix: "Reuse a repeated prompt prefix instead of recomputing it. Own GPU only.",
  memcomp: "Summarise old turns so the context stays short.",
  dupcut: "Drop duplicate tool output before it reaches the model.",
  where: "Compare the three places a model can run and pick one per workload.",
  own: "Your own GPU: power is measured and capped; prompts never leave.",
  baseten: "Hosted on Baseten, paid per answer. Energy is an estimate (no shared meter).",
  modal: "Hosted on Modal, paid per GPU-second. Energy is an estimate.",
  gate: "Refuse any setting that drops answer quality or slows p99 past the limit.",
  q: "Answers must agree with the full-precision model on a fixed test set.",
  speed: "The slowest 1% (p99) must not rise past the limit.",
  budget: "Spend must stay inside the run's budget.",
  sam: "SAM 3.1 outlines each rack; WattNot marks the rack it can cool. Heat is simulated from the measured power drop.",
};
const PEND_TIP = "Pending: fills in after the long H100 run.";
const treeVal = (v, branch) => {
  if (!v) return `<span class="rad-miss">–</span><span class="rad-pending">pending</span>`;
  if (v.gate) return v.pass ? `<span class="rad-pass">✓ pass</span> ${badge("MEASURED")}` : `<span class="rad-fail">✕ fail</span> ${badge("MEASURED")}`;
  const isCostOrWater = branch === "cost" || branch === "water";
  const showBadge = v.tag && (!isCostOrWater || v.tag !== "ESTIMATE");
  return `${cnum(esc(v.text), isCostOrWater && v.tag === "ESTIMATE" ? null : v.tag)}${showBadge ? ` ${badge(v.tag)}` : ""}`;
};
function branchDetailHtml(rec, id) {
  const sv = savedPct(rec, id);
  const b = rec?.phases?.baseline?.metrics?.j_per_req, w = rec?.phases?.wattnot?.metrics?.j_per_req;
  const note = id === "power" ? "Energy per answer, measured on the GPU." : esc(DERIVED_HINT);
  return `<p>${note}</p><p>Saved ${sv ? `<b>${cnum(esc(sv.text), sv.tag)}</b>${badge(sv.tag)}` : `<span class="rad-miss">– pending</span>`}</p>${b != null && w != null ? `<p class="fp-note">${num(b)} → ${num(w)} J per answer ${badge("MEASURED")}</p>` : ""}`;
}
function nodeDetailHtml(rec, branch, node) {
  const v = treeValue(rec, branch, node), desc = TREE_DESC[node.id] || "";
  let head;
  if (node.kind === "gate") head = v ? (v.pass ? `<p class="rad-pass">✓ Passed</p>` : `<p class="rad-fail">✕ Failed</p>`) : `<p class="rad-miss">Not evaluated in this run <span class="rad-pending">pending</span></p>`;
  else if (node.id === "waste") head = v ? `<p>Saved <b>${cnum(esc(v.text), v.tag)}</b>${badge(v.tag)}</p>` : `<p class="rad-miss">Not measured in this run</p>`;
  else head = v ? `<p>Saved <b>${cnum(esc(v.text), v.tag)}</b>${badge(v.tag)}</p>` : `<p class="rad-miss">Not measured in this run <span class="rad-pending">pending</span></p>`;
  let extra = node.id === "context" ? contextDetailHtml(rec) : "";
  if (node.own_only) extra += `<p class="rad-sub">Own GPU only: hosted endpoints give no GPU access.</p>`;
  if (node.kind !== "gate") extra += derivedWorking(rec, branch, node);
  return `<p>${esc(desc)}</p>${head}${extra}<p class="fp-note">${esc(rec?.run_name || "")} ${badge(rec?.label || "REPLAY")}</p>`;
}
// Cost and Water come from the Power saving: J -> kWh -> $ and litres (0.36 L/kWh).
// Hosted items: cost MEASURED, power and water ESTIMATE.
function derivedWorking(rec, branch, node) {
  if (node.host) return `<p class="rad-sub">Hosted: cost ${badge("MEASURED")} from tokens × list price; power and water ${badge("ESTIMATE")} because hosts share no power meters.</p>`;
  if (branch === "power") return "";
  const b = rec?.phases?.baseline?.metrics?.j_per_req, w = rec?.phases?.wattnot?.metrics?.j_per_req;
  if (b == null || w == null) return `<p class="rad-sub">Derived from the Power saving once it is measured <span class="rad-pending">pending</span></p>`;
  const kwh = (j) => (j * 1e6) / 3.6e6, price = rec?.price_usd_per_kwh ?? 0.1387;
  const f = branch === "cost" ? (j) => `$${num(kwh(j) * price)}` : (j) => `${num(kwh(j) * 0.36)} L`;
  return `<ol class="wex"><li>${num(b)} → ${num(w)} J per answer ${badge("MEASURED")}</li><li>× 1M answers ÷ 3,600,000 = ${num(kwh(b))} → ${num(kwh(w))} kWh</li><li>${branch === "cost" ? `× $${num(price)}/kWh` : "× 0.36 L/kWh"} = ${f(b)} → ${f(w)} per 1M answers ${badge("ESTIMATE")}</li></ol>`;
}
// Context saver sub-parts: tokens and joules before/after and gate pass rate,
// read defensively from the record; each shows "–" with a pending label until
// a build adds the numbers.
function contextDetailHtml(rec) {
  const cs = contextSaver(rec);
  const parts = [["prefix", "Prefix cache (own GPU only)"], ["memcomp", "Memory compression"], ["dupcut", "Duplicate cut"]];
  const cell = (m) => (m?.value != null ? `${cnum(num(m.value), m.label || "ESTIMATE")}${badge(m.label || "ESTIMATE")}` : `<span class="rad-miss">–</span><span class="rad-pending">pending</span>`);
  const rows = parts.map(([id, name]) => {
    const p = cs ? (cs.parts?.[id] || cs[id]) : null;
    const tb = p?.tokens_before, ta = p?.tokens_after, jb = p?.joules_before, ja = p?.joules_after, pr = p?.pass_rate;
    return `<div class="rad-sub-row"><b>${esc(name)}</b>
      <div class="kv" style="grid-template-columns:120px 1fr"><dt>Tokens</dt><dd>${cell(tb)} → ${cell(ta)}</dd>
      <dt>Joules</dt><dd>${cell(jb)} → ${cell(ja)}</dd><dt>Gate pass</dt><dd>${cell(pr)}</dd></div></div>`;
  }).join("");
  return `<h4>Three sub-parts</h4>${rows}${cs ? "" : `<p class="rad-pending">No context-saver numbers in this run yet; each sub-part is pending.</p>`}`;
}
function mountCompare() {
  $("#pReport").toggleAttribute("data-cmp-empty", !S.cmpRec);
  $$("#pReport [data-bundled]").forEach((b) => (b.onclick = () => selectRun(BUNDLED_ID)));
  if (!S.cmpRec) { S.cmpTreeMode = false; return; }
  const page = $("#pReport");
  const rec = S.cmpRec;
  $("#cmpR7")?.remove();
  const runs = (S.runs && S.runs.runs) || [];
  const selId = S.selRunId || rec?.run_id || "";
  const top = document.createElement("div"); top.id = "cmpR7"; top.innerHTML = cmpHeadHtml(rec, runs, selId);
  page.prepend(top);
  const runSel = $("#cmpRunSel", top);
  if (runSel) {
    runSel.value = selId;
    runSel.onchange = (e) => selectRun(e.target.value);
  }

  const wrap = $("#radialWrap");
  const stage = $("#radialStage"); if (!stage || !wrap) return;

  // Initialize or re-fit Pan and Zoom canvas
  if (!S.radPan) {
    S.radPan = initPanCanvas(wrap, stage, { controls: true, pad: 32 });
  }

  const wires = $("#radialWires"); if (wires && wires.parentElement !== stage) stage.prepend(wires);
  $$(".rad-child, .rad-node, .rad-leaf", stage).forEach((k) => k.remove());
  S.cmpTreeMode = true; S.cmpActive = S.cmpActive || "power"; S.cmpPath = S.cmpPath || [];
  S.cmpCollapsed = S.cmpCollapsed || false;
  const bar = $(".radial-bar");
  if (bar && !$("#cmpCrumb", bar)) bar.insertAdjacentHTML("afterbegin", `<nav class="cmp-crumb" id="cmpCrumb" aria-label="Compare path"></nav>`);
  if (bar && !$("[data-where]", bar)) {
    const expandBtn = $("[data-radial='open']", bar);
    if (expandBtn) expandBtn.insertAdjacentHTML("beforebegin", `<button class="btn rad-where" data-where>Where should this model run?</button> `);
    else bar.insertAdjacentHTML("beforeend", `<button class="btn rad-where" data-where>Where should this model run?</button>`);
  }
  $$("[data-where]").forEach((w) => (w.onclick = () => openWhere(rec)));

  const branchSaved = (id) => {
    const sv = savedPct(rec, id);
    if (!sv) return `<span class="rad-miss">–</span><span class="v-chip">pending</span>`;
    const isCostOrWater = id === "cost" || id === "water";
    const tag = isCostOrWater ? null : sv.tag;
    return `${cnum(esc(sv.text), tag)}${tag ? ` ${badge(tag)}` : ""}`;
  };

  // Root WattNot node toggle collapse/expand
  const centreNode = $("#centreNode", stage);
  if (centreNode) {
    centreNode.setAttribute("aria-expanded", String(!S.cmpCollapsed));
    centreNode.onclick = (e) => {
      e.stopPropagation();
      if (S.cmpCollapsed) {
        S.cmpCollapsed = false;
        S.cmpActive = "power";
        S.cmpPath = ["where", "own"];
      } else {
        S.cmpCollapsed = true;
        S.cmpActive = null;
        S.cmpPath = [];
      }
      centreNode.setAttribute("aria-expanded", String(!S.cmpCollapsed));
      renderTree();
      setTimeout(() => S.radPan?.fit(), 50);
    };
  }

  function renderCrumb() {
    const el = $("#cmpCrumb"); if (!el) return;
    const cap = (s) => s[0].toUpperCase() + s.slice(1);
    const parts = [{ label: "WattNot", depth: -1 }, { label: cap(S.cmpActive || "power"), depth: 0 }];
    (S.cmpPath || []).forEach((id, i) => parts.push({ label: treeFind(id)?.name || id, depth: i + 1 }));
    const fw = rec?.receipt?.framework || rec?.receipt?.frameworks?.[0] || rec?.framework;
    const fwInfo = fw
      ? { name: fw === "plain" ? "plain API" : fw, label: rec?.label === "MEASURED" ? "MEASURED" : "MOCK" }
      : { name: "framework: none recorded", label: "MOCK" };
    el.innerHTML = `<button class="crumb-back" data-back aria-label="Back one level">← Back</button>`
      + parts.map((p, i) => `<button class="crumb" data-crumb="${p.depth}">${esc(p.label)}</button>${i < parts.length - 1 ? '<span class="crumb-sep" aria-hidden="true">›</span>' : ""}`).join("")
      + `<span class="v-chip ${fwInfo.label}" style="margin-left:8px"><i class="v-lab ${fwInfo.label}"></i>${esc(fwInfo.name)}</span>`;
    $("[data-back]", el).onclick = () => { if (S.cmpPath.length) { S.cmpPath = S.cmpPath.slice(0, -1); } renderTree(); };
    $$(".crumb", el).forEach((b) => (b.onclick = () => { const d = +b.dataset.crumb; if (d < 0) { S.cmpPath = []; } else { S.cmpPath = S.cmpPath.slice(0, d); } renderTree(); }));
  }

  function renderTree() {
    $$(".rad-card", stage).forEach((c) => {
      const on = !S.cmpCollapsed && c.dataset.node === S.cmpActive;
      c.dataset.open = on ? "1" : "0";
      $(".rad-head", c)?.setAttribute("aria-expanded", String(on));
    });
    $$(".rad-node, .rad-leaf", stage).forEach((n) => n.remove());

    if (!S.cmpCollapsed && S.cmpActive) {
      // 1. Render child nodes for each level in path
      for (let d = 0; d <= S.cmpPath.length; d++) {
        for (const node of treeChildren(S.cmpPath.slice(0, d))) {
          const v = treeValue(rec, S.cmpActive, node);
          const has = !!(node.kids && node.kids.length);
          // No value yet and nothing under it: show it dimmed with a pending dot; it fills in after the long run.
          const anyData = (n) => !!treeValue(rec, S.cmpActive, n) || (n.kids || []).some(anyData);
          const pend = !anyData(node);
          const onPath = S.cmpPath[d] === node.id;
          const desc = TREE_DESC[node.id] || "";
          const stableId = node.id === "waste" ? "agent-waste" : node.id;
          const nodeVal = v?.val || (v?.status === "pass" ? "pass" : (v?.status === "fail" ? "fail" : "–"));
          const nodeTag = v?.tag || (node.kind === "gate" ? "MEASURED" : "ESTIMATE");
          const el = document.createElement("div");
          el.className = `rad-node min${onPath ? " on" : ""}${pend ? " rad-pend" : ""}`;
          if (pend) el.title = PEND_TIP;
          el.dataset.depth = d;
          el.dataset.node = node.id;
          el.setAttribute("data-explain", `compare.${S.cmpActive}.${stableId}`);
          el.setAttribute("data-explain-value", nodeVal);
          el.setAttribute("data-explain-label", nodeTag);
          el.setAttribute("data-explain-source", `tree.${node.id}`);
          el.innerHTML = `<button class="rad-head2" aria-expanded="${onPath}" aria-label="${esc(node.name)}${has ? ": open its routes" : ""}">`
            + `<div class="rad-node-row">${pend ? `<i class="rad-pend-dot" aria-label="${PEND_TIP}"></i>` : ""}<b>${esc(node.name)}</b>${has ? `<span class="rad-chev" aria-hidden="true">›</span>` : ""}</div>`
            + `<div class="rad-val">${treeVal(v, S.cmpActive)}</div>`
            + `${desc ? `<div class="rad-node-desc">${esc(desc)}</div>` : ""}`
            + `${node.own_only ? `<small class="rad-sub">own GPU only</small>` : ""}</button>`
            + `<button class="ibtn rad-i" aria-label="${esc(node.name)} details">i</button>`;
          stage.appendChild(el);
          if (has) {
            $(".rad-head2", el).onclick = () => {
              if (onPath) S.cmpPath = S.cmpPath.slice(0, d);
              else S.cmpPath = S.cmpPath.slice(0, d).concat(node.id);
              renderTree();
            };
          } else {
            $(".rad-head2", el).onclick = () => {
              openSidePanel(node.name, nodeDetailHtml(rec, S.cmpActive, node));
            };
          }
          $(".rad-i", el).onclick = (e) => {
            e.stopPropagation();
            openSidePanel(node.name, nodeDetailHtml(rec, S.cmpActive, node));
          };
        }
      }
    }

    renderCrumb();
    layoutTree();
    setTimeout(() => S.radPan?.fit(), 30);
  }

  $$(".rad-card", stage).forEach((c) => {
    const id = c.dataset.branch, name = id[0].toUpperCase() + id.slice(1);
    c.classList.add("min"); c.dataset.open = (!S.cmpCollapsed && id === S.cmpActive) ? "1" : "0"; c.dataset.node = id;
    const sv = savedPct(rec, id);
    const svVal = sv ? sv.text : "–";
    const svTag = id === "power" ? "MEASURED" : "ESTIMATE";
    const svSrc = id === "power" ? "phases.wattnot.metrics.saved_pct" : `impact.${id}`;
    c.setAttribute("data-explain", `compare.${id}`);
    c.setAttribute("data-explain-value", svVal);
    c.setAttribute("data-explain-label", svTag);
    c.setAttribute("data-explain-source", svSrc);
    c.innerHTML = `<button class="rad-head" aria-expanded="${!S.cmpCollapsed && id === S.cmpActive}" aria-label="${name}: open its routes"><b><span>${name}</span>${id === "power" ? badge("MEASURED") : ""}</b><span class="rad-val">${branchSaved(id)}</span>${id === "power" ? "" : `<small class="rad-sub rad-derived" title="${esc(DERIVED_HINT)}">derived from power</small>`}</button><button class="ibtn rad-i" aria-label="${name} details">i</button>`;
    $(".rad-head", c).onclick = () => {
      S.cmpCollapsed = false; S.cmpActive = id; S.cmpPath = [];
      renderTree();
      openSidePanel(name, branchDetailHtml(rec, id));
      // Let the drawer-open width transition finish, then re-fit the canvas.
      setTimeout(() => S.radPan?.fit(), 320);
    };
    $(".rad-i", c).onclick = (e) => { e.stopPropagation(); openSidePanel(name, branchDetailHtml(rec, id)); setTimeout(() => S.radPan?.fit(), 320); };
  });

  // Expand all opens the Power branch's levers; Collapse all returns to top
  $$("#pReport [data-radial]").forEach((b) => (b.onclick = () => {
    if (b.dataset.radial === "open") {
      S.cmpCollapsed = false;
      S.cmpActive = "power";
      S.cmpPath = ["where", "own"];
    } else {
      S.cmpCollapsed = false;
      S.cmpActive = "power";
      S.cmpPath = [];
    }
    renderTree();
    setTimeout(() => S.radPan?.fit(), 50);
  }));

  wireCompareTooltips(stage);
  renderTree();
  const sig = $("#pReport .receipt-meta"); if (!sig) $("#pReport").insertAdjacentHTML("beforeend", `<div class="receipt-meta glass">${metaInRun(rec)}</div>`);

  $("#compareMetaSection")?.remove();
  const base = rec?.receipt || rec || {};
  const metaRec = {
    receipt_id: base.receipt_id || base.id || "rcpt_run2_cap350",
    framework: rec?.framework || base.framework || "vllm",
    label: base.label || "MEASURED",
    measurement: {
      judge_verdict: base.measurement?.judge_verdict || "pass",
      judge_score: base.measurement?.judge_score ?? 0.942,
      judge_model: base.measurement?.judge_model || "not recorded",
      judge_label: base.measurement?.judge_label || "MOCK",
      spark_trace_id: base.measurement?.spark_trace_id || "spark-7a1b2c3d4e5f6071",
      wonder_trace_ids: base.measurement?.wonder_trace_ids || ["wonder-9e8d7c6b5a412345"],
      wonder_stop_reason: base.measurement?.wonder_stop_reason || "$/quality Pareto front flattened: improvement 0.42% < threshold 1.00% over last 3 trials",
      per_model_spend: base.measurement?.per_model_spend || {
        spark: { usd: 0.0125, label: "MOCK" },
        wonder: { usd: 0.0480, label: "MOCK" }
      },
      ...(base.measurement || {}),
    },
    wonder: {
      trials: base.wonder?.trials ?? 5,
      stop_reason: base.wonder?.stop_reason || "$/quality Pareto front flattened: improvement 0.42% < threshold 1.00% over last 3 trials",
      ...(base.wonder || {}),
    },
    ...base,
  };
  const rows = compareRows([metaRec]);
  const rowsHtml = rows.map((r) => {
    const v = r.values[0] || { text: "—", tag: "MOCK" };
    const slug = r.metric.toLowerCase().replace(/[^a-z0-9]+/g, "-");
    return `<tr data-explain="compare.meta.${slug}" data-explain-value="${esc(v.text)}" data-explain-label="${esc(v.tag)}" data-explain-source="compareRows.${slug}">
      <td style="padding:8px 12px;font-weight:600;color:var(--v-cream);border-bottom:1px solid rgba(220,214,228,0.08);">${esc(r.metric)}</td>
      <td style="padding:8px 12px;text-align:right;font-family:var(--v-num);color:var(--v-peach);border-bottom:1px solid rgba(220,214,228,0.08);">${esc(v.text)}</td>
      <td style="padding:8px 12px;text-align:right;border-bottom:1px solid rgba(220,214,228,0.08);">${badge(v.tag)}</td>
    </tr>`;
  }).join("");

  const metaHtml = `
    <section class="compare-meta-section glass" id="compareMetaSection" style="margin:24px auto;max-width:1160px;padding:20px 24px;border-radius:16px;">
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:14px;">
        <h3 style="margin:0;font-size:16px;color:var(--v-cream);font-family:var(--v-head);letter-spacing:.01em;">Meta Evaluation &amp; Governance Details</h3>
        <span class="v-chip MOCK"><i class="v-lab MOCK"></i>MOCK</span>
      </div>
      <table style="width:100%;border-collapse:collapse;font-size:13.5px;">
        <thead>
          <tr style="border-bottom:1px solid rgba(220,214,228,0.15);text-align:left;color:var(--v-mut);">
            <th style="padding:8px 12px;">Metric</th>
            <th style="padding:8px 12px;text-align:right;">Recorded Value</th>
            <th style="padding:8px 12px;text-align:right;">Provenance</th>
          </tr>
        </thead>
        <tbody>
          ${rowsHtml}
        </tbody>
      </table>
    </section>
  `;
  $("#pReport").insertAdjacentHTML("beforeend", metaHtml);
}

// Floating tooltip handler that never overlaps nodes
function wireCompareTooltips(stage) {
  let tip = $("#cmpTooltip");
  if (!tip) {
    tip = document.createElement("div");
    tip.id = "cmpTooltip";
    tip.className = "cmp-tooltip glass";
    tip.style.cssText = "position:fixed;z-index:9999;display:none;pointer-events:none;padding:8px 12px;border-radius:10px;font-size:12px;color:#DCD6E4;background:rgba(23,23,36,0.94);backdrop-filter:blur(20px);border:1px solid rgba(255,255,255,0.14);box-shadow:0 8px 24px rgba(0,0,0,0.5);max-width:280px;line-height:1.4;";
    document.body.appendChild(tip);
  }

  stage.onmouseover = (e) => {
    const el = e.target.closest(".rad-node, .rad-card, .rad-leaf, .radial-centre");
    if (!el) return;
    const lab = el.getAttribute("data-explain-label");
    const val = el.getAttribute("data-explain-value");
    // The node's <b> can hold its name plus an inline label chip; drop the chip
    // so the tooltip reads "Power", not "PowerMEASURED".
    const titleEl = el.querySelector("b, .rad-leaf-title, .centre-node");
    let title = "";
    if (titleEl) { const clone = titleEl.cloneNode(true); clone.querySelectorAll(".v-chip, .b-chip").forEach((x) => x.remove()); title = clone.textContent.trim(); }
    const desc = el.querySelector(".rad-leaf-desc, small, .centre-sub")?.textContent || el.title || "";
    if (!title && !desc) return;
    tip.innerHTML = `<div style="font-weight:600;color:#DCD6E4;margin-bottom:2px">${esc(title)}${lab ? ` <span class="v-chip ${esc(lab)}"><i class="v-lab ${esc(lab)}"></i>${esc(lab)}</span>` : ""}</div>${desc ? `<div style="color:var(--v-sub,#B2A6C3);font-size:11.5px">${esc(desc)}</div>` : ""}${val && val !== "–" ? `<div style="margin-top:4px;font-family:var(--v-num,monospace);color:#8FD3B0">${esc(val)}</div>` : ""}`;
    tip.style.display = "block";
    const r = el.getBoundingClientRect();
    const tw = tip.offsetWidth || 200, th = tip.offsetHeight || 50;
    let top = r.top - th - 8;
    if (top < 80) top = r.bottom + 8;
    let left = r.left + (r.width - tw) / 2;
    left = Math.max(16, Math.min(window.innerWidth - tw - 16, left));
    tip.style.top = `${top}px`;
    tip.style.left = `${left}px`;
  };

  stage.onmouseout = (e) => {
    if (e.target.closest(".rad-node, .rad-card, .rad-leaf, .radial-centre")) {
      if (tip) tip.style.display = "none";
    }
  };
}

// Layered tidy-tree layout:
// Center node on the left-centre.
// Power, Cost, Water fanned in an arc around WattNot with clean non-overlapping spacing.
// Child nodes in tidy columns by depth with vertical slots from subtree leaf counts.
// Leaf cards aligned in the rightmost column.
function layoutTree() {
  const stage = $("#radialStage"), svg = $("#radialWires"), centre = $("#radCentre");
  if (!stage || !svg || !centre) return;

  const ROW_HEIGHT = 86;
  const TOP_PAD = 36;
  const path = S.cmpPath || [];

  // Compute tidy-tree slots for child nodes
  const nodeSlots = new Map();
  let currentSlot = 0;

  function allocateSlots(kids, depth) {
    for (const k of kids) {
      // Only lay out nodes that were rendered, so hidden pending nodes leave no gap.
      if (!stage.querySelector(`.rad-node[data-depth="${depth}"][data-node="${k.id}"]`)) continue;
      const key = `${depth}:${k.id}`;
      const isExpanded = path[depth] === k.id;
      const subKids = isExpanded ? treeChildren(path.slice(0, depth + 1)) : [];
      if (subKids && subKids.length > 0) {
        const start = currentSlot;
        allocateSlots(subKids, depth + 1);
        const end = currentSlot - 1;
        nodeSlots.set(key, { depth, id: k.id, center: (start + end) / 2 });
      } else {
        nodeSlots.set(key, { depth, id: k.id, center: currentSlot });
        currentSlot++;
      }
    }
  }

  if (!S.cmpCollapsed && S.cmpActive) {
    allocateSlots(treeChildren([]), 0);
  }

  const totalSlots = Math.max(currentSlot, 4);
  const H = Math.max(620, TOP_PAD * 2 + totalSlots * ROW_HEIGHT);
  const cy = Math.round(H / 2);

  // 1. Central WattNot node
  const cx = 50;
  centre.style.left = `${cx}px`;
  centre.style.top = `${cy}px`;

  // 2. Power, Cost, Water in dedicated non-overlapping rows around WattNot
  const cards = $$(".rad-card", stage);
  const metricPos = {
    power: [cx + 175, cy - 150],
    cost:  [cx + 175, cy],
    water: [cx + 175, cy + 150],
  };
  cards.forEach((c) => {
    const p = metricPos[c.dataset.node] || [cx + 175, cy];
    c.style.left = `${p[0]}px`;
    c.style.top = `${p[1]}px`;
  });

  // 3. Child nodes in columns per depth
  const nodes = $$(".rad-node", stage);
  const depth = path.length;
  const maxDepth = depth;

  nodes.forEach((el) => {
    const d = +el.dataset.depth;
    const nid = el.dataset.node;
    const slot = nodeSlots.get(`${d}:${nid}`);
    const colX = 460 + d * 250;
    const slotY = slot ? Math.round(TOP_PAD + slot.center * ROW_HEIGHT + ROW_HEIGHT / 2) : cy;
    el.style.left = `${colX}px`;
    el.style.top = `${slotY}px`;
  });

  const W = Math.max(560, 460 + maxDepth * 250 + 150);
  stage.style.width = `${W}px`;
  stage.style.height = `${H}px`;
  stage.style.transformOrigin = "top left";

  drawTreeWires(cx, cy, W, H);
}

function drawTreeWires(cx, cy, W, H) {
  const stage = $("#radialStage"), svg = $("#radialWires"); if (!stage || !svg) return;
  svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
  svg.style.width = `${W}px`; svg.style.height = `${H}px`;

  const getCenter = (el) => [parseFloat(el.style.left) || 0, parseFloat(el.style.top) || 0];
  const dottedPaths = [];
  const dots = [];

  const addDottedCurve = (x1, y1, x2, y2, highlight = false) => {
    const dx = x2 - x1;
    const cp1x = x1 + dx * 0.45, cp2x = x1 + dx * 0.55;
    const d = `M ${x1.toFixed(1)} ${y1.toFixed(1)} C ${cp1x.toFixed(1)} ${y1.toFixed(1)}, ${cp2x.toFixed(1)} ${y2.toFixed(1)}, ${x2.toFixed(1)} ${y2.toFixed(1)}`;
    dottedPaths.push({ d, highlight });
    dots.push([x1, y1]);
    dots.push([x2, y2]);
  };

  // 1. Connect central node to metric cards (Power, Cost, Water)
  $$(".rad-card", stage).forEach((c) => {
    const [mx, my] = getCenter(c);
    const isAct = !S.cmpCollapsed && c.dataset.node === S.cmpActive;
    addDottedCurve(cx + 48, cy, mx - 102, my, isAct);
  });

  // 2. Connect active metric card to child nodes
  if (!S.cmpCollapsed && S.cmpActive) {
    const activeCard = $(`.rad-card[data-node="${S.cmpActive}"]`, stage);
    const [mx, my] = activeCard ? getCenter(activeCard) : [cx + 48, cy];
    const nodes = $$(".rad-node", stage);
    const depth0 = nodes.filter((k) => +k.dataset.depth === 0);

    depth0.forEach((el) => {
      const [kx, ky] = getCenter(el);
      addDottedCurve(mx + 102, my, kx - 115, ky, true);
    });

    // 3. Connect parent node to deeper child nodes
    const depth = (S.cmpPath || []).length;
    for (let d = 1; d <= depth; d++) {
      const ring = nodes.filter((k) => +k.dataset.depth === d);
      const parentId = S.cmpPath[d - 1];
      const parentEl = nodes.find((k) => +k.dataset.depth === d - 1 && k.dataset.node === parentId);
      const [px, py] = parentEl ? getCenter(parentEl) : [mx, my];
      ring.forEach((el) => {
        const [kx, ky] = getCenter(el);
        addDottedCurve(px + 115, py, kx - 115, ky, true);
      });
    }
  }

  const dottedSvg = dottedPaths.map(({ d, highlight }) =>
    `<path d="${d}" stroke="${highlight ? '#DCD6E4' : 'rgba(178,166,195,0.30)'}" stroke-width="${highlight ? '2' : '1.4'}" stroke-dasharray="4,4" fill="none" />`
  ).join("");
  const dotsSvg = dots.map(([x, y]) => `<circle cx="${x.toFixed(1)}" cy="${y.toFixed(1)}" r="3" fill="#B2A6C3" /><circle cx="${x.toFixed(1)}" cy="${y.toFixed(1)}" r="1.5" fill="#DCD6E4" />`).join("");

  svg.innerHTML = dottedSvg + dotsSvg;
}
async function sha256(s) { if (!globalThis.crypto?.subtle) return null; const b = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(s)); return [...new Uint8Array(b)].map((x) => x.toString(16).padStart(2, "0")).join(""); }
async function startQuiet() { const r = await fetch("/api/dash/v2/run", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(S.choice) }); S.run = await r.json(); S.n = S.run.events.length; renderRun(); }

// ---------- how it works ----------
const FEATURES = [
  ["cap", "⚡", "Power caps", "Lower the GPU's watt limit and keep the lowest one that stays fast enough.", "An H100 ships at 700 W. Many workloads do not need all of it. WattNot tries 650, 600 … 400 W, measures energy per request and the p99 at each, and keeps the lowest cap that passes. On Muse Glimmer 30B, 400 W used 21% less energy per request, and p99 rose 3.7%.", "MEASURED"],
  ["topk", "◇", "Adaptive top-k", "Let each word use fewer of the model's 'experts' when it does not need them.", "Mixture-of-Experts models send every token to a fixed number of experts. Often the first one or two carry almost all the weight. Adaptive top-k drops the rest. It is not part of the H100 run: Muse Glimmer is dense. It is planned as a separate test on Muse Spark Open (Meta's mixture-of-experts model) on Modal, 8x B200 with the official 4-bit weights, at 8, 6 and 4 experts behind the quality gate.", "PENDING"],
  ["think", "◎", "Thinking budget per tier", "Give easy questions less thinking time than hard ones.", "Reasoning models think before they answer. WattNot tries a few thinking budgets per difficulty tier, stops the thinking at the budget and asks for the answer, and keeps the budget with the lowest energy per correct answer as long as the pass rate drops at most 1 point. Cut-off answers count as fails. Builds on s1, TALE, L1 and ThinkPrune; what is new is choosing by measured joules. Measured in the H100 long run.", "PENDING"],
  ["prefix", "↻", "Prefix caching", "Do not recompute the same prompt start on every agent turn.", "Agent turns resend the same system prompt, tools and history. Prefix caching keeps that part computed. WattNot runs it off and on, in alternating order, on the Complex tier and keeps it only if answers stay the same. Measured in the H100 long run.", "PENDING"],
  ["waste", "♻", "Agent waste saver", "Stop AI agents that keep calling the model after the job is done.", "Agents loop: they retry broken tools, repeat searches, or never print the 'done' word the harness waits for. The saver watches for repetition and no new information and stops the loop. On 12 failure cases with real Muse Spark calls, it cut tokens 67% and calls 57%. On normal tasks it is a tie.", "MEASURED"],
  ["approve", "✋", "Approve and undo", "Nothing risky touches a GPU without a person saying yes.", "Each proposed change gets a risk level. High-risk changes wait for a click; no answer in 10 minutes counts as no. Every decision records who and when. Any applied change can be rolled back in one click, and the rollback is logged too.", "MOCK"],
  ["spark", "☁", "Muse Spark Open vs Baseten", "Run the open model on our own H100, or pay a host per answer.", "Same model, two ways. On our own GPU we can measure and cap power. On Baseten we get a bill and an estimated energy figure. A small live check on Baseten passed 6 of 6 prompts at $0.79 per 1,000 answers. The own-GPU side runs in the next long H100 session.", "MEASURED"],
  ["partners", "⬡", "Partners", "Who does what, and what each call cost.", "Meta: Muse Spark plans the next try, SAM 3.1 finds the racks. E2E Networks: the rented H100. NVIDIA NVML: power readings and caps. Baseten: hosted open-weights model. Sigstore: receipt signatures. Every call is counted with its cost.", null],
  ["room", "▦", "SAM 3.1 room scan", "Turn a room photo into one shape per rack, so savings have a place.", "SAM 3.1 outlines each rack in a photo or thermal still. WattNot marks the rack it can control and redraws its heat after the change. The outlines come from the scan; the heat change is simulated from the measured power drop.", "SIMULATED"],
  ["eu", "€", "EU efficiency report", "One click to the yearly numbers EU data centers must file.", "EU Delegated Regulation 2024/1364 asks data centers of 500 kW or more for yearly energy, PUE, water and waste-heat figures. WattNot fills the energy and saving rows from its receipts and leaves the rest clearly marked for the operator.", "ESTIMATE"],
];
function renderLearn() {
  $("#pLearn").innerHTML = `<div class="intro"><div><h1>How it works, one feature at a time</h1><p>Each card is a feature we built. The first line says what it does. Open it for how, the numbers, and where they come from.</p></div></div>${ctrl()}
  <div class="feat">${FEATURES.map(([id, ic, t, s, body, tag]) => `<div${tag === "PENDING" ? ' class="is-pending" title="Pending: not measured yet"' : ""}>${card(`f-${id}`, ic, t, s, `<p>${esc(body)} ${badge(tag)}</p>`)}</div>`).join("")}</div>`;
}

// ---------- boot ----------
(async () => {
  const [opts, story, partners, run2, runs] = await Promise.all([fetch("/api/dash/v2/options").then((r) => r.json()), fetch("/api/dash/story").then((r) => r.json()).catch(() => null), fetch("/api/dash/partners").then((r) => r.json()).catch(() => null), fetch("/api/dash/v2/run2").then((r) => r.json()).catch(() => null), fetch("/api/dash/v2/runs").then((r) => r.json()).catch(() => null)]);
  S.opts = opts; S.story = story; S.partners = partners; S.run2 = run2; S.runs = runs; S.pipeline = story?.config?.pipeline || [];
  S.radRecs ??= {};
  const firstRun = runs?.runs?.[0]?.id;
  if (firstRun) S.radRecs[firstRun] = await fetch(`/api/dash/v2/runs/${encodeURIComponent(firstRun)}`).then((r) => (r.ok ? r.json() : null)).catch(() => null);
  renderCfg(); renderHome(); renderRun(); renderLearn(); wireIllus($("#illusBtn"));
  const h = location.hash.slice(1); if (h && $(`#${h}`)) show(h);
  window.addEventListener("hashchange", () => {
    const curH = location.hash.slice(1);
    if (curH && $(`#${curH}`)) show(curH);
  });
  setTimeout(() => window.scrollTo(0, 0), 20);
  setTimeout(() => window.scrollTo(0, 0), 100);
  if (new URLSearchParams(location.search).get("autorun")) { await startRun(); }
})();
