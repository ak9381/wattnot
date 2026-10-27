// The Architecture deep dive: the whole map, a partner filter (with "Meta did this"),
// replay of recorded runs, live following of an events file, and the side panel.
import { ArchView, panelHtml, statHtml } from "./arch.js";
import { esc, pill } from "./theme.js";
import { Product } from "./product.js";
import { mountRoomScan } from "./roomnode.js";

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
    this.host = host; this.d = data; this.h = hooks; this.speed = 2; this.replay = "h100"; this.filter = null;
    host.innerHTML = `<div class="archmode" role="group" aria-label="architecture view"><button data-m="pipe" class="on" aria-pressed="true">Run pipeline</button><button data-m="all" aria-pressed="false">All modules</button>
      <span class="muted" id="amodeNote">The real steps of one run, lit up as its events arrive. Click a step for its sub-steps and numbers.</span></div>
      <div id="apipe" class="apipe"></div><div id="aall" hidden><div class="archbar"><label class="muted" for="rsel">Replay</label><select id="rsel" class="btn" aria-label="recorded run to replay"></select>
      <button class="btn pri" id="aplay">Play</button><button class="btn" id="apause">Pause</button><button class="btn" id="areset">Reset</button><button class="btn" id="aexpand">Expand all</button><button class="btn" id="acollapse">Collapse all</button>
      <span class="seg" id="aspd"><button data-s="1">1×</button><button data-s="2" class="on">2×</button><button data-s="4">4×</button></span><span class="sp"></span><span class="cap" id="aprog"></span></div>
      <div class="avlegend" id="afil" role="group" aria-label="filter by partner"></div>
      <div class="mstrip" id="mstrip" hidden></div>
      <div class="archcap" id="acap" aria-live="polite">Click a step to see its code, inputs, outputs, timing and partner. Pick a replay and press Play to watch a recorded run.</div>
      <section class="aroom" id="aroom" hidden aria-label="room scan"><h3>Room scan · SAM 3.1 <span id="aroomTag"></span></h3><div id="aroomBody"></div></section><div id="amap"></div></div><aside class="panel in-drawer" id="apanel" aria-label="step details"><button class="btn x" id="apx" aria-label="close details">Close</button><div id="apbody"></div></aside>`;
    const rs = Object.values(data.replays);
    $("#rsel", host).innerHTML = rs.map((r) => `<option value="${r.id}">${esc(r.title)}</option>`).join("") + `<option value="live">Live run (events file)</option>`;
    $("#afil", host).innerHTML = FILTERS.map(([id, t, c]) => `<button class="fchip ${id === "meta" ? "meta" : ""}" data-f="${id}" ${c ? `style="--c:${c}"` : ""} aria-pressed="${id === "all"}">${c ? "<i></i>" : ""}${esc(t)}${id === "meta" ? ` <span class="muted">${data.nodes.filter((n) => n.partners.includes("meta")).length} steps</span>` : ""}</button>`).join("");
    $("#mstrip", host).innerHTML = data.meta.map((m) => `<div class="mcard"><b>${esc(m.name)}</b><p>${esc(m.does)}</p>${m.stats.slice(0, 3).map(statHtml).join("")}</div>`).join("");
    this.view = new ArchView($("#amap", host), data, { mode: "map", onSelect: (n, s) => this.panel(n, s), onCue: (c) => { $("#acap", host).innerHTML = `<b>${esc(this.d.nodes.find((n) => n.id === c.node).title)}</b> · ${esc(c.text)} ${c.tag ? pill(c.tag) : ""}`; }, onProgress: (i, n) => { $("#aprog", host).textContent = i >= n ? "done" : `${i + 1} of ${n}`; } });
    this.wire(); this.mode = "pipe";
  }
  setMode(m) {
    this.mode = m; const H = this.host; this.closePanel();
    H.querySelectorAll(".archmode button").forEach((b) => { const on = b.dataset.m === m; b.classList.toggle("on", on); b.setAttribute("aria-pressed", on); });
    $("#apipe", H).hidden = m !== "pipe"; $("#aall", H).hidden = m !== "all";
    $("#amodeNote", H).textContent = m === "pipe" ? "The real steps of one run, lit up as its events arrive. Click a step for its sub-steps and numbers." : "Every module in the system, with who built it. Pick a replay to watch it.";
    if (m === "pipe") this.ensurePipe(); else requestAnimationFrame(() => this.view.layoutEdges());
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
    $("#aplay", H).onclick = () => this.start();
    $("#apause", H).onclick = (e) => { const b = e.currentTarget; if (this.view.paused) { this.view.resume(); b.textContent = "Pause"; } else { this.view.pause(); b.textContent = "Resume"; } };
    $("#areset", H).onclick = () => { this.stopLive(); this.view.reset(); $("#aprog", H).textContent = ""; $("#apause", H).textContent = "Pause"; $("#acap", H).textContent = "Reset. Pick a replay and press Play."; };
    $("#aexpand", H).onclick = () => this.view.expandAll();
    $("#acollapse", H).onclick = () => this.view.collapseAll();
    $("#rsel", H).onchange = (e) => { this.replay = e.target.value; this.view.reset(); if (this.replay === "live") this.startLive(); };
    H.querySelectorAll("#aspd button").forEach((b) => (b.onclick = () => { this.speed = +b.dataset.s; H.querySelectorAll("#aspd button").forEach((x) => x.classList.toggle("on", x === b)); }));
    H.querySelectorAll("#afil button").forEach((b) => (b.onclick = () => this.setFilter(b.dataset.f)));
    $("#apx", H).onclick = () => this.closePanel();
    $("#apanel", H).addEventListener("click", (e) => {
      const go = e.target.closest("[data-go]"); if (go) { go.dataset.go === "meta" ? this.setFilter("meta") : this.h.openDrawer(go.dataset.go); return; }
      const sb = e.target.closest("a[data-sub]"); if (sb) { e.preventDefault(); if (this.mode === "pipe") { const sub = this.cur.subs.find((x) => x.id === sb.dataset.sub); this.panel(this.cur, sub, this.pv, this.pipe?.runBlock(this.cur, sub) || ""); } else this.view.select(this.cur.id, sb.dataset.sub); }
    });
  }
  setFilter(f) {
    this.filter = f === "all" ? null : f; this.view.setFilter(this.filter);
    this.host.querySelectorAll("#afil button").forEach((b) => b.setAttribute("aria-pressed", b.dataset.f === f));
    $("#mstrip", this.host).hidden = f !== "meta";
    if (f === "meta") $("#acap", this.host).innerHTML = `<b>Meta did this:</b> ${this.d.nodes.filter((n) => n.partners.includes("meta")).map((n) => `${esc(n.title)} (${esc(n.meta_product)})`).join(" · ")}`;
    else if (f && f !== "all") $("#acap", this.host).innerHTML = `Highlighting every step ${esc(this.d.partners[f].name)} did real work in.`;
    requestAnimationFrame(() => this.view.layoutEdges());
  }
  async start() {
    this.stopLive(); if (this.replay === "live") return this.startLive();
    const r = this.d.replays[this.replay]; $("#acap", this.host).innerHTML = esc(r.note); $("#apause", this.host).textContent = "Pause";
    await this.view.play(r.cues, { speed: this.speed });
  }
  panel(n, s, view = this.view, extra = "") {
    this.cur = n; this.pv = view; const p = $("#apanel", this.host); const h = panelHtml(view, n, s);
    $("#apbody", this.host).innerHTML = extra ? h.replace(/(<h3>.*?<\/h3>)/, `$1${extra}`) : h;
    p.classList.add("open"); this.host.classList.add("pn"); $("#apx", this.host).focus();
  }
  closePanel() { $("#apanel", this.host).classList.remove("open"); this.host.classList.remove("pn"); }
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
    if (v.cur === node) { el.querySelector(".now").innerHTML = `${esc(d.summary || d.label || sub)}${tag ? " " + pill(tag) : ""}`; }
    $("#acap", this.host).innerHTML = `<b>${esc(this.d.nodes.find((n) => n.id === node)?.title || node)}</b> · ${esc(d.summary || d.label || "")} ${tag ? pill(tag) : ""}`;
  }
  liveBadge(ev) {
    const d = ev.data || {}, l = d.mode === "mock" || d.label === "MOCK" ? "MOCK" : this.h.health?.replay_only || d.replay || d.mode === "replay" ? "REPLAY" : "LIVE";
    $("#aprog", this.host).innerHTML = `${pill(l)} <span class="muted">${esc(d.run || d.model || "")}</span>`;
  }
  // The room node: a room_scan payload (before, then after) mounted with web/room/node.js (see roomnode.js).
  async roomScan(p) {
    this.roomPay[p.phase || "before"] = p; const host = $("#aroomBody", this.host), box = $("#aroom", this.host); box.hidden = false;
    try {
      const { labels } = await mountRoomScan(host, this.roomPay, (this.roomCache ||= {}));
      $("#aroomTag", this.host).innerHTML = `<span class="muted">racks</span> ${pill(labels.rack_outline || "MEASURED")} <span class="muted">heat</span> ${pill(labels.thermal_frame || "SIMULATED")}`;
    } catch (e) { host.innerHTML = `<p class="muted">The room picture could not be drawn (${esc(e.message)}).</p>`; }
  }
  stopLive() { this.es?.close(); this.es = null; }
  show() { if (this.mode === "pipe") this.ensurePipe(); else requestAnimationFrame(() => this.view.layoutEdges()); }
  destroy() { this.stopLive(); this.pipe?.destroyRun(); this.view.destroy(); }
}
