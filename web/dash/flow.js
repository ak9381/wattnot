// The workflow view: input -> router -> expert groups -> output, drawn as a node flow with
// marching-ants edges. Edge thickness and speed follow the routed-token rate.
import * as C from "./core.js";
import { GROUP_COLORS, esc, dot, numLab, pct, topicColor } from "./theme.js";

const NS = "http://www.w3.org/2000/svg";
const bar = (label, id, color) => `<div class="row"><span>${label}</span><div class="bar2"><i id="${id}" style="--c:${color}"></i></div><span class="val" id="${id}v">–</span></div>`;

export class Flow {
  constructor(host, ctx) {
    this.host = host; this.ctx = ctx; this.expanded = new Set(); this.edges = [];
    new ResizeObserver(() => this.layoutEdges()).observe(host);
  }

  build() {
    const { st } = this.ctx, colors = GROUP_COLORS[st.mode];
    const E = st.meta.meta;
    this.host.innerHTML = `<div class="flow" id="flow">
      <svg class="edges" id="edges"></svg>
      <div class="frow"><div class="node in" id="n-in"><h4>input · tokens in</h4>
        <div class="sub" id="in-sub">waiting for the first token</div>
        ${bar("tokens", "in-bar", "var(--blue)")}
        <div class="desc" id="in-desc"></div></div></div>
      <div class="frow"><div class="node router" id="n-router">
        <div class="router-t"><h4>router · gate and split</h4><span>${esc(E.model)}</span></div>
        <div class="sub">${E.layers} layers · ${E.experts} experts each · top-${E.top_k}</div>
        ${bar("active/tok", "r-act", "var(--green)")}
        ${bar("top-1 weight", "r-top", "var(--green)")}
        ${bar("spread", "r-ent", "var(--blue)")}
        <div class="desc" id="r-desc"></div>
        <div class="st"><span class="dot"></span><span id="r-st">idle</span></div></div></div>
      <div class="frow" id="grow">${st.names.map((n, g) => this.groupHtml(g, n, colors[g])).join("")}</div>
      <div class="frow"><div class="node out" id="n-out"><h4>output · merged</h4>
        <div class="kv"><span class="big num${numLab("MEASURED")}" id="o-j">0.0 J</span>${dot("MEASURED")}
          <span id="o-jt" class="muted"></span></div>
        <div class="sub" id="o-sub"></div>
        <div class="desc" id="o-desc"></div></div></div></div>`;
    this.svg = this.host.querySelector("#edges");
    st.names.forEach((_, g) => {
      const node = this.host.querySelector(`#n-g${g}`);
      node.onmouseenter = () => this.ctx.hoverGroup(g);
      node.onmouseleave = () => this.ctx.hoverGroup(null);
      node.onclick = (e) => { if (!e.target.closest(".xp")) this.ctx.selectGroup(g); };
      node.querySelector(".xp").onclick = () => { this.expanded.has(g) ? this.expanded.delete(g) : this.expanded.add(g); this.build(); this.update(); };
      if (this.expanded.has(g)) this.paintMini(g);
    });
    this.makeEdges();
    requestAnimationFrame(() => this.layoutEdges());
  }

  groupHtml(g, name, color) {
    const { st } = this.ctx, rep = st.report[g], open = this.expanded.has(g);
    const per = Math.max(1, Math.round(rep.units / st.L));
    return `<div class="node grp" id="n-g${g}" style="--c:${color}">
      <h4>${esc(name)}</h4>
      <div class="sub">${rep.units} seats · about ${per} per layer</div>
      ${bar("tokens", `g${g}-t`, color)}
      ${bar("energy", `g${g}-e`, color)}
      <div class="desc" id="g${g}-d">–</div>
      <div class="st"><span class="dot"></span><span id="g${g}-s">idle</span>
        <button class="xp" title="show every expert in this group">${open ? "collapse ▴" : "experts ▾"}</button></div>
      ${open ? `<canvas id="mini${g}" height="${st.L * 3 + 2}"></canvas>` : ""}
    </div>`;
  }

