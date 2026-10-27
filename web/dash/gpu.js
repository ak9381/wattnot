// The Power page: live GPU numbers, the approve-before-apply proposals and the receipts.
// Source is either a replay scene of the real H100 run, or a live run read from /api/runs/<id>/events.
// The replay scene also follows the shared replay clock the Architecture replay
// publishes (play/pause/reset, progress, phase); the live run ignores it.
import { esc, pill } from "./theme.js";
import { clock } from "./replayclock.js";
import { init as initPan } from "./pancanvas.js";
import { broadcast } from "./clockbus.js";

const f1 = (x, d = 0) => (x == null ? "–" : Number(x).toFixed(d));

export class Power {
  constructor(host) {
    this.host = host; this.samples = []; this.cur = null; this.es = null; this.info = null; this.speed = 12;
    this.scene = null; this.paused = false;
    this.followClock = false; this.clockPhase = 0; this.stockCap = null; this.baseNote = "";
    clock.subscribe((ev) => this.onClock(ev));
  }

  // The shared replay clock: pause/reset/phase sync the scene replay to the
  // Architecture replay. Phase 1 shows the baseline arm/scene, Phase 2 the
  // WattNot one, where the data has both. A live run ignores the clock.
  onClock(ev) {
    if (this.scene?.kind === "live") return;
    if (ev.type === "reset") { this.followClock = true; this.clockPhase = 1; this.stockCap = null; this.pick(this.$("sceneSel").value); return; }
    if (!this.scene) return;
    this.followClock = true;
    if (ev.type === "pause") { this.paused = true; this.$("pPlay").textContent = "play"; return; }
    if (ev.type === "play") { this.paused = false; this.$("pPlay").textContent = "pause"; (this.buf || []).splice(0).forEach((v) => this.add(v)); return; }
    if (ev.type === "phase" || ev.type === "progress") { this.clockPhase = ev.phase; this.renderNote(); }
  }
  // Phase 1 keeps only the baseline samples of a scene that has both arms:
  // the off arm (adaptive), the stock cap (sweep), the before phase (flex).
  phaseHit(s) {
    if (!this.followClock || this.clockPhase !== 1) return true;
    if (s.arm) return s.arm === "off";
    if (s.phase) return s.phase !== "after";
    if (s.gate) return s.gate === "baseline";
    if (s.cap_w != null) return this.stockCap == null || s.cap_w === this.stockCap;
    return true;
  }
  renderNote() {
    const ph = this.followClock && this.clockPhase ? (this.clockPhase === 1 ? " · Phase 1 of 2 · baseline" : " · Phase 2 of 2 · WattNot") : "";
    this.$("sceneNote").innerHTML = `${this.baseNote}${ph}`;
  }

