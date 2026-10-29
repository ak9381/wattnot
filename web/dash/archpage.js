// The Architecture deep dive: the whole map, a partner filter (with "Meta did this"),
// replay of recorded runs, live following of an events file, and the side panel.
import { ArchView, fmt, panelHtml, statHtml } from "./arch.js";
import { init as initPan } from "./pancanvas.js";
import { expertStrip } from "./expertstrip.js";
import { openThermal } from "./thermal.js";
import { wireR7, infoBtn } from "./r7.js";
import "./v2r7.js";
import { esc, dot, numLab, legendHtml } from "./theme.js";

// ---- live meter strip: power, cost and water per request scale, straight from the bundle.
// Every value comes from the recorded runs, the tuning ledger or a stated published factor;
// a value with no measurement is null and renders as "–" with no badge.
const JH = 3600; // joules per watt-hour
const mmMoney = (x) => (x < 100 ? `$${x.toFixed(2)}` : "$" + Math.round(x).toLocaleString("en-US"));

// Which stage of a replay the meter shows: 0 baseline, 1 baseline seen in the run, 2 tuned.
// The markers are the run's own words: the first per-request reading, then the tuned one.
export function meterStage(cues, shown) {
  const t = cues.slice(0, Math.max(0, shown)).map((c) => c.text).join("\n");
  if (/Kept the cheapest|400 W: [\d.]+ mWh\/request/.test(t)) return 2;
  if (/mWh\/request|\$[\d.,]+ for \d+ requests/.test(t)) return 1;
  return 0;
}

// Meter tiles for one replay: power (W and J per request), cost ($ per 1M requests) and
// water (L per 1M requests, ESTIMATE from the bundle's stated WUE factor).
export function meterFor(d, id) {
  const wue = d.story.water.factor_l_per_kwh, rate = d.story.price.usd_per_kwh;
  const foot = `water = GPU kWh × WUE ${wue} L/kWh (on-site cooling, ESTIMATE); up to 1.8 L/kWh including power-plant water (ESTIMATE) · electricity $${rate}/kWh (EIA) · – = not metered, never guessed`;
  if (id === "hosted") {
    const L = d.story.levers.find((l) => l.id === "hosted"), e = L.energy_wh_per_75;
    const kwh = (wh75) => (wh75 / 75) * 1e6 / 1000;
    return { title: d.replays.hosted.title, lever: L, foot, tiles: [
      { k: "power", label: "Power · per request", before: `${((e.high / 75) * JH).toFixed(1)} J`, after: `${((e.low / 75) * JH).toFixed(1)} J`, tag: "ESTIMATE", sub: "hosted GPUs are not metered — modeled energy range" },
      { k: "cost", label: "Cost · per 1M requests", before: mmMoney(L.usd_before * 1e6), after: mmMoney(L.usd_after * 1e6), tag: "MEASURED", sub: "tuning ledger, per-request rate × 1M" },
      { k: "water", label: "Water · per 1M requests", before: `${(kwh(e.high) * wue).toFixed(1)} L`, after: `${(kwh(e.low) * wue).toFixed(1)} L`, tag: "ESTIMATE", sub: `GPU kWh × WUE ${wue} L/kWh (on-site cooling); up to 1.8 L/kWh incl. power-plant water` },
    ] };
  }
  const L = d.story.levers.find((l) => l.id === "cap");
  const pw = d.nodes.find((n) => n.id === "telemetry")?.stats.find((s) => s.k === "probe_w");
  const kwh = (wh) => wh * 1000;
  return { title: d.replays.h100.title, lever: L, foot, tiles: [
    { k: "power", label: "Power · per request", before: `${(L.wh_before * JH).toFixed(1)} J`, after: `${(L.wh_after * JH).toFixed(1)} J`, tag: "MEASURED", sub: pw ? `probe mean ${fmt(pw.v)} W · NVML` : "measured GPU energy (NVML)" },
    { k: "cost", label: "Cost · per 1M requests", before: mmMoney(kwh(L.wh_before) * rate), after: mmMoney(kwh(L.wh_after) * rate), tag: "ESTIMATE", sub: `measured GPU kWh × $${rate}/kWh` },
    { k: "water", label: "Water · per 1M requests", before: `${(kwh(L.wh_before) * wue).toFixed(1)} L`, after: `${(kwh(L.wh_after) * wue).toFixed(1)} L`, tag: "ESTIMATE", sub: `GPU kWh × WUE ${wue} L/kWh (on-site cooling); up to 1.8 L/kWh incl. power-plant water` },
  ] };
}
import { Product } from "./product.js";
import { mountRoomScan } from "./roomnode.js";
import { createState, replayAll, groupTotals, jPerSlot } from "./core.js";
import { heatSvg, heatLegend, wireHeat } from "./heat.js";
import { clock } from "./replayclock.js";
import { broadcast } from "./clockbus.js";