  makeEdges() {
    const { st } = this.ctx, colors = GROUP_COLORS[st.mode];
    this.edges = [];
    const add = (id, from, to, color) => this.edges.push({ id, from, to, color });
    add("e-in", "n-in", "n-router", "var(--blue)");
    st.names.forEach((_, g) => { add(`e-rg${g}`, "n-router", `n-g${g}`, colors[g]); add(`e-go${g}`, `n-g${g}`, "n-out", colors[g]); });
    this.svg.innerHTML = this.edges.map((e) => `<g id="${e.id}-g" style="--c:${e.color}">
      <path class="edge ghost" id="${e.id}-ghost" stroke="${e.color}"/>
      <path class="edge" id="${e.id}" stroke="${e.color}"/>
      <circle class="dotm" r="2.6"><animateMotion id="${e.id}-m1" dur="2s" repeatCount="indefinite"><mpath href="#${e.id}"/></animateMotion></circle>
      <circle class="dotm" r="2.6"><animateMotion id="${e.id}-m2" dur="2s" begin="-1s" repeatCount="indefinite"><mpath href="#${e.id}"/></animateMotion></circle></g>`).join("");
  }

  layoutEdges() {
    if (!this.svg) return;
    const base = this.host.getBoundingClientRect();
    this.svg.setAttribute("viewBox", `0 0 ${base.width} ${base.height}`);
    const pt = (id, side) => {
      const r = this.host.querySelector(`#${id}`)?.getBoundingClientRect(); if (!r) return null;
      return [r.left - base.left + r.width / 2, (side === "b" ? r.bottom : r.top) - base.top];
    };
    const spread = (id, from, to) => {
      // fan out where several edges share one node edge
      const a = pt(from, "b"), b = pt(to, "t"); if (!a || !b) return "";
      return `M${a[0]},${a[1]} C${a[0]},${a[1] + (b[1] - a[1]) * 0.55} ${b[0]},${b[1] - (b[1] - a[1]) * 0.55} ${b[0]},${b[1]}`;
    };
    for (const e of this.edges) {
      const d = spread(e.id, e.from, e.to);
      this.host.querySelector(`#${e.id}`)?.setAttribute("d", d);
      this.host.querySelector(`#${e.id}-ghost`)?.setAttribute("d", d);
    }
  }

  paintMini(g) {
    const cv = this.host.querySelector(`#mini${g}`); if (!cv) return;
    const { st, arm } = this.ctx, L = st.L, E = st.E, color = GROUP_COLORS[st.mode][g];
    const w = cv.clientWidth || 180; cv.width = w;
    const x = cv.getContext("2d"), cw = w / E, ch = 3;
    x.clearRect(0, 0, w, cv.height);
    const counts = this.ctx.live() ? st.arm[arm].units : st.meta.counts[arm], max = Math.max(...counts) || 1;
    for (let l = 0; l < L; l++) for (let e = 0; e < E; e++) {
      const u = l * E + e;
      if (st.assign[u] !== g) { x.fillStyle = "#10171E"; x.fillRect(e * cw, l * ch + 1, cw - 0.6, ch - 0.6); continue; }
      const a = 0.2 + 0.8 * (counts[u] / max) ** 0.6 + st.recent[u] * 0.5;
      x.globalAlpha = Math.min(1, a); x.fillStyle = color; x.fillRect(e * cw, l * ch + 1, cw - 0.6, ch - 0.6);
    }
    x.globalAlpha = 1;
    cv.onmousemove = (ev) => {
      const r = cv.getBoundingClientRect();
      const e = Math.floor((ev.clientX - r.left) / (r.width / E)), l = Math.floor((ev.clientY - r.top) / (r.height / L));
      if (e >= 0 && e < E && l >= 0 && l < L && st.assign[l * E + e] === g) this.ctx.tipUnit(l * E + e, ev); else this.ctx.tipUnit(null);
    };
    cv.onmouseleave = () => this.ctx.tipUnit(null);
  }

  $(id) { return this.host.querySelector(`#${id}`); }
  setBar(id, frac, text) { const b = this.$(id), v = this.$(id + "v"); if (b) b.style.width = `${Math.max(0, Math.min(1, frac)) * 100}%`; if (v) v.textContent = text; }