  async init() {
    const r = await fetch("/api/dash/gpu"); this.info = await r.json();
    const scenes = this.info.scenes;
    this.host.innerHTML = `
      <div class="cards" id="cards">
        ${[["power", "GPU power", "W"], ["jtok", "Energy per token", "J"], ["toks", "Tokens per second", "tok/s"], ["p99", "p99 latency", "ms"], ["cap", "Power cap", "W"], ["req", "Requests per second", "req/s"]]
          .map(([id, k, u]) => `<div class="card"><div class="k"><span>${k}</span><span id="${id}-l"></span></div><div class="v"><span id="${id}-v">–</span><small>${u}</small></div><div class="d" id="${id}-d"></div></div>`).join("")}
      </div>
      <div class="pgrid">
        <div class="panel ppan"><h3>trace <span class="r">
            <select id="sceneSel">${scenes.map((s) => `<option value="${s.id}">${esc(s.title)}</option>`).join("")}<option value="run:">live run (API)…</option></select>
            <button class="ghost" id="pPlay">pause</button><button class="ghost" id="pRestart">restart</button></span></h3>
          <canvas id="chart"></canvas>
          <p class="note2" id="sceneNote"></p></div>
        <div class="panel"><h3>approve before apply</h3><div id="props"></div>
          <h3 style="margin-top:12px">receipts</h3><div id="rcpts"></div></div>
      </div>`;
    // The trace is a pan/zoom canvas too: the chart sits on a transformed
    // inner layer with the shared wheel/pinch/drag/controls, 'Go to current
    // step' fits the live end of the trace, follow drops on manual pan.
    {
      const chart = this.$("chart"), panel = chart.closest(".ppan");
      const inner = document.createElement("div");
      inner.className = "ppan-inner";
      chart.replaceWith(inner); inner.appendChild(chart);
      this.pan = initPan(panel, inner, { goto: () => this.pan.fit() });
      requestAnimationFrame(() => this.pan.fit());
    }
    this.$("sceneSel").onchange = (e) => { this.followClock = false; this.pick(e.target.value); };
    this.$("pPlay").onclick = () => { this.followClock = false; this.paused = !this.paused; this.$("pPlay").textContent = this.paused ? "play" : "pause"; if (!this.paused) { (this.buf || []).splice(0).forEach((v) => this.add(v)); } };
    this.$("pRestart").onclick = () => { this.followClock = false; this.pick(this.$("sceneSel").value); };
    this.renderApprovals();
    const wanted = new URLSearchParams(location.search).get("scene");
    const first = scenes.find((s) => s.id === wanted) || scenes.find((s) => s.kind === "adaptive") || scenes[0];
    if (first) { this.$("sceneSel").value = first.id; this.pick(first.id); }
    window.addEventListener("resize", () => this.draw());
  }

  $(id) { return this.host.querySelector(`#${id}`); }

  show() { requestAnimationFrame(() => { this.pan?.fit(); this.draw(); }); }

  pick(id) {
    this.es?.close(); this.samples = []; this.cur = null; this.cap = null; this.baseJ = null;
    if (id === "run:") {
      const run = prompt("Run id from /api/runs") || ""; if (!run) return;
      this.live(run); return;
    }
    const sc = this.info.scenes.find((s) => s.id === id); this.scene = sc;
    this.stockCap = null;
    this.baseNote = `${esc(sc.note || "")} ${pill(sc.label || "MEASURED")} <span class="muted">replay of the real run, ${sc.n} one-second samples</span>`;
    this.renderNote();
    this.baseJ = sc.baseline_j_per_tok ?? null;
    this.es = new EventSource(`/api/dash/gpu/scene/${encodeURIComponent(id)}/events?speed=${this.speed}`);
    this.buf = [];
    this.es.addEventListener("sample", (ev) => { const v = JSON.parse(ev.data); this.paused ? this.buf.push(v) : this.add(v); });
    this.es.addEventListener("end", () => this.es.close());
  }

  // live: the existing run API (telemetry.sample, action.applied) in the same shape as a replay sample
  live(run) {
    this.followClock = false;
    this.scene = { title: `run ${run}`, kind: "live", label: "MEASURED" };
    this.$("sceneNote").innerHTML = `live from /api/runs/${esc(run)}/events`;
    this.es = new EventSource(`/api/runs/${encodeURIComponent(run)}/events?speed=1`);
    let t0 = null, cap = null;
    const on = (ev) => {
      const e = JSON.parse(ev.data); const ts = Date.parse(e.ts); t0 ??= ts;
      if (e.type === "action.applied" && e.data.change?.power_cap_w) cap = e.data.change.power_cap_w;
      if (e.type === "action.rolled_back" && e.data.restored_cap_w) cap = e.data.restored_cap_w;
      if (e.type === "telemetry.sample") this.add({ t_s: (ts - t0) / 1000, power_w: e.data.power_w, cap_w: cap, label: e.data.label });
      if (e.type === "trial.measured" && e.data) this.add({ t_s: (ts - t0) / 1000, power_w: this.cur?.power_w ?? null, cap_w: cap, p99_ms: e.data.p99_ms?.mean ?? e.data.p99_ms, j_per_tok: null });
    };
    ["telemetry.sample", "action.applied", "action.rolled_back", "trial.measured"].forEach((t) => this.es.addEventListener(t, on));
  }

