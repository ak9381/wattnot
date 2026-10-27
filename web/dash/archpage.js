// The Architecture deep dive: the whole map, a partner filter (with "Meta did this"),
// replay of recorded runs, live following of an events file, and the side panel.
import { ArchView, panelHtml, statHtml } from "./arch.js";
import { esc, pill } from "./theme.js";

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
    host.innerHTML = `<div class="archbar"><label class="muted" for="rsel">Replay</label><select id="rsel" class="btn" aria-label="recorded run to replay"></select>
      <button class="btn pri" id="aplay">Play</button><button class="btn" id="apause">Pause</button><button class="btn" id="areset">Reset</button>
      <span class="seg" id="aspd"><button data-s="1">1×</button><button data-s="2" class="on">2×</button><button data-s="4">4×</button></span><span class="sp"></span><span class="cap" id="aprog"></span></div>
      <div class="avlegend" id="afil" role="group" aria-label="filter by partner"></div>
      <div class="mstrip" id="mstrip" hidden></div>
      <div class="archcap" id="acap" aria-live="polite">Click a step to see its code, inputs, outputs, timing and partner. Pick a replay and press Play to watch a recorded run.</div>
      <div id="amap"></div><aside class="panel in-drawer" id="apanel" aria-label="step details"><button class="btn x" id="apx" aria-label="close details">Close</button><div id="apbody"></div></aside>`;
    const rs = Object.values(data.replays);
    $("#rsel", host).innerHTML = rs.map((r) => `<option value="${r.id}">${esc(r.title)}</option>`).join("") + `<option value="live">Live run (events file)</option>`;
    $("#afil", host).innerHTML = FILTERS.map(([id, t, c]) => `<button class="fchip ${id === "meta" ? "meta" : ""}" data-f="${id}" ${c ? `style="--c:${c}"` : ""} aria-pressed="${id === "all"}">${c ? "<i></i>" : ""}${esc(t)}${id === "meta" ? ` <span class="muted">${data.nodes.filter((n) => n.partners.includes("meta")).length} steps</span>` : ""}</button>`).join("");
    $("#mstrip", host).innerHTML = data.meta.map((m) => `<div class="mcard"><b>${esc(m.name)}</b><p>${esc(m.does)}</p>${m.stats.slice(0, 3).map(statHtml).join("")}</div>`).join("");
    this.view = new ArchView($("#amap", host), data, { mode: "map", onSelect: (n, s) => this.panel(n, s), onCue: (c) => { $("#acap", host).innerHTML = `<b>${esc(this.d.nodes.find((n) => n.id === c.node).title)}</b> · ${esc(c.text)} ${c.tag ? pill(c.tag) : ""}`; }, onProgress: (i, n) => { $("#aprog", host).textContent = i >= n ? "done" : `${i + 1} of ${n}`; } });
    this.wire();
  }
  wire() {
    const H = this.host;
    $("#aplay", H).onclick = () => this.start();
    $("#apause", H).onclick = (e) => { const b = e.currentTarget; if (this.view.paused) { this.view.resume(); b.textContent = "Pause"; } else { this.view.pause(); b.textContent = "Resume"; } };
    $("#areset", H).onclick = () => { this.stopLive(); this.view.reset(); $("#aprog", H).textContent = ""; $("#apause", H).textContent = "Pause"; $("#acap", H).textContent = "Reset. Pick a replay and press Play."; };
    $("#rsel", H).onchange = (e) => { this.replay = e.target.value; this.view.reset(); if (this.replay === "live") this.startLive(); };
    H.querySelectorAll("#aspd button").forEach((b) => (b.onclick = () => { this.speed = +b.dataset.s; H.querySelectorAll("#aspd button").forEach((x) => x.classList.toggle("on", x === b)); }));
    H.querySelectorAll("#afil button").forEach((b) => (b.onclick = () => this.setFilter(b.dataset.f)));
    $("#apx", H).onclick = () => this.closePanel();
    $("#apanel", H).addEventListener("click", (e) => {
      const go = e.target.closest("[data-go]"); if (go) { go.dataset.go === "meta" ? this.setFilter("meta") : this.h.openDrawer(go.dataset.go); return; }
      const sb = e.target.closest("a[data-sub]"); if (sb) { e.preventDefault(); this.view.select(this.cur.id, sb.dataset.sub); }
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
  panel(n, s) {
    this.cur = n; const p = $("#apanel", this.host); $("#apbody", this.host).innerHTML = panelHtml(this.view, n, s);
    p.classList.add("open"); this.host.classList.add("pn"); $("#apx", this.host).focus();
  }
  closePanel() { $("#apanel", this.host).classList.remove("open"); this.host.classList.remove("pn"); }
  // ---- live following
  async startLive() {
    const H = this.host, cap = $("#acap", H);
    let ok = true; try { const r = await fetch("/api/dash/health"); ok = (await r.json()).events; } catch { ok = false; }
    if (!ok) { cap.innerHTML = `<span class="err">No run is being followed. Start the server with <code>--events path/to/events.jsonl</code> to watch a live run here. Recorded replays work without it.</span>`; return; }
    this.view.reset(); cap.textContent = "Following the run as it writes events…";
    const es = (this.es = new EventSource("/api/dash/arch/live")); let last = 0;
    es.addEventListener("event", (m) => {
      const ev = JSON.parse(m.data), map = this.d.event_map[ev.type]; if (!map) return;
      const now = performance.now(); if ((ev.type === "telemetry.sample" || ev.type === "step.progress") && now - last < 500) return; last = now;
      this.view.activate({ node: map[0], sub: map[1], text: livePhrase(ev), tag: ev.data?.label && this.d.labels.includes(ev.data.label) ? ev.data.label : null, ok: !(ev.type === "gate.result" && ev.data?.passed === false) });
    });
    es.onerror = () => { cap.innerHTML += ` <span class="muted">(connection lost, retrying)</span>`; };
  }
  stopLive() { this.es?.close(); this.es = null; }
  show() { requestAnimationFrame(() => this.view.layoutEdges()); }
  destroy() { this.stopLive(); this.view.destroy(); }
}