// The two phases of every replay: first the stock baseline without WattNot,
// then the full run with it. Shown in the toolbar and the meter strip.
export const PHASE_LABEL = { 1: "Phase 1 of 2 · Without WattNot (baseline)", 2: "Phase 2 of 2 · With WattNot" };

const $ = (s, r = document) => r.querySelector(s);
const G = () => window.gsap;
const FILTERS = [["all", "Everything", null], ["meta", "Meta did this", "var(--m)"], ["baseten", "Baseten", "var(--bt)"], ["e2e", "E2E Networks", "var(--e2)"], ["hf", "Hugging Face", "var(--hf)"], ["nvidia", "NVIDIA", "var(--nv)"], ["sigstore", "Sigstore", "var(--sg)"]];

export function livePhrase(ev) {
  const d = ev.data || {};
  switch (ev.type) {
    case "run.started": return `Run started on ${d.host?.gpu || "a GPU"}${d.label ? ` (${d.label})` : ""}`;
    case "telemetry.sample": return `Power read: ${d.power_w} W (${d.source || "meter"})`;
    case "step.started": return d.label || "Step started";
    case "step.progress": return d.message || `${d.pct}%`;
    case "trial.proposed": return "Trial proposed";
    case "trial.measured": return `Trial ${d.trial_no}: ${d.cap_w ?? "–"} W, ${d.wh_per_req?.mean ?? "–"} Wh per request, p99 ${d.p99_ms ?? "–"} ms`;
    case "decision": return d.reason || "Decision made";
    case "gate.result": return `Gate ${d.check}: ${d.passed ? "passed" : "failed"}`;
    case "action.applied": return `Applied ${JSON.stringify(d.change || {})}`;
    case "action.rolled_back": return `Rolled back ${JSON.stringify(d.change || {})}`;
    case "receipt.signed": return `Receipt ${d.receipt_id} signed (${d.grade || "grade –"})`;
    case "recommendations": return d.verdict || "Recommendations";
    default: return ev.type;
  }
}