  add(s) {
    if (s.cap_w != null && this.stockCap == null) this.stockCap = s.cap_w;
    if (!this.phaseHit(s)) return;
    this.samples.push(s); if (this.samples.length > 900) this.samples.shift();
    this.cur = { ...(this.cur || {}), ...Object.fromEntries(Object.entries(s).filter(([, v]) => v != null)) };
    if (this.scene?.kind === "sweep" || this.scene?.kind === "adaptive") this.cur.cap_w = s.cap_w ?? this.cur.cap_w;
    this.paintCards(s); this.draw();
    if ((this._bc = (this._bc || 0) + 1) % 8 === 0 && s.power_w != null) {
      broadcast({ type: "tick", view: "power", phase: this.clockPhase || 2, step: `t+${Math.round(s.t_s || 0)}s`, text: `${s.power_w} W`, powerSamples: this.samples.slice(-60).map((q) => q.power_w).filter((v) => v != null) });
    }
  }

  paintCards(s) {
    const c = this.cur, set = (id, v, label, d) => { this.$(id + "-v").textContent = v; this.$(id + "-l").innerHTML = label ? pill(label) : ""; this.$(id + "-d").textContent = d || ""; };
    const lab = s.label || this.scene?.label || "MEASURED";
    set("power", f1(s.power_w), s.power_w == null ? "" : lab, s.phase ? `phase: ${s.phase}` : "");
    const jt = s.j_per_tok ?? (s.power_w != null && s.rps ? null : null);
    set("jtok", f1(s.j_per_tok, 2), s.j_per_tok == null ? "" : "MEASURED",
      s.j_per_tok != null ? (s.arm ? `adaptive top-k ${s.arm}${s.active_experts ? ` · ${f1(s.active_experts, 1)} experts/token` : ""}` : (this.baseJ ? `normal cap: ${f1(this.baseJ, 2)} J` : "averaged over repeats")) : "this run did not count tokens");
    set("toks", f1(s.tok_s, 0), s.tok_s == null ? "" : "MEASURED", s.tok_s == null ? "not measured in this run" : "tokens ÷ run time");
    set("p99", f1(s.p99_ms, 0), s.p99_ms == null ? "" : "MEASURED", s.pass_rate != null ? `pass rate on the fixed check ${f1(s.pass_rate * 100, 0)}%` : (s.gate ? `quality gate: ${s.gate}` : ""));
    const cap = s.cap_w ?? c.cap_w;
    set("cap", f1(cap), cap == null ? "" : "MEASURED", cap == null ? "no cap in this run" : (s.target_w ? `target ${f1(s.target_w)} W` : ""));
    set("req", f1(s.rps, 1), s.rps == null ? "" : "MEASURED", "");
  }

