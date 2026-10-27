// The product flow: Configure -> Run -> Results.
// Config comes from the bundle (recorded runs). A run is a stream of wattnot.event/1 events: replayed from
// POST /api/dash/run/plan (always labelled REPLAY) or followed live from --events (labelled by what the stream says).
import { ArchView, fmt, statHtml } from "./arch.js";
import { esc, pill } from "./theme.js";
import { livePhrase } from "./archpage.js";
import { mountRoomScan } from "./roomnode.js";
import { heatSvg, heatLegend, scanTag, wireHeat, pulse, recolor } from "./heat.js";

const $ = (s, r = document) => r.querySelector(s);
const RUN2_STAGE = { room_scan: "room", baseline: "baseline", quality_gate: "gate", cap_sweep: "sweep", adaptive_topk: "topk", keep_rollback: "keep", receipt: "receipt" };
const LABELS = ["MEASURED", "ESTIMATE", "SIMULATED", "MOCK", "ADVICE"];
const reduce = () => matchMedia("(prefers-reduced-motion: reduce)").matches;
const PCOL = { meta: "var(--m)", baseten: "var(--bt)", e2e: "var(--e2)", hf: "var(--hf)", nvidia: "var(--nv)", sigstore: "var(--sg)" };
const PNAME = { meta: "Meta", baseten: "Baseten", e2e: "E2E Networks", hf: "Hugging Face", nvidia: "NVIDIA", sigstore: "Sigstore" };
const num = (v, d = 2) => (v == null || Number.isNaN(v) ? "–" : Number(v).toLocaleString("en-US", { maximumFractionDigits: d, minimumFractionDigits: 0 }));
const tagOf = (t) => (LABELS.includes(t) ? pill(t) : "");
export const badge = (l) => `<span class="srcb ${esc(l)}" title="${esc({ REPLAY: "A recorded real run, re-evaluated against your limits. Nothing is running on a GPU now.", LIVE: "Events are arriving from a run that is happening now.", EXAMPLE: "An example event stream, not a real run.", MOCK: "A run against a mock backend.", WAITING: "Waiting for the first event." }[l] || l)}"><i></i>${esc(l)}</span>`;

