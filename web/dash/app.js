import * as C from "./core.js";
import { Flow } from "./flow.js";
import { Orchestra } from "./orchestra.js";
import { Power } from "./gpu.js";
import { esc, pill, pct, topicColor, GROUP_COLORS } from "./theme.js";

const $ = (id) => document.getElementById(id);
const params = new URLSearchParams(location.search);
const app = { st: null, ticks: [], arm: params.get("arm") === "on" ? "on" : "off", speed: +(params.get("speed") || 3), playing: true,
  sel: { steps: [], group: null }, hov: { steps: null, group: null }, ready: false, live: false, cursor: 0, clock: 0, runningUntil: 0 };

// ---- tabs
function showTab(name) {
  document.querySelectorAll(".tabs button").forEach((b) => b.classList.toggle("on", b.dataset.tab === name));
  $("power").classList.toggle("on", name === "power"); $("experts").classList.toggle("on", name === "experts");
  $("expertCtl").hidden = name !== "experts";
  if (name === "experts") { initExperts(); requestAnimationFrame(() => { flow?.layoutEdges(); orch?.layout(); }); }
  history.replaceState(null, "", `?${new URLSearchParams({ ...Object.fromEntries(params), tab: name })}`);
}
document.querySelectorAll(".tabs button").forEach((b) => (b.onclick = () => showTab(b.dataset.tab)));

// ---- power
const power = new Power($("power"));
power.init().catch((e) => ($("power").innerHTML = `<p class="note2">Could not load the GPU replay: ${esc(e.message)}</p>`));

// ---- experts
let flow, orch, inited = false, ctx;

async function initExperts() {
  if (inited) return; inited = true;
  $("flowwrap").innerHTML = `<p class="note2" style="padding:20px">loading the capture…</p>`;
  const [meta, ticks] = await Promise.all([fetch("/api/dash/moe/meta").then((r) => r.json()), fetch("/api/dash/moe/ticks").then((r) => r.json())]);
  app.ticks = ticks;
  app.st = C.createState(meta, params.get("mode") === "kmeans" ? "kmeans" : "tier");
  ctx = {
    get st() { return app.st; }, get arm() { return app.arm; },
    running: () => performance.now() < app.runningUntil, live: () => app.live,
    focus: () => focus(), pickedGroup: () => app.sel.group,
    hoverGroup: (g) => { app.hov.group = g; refreshFocus(); },
    selectGroup: (g) => { app.sel.group = app.sel.group === g ? null : g; if (app.sel.group != null) app.sel.steps = []; refreshFocus(); },
    tipUnit,
  };
  flow = new Flow($("flowwrap"), ctx); orch = new Orchestra($("orch"), ctx);
  setView(params.get("view") || "both");
  $("srcChip").innerHTML = `${esc(meta.meta.model)} · routing captured on ${esc(meta.meta.capture)} ${pill("MEASURED")} · energy ${meta.energy ? "rate from H100 counter" : "n/a"} ${meta.energy ? pill("MEASURED") : ""}`;
  buildAll();
  wireControls();
  fastForwardTo(+(params.get("at") || 0));
  const health = await fetch("/api/dash/health").then((r) => r.json());
  if (health.following) goLive();
  requestAnimationFrame(loop);
  const sel = params.get("step"); if (sel != null) { app.sel.steps = sel.split(",").map(Number); refreshFocus(); }
  const grp = params.get("group"); if (grp != null) { app.sel.group = +grp; app.sel.steps = []; refreshFocus(); }
}

function buildAll() {
  buildTimeline(); flow.build(); orch.build(); orch.note(); renderLog(); refreshFocus();
  $("armBtn").setAttribute("aria-pressed", app.arm === "on"); $("armState").textContent = app.arm;
  document.querySelectorAll("#modeSeg button").forEach((b) => b.classList.toggle("on", b.dataset.mode === app.st.mode));
}

function setView(v) {
  $("experts").classList.remove("v-flow", "v-orchestra"); if (v !== "both") $("experts").classList.add(`v-${v}`);
  document.querySelectorAll("#viewSeg button").forEach((b) => b.classList.toggle("on", b.dataset.view === v));
  requestAnimationFrame(() => { flow?.layoutEdges(); orch?.layout(); });
}