export class ArchPage {
  constructor(host, data, hooks) {
    wireR7();
    this.host = host; this.d = data; this.h = hooks; this.speed = 2; this.replay = "h100"; this.filter = null;
    this.phase = 1; this._p0 = 0; this._p1 = 1;
    host.innerHTML = `
      <div id="aall">
        <div class="mmeter" id="mmeter" aria-live="polite"></div>
        <div class="avlegend" id="afil" role="group" aria-label="filter by partner"></div>
        <div class="mstrip" id="mstrip" hidden></div>
        <div class="archcap" id="acap" aria-live="polite">Click a step to see its code, inputs, outputs, timing and partner.</div>
        <section id="aexpertBox" hidden aria-label="expert details"></section>
        <section class="aroom" id="aroom" hidden aria-label="room scan"><h3>Room scan · SAM 3.1 <span id="aroomTag"></span></h3><div id="aroomBody"></div></section>
        <div id="amap"></div>
      </div>
      <aside class="panel in-drawer" id="apanel" aria-label="step details"><button class="btn x" id="apx" aria-label="close details">Close</button><div id="apbody"></div></aside>`;
    const rs = Object.values(data.replays);
    if ($("#rsel", host)) $("#rsel", host).innerHTML = rs.map((r) => `<option value="${r.id}">${esc(r.title)}</option>`).join("") + `<option value="live">Live run (events file)</option>`;
    $("#afil", host).innerHTML = FILTERS.map(([id, t, c]) => `<button class="fchip ${id === "meta" ? "meta" : ""}" data-f="${id}" ${c ? `style="--c:${c}"` : ""} aria-pressed="${id === "all"}">${c ? "<i></i>" : ""}${esc(t)}${id === "meta" ? ` <span class="muted">${data.nodes.filter((n) => n.partners.includes("meta")).length} steps</span>` : ""}</button>`).join("");
    $("#mstrip", host).innerHTML = data.meta.map((m) => `<div class="mcard"><b>${esc(m.name)}</b><p>${esc(m.does)}</p>${m.stats.slice(0, 3).map((s) => statHtml(s)).join("")}</div>`).join("");
    this.view = new ArchView($("#amap", host), data, { mode: "map", follow: false, onSelect: (n, s) => this.panel(n, s), onCue: (c) => { $("#acap", host).innerHTML = `<b>${esc(this.d.nodes.find((n) => n.id === c.node).title)}</b> · ${esc(c.text)}${c.tag ? dot(c.tag) : ""}`; if (this.pan && this.pan.getFollow()) { const el = this.view.el(c.node); if (el) this.pan.focus(el); } broadcast({ type: "tick", view: "arch", phase: this.phase, step: `${c.node}.${c.sub}`, text: c.text, archActive: this.d.nodes.findIndex((n) => n.id === c.node) % 8 }); }, onProgress: (i, n) => { $("#aprog", host) && ($("#aprog", host).textContent = i >= n ? "done" : `${i + 1} of ${n}`); this.renderMeter(i); clock.setProgress(this._p0 + (this._p1 - this._p0) * (i / Math.max(1, n))); } });
    this.pan = initPan($("#amap", host), this.view.root, { goto: () => { const id = this.view.cur; if (id) { const el = this.view.el(id); if (el) this.pan.focus(el, { zoom: Math.max(this.pan.state.scale, 1) }); } } });
    { const _le = this.view.layoutEdges.bind(this.view); this.view.layoutEdges = () => { _le(); this.pan?.refreshMinimap(); }; }
    this._fitted = false;
    this.wire(); this.mode = "all"; this.liveW = null; this.renderMeter(0);
    // The live expert card nested in the Adaptive top-k block follows the same
    // replay clock (progress 0..1 across both phases) as the map animation.
    this._expertProgress = 0;
    this._unsubClock = clock.subscribe((ev) => this.onClockExpert(ev));
  }
  // Paint the Adaptive top-k block's nested expert bars from the shared replay
  // clock: progress fraction -> routed ticks so far -> per-group shares.
  async onClockExpert(ev) {
    if (ev.type !== "progress" && ev.type !== "reset") return;
    if (!this.view || !this.view.ids.has("adaptive")) return;
    this._expertProgress = ev.type === "reset" ? 0 : (ev.progress || 0);
    let data; try { data = await this.ensureExperts(); } catch { return; }
    const target = Math.min(data.ticks.length, Math.floor(this._expertProgress * data.ticks.length));
    const st = createState(data.meta, this.expertMode || "tier");
    replayAll(st, data.ticks.slice(0, target));
    this.view.paintExpertStrip("adaptive", expertStrip(data, target));
    this.view.layoutEdges();
  }
  setMode(m) {
    this.mode = m; const H = this.host; this.closePanel();
    H.querySelectorAll(".archmode button").forEach((b) => { const on = b.dataset.m === m; b.classList.toggle("on", on); b.setAttribute("aria-pressed", on); });
    $("#apipe", H).hidden = m !== "pipe"; $("#aall", H).hidden = m !== "all";
    $("#amodeNote", H).textContent = m === "pipe" ? "The real steps of one run, lit up as its events arrive. Click a step for its sub-steps and numbers." : "Every module in the system, with who built it. Pick a replay to watch it.";
    if (m === "pipe") this.ensurePipe(); else requestAnimationFrame(() => { this.view.layoutEdges(); this.fitOnce(); });
  }
  // The run pipeline: the same run view as the product, mounted here and started on the default H100 settings.
  ensurePipe() {
    if (this.pipe || !this.d.config) return;
    const open = (n, s, v, extra) => this.panel(n, s, v, extra);
    this.pipe = new Product($("#apipe", this.host), null, { bundle: this.d, health: this.h.health || {}, open, drawer: this.h.openDrawer }, { embed: true });
  }
  wire() {
    const H = this.host;
    H.querySelectorAll(".archmode button").forEach((b) => (b.onclick = () => this.setMode(b.dataset.m)));
    const aplay = $("#aplay", H); if (aplay) aplay.onclick = () => this.start();
    const apause = $("#apause", H); if (apause) apause.onclick = (e) => { const b = e.currentTarget; if (this.view.paused) { this.view.resume(); b.textContent = "Pause"; } else { this.view.pause(); b.textContent = "Resume"; } if (this.replay !== "live") clock.setPlaying(!this.view.paused); };
    const areset = $("#areset", H); if (areset) areset.onclick = () => { this.stopLive(); this.view.reset(); this.liveW = null; this._p0 = 0; this._p1 = 1; this.setPhase(1); clock.reset(); $("#aprog", H) && ($("#aprog", H).textContent = ""); $("#apause", H) && ($("#apause", H).textContent = "Pause"); $("#acap", H) && ($("#acap", H).textContent = "Reset. Pick a replay and press Play."); };
    const aexpand = $("#aexpand", H); if (aexpand) aexpand.onclick = () => this.view.expandAll();
    const acollapse = $("#acollapse", H); if (acollapse) acollapse.onclick = () => this.view.collapseAll();
    const rsel = $("#rsel", H); if (rsel) rsel.onchange = (e) => { this.replay = e.target.value; this.view.reset(); this.liveW = null; this.phase = 1; $("#aphase", H) && ($("#aphase", H).textContent = PHASE_LABEL[1]); this.view.setPhase(1); this.renderMeter(0); if (this.replay === "live") this.startLive(); };
    H.querySelectorAll("#aspd button").forEach((b) => (b.onclick = () => { this.speed = +b.dataset.s; H.querySelectorAll("#aspd button").forEach((x) => x.classList.toggle("on", x === b)); }));
    H.querySelectorAll("#afil button").forEach((b) => (b.onclick = () => this.setFilter(b.dataset.f)));
    const apx = $("#apx", H); if (apx) apx.onclick = () => this.closePanel();
    const aexpert = $("#aexpert", H); if (aexpert) aexpert.onclick = (e) => this.setExpert(e.currentTarget.getAttribute("aria-pressed") !== "true");
    $("#aexpertBox", H)?.addEventListener("click", (e) => {
      const b = e.target.closest("#aexpertMode button"); if (!b) return;
      this.expertMode = b.dataset.mode; this.renderExperts();
    });
    $("#apanel", H)?.addEventListener("click", (e) => {
      const go = e.target.closest("[data-go]"); if (go) { this.followGo(go.dataset.go); return; }
      const sb = e.target.closest("a[data-sub]"); if (sb) { e.preventDefault(); if (this.mode === "pipe") { const sub = this.cur.subs.find((x) => x.id === sb.dataset.sub); this.panel(this.cur, sub, this.pv, this.pipe?.runBlock(this.cur, sub) || ""); } else this.view.select(this.cur.id, sb.dataset.sub); }
    });
  }
  // The meter strip follows the replay: baseline until the run's own readings arrive,
  // tuned numbers once the run reaches them. `shown` is how many cues have played.
  renderMeter(shown = 0) {
    const box = $("#mmeter", this.host); if (!box) return;
    if (this.replay === "live") {
      const w = this.liveW;
      box.innerHTML = `<div class="mm-head">Live meters${dot("LIVE")} <span class="muted">waiting for the run's events</span>${legendHtml()}</div>
        <div class="mm-grid">
        <div class="mm"><span class="mk">Power · now</span><span class="mv num${numLab(w == null ? "" : "MEASURED")}" data-mv="power">${w == null ? "–" : `${fmt(w)} W`}</span>${w == null ? "" : dot("MEASURED")}<span class="ms">${w == null ? "waiting for telemetry.sample…" : "live meter (NVML)"}</span></div>
        <div class="mm"><span class="mk">Cost · per 1M requests</span><span class="mv num">–</span><span class="ms">totals at run end</span></div>
        <div class="mm water"><span class="mk">Water · per 1M requests</span><span class="mv num">–</span><span class="ms">totals at run end</span></div>
        </div><span class="mm-foot">– = not metered, never guessed</span>`;
      return;
    }
    const r = this.d.replays[this.replay]; if (!r) { box.innerHTML = ""; return; }
    // The meter follows the phase, not the cue count: Phase 1 shows the
    // baseline numbers, Phase 2 the WattNot numbers with the saving vs baseline.
    const m = meterFor(this.d, this.replay), st = this.phase === 1 ? 0 : 2;
    const note = this.phase === 1 ? "baseline (stock settings)" : (m.lever.saved_pct != null ? `−${m.lever.saved_pct}% vs baseline` : "with WattNot");
    box.innerHTML = `<div class="mm-head">Live meters${dot("REPLAY")} <span>${esc(m.title)}</span><span class="muted">· ${esc(note)}</span><span class="muted">· ${esc(PHASE_LABEL[this.phase])}</span>${legendHtml()}</div>
      <div class="mm-grid">${m.tiles.map((t) => {
        const showDot = t.tag && !((t.k === "cost" || t.k === "water") && t.tag === "ESTIMATE");
        return `<div class="mm${t.k === "water" ? " water" : ""}"><span class="mk">${esc(t.label)}</span><span class="mv num${numLab(t.tag)}">${esc(st >= 2 ? t.after : t.before)}</span>${showDot ? dot(t.tag) : ""}<span class="ms">${esc(t.sub)}</span></div>`;
      }).join("")}</div>
      <span class="mm-foot">${esc(m.foot)}</span>`;
  }
  setFilter(f) {
    this.filter = f === "all" ? null : f; this.view.setFilter(this.filter);
    this.host.querySelectorAll("#afil button").forEach((b) => b.setAttribute("aria-pressed", b.dataset.f === f));
    $("#mstrip", this.host).hidden = f !== "meta";
    if (f === "meta") $("#acap", this.host).innerHTML = `<b>Meta did this:</b> ${this.d.nodes.filter((n) => n.partners.includes("meta")).map((n) => `${esc(n.title)} (${esc(n.meta_product)})`).join(" · ")}`;
    else if (f && f !== "all") $("#acap", this.host).innerHTML = `Highlighting every step ${esc(this.d.partners[f].name)} did real work in.`;
    requestAnimationFrame(() => this.view.layoutEdges());
  }
  // Every replay runs in two phases: Phase 1 lights only the baseline blocks
  // (load, meter, rig), Phase 2 the full path with WattNot. The shared clock
  // carries play/pause/progress/phase to the Power and Experts tabs.
  setPhase(p) {
    this.phase = p;
    $("#aphase", this.host).textContent = PHASE_LABEL[p];
    this.view.setPhase(p);
    this.renderMeter(0);
    clock.setPhase(p);
    broadcast({ type: "phase", view: "all", phase: p, step: null, text: PHASE_LABEL[p] });
  }
  baselineCues(cues) {
    const base = new Set(this.d.nodes.filter((n) => n.baseline).map((n) => n.id));
    if (!base.size) return [];
    return cues.filter((c) => base.has(c.node));
  }
  async start() {
    this.stopLive(); if (this.replay === "live") return this.startLive();
    const r = this.d.replays[this.replay]; $("#acap", this.host).innerHTML = esc(r.note); $("#apause", this.host).textContent = "Pause";
    clock.reset();
    const first = this.baselineCues(r.cues);
    clock.setPlaying(true);
    if (first.length && first.length < r.cues.length) {
      this._p0 = 0; this._p1 = 0.5; this.setPhase(1);
      if (!(await this.view.play(first, { speed: this.speed, phase: 1 }))) return;
      this._p0 = 0.5; this._p1 = 1; this.setPhase(2);
      await this.view.play(r.cues, { speed: this.speed, phase: 2 });
    } else {
      this._p0 = 0; this._p1 = 1; this.setPhase(2);
      await this.view.play(r.cues, { speed: this.speed, phase: 2 });
    }
    clock.setPlaying(false);
  }
  panel(n, s, view = this.view, extra = "") {
    this.cur = n; this.pv = view; const p = $("#apanel", this.host); const h = panelHtml(view, n, s);
    $("#apbody", this.host).innerHTML = extra ? h.replace(/(<h3>.*?<\/h3>)/, `$1${extra}`) : h;
    p.classList.add("open"); this.host.classList.add("pn"); $("#apx", this.host).focus();
    if (n.id === "sam") this.loadRoomInto($("#apbody", this.host));
    if (n.id === "adaptive") this.loadExpertsInto($("#apbody", this.host));
  }
  closePanel() { $("#apanel", this.host).classList.remove("open"); this.host.classList.remove("pn"); }
  // Card links stay inside Architecture: experts enables the Expert toggle,
  // room opens the rack map, meta filters to Meta's steps.
  followGo(go) {
    if (go === "meta") return this.setFilter("meta");
    if (go === "experts") return this.setExpert(true);
    if (go === "room") return this.openRoom();
    return this.h.openDrawer("arch");
  }
  // ---- Expert toggle: the merged Experts view, on the Adaptive top-k card's numbers.
  // Grouping and energy reuse the expert content (core.js over /api/dash/moe/meta+ticks).
  async setExpert(on) {
    this.expertOn = on;
    $("#aexpert", this.host).setAttribute("aria-pressed", String(on));
    $("#aexpert", this.host).classList.toggle("on", on);
    const box = $("#aexpertBox", this.host);
    if (!on) { box.hidden = true; return; }
    if (this.mode !== "all") this.setMode("all");
    box.hidden = false;
    await this.renderExperts();
    if (this.cur?.id !== "adaptive") this.view.select("adaptive");
  }
  async ensureExperts() {
    if (this.expertCache) return this.expertCache;
    const [meta, ticks] = await Promise.all([fetch("/api/dash/moe/meta").then((r) => r.json()), fetch("/api/dash/moe/ticks").then((r) => r.json())]);
    this.expertCache = { meta, ticks };
    return this.expertCache;
  }
  async renderExperts() {
    const box = $("#aexpertBox", this.host);
    box.innerHTML = `<p class="muted">Loading the expert routing…</p>`;
    try {
      const { meta, ticks } = await this.ensureExperts();
      const mode = this.expertMode || "tier";
      const st = createState(meta, mode); replayAll(st, ticks);
      const gt = groupTotals(st, "off"), tot = gt.reduce((a, g) => a + g.routed, 0) || 1;
      const per = jPerSlot(st, "off");
      const t = meta.totals || {};
      box.innerHTML = `<h3>Experts · routed tokens per group${dot("MEASURED")}</h3>
        <p class="muted">${esc(meta.meta.model)} · captured ${esc(meta.meta.capture)} · ${esc(meta.meta.steps.length)} steps</p>${legendHtml()}
        <span class="seg" id="aexpertMode" role="group" aria-label="how experts are grouped">
          <button data-mode="tier" class="${mode === "tier" ? "on" : ""}">tiers</button><button data-mode="kmeans" class="${mode === "kmeans" ? "on" : ""}">k-means</button>
        </span>
        <div class="stl">${gt.map((g, i) => `<div class="stat"><span class="k">${esc(st.names[i])}</span><span class="v num${numLab("MEASURED")}">${((g.routed / tot) * 100).toFixed(1)}%</span>${dot("MEASURED")}<span class="mbar" aria-hidden="true"><i style="width:${Math.max(4, Math.round((g.routed / tot) * 100))}%"></i></span></div>
          <p class="muted">energy share ${per == null ? "– (no meter in this capture)" : `~${(g.routed * per).toFixed(1)} J`}${dot("ESTIMATE")}</p>`).join("")}</div>
        ${t.on && t.off ? `<p class="muted">Adaptive top-k routes to ${t.on.active_per_token.toFixed(2)} vs ${t.off.active_per_token.toFixed(2)} experts per token (on vs off)${dot("MEASURED")}</p>` : ""}
        <p class="cap">Counts are routed tokens${dot("MEASURED")}; per-group joules split the window total by routed share${dot("ESTIMATE")}.</p>`;
    } catch (e) { box.innerHTML = `<p class="muted">The expert routing could not load (${esc(e.message)}).</p>`; }
  }
  async loadExpertsInto(root) {
    const div = document.createElement("div"); div.innerHTML = `<p class="muted">Loading the expert routing…</p>`;
    root.appendChild(div);
    try {
      const { meta, ticks } = await this.ensureExperts();
      const st = createState(meta, "tier"); replayAll(st, ticks);
      const gt = groupTotals(st, "off"), tot = gt.reduce((a, g) => a + g.routed, 0) || 1;
      div.innerHTML = `<h5>Expert routing in this capture</h5>${gt.map((g, i) => `<div class="stat"><span class="k">${esc(st.names[i])}</span><span class="v num${numLab("MEASURED")}">${((g.routed / tot) * 100).toFixed(1)}%</span>${dot("MEASURED")}</div>`).join("")}
        <p><button class="btn" data-go="experts">Show expert details</button></p>`;
    } catch (e) { div.innerHTML = `<p class="muted">The expert routing could not load (${esc(e.message)}).</p>`; }
  }
  // ---- Room drill-down: the merged Power/Room view, on the Rack map (SAM 3.1) card.
  // Racks reuse the room rendering (heat.js over the served scan, or the live room_scan payload).
  openRoom() {
    if (this.mode !== "all") this.setMode("all");
    this.view.open("sam", true);
    this.view.select("sam");
    requestAnimationFrame(() => this.view.layoutEdges());
  }
  async loadRoomInto(root) {
    const div = document.createElement("div");
    div.innerHTML = `<h5>Rack map</h5><div class="aroomBody"><p class="muted">Loading the racks…</p></div>`;
    root.appendChild(div);
    let body = div.querySelector(".aroomBody");
    const pay = this.roomPay && (this.roomPay.after || this.roomPay.before);
    if (pay) {
      try {
        const host = document.createElement("div"); body.replaceWith(host);
        await mountRoomScan(host, this.roomPay, (this.roomCache ||= {}));
        return;
      } catch { body = div.querySelector(".aroomBody") || div; /* fall through to the served scan */ }
    }
    try {
      const scan = await fetch("/room/data/arsat/scan.json").then((r) => { if (!r.ok) throw new Error(`HTTP ${r.status}`); return r.json(); });
      body.innerHTML = `<p><button class="btn" data-thermal>Open thermal floor plan</button> ${infoBtn("thermal", "how the heat is made")}</p>${heatSvg(scan, {})}${heatLegend()}<p class="cap" data-cap>Hover or tab to a rack for its numbers.</p>`;
      body.querySelector("[data-thermal]").addEventListener("click", () => openThermal());
      wireHeat(body, scan, body.querySelector("[data-cap]"));
    } catch (e) { body.innerHTML = `<p class="muted">The racks could not be drawn (${esc(e.message)}).</p>`; }
  }
  // ---- live following
  async startLive() {
    const H = this.host, cap = $("#acap", H);
    let ok = true; try { const r = await fetch("/api/dash/health"); ok = (await r.json()).events; } catch { ok = false; }
    if (!ok) { cap.innerHTML = `<span class="err">No run is being followed. Start the server with <code>--events path/to/events.jsonl</code> to watch a live run here. Recorded replays work without it.</span>`; return; }
    this.view.reset(); cap.textContent = "Following the run as it writes events…";
    const es = (this.es = new EventSource(`/api/dash/arch/live?speed=${this.speed}`)); let last = 0;
    this.roomPay = {}; this.tagged = false; $("#aroom", H).hidden = true;
    es.addEventListener("event", (m) => {
      let ev; try { ev = JSON.parse(m.data); } catch { return; }
      const d = ev.data || {};
      if (d.kind === "room_scan") this.roomScan(d);                       // a room_scan payload: draw it with the room node
      if (ev.type === "telemetry.sample" && d.power_w != null) { this.liveW = d.power_w; this.renderMeter(0); }
      if (ev.type === "run.started") this.liveBadge(ev);
      if (ev.type === "run.finished") { this.view.finish(this.view.cur); this.view.cur = null; return; }
      const r = this.route(ev); if (!r) return;
      const now = performance.now();
      if (!d.node && (ev.type === "telemetry.sample" || ev.type === "step.progress") && now - last < 500) return; last = now;
      const tag = d.label && this.d.labels.includes(d.label) ? d.label : null;
      if (ev.type === "step.finished") return this.stepDone(r, ev, tag);
      this.view.activate({ node: r[0], sub: r[1], text: ev.type === "step.started" ? d.label || livePhrase(ev) : livePhrase(ev), tag: ev.type === "step.started" ? null : tag, ok: !(ev.type === "gate.result" && d.passed === false) });
    });
    es.onerror = () => { cap.innerHTML += ` <span class="muted">(connection lost, retrying)</span>`; };
  }
  // Where an event lights the map: the step's own [data.node, data.sub] when it names a node on this map,
  // otherwise the type-based table (live streams that do not tag their steps).
  route(ev) {
    const d = ev.data || {};
    if (d.node && this.view.ids.has(d.node)) { this.tagged = true; return [d.node, d.sub]; }
    if (this.tagged && ev.type === "recommendations") return null;   // the EU mapping is for untagged streams only
    return this.d.event_map[ev.type] || null;
  }
  // step.finished: mark that sub-step done and keep its timing. The node stays lit only when it is the active one,
  // so a parent step ending after its child (e2e_rig.sweep after agent.call) does not pull the map back.
  stepDone([node, sub], ev, tag) {
    const d = ev.data || {}, v = this.view, el = v.el(node), b = el?.querySelector(`.sub[data-sub="${sub}"]`);
    if (d.duration_s != null) v.timing[`${node}.${sub}`] = d.duration_s;
    if (b) { b.classList.remove("on"); b.classList.add(d.status === "ok" || d.status == null ? "done" : "bad"); }
    if (v.cur === node) { el.querySelector(".now").innerHTML = `${esc(d.summary || d.label || sub)}${tag ? dot(tag) : ""}`; }
    $("#acap", this.host).innerHTML = `<b>${esc(this.d.nodes.find((n) => n.id === node)?.title || node)}</b> · ${esc(d.summary || d.label || "")}${tag ? dot(tag) : ""}`;
  }
  liveBadge(ev) {
    const d = ev.data || {}, l = d.mode === "mock" || d.label === "MOCK" ? "MOCK" : this.h.health?.replay_only || d.replay || d.mode === "replay" ? "REPLAY" : "LIVE";
    $("#aprog", this.host).innerHTML = `${dot(l)} <span class="muted">${esc(d.run || d.model || "")}</span>`;
  }
  // The room node: a room_scan payload (before, then after) mounted with web/room/node.js (see roomnode.js).
  async roomScan(p) {
    this.roomPay[p.phase || "before"] = p; const host = $("#aroomBody", this.host), box = $("#aroom", this.host); box.hidden = false;
    try {
      const { labels } = await mountRoomScan(host, this.roomPay, (this.roomCache ||= {}));
      $("#aroomTag", this.host).innerHTML = `<span class="muted">racks</span> ${dot(labels.rack_outline || "MEASURED")} <span class="muted">heat</span> ${dot(labels.thermal_frame || "SIMULATED")}`;
    } catch (e) { host.innerHTML = `<p class="muted">The room picture could not be drawn (${esc(e.message)}).</p>`; }
  }
  stopLive() { this.es?.close(); this.es = null; }
  fitOnce() {
    if (this._fitted) return;
    const r = this.host.querySelector("#amap")?.getBoundingClientRect();
    if (!r || !r.width) return;
    this.pan.fit();
    if (this.pan.state.scale < 0.82) {
      this.pan.setZoom(0.85, r.width / 2, r.height / 2);
    }
    this._fitted = true;
  }
  show() { if (this.mode === "pipe") this.ensurePipe(); else requestAnimationFrame(() => { this.view.layoutEdges(); this.fitOnce(); }); }
  destroy() { this.stopLive(); this.pipe?.destroyRun(); this.pan?.destroy(); this._unsubClock?.(); this.view.destroy(); }
}