export class Product {
  constructor(host, rail, D, opts = {}) {
    this.embed = !!opts.embed; if (this.embed) rail = document.createElement("div");
    this.host = host; this.rail = rail; this.D = D; this.b = D.bundle; this.cfg = D.bundle.config; this.health = D.health || {};
    const m0 = this.cfg.models[0];
    const lim = Object.fromEntries(Object.entries(this.cfg.limits).map(([k, v]) => [k, v.default]));
    this.sel = { place: m0.place, workload: m0.workload, model: m0.id, levers: new Set(m0.levers), room: "example", ...lim };
    this.roomImg = null;
    this.screen = "config"; this.plan = null; this.done = false; this.speed = 2; this.log = {}; this.scale = 10_000_000;
    this.renderRail(); if (this.embed) (this.health.events ? this.startLive() : this.start()); else this.config();
  }
  $(s) { return this.host.querySelector(s); }
  // ---------------------------------------------------------------- stepper
  renderRail() {
    const S = [["config", "1  Configure"], ["run", "2  Run"], ["results", "3  Results"]];
    this.rail.innerHTML = `<nav class="stepper" aria-label="steps">${S.map(([id, t]) => `<button data-s="${id}" class="${this.screen === id ? "cur" : ""}" ${this.screen === id ? 'aria-current="step"' : ""} ${this.can(id) ? "" : "disabled"}>${t}</button>`).join('<span class="stl"></span>')}</nav>
      <span class="sp"></span><span class="railnote" id="railnote"></span>`;
    this.rail.querySelectorAll("button").forEach((b) => (b.onclick = () => this.go(b.dataset.s)));
  }
  can(id) { return id === "config" || (id === "run" && !!this.plan) || (id === "results" && this.done); }
  go(id) {
    if (!this.can(id)) return;
    if (id !== "run") this.player?.pause(true);
    this.screen = id; this.renderRail();
    ({ config: () => this.config(), run: () => this.runView(true), results: () => this.results() })[id]();
  }
  // ---------------------------------------------------------------- 1. configure
  model() { return this.cfg.models.find((m) => m.id === this.sel.model); }
  config() {
    this.destroyRun();
    const c = this.cfg, s = this.sel, m = this.model();
    const places = c.places.filter((p) => c.models.some((x) => x.place === p.id));
    const wls = c.workloads.filter((w) => w.place === s.place && c.models.some((x) => x.workload === w.id));
    const models = c.models.filter((x) => x.workload === s.workload);
    const radio = (name, id, title, desc, on, extra = "") => `<label class="opt ${on ? "on" : ""}"><input type="radio" name="${name}" value="${esc(id)}" ${on ? "checked" : ""}><span class="ot">${esc(title)}${extra}</span><span class="od">${esc(desc)}</span></label>`;
    this.host.innerHTML = `<div class="pf cfg"><form class="pf-main" id="cfgForm" autocomplete="off" onsubmit="return false">
      <h1>Configure a run</h1>
      <p class="lede">Pick what to tune and how far it may go. WattNot then re-evaluates a recorded real run against your limits and shows every step, with the numbers.</p>
      <fieldset><legend><i>1</i>Where does it run?</legend><div class="opts">${places.map((p) => radio("place", p.id, p.title, p.note, p.id === s.place, `<em class="pb" style="--c:${PCOL[p.partner]}">${esc(PNAME[p.partner])}</em>`)).join("")}</div></fieldset>
      <fieldset><legend><i>2</i>What is the workload?</legend><div class="opts">${wls.map((w) => radio("workload", w.id, w.title, w.desc, w.id === s.workload)).join("")}</div></fieldset>
      <fieldset><legend><i>3</i>Which model?</legend><div class="opts">${models.map((x) => radio("model", x.id, x.name, x.meta ? `Meta · ${x.product}` : "Open model (not Meta)", x.id === s.model)).join("")}</div></fieldset>
      ${s.place === "h100" ? `<fieldset><legend><i>4</i>Which room is it in?</legend><div class="opts picker">${c.rooms.map((r) => radio("room", r.id, r.title, r.note, r.id === s.room, r.tag ? `<em class="pb" style="--c:var(--dim)">${esc(r.tag)}</em>` : "")).join("")}</div>
        ${s.room === "upload" ? `<div class="up"><input type="file" id="roomFile" accept="image/*" aria-label="a photo or infrared still of the room">${this.roomImg ? `<img class="prev" alt="your upload" src="${esc(this.roomImg)}">` : ""}</div>` : ""}
        <p class="hnt">The room scan is the first step of the run: find the racks, read their heat, check it against GPU power, pick where to start. It is a stand-in until a SAM 3.1 room module is connected (${esc(c.room_schema)}).</p></fieldset>` : ""}
      <fieldset><legend><i>${s.place === "h100" ? 5 : 4}</i>Which levers may it use?</legend><div class="opts">${c.levers.map((l) => {
        const ok = m.levers.includes(l.id);
        return `<label class="opt ${ok && s.levers.has(l.id) ? "on" : ""} ${ok ? "" : "off"}"><input type="checkbox" name="lever" value="${l.id}" ${ok && s.levers.has(l.id) ? "checked" : ""} ${ok ? "" : "disabled"}><span class="ot">${esc(l.title)}<em class="pb" style="--c:${PCOL[l.partner]}">${esc(PNAME[l.partner])}</em></span><span class="od">${ok ? esc(l.desc) : `Not available for this model: needs ${esc(l.needs)}.`}</span></label>`;
      }).join("")}</div><p class="hnt">Levers are measured one at a time and never added together.</p></fieldset>
      <fieldset><legend><i>${s.place === "h100" ? 6 : 5}</i>Limits</legend><div class="lims">
        <label>${esc(c.limits.p99_slack_pct.label)}<span class="rg"><input type="range" id="limP" min="${c.limits.p99_slack_pct.min}" max="${c.limits.p99_slack_pct.max}" step="${c.limits.p99_slack_pct.step}" value="${s.p99_slack_pct}"><output id="limPo">${s.p99_slack_pct}%</output></span></label>
        <label>${esc(c.limits.min_quality_pct.label)}<span class="rg"><input type="range" id="limQ" min="${c.limits.min_quality_pct.min}" max="${c.limits.min_quality_pct.max}" step="${c.limits.min_quality_pct.step}" value="${s.min_quality_pct}"><output id="limQo">${s.min_quality_pct}%</output></span></label>
        <label class="ck"><input type="checkbox" id="limS" ${s.strict_quality ? "checked" : ""}> ${esc(c.limits.strict_quality.label)}</label></div></fieldset>
      <div class="startrow"><button class="btn pri big2" id="go" type="button">Start replay</button>${badge("REPLAY")}
        ${this.health.events ? `<button class="btn" id="goLive" type="button">${this.health.replay_only ? "Replay the saved run" : "Watch the live run"}</button>` : ""}
        <span class="hnt" id="startnote">Re-evaluates a recorded real run against your limits. Nothing runs on a GPU now.</span></div>
      <p class="err" id="cfgErr" role="alert" hidden></p>
    </form><aside class="pf-why" id="why" aria-label="why this run is complex"></aside></div>`;
    this.whyPanel();
    const f = $("#cfgForm", this.host);
    f.onchange = (e) => {
      const t = e.target;
      if (t.name === "place") { s.place = t.value; const m1 = c.models.find((x) => x.place === s.place); Object.assign(s, { workload: m1.workload, model: m1.id, levers: new Set(m1.levers) }); this.config(); }
      else if (t.name === "workload") { s.workload = t.value; const m1 = c.models.find((x) => x.workload === s.workload); Object.assign(s, { model: m1.id, levers: new Set(m1.levers) }); this.config(); }
      else if (t.name === "model") { s.model = t.value; s.levers = new Set(this.model().levers); this.config(); }
      else if (t.name === "room") { s.room = t.value; this.config(); }
      else if (t.id === "roomFile") { const f0 = t.files?.[0]; if (f0 && f0.type.startsWith("image/")) { if (this.roomImg) URL.revokeObjectURL(this.roomImg); this.roomImg = URL.createObjectURL(f0); this.config(); } }
      else if (t.name === "lever") { t.checked ? s.levers.add(t.value) : s.levers.delete(t.value); t.closest(".opt").classList.toggle("on", t.checked); }
      else if (t.id === "limS") { s.strict_quality = t.checked; this.whyPanel(); }
    };
    $("#limP", f).oninput = (e) => { s.p99_slack_pct = +e.target.value; this.$("#limPo").textContent = `${s.p99_slack_pct}%`; this.whyPanel(); };
    $("#limQ", f).oninput = (e) => { s.min_quality_pct = +e.target.value; this.$("#limQo").textContent = `${s.min_quality_pct}%`; };
    $("#go", f).onclick = () => this.start();
    $("#goLive", f)?.addEventListener("click", () => this.startLive());
  }
  whyPanel() {
    const x = this.cfg.complexity[this.sel.model], s = this.sel, m = this.model();
    const base = x.p99_base_s, lim = base == null ? null : base * (1 + s.p99_slack_pct / 100);
    const what = m.workload === "moe" ? "slowest batch" : m.workload === "classes" ? "slowest request" : "slowest 1% of requests (p99)";
    this.$("#why").innerHTML = `<h2>Why this run is complex</h2><p class="muted">Computed from the recorded workload for ${esc(m.name)}.</p>
      <dl class="cx">${x.items.map((i) => `<div class="${i.v == null ? "na" : ""}" title="${esc(i.src || "")}${i.note ? ": " + esc(i.note) : ""}"><dt>${esc(i.label)}</dt><dd><b class="num">${i.v == null ? "–" : esc(fmt(i.v))}</b> ${i.unit ? `<span class="u">${esc(i.unit)}</span>` : ""} ${tagOf(i.tag)}${i.v == null && i.note ? `<span class="u"> ${esc(i.note)}</span>` : ""}</dd></div>`).join("")}</dl>
      <div class="target"><span class="muted">Your latency target</span><b class="num">${lim == null ? "–" : `${lim.toFixed(2)} s`}</b><span class="muted">${esc(what)} at stock ${base == null ? "–" : base.toFixed(2) + " s"}, plus ${s.p99_slack_pct}%</span></div>
      <ul class="whylist">${x.why.map((w) => `<li>${esc(w)}</li>`).join("")}</ul>`;
  }
  // ---------------------------------------------------------------- 2. run
  async start() {
    const err = $("#cfgErr", this.host) || {}, btn = $("#go", this.host) || {}; err.hidden = true;
    const s = this.sel, body = { model: s.model, levers: [...s.levers], p99_slack_pct: s.p99_slack_pct, min_quality_pct: s.min_quality_pct, strict_quality: s.strict_quality, room: s.place === "h100" ? s.room : "none" };
    if (!body.levers.length) { err.textContent = "Pick at least one lever. This model supports: " + this.model().levers.join(", ") + "."; err.hidden = false; return; }
    btn.disabled = true; btn.textContent = "Planning…";
    try {
      const r = await fetch("/api/dash/run/plan", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
      if (!r.ok) { let m = `HTTP ${r.status}`; try { m = (await r.json()).detail || m; } catch { /* keep status */ } throw new Error(m); }
      this.plan = await r.json();
    } catch (e) { err.textContent = `Could not plan this run: ${e.message}. The server may need a restart, or the story bundle needs rebuilding.`; err.hidden = false; btn.disabled = false; btn.textContent = "Start replay"; return; }
    this.live = false; this.done = false; this.screen = "run"; this.renderRail(); this.runView(false);
  }
  destroyRun() { this.player?.stop(); this.player = null; this.es?.close(); this.es = null; this.view?.destroy?.(); this.view = null; }
  runView(again) {
    this.destroyRun(); this.log = {}; this.nodeLog = {}; const live = this.live, P = this.plan;
    const events = live ? [] : P.events.map((f) => f.event);
    // the run graph is the pipeline: Room scan, Baseline, Quality gate, Cap sweep / Adaptive top-k, Keep or roll back, Receipt
    const pnodes = live ? this.cfg.pipeline : P.pipeline;
    this.pd = { ...this.b, nodes: pnodes, edges: [], event_map: this.cfg.pipe_map };
    const ids = pnodes.map((n) => n.id), room = live ? null : P.room;
    const lim = live ? null : P.config;
    this.host.innerHTML = `<div class="pf run"><div class="rbar"><span id="rbadge">${badge(live ? "WAITING" : "REPLAY")}</span>
        <span class="rt" id="rtitle">${live ? "Following the events file" : `${esc(P.results.model)} · ${esc(P.results.workload)}`}</span>
        ${live ? "" : `<span class="chipn">p99 may rise ${lim.p99_slack_pct}%</span><span class="chipn">agreement ≥ ${lim.min_quality_pct}%</span>${lim.strict_quality ? '<span class="chipn">quality must be verified</span>' : ""}`}
        <span class="sp"></span>
        ${live ? "" : `<span class="seg" id="spd" role="group" aria-label="replay speed"><button data-s="1">1×</button><button data-s="2" class="on">2×</button><button data-s="4">4×</button></span>`}
        ${this.embed && this.health.events ? `<button class="btn" id="rswitch">${live ? "Show the bundled recorded run" : "Follow the events file"}</button>` : ""}<button class="btn" id="rpause">Pause</button>${live ? "" : `<button class="btn" id="rskip">Skip to the end</button><button class="btn" id="rrestart">Restart</button>`}</div>
      <p class="rnote" id="rnote">${live ? "Waiting for the first event from the run…" : `This is a <b>replay</b> of a recorded real run (${esc(P.results.place === "h100" ? "H100 on E2E Networks" : "Baseten")}), re-evaluated against your limits. Run id ${esc(P.run_id)}. The animation is compressed; each step shows its real measured time.`}</p>
      <div class="rgrid"><section class="rgraph" aria-label="live architecture graph"><div id="rg"></div></section>
        <div class="rside">${room ? `<section class="rroom" aria-label="room heat map"><h3>Room · ${esc(room.pick === "upload" ? "your picture" : "example data center")} ${scanTag(room.scan)}<span class="muted" id="roomPhase">before</span></h3><div id="roomMap"></div>${heatLegend()}<p class="cap" id="roomCap">Hover or tab to a rack for its numbers.</p></section>` : ""}<aside class="rnar" aria-label="narration"><h3>What is happening</h3><ol id="nar" aria-live="polite"></ol>
          <div class="meter" id="meter" hidden></div>
          <details class="evs"><summary>Events <span id="evn">0</span> · wattnot.event/1</summary><ol id="evl"></ol></details></aside></div>
      </div><div class="rfoot"><span id="rstat" class="muted"></span><button class="btn pri" id="toRes" hidden>See the results</button></div></div>`;
    this.room = room; if (room) this.drawRoom(room.scan, false);
    this.view = new ArchView(this.$("#rg"), this.pd, { ids, chain: true, mode: "row", stats: 0, onSelect: (n, s, v) => this.D.open(n, s, v, this.runBlock(n, s)) });
    this.nar = this.$("#nar"); this.evl = this.$("#evl"); this.evCount = 0;
    this.$("#rswitch")?.addEventListener("click", () => { if (this.live) { this.destroyRun(); this.start(); } else this.startLive(); });
    this.$("#rpause").onclick = (e) => { const p = this.live ? null : this.player; if (this.live) { this.livePaused = !this.livePaused; } else p.pause(!p.paused); const on = this.live ? this.livePaused : p.paused; e.currentTarget.textContent = on ? "Resume" : "Pause"; this.view?.[on ? "pause" : "resume"](); };
    if (live) return;
    this.host.querySelectorAll("#spd button").forEach((b) => (b.onclick = () => { this.speed = +b.dataset.s; this.player.speed = this.speed; this.host.querySelectorAll("#spd button").forEach((x) => x.classList.toggle("on", x === b)); }));
    this.$("#rskip").onclick = () => this.player.skip();
    this.$("#rrestart").onclick = () => this.runView(true);
    this.$("#toRes").onclick = () => this.go("results");
    this.host.querySelectorAll("#spd button").forEach((b) => b.classList.toggle("on", +b.dataset.s === this.speed));
    this.player = new Player(P.events, this.speed, (ev) => this.onEvent(ev), () => this.finished());
    if (again && this.done && this.screen === "run" && this.replayDone) { /* viewing a finished run again: show it complete */ this.player.skip(); } else { this.done = false; this.replayDone = false; this.renderRail(); this.player.run(); }
  }
  finished() {
    this.done = true; this.replayDone = true; this.renderRail(); this.view?.finish?.(this.view.cur); this.view && (this.view.cur = null);
    this.$("#toRes").hidden = false; this.$("#rstat").textContent = "The run is finished."; this.$("#rpause").disabled = true; this.$("#toRes").focus({ preventScroll: true });
  }
  // one event -> graph, narration, log
  onEvent(ev) {
    const d = ev.data || {}, map = this.pd.event_map[ev.type] || [], v = this.view;
    let node = d.node || map[0], sub = d.sub || map[1];
    // the RUN2 stream tags steps with architecture-map nodes (sam, e2e_rig...) and names its stages in step_id:
    // room_scan, baseline, quality_gate, cap_sweep, adaptive_topk, keep_rollback, receipt. Those are this graph's nodes.
    const stage = RUN2_STAGE[(ev.step_id || "").split(".")[0]];
    if (stage && v?.ids.has(stage) && !v.ids.has(d.node)) { node = stage; sub = ev.step_id.includes(".") ? ev.step_id.split(".").slice(1).join(".") : d.sub; }
    this.evCount++; this.$("#evn").textContent = this.evCount;
    const li = document.createElement("li"); li.innerHTML = `<code>${esc(ev.type)}</code> <span class="muted">${esc(ev.step_id || "")}</span>`; this.evl.appendChild(li); if (this.evl.children.length > 150) this.evl.firstChild.remove();
    if (ev.type === "run.started") {
      if (this.live) { this.liveBadge(ev); if (d.room?.scan && !this.room) this.liveRoom(d.room); }
      this.say({ id: "run", text: d.narration?.text || livePhrase(ev), source: d.narration?.source, head: true });
    }
    if (ev.type === "telemetry.sample") { const m = this.$("#meter"); m.hidden = false; m.innerHTML = `Power now <b class="num">${num(d.power_w, 0)} W</b>${d.cap_w ? ` at a ${d.cap_w} W cap` : ""} ${tagOf(/mock|fake/i.test(`${d.source || ""} ${d.label || ""}`) ? "MOCK" : d.label || "MEASURED")}${/mock|fake/i.test(`${d.source || ""} ${d.label || ""}`) ? " <span class=\"muted\">fake meter</span>" : ""}${d.rack ? ` <span class="muted">rack ${esc(d.rack)}</span>` : ""} <span class="muted">${this.live ? "" : "recorded"}</span>`; }
    if (d.kind === "room_scan") this.roomPayload(d);
    const key = `${node}.${sub}`;
    const entry = node ? ((this.log[key] ||= []), this.log[key]) : null;
    if (ev.type === "step.started" && node) {
      const rec = { sid: ev.step_id, label: d.label, inputs: d.inputs, outputs: null, took: null, numbers: [], events: [], status: "running", tag: null };
      entry.push(rec); (this.nodeLog[node] ||= []).push(rec);
      v?.activate({ node, sub, text: d.label || sub, tag: null, ok: true });
      if (d.rack) this.pulseRack(d.rack);
      if (node === "receipt" && sub === "heat") this.drawRoom(this.room?.after, true);
      this.say({ id: ev.step_id, node, sub, text: d.narration?.text || d.label || livePhrase(ev), source: d.narration?.source, model: d.narration?.model, label: d.label });
      this.$("#rstat").textContent = `Running: ${d.label || ""}`;
    } else if (ev.type === "step.finished" && node) {
      const rec = entry.find((r) => r.sid === ev.step_id) || entry[entry.length - 1] || {};
      Object.assign(rec, { outputs: d.outputs, took: d.duration_s, numbers: d.numbers || [], status: d.status, tag: d.label });
      if (d.duration_s != null && v) v.timing[`${node}.${sub}`] = d.duration_s;
      v?.activate({ node, sub, text: d.summary || d.label || sub, tag: LABELS.includes(d.label) ? d.label : null, ok: d.status === "ok", took: d.duration_s });
      this.sayDone(ev.step_id, d);
    } else if (ev.step_id) {
      const rec = (this.nodeLog[node] || []).find((r) => r.sid === ev.step_id);
      if (rec) rec.events.push({ type: ev.type, data: d });
      if (d.narration?.text) this.sayUpdate(ev.step_id, d.narration);
      if (!node && ev.type !== "step.progress") this.say({ id: ev.step_id, text: livePhrase(ev) });
    } else if (!["run.started"].includes(ev.type) && ev.type !== "telemetry.sample") {
      if (ev.type === "run.finished") { this.say({ id: "fin", text: d.narration?.text || "The run finished.", head: true }); if (this.live) this.finished(); }
      else this.say({ id: `e${this.evCount}`, text: livePhrase(ev) });
    }
  }
  // a live stream may carry its room scan in run.started.data.room ({scan, after, target, pick}, wattnot.room/1)
  liveRoom(room) {
    this.room = room; const side = this.$(".rside"); if (!side || this.$("#roomMap")) return;
    const sec = document.createElement("section"); sec.className = "rroom"; sec.setAttribute("aria-label", "room heat map");
    sec.innerHTML = `<h3>Room · ${esc(room.pick === "upload" ? "your picture" : "example data center")} ${scanTag(room.scan)}<span class="muted" id="roomPhase">before</span></h3><div id="roomMap"></div>${heatLegend()}<p class="cap" id="roomCap">Hover or tab to a rack for its numbers.</p>`;
    side.prepend(sec); this.drawRoom(room.scan, false);
  }
  // a RUN2 room_scan payload (photo, SAM masks, thermal): draw it with the room module's node in the room panel
  async roomPayload(p) {
    const side = this.$(".rside"); if (!side) return;
    this.roomPay = { ...(this.roomPay || {}), [p.phase || "before"]: p };
    let sec = this.$("#roomNode");
    if (!sec) {
      this.$(".rroom")?.remove(); sec = document.createElement("section"); sec.className = "rroom"; sec.id = "roomNode"; sec.setAttribute("aria-label", "room scan");
      sec.innerHTML = `<h3>Room · SAM 3.1 <span id="roomNodeTag"></span></h3><div id="roomNodeBody"></div>`; side.prepend(sec);
    }
    try {
      const { labels } = await mountRoomScan(this.$("#roomNodeBody"), this.roomPay, (this.roomCache ||= {}));
      this.$("#roomNodeTag").innerHTML = `${tagOf(labels.rack_outline || "MEASURED")} <span class="muted">racks</span> ${tagOf(labels.thermal_frame || "SIMULATED")} <span class="muted">heat</span>`;
    } catch (e) { this.$("#roomNodeBody").innerHTML = `<p class="muted">The room picture could not be drawn (${esc(e.message)}).</p>`; }
  }
  drawRoom(scan, after) {
    const el = this.$("#roomMap"); if (!el || !scan) return;
    if (!el.firstChild) { el.innerHTML = heatSvg(scan, { target: this.room?.target, img: this.room.pick === "upload" ? this.roomImg : null }); wireHeat(el, scan, this.$("#roomCap")); }
    else recolor(el, scan);                  // same racks, new heat: the fill fades (CSS transition)
    this.$("#roomPhase").textContent = after ? "after (simulated)" : "before";
  }
  pulseRack(id) { const el = this.$("#roomMap"); if (el) pulse(el, id); const c = this.$("#roomCap"); if (c && this.room) { const r = this.room.scan.racks.find((x) => x.id === id); if (r) c.textContent = `Acting on ${r.id}.`; } }
  liveBadge(ev) {
    const d = ev.data || {}, name = this.health.events_name || "";
    const l = d.mode === "mock" || d.label === "MOCK" ? "MOCK" : this.health.replay_only ? "REPLAY" : /demo|example/i.test(name) ? "EXAMPLE" : d.replay || d.mode === "replay" ? "REPLAY" : d.mode === "mock" || d.label === "MOCK" ? "MOCK" : "LIVE";
    this.$("#rbadge").innerHTML = badge(l); this.liveKind = l;
    this.$("#rnote").innerHTML = l === "LIVE" ? "Following a run that is writing events now." : l === "REPLAY" ? "These events are a replay of a recorded run." : `These events come from ${l === "EXAMPLE" ? "an example file, not a real run" : "a mock backend"}.`;
  }
  say(o) {
    const li = document.createElement("li"); li.dataset.id = o.id; if (o.node) { li.dataset.node = o.node; li.dataset.sub = o.sub; li.tabIndex = 0; li.setAttribute("role", "button"); }
    li.className = `${o.head ? "head" : ""} cur`;
    li.innerHTML = `<span class="tx">${esc(o.text)}</span>${o.source === "muse-spark" ? `<em class="src ms">Muse Spark${o.model ? " · " + esc(o.model) : ""}</em>` : ""}<div class="nn"></div>`;
    const open = () => { const n = this.pd.nodes.find((x) => x.id === o.node); if (n) this.D.open(n, n.subs.find((s) => s.id === o.sub), this.view, this.runBlock(n, n.subs.find((s) => s.id === o.sub))); };
    if (o.node) { li.onclick = open; li.onkeydown = (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); open(); } }; }
    this.nar.querySelectorAll("li.cur").forEach((x) => x.classList.remove("cur")); this.nar.appendChild(li);
    li.scrollIntoView({ block: "nearest", behavior: reduce() ? "auto" : "smooth" });
  }
  sayUpdate(id, nar) { const li = this.nar.querySelector(`li[data-id="${CSS.escape(id)}"]`); if (!li) return; li.querySelector(".tx").textContent = nar.text; let e = li.querySelector(".src"); if (nar.source === "muse-spark") { if (!e) { e = document.createElement("em"); e.className = "src ms"; li.insertBefore(e, li.querySelector(".nn")); } e.textContent = `Muse Spark${nar.model ? " · " + nar.model : ""}`; } }
  sayDone(id, d) {
    const li = this.nar.querySelector(`li[data-id="${CSS.escape(id)}"]`); if (!li) return;
    li.classList.add(d.status === "ok" ? "ok" : "bad"); li.classList.remove("cur");
    li.querySelector(".nn").innerHTML = `${d.duration_s != null ? `<span class="took num">${fmt(d.duration_s)} s</span>` : ""}${(d.numbers || []).map((n) => `<span class="nc"><span class="k">${esc(n.label)}</span> <b class="num">${n.v == null ? "–" : esc(fmt(n.v))}${n.unit ? " " + esc(n.unit) : ""}</b> ${tagOf(n.tag)}</span>`).join("")}`;
    li.classList.add("cur"); setTimeout(() => li.classList.remove("cur"), 400);
  }
  // what this run did in a step, for the side panel
  runBlock(n, s) {
    const recs = s ? this.log[`${n.id}.${s.id}`] || [] : this.nodeLog?.[n.id] || [];
    if (!recs.length) return `<h5>In this run</h5><p class="muted">${this.view ? "This step has not run yet in this run, or is not part of it." : "No run is open."}</p>`;
    return `<h5>In this run</h5>${recs.map((r) => `<div class="rb ${r.status}"><b>${esc(r.label || "")}</b>
      <div class="row2"><div><h5>Went in</h5><p>${esc(r.inputs || "–")}</p></div><div><h5>Came out</h5><p>${esc(r.outputs || (r.status === "running" ? "running…" : "–"))}</p></div></div>
      <p>${r.took != null ? `Took ${fmt(r.took)} s <span class="muted">(real time in the recorded run)</span>` : `<span class="muted">Not timed separately in the recording.</span>`} · ${esc(r.status)} ${tagOf(r.tag)}</p>
      ${r.numbers.map((x) => statHtml({ label: x.label, v: x.v, unit: x.unit, tag: x.tag })).join("")}
      ${r.events.length ? `<p class="muted">Events: ${r.events.map((e) => `<code>${esc(e.type)}</code>`).join(" ")}</p>` : ""}</div>`).join("")}`;
  }
  // ---------------------------------------------------------------- live following
  startLive() {
    this.plan = null; this.live = true; this.done = false; this.screen = "run"; this.renderRail(); this.runView(false);
    const es = (this.es = new EventSource(`/api/dash/arch/live?speed=${this.speed}`)); this.liveEvents = [];
    es.addEventListener("event", (m) => { if (this.livePaused) return; try { const ev = JSON.parse(m.data); this.liveEvents.push(ev); this.onEvent(ev); } catch { /* skip a bad line */ } });
    es.onerror = () => { this.$("#rstat").textContent = "Connection lost, retrying…"; };
  }
  // ---------------------------------------------------------------- 3. results
  results() {
    this.destroyRun(); const live = !this.plan;
    if (live) { this.host.innerHTML = `<div class="pf res"><h1>Results</h1><div class="err">A live run's results come from its signed receipt. Open the receipt once the run has written it; this page does not compute before/after numbers from a partial stream.</div><p class="muted">Gate results seen: ${esc(JSON.stringify((this.liveEvents || []).filter((e) => e.type === "gate.result").map((e) => ({ check: e.data.check, passed: e.data.passed }))))}</p><button class="btn" id="again">Back to settings</button></div>`; this.$("#again").onclick = () => this.go("config"); return; }
    const R = this.plan.results, S = this.scale;
    this.host.innerHTML = `<div class="pf res"><div class="rbar"><h1>Results</h1>${badge("REPLAY")}<span class="rt">${esc(R.model)} · ${esc(R.workload)}</span><span class="sp"></span>
        <label class="scl">At <input id="scale" type="number" min="1" step="100000" value="${S}" aria-label="requests per day"> requests a day</label></div>
      <p class="rnote">Recorded run <code>${esc(R.run_id)}</code>, re-evaluated against your limits: p99 may rise ${R.limits.p99_slack_pct}%, agreement at least ${R.limits.min_quality_pct}%${R.limits.strict_quality ? ", quality must be verified" : ""}. Levers are shown separately and never added together.</p>
      ${R.room ? this.roomResults(R) : ""}
      <div id="cards">${R.levers.map((L) => this.card(L)).join("")}</div>
      <div class="resfoot"><button class="btn" id="back">Change settings</button><button class="btn" id="seeRun">See the run again</button><button class="btn" id="rerun">Run again</button><button class="btn" id="openArch">Open the Architecture map</button></div></div>`;
    if (R.room) { const b = this.$("#resB"), a = this.$("#resA"), cap = this.$("#resCap"); wireHeat(b, R.room.before, cap); wireHeat(a, R.room.after, cap); }
    this.$("#scale").onchange = (e) => { const v = Math.max(1, +e.target.value || 1); this.scale = v; this.$("#cards").innerHTML = R.levers.map((L) => this.card(L)).join(""); };
    this.$("#back").onclick = () => this.go("config"); this.$("#seeRun").onclick = () => this.go("run"); this.$("#rerun").onclick = () => { this.runView(false); this.screen = "run"; this.done = false; this.renderRail(); };
    this.$("#openArch").onclick = () => this.D.drawer("arch");
  }
  roomResults(R) {
    const Rm = R.room, t = Rm.target, b = Rm.before.racks.find((x) => x.id === t), a = Rm.after.racks.find((x) => x.id === t), c = Rm.cooling;
    const img = this.plan.room?.pick === "upload" ? this.roomImg : null;
    const tt = c ? `<b>ESTIMATE</b> ${esc(c.formula)}<br><br><b>Source:</b> ${esc(c.source)}<br><br><b>Caveat:</b> ${esc(c.caveat)}` : "";
    const kl = b && a && b.power_w != null && a.power_w != null ? `<div class="rkline"><b>${esc(t)}</b> GPU power <b class="num">${num(b.power_w, 0)} W</b> → <b class="num">${num(a.power_w, 0)} W</b> ${tagOf(b.power_label)} · heat <b class="num">${num(b.temp_c, 1)} °C</b> → <b class="num">${num(a.temp_c, 1)} °C</b> ${tagOf("SIMULATED")}</div>` : t ? `<div class="rkline"><b>${esc(t)}</b> acted on. No power figure, so the heat map is unchanged.</div>` : `<div class="rkline">No rack in this scan could be acted on.</div>`;
    return `<section class="roomres" aria-label="room before and after"><h2>The room, before and after ${scanTag(Rm.before)}</h2>
      <p class="hnt">${esc(Rm.before.note || "")} ${img ? "Your picture is under the racks; the racks drawn on it are the example layout." : ""} Heat after is a simple model: the rack's rise above a ${22} °C supply falls in proportion to its power. GPU power and energy are measured; temperatures are not.</p>
      <div class="ba"><figure><figcaption>Before</figcaption><div id="resB">${heatSvg(Rm.before, { target: t, img })}</div></figure><figure><figcaption>After ${tagOf("SIMULATED")}</figcaption><div id="resA">${heatSvg(Rm.after, { target: t, img })}</div></figure></div>${heatLegend()}
      <p class="cap hnt" id="resCap">Hover or tab to a rack for its numbers.</p>${kl}
      ${c ? `<div class="rkline"><span class="tipx" tabindex="0" data-tt="${esc(tt)}">Cooling and overhead energy per 1M requests</span> <b class="num">${num(c.extra_before_kwh_per_m, 2)} kWh</b> → <b class="num">${num(c.extra_after_kwh_per_m, 2)} kWh</b> ${tagOf("ESTIMATE")} <span class="muted">PUE ${c.value}, ${esc(c.source.split(":")[0])}</span></div>` : ""}
      <div class="rkline muted">${Rm.picked_by === "rule-based placeholder" ? "Which rack to start with was chosen by a plain rule here (hottest rack with card access). Muse Spark's pick replaces it when the run's event stream carries one." : ""}</div></section>`;
  }
  card(L) {
    const W = this.plan.results.water, P = this.plan.results.price, scale = this.scale * 365 / 1e6;
    const wtt = `<b>ESTIMATE</b> ${esc(W.formula)}<br><br><b>Source:</b> ${esc(W.source)}<br><br><b>Caveat:</b> ${esc(W.caveat)}`;
    const ptt = `<b>ESTIMATE</b> ${esc(P.formula)}<br><br><b>Source:</b> ${esc(P.source)}<br><br>Electricity only. The rent for the machine is not included.`;
    const bar = (b, a) => `<span class="bars"><i class="b0" style="width:100%"></i><i class="b1" style="width:${b > 0 ? Math.max(2, (a / b) * 100) : 100}%"></i></span>`;
    const row = (k, b, a, unit, tag, o = {}) => `<tr><th scope="row">${o.tt ? `<span class="tipx" tabindex="0" data-tt="${esc(o.tt)}">${esc(k)}</span>` : esc(k)}</th><td class="num">${b}</td><td class="num"><b>${a}</b></td><td class="num chg">${o.chg || ""}</td><td>${bar(o.nb ?? 0, o.na ?? 0)}</td><td>${tagOf(tag)}</td></tr>`;
    const pct = (b, a) => (b ? `${(a / b - 1) * 100 > 0 ? "+" : ""}${num((a / b - 1) * 100, 1)}%` : "");
    const q = L.quality || {}, head = L.applied ? `${num(L.saved_pct, 1)}% ${L.id === "cache" ? "less input cost" : "less"}` : "Not kept";
    let rows = "", annual = "";
    if (L.id === "effort") {
      const eb = L.energy_saved_wh_per_req, wl = L.energy_saved_litres_per_req;
      rows = row("Cost per 1M requests", `$${num(L.usd_before * 1e6, 0)}`, `$${num(L.usd_after * 1e6, 0)}`, "", "MEASURED", { nb: L.usd_before, na: L.usd_after, chg: pct(L.usd_before, L.usd_after) })
        + `<tr><th scope="row"><span class="tipx" tabindex="0" data-tt="${esc(L.energy_kind)}">Energy saved per 1M requests</span></th><td colspan="2" class="num">${eb ? `${num(eb.low * 1e3, 1)} to ${num(eb.high * 1e3, 1)} kWh` : "–"}</td><td></td><td></td><td>${tagOf("ESTIMATE")}</td></tr>`
        + `<tr><th scope="row"><span class="tipx" tabindex="0" data-tt="${wtt}">Water saved per 1M requests</span></th><td colspan="2" class="num">${wl ? `${num(wl.low * 1e6, 1)} to ${num(wl.high * 1e6, 1)} L` : "–"}</td><td></td><td></td><td>${tagOf("ESTIMATE")}</td></tr>`
        + row("Slowest request (p99)", `${num(L.p99_before_s, 2)} s`, `${num(L.p99_after_s, 2)} s`, "", "MEASURED", { nb: L.p99_before_s, na: L.p99_after_s, chg: pct(L.p99_before_s, L.p99_after_s) });
      annual = L.applied ? `At ${num(this.scale, 0)} requests a day: about <b>$${num((L.usd_before - L.usd_after) * this.scale * 365, 0)}</b> a year in billed tokens ${tagOf("MEASURED")} (the per-request saving, scaled). Energy ${eb ? `${num(eb.low * this.scale * 365 / 1e3, 0)} to ${num(eb.high * this.scale * 365 / 1e3, 0)} kWh` : "–"} and water ${wl ? `${num(wl.low * this.scale * 365, 0)} to ${num(wl.high * this.scale * 365, 0)} L` : "–"} ${tagOf("ESTIMATE")}.` : "";
    } else if (L.id === "cache") {
      rows = `<tr><th scope="row">Input cost</th><td class="num">changing text first</td><td class="num"><b>${num(L.saved_pct, 1)}% less</b></td><td></td><td><span class="bars"><i class="b0" style="width:100%"></i><i class="b1" style="width:${100 - L.saved_pct}%"></i></span></td><td>${tagOf("MEASURED")}</td></tr>`;
    } else {
      const m = L.wh_before * 1000;
      rows = row("Cost per 1M requests (electricity)", `$${num(L.usd_before * 1e6, 2)}`, `$${num(L.usd_after * 1e6, 2)}`, "", "ESTIMATE", { tt: ptt, nb: L.usd_before, na: L.usd_after })
        + row("Energy per request", `${num(m, 2)} mWh`, `${num(L.wh_after * 1000, 2)} mWh`, "", "MEASURED", { nb: L.wh_before, na: L.wh_after, chg: pct(L.wh_before, L.wh_after) })
        + row("Water per 1M requests", `${num(L.litres_before * 1e6, 1)} L`, `${num(L.litres_after * 1e6, 1)} L`, "", "ESTIMATE", { tt: wtt, nb: L.litres_before, na: L.litres_after })
        + row(L.id === "topk" ? "Slowest batch (p99)" : "Slowest 1% of requests (p99)", `${num(L.p99_before_s, 2)} s`, `${num(L.p99_after_s, 2)} s`, "", "MEASURED", { nb: L.p99_before_s, na: L.p99_after_s, chg: L.applied ? pct(L.p99_before_s, L.p99_after_s) : "" });
      annual = L.applied ? `At ${num(this.scale, 0)} requests a day, over a year: <b>${num(L.per_million.kwh * scale, 0)} kWh</b>, <b>$${num(L.per_million.usd * scale, 0)}</b> electricity and <b>${num(L.per_million.litres * scale, 0)} L</b> of water saved ${tagOf("ESTIMATE")}. The rent for the card is not included.` : "";
    }
    const qcls = { pending: "warn", passed: "ok", failed: "bad", "n/a": "" }[q.state] || "";
    const tried = L.tried_p99_change_pct != null || (L.tried && L.id === "topk");
    const credits = (L.credits || []).map(([p, t]) => `<li class="cr ${p === "meta" ? "meta" : ""}"><b style="--c:${PCOL[p]}">${esc(PNAME[p])}</b><span>${esc(t)}</span></li>`).join("");
    return `<section class="rc ${L.applied ? "kept" : "nokept"}" aria-label="${esc(L.title)}"><header><h2>${esc(L.title)}</h2><span class="hd2 num">${esc(head)}</span><span class="lb">${esc(L.before_label)} → ${esc(L.after_label)}</span></header>
      <table class="cmpt"><thead><tr><th></th><th>Before</th><th>After</th><th>Change</th><th></th><th></th></tr></thead><tbody>${rows}</tbody></table>
      <div class="qrow ${qcls}"><b>Quality kept</b><span>${esc(q.text || "–")}</span>${q.state && q.state !== "n/a" ? `<em class="qs ${qcls}">${esc(q.state)}</em>` : ""}</div>
      ${q.why ? `<p class="hnt">${esc(q.why)}</p>` : ""}
      ${q.classes ? `<p class="hnt">${q.classes.map((c) => `${esc(c.class)}: ${esc(c.chosen)} ${c.kept ? "kept" : "not kept"}`).join(" · ")}</p>` : ""}
      ${L.note ? `<p class="note2">${esc(L.note)}</p>` : ""}${annual ? `<p class="annual">${annual}</p>` : ""}
      <div class="who"><h3>Who did what</h3><ul>${credits}</ul>${L.receipt ? `<p class="hnt">Receipt <code>${esc(L.receipt)}</code> · ${this.cfg.receipts[L.receipt]?.signed ? "signed with an offline key" : "not signed"} · no public Rekor entry</p>` : `<p class="hnt">Receipt: –</p>`}<p class="hnt">Source: ${esc(L.src)}. Cost basis: ${esc(L.usd_kind)}.</p></div></section>`;
  }
}

// plays a plan's frames at their t_ms offsets; pausable, skippable, speed-adjustable
class Player {
  constructor(frames, speed, on, end) { this.f = frames; this.speed = speed; this.on = on; this.end = end; this.i = 0; this.paused = false; this.tok = 0; }
  pause(p) { this.paused = p; }
  stop() { this.tok++; }
  skip() { const t = ++this.tok; while (this.i < this.f.length) this.on(this.f[this.i++].event); void t; this.end(); }
  async run() {
    const tok = ++this.tok; let prev = this.f[0]?.t_ms || 0;
    while (this.i < this.f.length) {
      const fr = this.f[this.i], wait = (fr.t_ms - prev) / this.speed; prev = fr.t_ms;
      if (!(await this.sleep(wait, tok))) return;
      this.on(fr.event); this.i++;
    }
    this.end();
  }
  sleep(ms, tok) { return new Promise((r) => { let el = 0, last = performance.now(); const f = () => { if (tok !== this.tok) return r(false); const n = performance.now(); if (!this.paused) el += n - last; last = n; el >= ms ? r(true) : setTimeout(f, 25); }; f(); }); }
}