function wireControls() {
  document.querySelectorAll("#modeSeg button").forEach((b) => (b.onclick = () => { C.setMode(app.st, b.dataset.mode); C.replayAll(app.st, app.ticks.slice(0, app.cursor)); app.sel.group = null; buildAll(); }));
  document.querySelectorAll("#viewSeg button").forEach((b) => (b.onclick = () => setView(b.dataset.view)));
  document.querySelectorAll("#speedSeg button").forEach((b) => (b.onclick = () => { app.speed = +b.dataset.speed; document.querySelectorAll("#speedSeg button").forEach((x) => x.classList.toggle("on", x === b)); }));
  $("playBtn").onclick = () => { app.playing = !app.playing; $("playBtn").textContent = app.playing ? "pause" : "play"; };
  $("restartBtn").onclick = () => restart();
  $("armBtn").onclick = () => { app.arm = app.arm === "off" ? "on" : "off"; restart(); };
  window.addEventListener("keydown", (e) => { if (e.key === " " && inited) { e.preventDefault(); $("playBtn").click(); } });
}

function armTicks() { return app.ticks.filter((t) => t.arm === app.arm); }

function restart() {
  C.reset(app.st); logged = { step: -1, arm: null }; app.cursor = 0; app.clock = 0; app.cursorTicks = armTicks(); app.pos = 0;
  $("log").innerHTML = ""; logLine("restart", `replay · adaptive top-k ${app.arm}`);
  buildAll();
}

// jump the run forward by n ticks without waiting (used for deep links and screenshots)
function fastForwardTo(n) {
  restart();
  const seq = app.cursorTicks;
  for (let i = 0; i < Math.min(n, seq.length); i++) consume(seq[i], false);
  app.pos = Math.min(n, seq.length); app.cursor = app.pos;
}

// ---- playback
let lastFrame = performance.now(), acc = 0, uiAcc = 0;
function consume(t, flash = true) {
  C.applyTick(app.st, t);
  if (flash) { C.bump(app.st, t); app.runningUntil = performance.now() + 1600; }
  logTick(t);
  setRunning(t.step);
}
function loop(now) {
  const dt = Math.min(0.1, (now - lastFrame) / 1000); lastFrame = now;
  C.decay(app.st, dt);
  if (app.playing && !app.live) {
    const seq = app.cursorTicks || (app.cursorTicks = armTicks());
    acc += dt * app.speed;
    while (app.pos < seq.length) {
      const t = seq[app.pos], wait = Math.min(0.5, t.dt_s || 0.1);
      if (acc < wait) break;
      acc -= wait; consume(t); app.pos++; app.cursor = app.pos;
    }
    if (app.pos >= seq.length) { app.runningUntil = 0; }
  }
  uiAcc += dt;
  orch.draw();
  if (uiAcc > 0.1) { uiAcc = 0; flow.update(); paintTimeline(); }
  requestAnimationFrame(loop);
}

// ---- live following (wattnot dash --follow file)
function goLive() {
  app.live = true; C.reset(app.st); $("log").innerHTML = ""; logLine("live", "following the capture file while the run writes it");
  const es = new EventSource("/api/dash/moe/events");
  es.addEventListener("tick", (ev) => { const t = JSON.parse(ev.data); app.ticks.push(t); if (t.arm === app.arm) consume(t); });
  es.addEventListener("end", () => { logLine("live", "run finished"); es.close(); });
  $("srcChip").insertAdjacentHTML("afterbegin", `<span class="pill MEASURED">LIVE</span> `);
}