  draw() {
    const cv = this.$("chart"); if (!cv) return;
    const dpr = window.devicePixelRatio || 1, W = cv.clientWidth, H = cv.clientHeight; if (!W) return;
    cv.width = W * dpr; cv.height = H * dpr;
    const x = cv.getContext("2d"); x.setTransform(dpr, 0, 0, dpr, 0, 0); x.clearRect(0, 0, W, H);
    const S = this.samples; if (S.length < 2) return;
    const pad = { l: 40, r: 10, t: 8, b: 20 };
    const t0 = S[0].t_s, t1 = S[S.length - 1].t_s || 1;
    const all = S.flatMap((s) => [s.power_w, s.cap_w]).filter((v) => v != null);
    const lo = Math.max(0, Math.floor((Math.min(...all) - 30) / 50) * 50), hi = Math.ceil((Math.max(...all) + 20) / 50) * 50;
    const X = (t) => pad.l + ((t - t0) / Math.max(1, t1 - t0)) * (W - pad.l - pad.r);
    const Y = (v) => pad.t + (1 - (v - lo) / (hi - lo)) * (H - pad.t - pad.b);
    x.strokeStyle = "#1A242D"; x.fillStyle = "#6D7B87"; x.font = "10px JetBrains Mono, monospace"; x.lineWidth = 1;
    for (let v = lo; v <= hi; v += 100) { x.beginPath(); x.moveTo(pad.l, Y(v)); x.lineTo(W - pad.r, Y(v)); x.stroke(); x.fillText(`${v} W`, 2, Y(v) + 3); }
    // arm bands for adaptive scene
    let prev = null, start = 0;
    const band = (i) => { if (prev == null) return; x.fillStyle = prev === "on" ? "rgba(242,153,74,.07)" : "rgba(90,169,240,.05)"; x.fillRect(X(S[start].t_s), pad.t, X(S[i].t_s) - X(S[start].t_s), H - pad.t - pad.b); };
    S.forEach((s, i) => { if (s.arm !== prev) { band(i); prev = s.arm; start = i; } }); band(S.length - 1);
    // cap
    x.strokeStyle = "#F2994A"; x.setLineDash([5, 4]); x.beginPath(); let on = false;
    S.forEach((s) => { if (s.cap_w == null) return; const px = X(s.t_s), py = Y(s.cap_w); on ? x.lineTo(px, py) : x.moveTo(px, py); on = true; }); x.stroke(); x.setLineDash([]);
    // power
    x.strokeStyle = "#22C7D9"; x.lineWidth = 1.6; x.beginPath(); on = false;
    S.forEach((s) => { if (s.power_w == null) return; const px = X(s.t_s), py = Y(s.power_w); on ? x.lineTo(px, py) : x.moveTo(px, py); on = true; }); x.stroke();
    x.fillStyle = "#22C7D9"; x.fillText("power (MEASURED)", pad.l + 6, pad.t + 12);
    x.fillStyle = "#F2994A"; x.fillText("cap", pad.l + 130, pad.t + 12);
    if (S.some((s) => s.arm)) { x.fillStyle = "#5AA9F0"; x.fillText("normal", pad.l + 170, pad.t + 12); x.fillStyle = "#F2994A"; x.fillText("adaptive", pad.l + 225, pad.t + 12); }
  }

  renderApprovals() {
    const P = this.info.approvals || [], R = this.info.receipts || [];
    this.$("props").innerHTML = P.length ? P.map((p) => `<div class="prop"><div class="t">${esc(p.title || p.id)}</div>
      <div class="m">${esc(p.kind)} · ${esc(p.id)} · now: <b>${esc(p.status)}</b></div>
      <div class="chain">${p.events.map((e) => `<span class="${esc(e.event)}" title="${esc(e.actor || "")}">${esc(e.event)}</span>`).join("")}</div>
      <code>${p.status === "proposed" ? `wattnot proposals approve ${esc(p.id)}` : `approved by ${esc((p.events.find((e) => e.event === "approved") || {}).actor || "–")}`}</code></div>`).join("")
      : `<p class="note2">No proposals.</p>`;
    this.$("rcpts").innerHTML = `<table class="rc"><tr><th>receipt</th><th>saving</th><th>grade</th><th>signed</th></tr>${R.map((r) => `<tr>
      <td title="${esc(r.model || "")}">${esc(r.id.replace("rcpt_", ""))}<br><span class="muted">${esc(r.profile)}</span></td>
      <td>${r.saving_pct == null ? "–" : `${r.saving_pct >= 0 ? "−" : "+"}${Math.abs(r.saving_pct).toFixed(1)}%`} ${pill(r.label)}</td>
      <td>${esc(r.grade || "–")}</td><td>${r.signed ? "yes" : "no"}</td></tr>`).join("")}</table>
      <p class="note2">Approving happens in <span class="mono">wattnot proposals</span>. This page only reads.</p>`;
  }
}