  update() {
    const { st, arm } = this.ctx; if (!this.svg) return;
    const a = st.arm[arm], E = st.meta.meta, tot = st.meta.totals[arm];
    const t = st.lastTick, step = t ? E.steps[t.step] : null;
    const running = this.ctx.running();
    // input
    const prompt = step ? step.prompt : "";
    this.$("in-sub").innerHTML = step ? `step ${step.id + 1} of ${E.steps.length} · <span style="color:${topicColor(step.topic)}">${esc(step.topic)}</span>` : "waiting for the first token";
    this.$("in-desc").textContent = prompt;
    this.setBar("in-bar", a.tokens / Math.max(1, tot.tokens), `${a.tokens}`);
    // router
    const act = C.activePerToken(st, arm), top1 = a.slots ? a.top1 / a.slots : 0, ent = a.slots ? a.ent / a.slots : 0;
    this.setBar("r-act", act / E.top_k, act.toFixed(2));
    this.setBar("r-top", top1, top1.toFixed(2));
    this.setBar("r-ent", ent, ent.toFixed(2));
    this.$("r-desc").innerHTML = `${E.top_k} slots offered · ${act.toFixed(2)} used ${dot("MEASURED")}${arm === "on" ? ` · tail cut at ${pct(E.mass)} mass` : ""}`;
    this.$("n-router").classList.toggle("run", running && !!t);
    this.$("r-st").textContent = running && t ? "routing" : "idle";
    // groups
    const f0 = this.ctx.focus(), gt = C.groupTotals(st, arm, f0.steps), perTok = C.staticPerToken(st, arm), perOff = C.staticPerToken(st, "off");
    const maxOff = Math.max(...perOff, 1e-9);
    const perNode = C.jPerSlot(st, arm) != null;
    const lit = new Array(st.k).fill(0), cnt = new Array(st.k).fill(0);
    for (let u = 0; u < st.U; u++) { lit[st.assign[u]] += st.recent[u]; cnt[st.assign[u]]++; }
    gt.forEach((g, i) => {
      const node = this.$(`n-g${i}`), l = lit[i] / Math.max(1, cnt[i] * 0.12), live = l > 0.08 && running;
      node.classList.toggle("run", live);
      this.$(`n-g${i}`).classList.toggle("stepview", !!f0.steps);
      this.setBar(`g${i}-t`, g.share, pct(g.share));
      this.setBar(`g${i}-e`, g.jshare, perNode ? `${C.fmtJ(g.joules)}J` : "–");
      this.$(`g${i}-s`).textContent = live ? "running" : (a.tokens ? "idle" : "ready");
      const rep = st.report[i];
      this.$(`g${i}-d`).textContent = `top: ${rep.top_tokens.filter(([w]) => w.trim() && !/^<\|.*\|>$/.test(w)).slice(0, 3).map(([w]) => w.trim()).join("  ")}`;
      // edges
      const width = 0.8 + 8 * (perTok[i] / maxOff), ghost = 0.8 + 8 * (perOff[i] / maxOff);
      const speed = Math.max(0.25, Math.min(1.8, 0.35 + l * 1.2));
      for (const id of [`e-rg${i}`, `e-go${i}`]) this.styleEdge(id, width, ghost, speed, live, arm === "on");
      if (this.expanded.has(i)) this.paintMini(i);
    });
    this.styleEdge("e-in", 3 + 4 * Math.min(1, a.tokens ? 1 : 0.3), 0, 1, running && !!t, false);
    // output
    const per = C.jPerSlot(st, arm), j = a.joules;
    this.$("o-j").textContent = per == null && t?.energy_j == null ? "no meter" : `${C.fmtJ(j)} J`;
    const gen = Math.max(1, a.gen);
    this.$("o-jt").textContent = a.gen ? `${(j / gen).toFixed(2)} J per generated token` : "";
    const en = st.meta.energy;
    this.$("o-sub").innerHTML = en ? `window total ${dot("MEASURED")} · per group ${dot("ESTIMATE")} · parts add up to the total` : "no energy source for this capture";
    this.$("o-desc").textContent = en ? `rate: ${en[arm].toFixed(2)} J/token measured on ${en.gpu_short || "an H100"}, applied to the ${a.gen} tokens written here` : "";
    // highlight
    const f = this.ctx.focus();
    st.names.forEach((_, g) => { const n = this.$(`n-g${g}`); n.classList.toggle("hl", f.group === g); n.classList.toggle("dim", f.group != null && f.group !== g); n.classList.toggle("pick", this.ctx.pickedGroup() === g); });
  }

  styleEdge(id, width, ghost, speed, live, showGhost) {
    const p = this.$(id), gp = this.$(id + "-ghost"); if (!p) return;
    p.setAttribute("stroke-width", width.toFixed(2));
    p.style.animationDuration = `${(1 / speed).toFixed(2)}s`;
    p.classList.toggle("act", live); p.classList.toggle("idle", !live);
    if (gp) { gp.setAttribute("stroke-width", ghost.toFixed(2)); gp.style.display = showGhost ? "" : "none"; }
    const dur = (3 / speed).toFixed(1);
    for (const m of ["m1", "m2"]) { const a = this.$(`${id}-${m}`); if (a && a.getAttribute("dur") !== `${dur}s`) a.setAttribute("dur", `${dur}s`); }
    const g = this.$(`${id}-g`); if (g) g.querySelectorAll(".dotm").forEach((c) => (c.style.display = live ? "" : "none"));
  }
}