// ---- focus (hover beats selection) and the link between the two views
function focus() {
  const g = app.hov.group ?? app.sel.group;
  const steps = app.hov.steps ?? (app.sel.steps.length ? app.sel.steps : null);
  let mask = null;
  const st = app.st;
  if (g != null) { mask = new Float64Array(st.U); for (let u = 0; u < st.U; u++) mask[u] = st.assign[u] === g ? 1 : 0; }
  else if (steps) {
    const units = C.sumSteps(st, steps, app.arm); let mx = 0;
    for (let u = 0; u < st.U; u++) mx = Math.max(mx, units[u]);
    mask = new Float64Array(st.U); for (let u = 0; u < st.U; u++) mask[u] = units[u] > 0 ? 0.06 + 0.94 * (units[u] / (mx || 1)) ** 0.8 : 0;
  }
  return { group: g, steps: g != null ? null : steps, mask };
}
function refreshFocus() {
  const f = focus();
  app.cachedFocus = f;
  ctx.focus = () => app.cachedFocus ?? focus();
  paintTimeline(); highlightLog();
  $("caption").innerHTML = captionHtml();
  flow?.update();
}
function captionHtml() {
  const st = app.st, g = app.hov.group ?? app.sel.group;
  if (g != null) return `<span><b>${esc(C.captionForGroup(st, g, app.arm))}</b></span>`;
  const steps = app.hov.steps ?? (app.sel.steps.length ? app.sel.steps : null);
  if (steps) return `<span>${esc(C.captionForSteps(st, steps, app.arm))}</span>`;
  return `<span class="muted">Hover or click a step below, a group in the flow, or a section of the stage to see what lit up and why.</span>`;
}

// ---- timeline
function buildTimeline() {
  const st = app.st, steps = st.meta.meta.steps;
  $("timeline").innerHTML = `<h3>steps · one prompt each <span class="muted" style="text-transform:none;letter-spacing:0">click to select · shift-click to extend · hover to preview</span></h3>
    <div class="steps" id="steps">${steps.map((s) => `<div class="step pending" data-i="${s.id}" style="--tc:${topicColor(s.topic)}">
      <div class="fill"></div><span class="tag" style="color:${topicColor(s.topic)}">${s.id + 1} ${esc(s.topic)}</span><div class="gshare"></div></div>`).join("")}</div>`;
  document.querySelectorAll(".step").forEach((el) => {
    const i = +el.dataset.i;
    el.onmouseenter = () => { app.hov.steps = [i]; refreshFocus(); };
    el.onmouseleave = () => { app.hov.steps = null; refreshFocus(); };
    el.onclick = (e) => {
      app.sel.group = null;
      const cur = app.sel.steps;
      if (e.shiftKey && cur.length) { const a = Math.min(cur[0], i), b = Math.max(cur[0], i); app.sel.steps = Array.from({ length: b - a + 1 }, (_, k) => a + k); }
      else app.sel.steps = cur.length === 1 && cur[0] === i ? [] : [i];
      refreshFocus();
    };
  });
}
function paintTimeline() {
  const st = app.st; if (!st) return;
  const g = app.hov.group ?? app.sel.group, share = g != null ? new Map(C.stepsUsingGroup(st, g, app.arm).map((x) => [x.step, x])) : null;
  const hl = new Set(app.hov.steps ?? []), sel = new Set(app.sel.steps), cur = st.lastTick?.step;
  const maxTok = Math.max(1, ...st.meta.meta.steps.map((s) => st.steps.get(s.id)?.[app.arm]?.tokens || 0));
  const colors = GROUP_COLORS[st.mode];
  document.querySelectorAll(".step").forEach((el) => {
    const i = +el.dataset.i, r = st.steps.get(i)?.[app.arm];
    el.classList.toggle("pending", !r); el.classList.toggle("run", cur === i && performance.now() < app.runningUntil);
    el.classList.toggle("sel", sel.has(i)); el.classList.toggle("hl", hl.has(i) || (share && share.get(i)?.lift > 1.15));
    el.querySelector(".fill").style.height = r ? `${Math.max(8, (r.tokens / maxTok) * 100)}%` : "0";
    const gs = el.querySelector(".gshare");
    if (share && share.get(i)) { gs.style.width = `${Math.round(share.get(i).share * 100)}%`; gs.style.setProperty("--gc", colors[g]); } else gs.style.width = "0";
    el.title = r ? `step ${i + 1}: ${st.meta.meta.steps[i].prompt}` : `step ${i + 1}: not run yet`;
  });
}

