import * as C from "./core.js";
import { Flow } from "./flow.js";
import { Orchestra } from "./orchestra.js";
import { Power } from "./gpu.js";
import { esc, pill, pct, topicColor, GROUP_COLORS } from "./theme.js";
import { Product } from "./product.js";
import { ArchPage } from "./archpage.js";
import { panelHtml } from "./arch.js";

const $ = (id) => document.getElementById(id);
const params = new URLSearchParams(location.search);
const app = { st: null, ticks: [], arm: params.get("arm") === "on" ? "on" : "off", speed: +(params.get("speed") || 3), playing: true,
  sel: { steps: [], group: null }, hov: { steps: null, group: null }, ready: false, live: false, cursor: 0, clock: 0, runningUntil: 0 };

// ---- tabs
let archPage = null, curTab = "power";
function showTab(name) {
  curTab = name;
  document.querySelectorAll(".tabs button").forEach((b) => b.classList.toggle("on", b.dataset.tab === name));
  $("power").classList.toggle("on", name === "power"); $("experts").classList.toggle("on", name === "experts"); $("arch").classList.toggle("on", name === "arch");
  $("expertCtl").hidden = name !== "experts"; $("srcChip").hidden = name === "arch";
  if (name === "experts") { initExperts(); requestAnimationFrame(() => { flow?.layoutEdges(); orch?.layout(); }); }
  if (name === "arch") ensureArch().then(() => archPage?.show());
}
function ensureArch() {
  if (archPage || !bundle) return Promise.resolve();
  archPage = new ArchPage($("arch"), bundle, { openDrawer });
  return Promise.resolve();
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
  window.addEventListener("keydown", (e) => { if (e.key === " " && inited && drawerOpen && curTab === "experts" && !e.target.closest("button,a,select,input")) { e.preventDefault(); $("playBtn").click(); } });
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

// ---- drawer
let drawerOpen = false, opener = null;
function openDrawer(tab, o = {}) {
  const d = $("drawer"); opener = document.activeElement; closePanel();
  showTab(tab);
  if (tab === "arch" && o.filter) ensureArch().then(() => archPage?.setFilter(o.filter));
  if (!drawerOpen) {
    drawerOpen = true; d.classList.add("open");
    if (window.gsap && !matchMedia("(prefers-reduced-motion: reduce)").matches && !o.instant) window.gsap.fromTo(d, { yPercent: 100, y: 0 }, { yPercent: 0, y: 0, duration: 0.45, ease: "power3.out", onComplete: () => { flow?.layoutEdges(); orch?.layout(); archPage?.show(); } });
    else { d.style.transform = "none"; }
  }
  $("dClose").focus({ preventScroll: true });
  history.replaceState(null, "", `?${new URLSearchParams({ ...Object.fromEntries(params), tab })}`);
}
function closeDrawer() {
  if (!drawerOpen) return; const d = $("drawer"); drawerOpen = false; archPage?.closePanel();
  const done = () => { d.classList.remove("open"); d.style.transform = ""; opener?.focus?.({ preventScroll: true }); };
  if (window.gsap && !matchMedia("(prefers-reduced-motion: reduce)").matches) window.gsap.to(d, { yPercent: 100, duration: 0.35, ease: "power2.in", onComplete: () => { done(); window.gsap.set(d, { clearProps: "transform" }); } }); else done();
  history.replaceState(null, "", location.pathname);
}
$("dClose").onclick = closeDrawer;
$("dPower").onclick = () => openDrawer("power"); $("dExperts").onclick = () => openDrawer("experts"); $("dArch").onclick = () => openDrawer("arch");
document.querySelectorAll(".tabs button").forEach((b) => (b.onclick = () => showTab(b.dataset.tab)));

// ---- side panel on the run screens
function closePanel() { $("spanel").classList.remove("open"); }
function openPanel(n, s, view, extra = "") {
  const h = panelHtml(view, n, s);
  $("spbody").innerHTML = extra ? h.replace(/(<h3>.*?<\/h3>)/, `$1${extra}`) : h;
  $("spanel").classList.add("open"); $("spx").focus({ preventScroll: true }); $("spanel")._ctx = { n, view, extra };
}
$("spx").onclick = closePanel;
$("spanel").addEventListener("click", (e) => {
  const go = e.target.closest("[data-go]"); if (go) { closePanel(); go.dataset.go === "meta" ? openDrawer("arch", { filter: "meta" }) : openDrawer(go.dataset.go); return; }
  const sb = e.target.closest("a[data-sub]"); if (sb) { e.preventDefault(); const c = $("spanel")._ctx; openPanel(c.n, c.n.subs.find((x) => x.id === sb.dataset.sub), c.view, product?.runBlock(c.n, c.n.subs.find((x) => x.id === sb.dataset.sub)) || ""); }
});

// ---- tooltips (water and cost factors, assumptions)
const tt = $("tt");
document.addEventListener("mouseover", (e) => { const t = e.target.closest("[data-tt]"); if (!t) return; tt.innerHTML = t.dataset.tt; tt.hidden = false; const r = t.getBoundingClientRect(); tt.style.left = `${Math.min(innerWidth - 440, Math.max(8, r.left))}px`; tt.style.top = `${Math.max(8, r.top - tt.offsetHeight - 8)}px`; if (r.top - tt.offsetHeight - 8 < 8) tt.style.top = `${r.bottom + 8}px`; });
document.addEventListener("mouseout", (e) => { if (e.target.closest("[data-tt]")) tt.hidden = true; });
document.addEventListener("focusin", (e) => { const t = e.target.closest("[data-tt]"); if (t) t.dispatchEvent(new MouseEvent("mouseover", { bubbles: true })); });

// ---- health chips
const chipSet = (el, cls, text) => { el.classList.remove("ok", "warn", "bad"); if (cls) el.classList.add(cls); el.querySelector("span").textContent = text; };
async function loadHealth() {
  try {
    const h = await fetch("/api/dash/health").then((r) => r.json());
    chipSet($("modeChip"), h.mode === "live" ? "ok" : "", h.mode === "live" ? "Live run" : `Replay of recorded runs${h.gpu_attached ? "" : " · no GPU attached"}`);
    $("modeChip").title = h.mode === "live" ? "Following a run that is writing events now" : "Every number is read from a recorded run. Nothing here needs a GPU or a network connection.";
    $("signout").hidden = !h.auth_required; return h;
  } catch { chipSet($("modeChip"), "bad", "Server not reachable"); return null; }
}
const META_TEXT = { ok: ["ok", "Meta API: connected"], key_missing: ["warn", "Meta API: reachable, no key"], key_rejected: ["bad", "Meta API: key rejected"], blocked: ["bad", "Meta API: blocked from here"], unreachable: ["bad", "Meta API: unreachable"], degraded: ["warn", "Meta API: degraded"], error: ["bad", "Meta API: check failed"] };
async function checkMeta(fresh) {
  const chip = $("metaChip"); chipSet(chip, "", "Meta API: checking…");
  let r; try { r = await fetch(`/api/dash/meta/health${fresh ? "?fresh=1" : ""}`).then((x) => { if (!x.ok) throw new Error(x.status); return x.json(); }); }
  catch (e) { r = { state: "error", steps: [], advice: "The health check could not be reached. The story still works from recorded runs.", spends_money: false }; }
  const [cls, text] = META_TEXT[r.state] || ["warn", `Meta API: ${r.state}`]; chipSet(chip, cls, text); renderPop(r); return r;
}
function renderPop(r) {
  $("pop").innerHTML = `<h4>Meta API connection</h4><ul>${(r.steps || []).map((s) => `<li class="${s.ok ? "" : "no"}"><b>${s.ok ? "ok" : "no"}</b><span>${esc(s.text)}</span></li>`).join("") || "<li><span>No steps ran.</span></li>"}</ul><p>${esc(r.advice || "")}</p><p>This check makes no model call and costs nothing. Replay mode needs no connection.</p><div class="go" style="display:flex;gap:8px;margin-top:8px"><button class="btn" id="popAgain">Check again</button><button class="btn" id="popClose">Close</button></div>`;
  $("popAgain").onclick = () => checkMeta(true); $("popClose").onclick = () => togglePop(false);
}
function togglePop(on) { $("pop").hidden = !on; $("metaChip").setAttribute("aria-expanded", on); if (on) $("popClose")?.focus(); }
$("metaChip").onclick = () => togglePop($("pop").hidden);

// ---- start the product flow
let product = null, bundle = null;
async function boot() {
  const stage = $("stage"); stage.innerHTML = "";
  const [health, sb] = await Promise.all([loadHealth(), fetch("/api/dash/story").then((r) => { if (!r.ok) throw new Error(r.status === 404 ? "The run data has not been built yet: run scripts/build_dash_arch.py." : `HTTP ${r.status}`); return r.json(); }).catch((e) => ({ error: e.message }))]);
  checkMeta(false);
  if (sb.error || !sb.config || !sb.config.models) {
    stage.innerHTML = `<div class="pf"><div class="err"><b>The run configuration could not load.</b><p>${esc(sb.error || "The bundle has no recorded run in it. Rebuild it with scripts/build_dash_arch.py.")}</p><button class="btn" id="retry">Try again</button> <button class="btn" id="goPower">Open Power instead</button></div></div>`;
    $("retry").onclick = boot; $("goPower").onclick = () => openDrawer("power"); return;
  }
  bundle = sb;
  if (curTab === "arch") ensureArch().then(() => archPage?.show());
  product = new Product(stage, $("rail"), { bundle: sb, health, open: openPanel, drawer: openDrawer });
  if (params.get("filter") === "meta" && params.get("tab") === "arch") ensureArch().then(() => archPage?.setFilter("meta"));
}
window.addEventListener("keydown", (e) => {
  if (e.key === "Escape") { if (!$("pop").hidden) return togglePop(false); if ($("spanel").classList.contains("open")) return closePanel(); if (drawerOpen) { if ($("apanel")?.classList.contains("open")) return archPage.closePanel(); return closeDrawer(); } return; }
});
window.__app = app; window.__product = () => product;
const tab = params.get("tab");
showTab(tab || "power");
if (tab) openDrawer(tab, { instant: true });
boot();