// ---- log
function logLine(kind, text, step) {
  const ol = $("log"); const li = document.createElement("li");
  const s = Math.round(app.clock);
  li.innerHTML = `<span class="t">${String(Math.floor(s / 60)).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}</span> ${esc(text)}`;
  if (kind === "warn") li.classList.add("warn");
  if (step != null) { li.dataset.step = step; li.onclick = () => { app.sel.group = null; app.sel.steps = [step]; refreshFocus(); };
    li.onmouseenter = () => { app.hov.steps = [step]; refreshFocus(); }; li.onmouseleave = () => { app.hov.steps = null; refreshFocus(); }; }
  ol.appendChild(li); ol.scrollTop = ol.scrollHeight; while (ol.children.length > 120) ol.removeChild(ol.firstChild);
}
function renderLog() { /* log is rebuilt by restart() */ }
let logged = { step: -1, arm: null };
function logTick(t) {
  app.clock += (t.dt_s || 0.1);
  if (t.step !== logged.step || t.arm !== logged.arm) {
    if (logged.step >= 0) endLog(logged.step, logged.arm);
    const s = app.st.meta.meta.steps[t.step];
    logLine("step", `step ${t.step + 1} · ${s.topic} · ${t.tokens} prompt tokens → router`, t.step);
    logged = { step: t.step, arm: t.arm };
  }
  if (t.energy_j == null && app.st.meta.energy == null && t.seq === 0) logLine("warn", "no energy source: counts only");
}
function endLog(step, arm) {
  const r = app.st.steps.get(step)?.[arm]; if (!r) return;
  const gt = C.groupTotals(app.st, arm, [step]);
  const top = gt.map((g, i) => [g, i]).sort((a, b) => b[0].share - a[0].share)[0];
  const e = r.joules ? `${C.fmtJ(r.joules)} J` : "";
  logLine("done", `step ${step + 1} done · ${r.gen} tokens · ${app.st.names[top[1]]} ${pct(top[0].share)}${e ? " · " + e + " est" : ""}`, step);
}
function setRunning() {}
function highlightLog() {
  const hl = new Set(app.hov.steps ?? []), sel = new Set(app.sel.steps);
  const g = app.hov.group ?? app.sel.group;
  const share = g != null ? new Set(C.stepsUsingGroup(app.st, g, app.arm).filter((x) => x.lift > 1.15).map((x) => x.step)) : null;
  document.querySelectorAll("#log li[data-step]").forEach((li) => { const s = +li.dataset.step; li.classList.toggle("sel", sel.has(s)); li.classList.toggle("hl", hl.has(s) || !!share?.has(s)); });
}

// ---- tooltip for one seat
function tipUnit(u, ev) {
  const tip = $("tip");
  if (u == null) { tip.hidden = true; return; }
  const st = app.st, arm = app.arm, L = Math.floor(u / st.E), e = u % st.E, g = st.assign[u];
  const n = st.meta.counts[arm][u], tot = st.meta.counts[arm].reduce((a, b) => a + b, 0);
  const toks = (st.meta.unit_tokens[String(u)] || []).slice(0, 4).map(([id, c]) => `${esc((st.meta.vocab[String(id)] || "").replace(/\n/g, "↵").trim() || "·")} ${c}`).join(" · ");
  const per = C.jPerSlot(st, arm);
  tip.innerHTML = `<b>layer ${L} · expert ${e}</b> · ${esc(st.names[g])}<br>routed ${n} tokens (${(100 * n / tot).toFixed(2)}%) ${pill("MEASURED")}<br>${per == null ? "" : `~${(n * per).toFixed(2)} J ${pill("ESTIMATE")}<br>`}top tokens: ${toks || "–"}`;
  tip.hidden = false;
  if (ev) { tip.style.left = `${Math.min(innerWidth - 300, ev.clientX + 14)}px`; tip.style.top = `${Math.min(innerHeight - 120, ev.clientY + 14)}px`; }
}

// ---- start
const tab = params.get("tab") || "power";
showTab(tab);
window.__app = app;
