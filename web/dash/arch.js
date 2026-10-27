// The architecture graph: nodes, their real sub-steps, the animation that walks through a recorded
// or live run, and the side panel. Pure DOM + SVG; GSAP only for the height and fade tweens.
import { esc, pill } from "./theme.js";
import { poweredByFor, poweredByHtml } from "./partners.js";

const g = () => window.gsap;
const has = (s, w) => (s || "").split(" ").includes(w);
const reduce = () => matchMedia("(prefers-reduced-motion: reduce)").matches;
const SVG = "http://www.w3.org/2000/svg";

export function fmt(v) {
  if (v == null) return "–";
  if (typeof v !== "number") return String(v);
  if (Number.isInteger(v)) return v.toLocaleString("en-US");
  const a = Math.abs(v);
  return (a >= 100 ? v.toFixed(0) : a >= 10 ? v.toFixed(1) : a >= 1 ? v.toFixed(2) : v.toPrecision(2)).replace(/\.?0+$/, (m) => (m.includes(".") ? "" : m));
}
export const statVal = (s) => (s.v == null ? "–" : `${s.unit === "$" ? "$" : ""}${fmt(s.v)}${s.unit && s.unit !== "$" ? " " + s.unit : ""}`);
// Which mini-bar metric a stat belongs to: watts, dollars, or watt-hours (energy, the water proxy).
export const barKey = (s) => (s.unit === "$" ? "$" : s.unit === "W" ? "W" : s.k === "wh" ? "wh" : null);
export const statHtml = (s, pct = null) => `<div class="stat"><span class="k" title="${esc(s.label)}">${esc(s.label)}</span><span class="v num ${s.v == null ? "na" : ""}">${esc(statVal(s))}</span>${s.tag ? pill(s.tag) : ""}${pct == null ? "" : `<span class="mbar" aria-hidden="true"><i style="width:${pct}%"></i></span>`}</div>`;

const SHORT = { meta: "Meta", baseten: "Baseten", e2e: "E2E", hf: "Hugging Face", nvidia: "NVIDIA", sigstore: "Sigstore", oss: "open source" };
const badge = (d, id) => { const p = d.partners[id]; return `<span class="bdg" style="--c:${p.color}" title="${esc(p.name)}">${esc(SHORT[id] || p.name)}</span>`; };
const STATUS = { stub: "stub, not wired", roadmap: "roadmap", "not-run": "not run" };
export const DEEP = { telemetry: "power", apply: "power", gates: "power", flex: "power", e2e_rig: "power", models: "power", adaptive: "experts" };

export class ArchView {
  constructor(host, data, o = {}) {
    this.host = host; this.d = data; this.o = o; this.pins = new Set(); this.timing = {}; this.token = 0; this.paused = false;
    this.nodes = data.nodes.filter((n) => !o.ids || o.ids.includes(n.id));
    if (o.ids) this.nodes.sort((a, b) => o.ids.indexOf(a.id) - o.ids.indexOf(b.id));
    this.ids = new Set(this.nodes.map((n) => n.id));
    this.edges = data.edges.filter((e) => this.ids.has(e.from) && this.ids.has(e.to));
    if (o.chain) this.edges = o.ids.slice(1).map((id, i) => ({ from: o.ids[i], to: id, partner: "oss" }));
    this.state = {}; this.filter = null; this.subOn = {};
    // Per-metric maxima for the mini bars under power / cost / energy numbers.
    this.barMax = {};
    for (const n of this.nodes) for (const s of n.stats) {
      const k = barKey(s);
      if (k && typeof s.v === "number") this.barMax[k] = Math.max(this.barMax[k] || 0, Math.abs(s.v));
    }
    this.build();
  }
  node(id) { return this.d.nodes.find((n) => n.id === id); }
  barPct(s) {
    const k = barKey(s), m = k && this.barMax[k];
    if (!k || !m || typeof s.v !== "number" || s.v <= 0) return null;
    return Math.max(6, Math.round((s.v / m) * 100));
  }
  build() {
    const mode = this.o.mode || "map", d = this.d;
    // Collapsible glass cards live only on the architecture map. The run-pipeline
    // embed (mode "row") keeps the original markup so that view does not change.
    const C = this.collapsible = (this.o.collapse ?? (mode === "map"));
    this.host.innerHTML = `<div class="av ${mode}${C ? " aglass" : ""}" style="--n:${this.nodes.length}"><svg class="av-edges" aria-hidden="true"></svg><div class="av-grid"></div></div>`;
    this.root = this.host.firstElementChild; this.svg = this.root.querySelector("svg"); const grid = this.root.querySelector(".av-grid");
    for (const n of this.nodes) {
      const el = document.createElement("div"); el.className = `avn ${n.status === "code" ? "" : n.status}`; el.dataset.id = n.id; el.setAttribute("role", "group"); el.setAttribute("aria-label", n.title);
      if (mode === "map") { el.style.gridColumn = n.col + 1; el.style.gridRow = n.row + 1; }
      const stats = n.stats.slice(0, this.o.stats ?? 2);
      const subs = `<div class="subs"${C ? ` id="subs-${n.id}"` : ""}>${n.subs.map((s, i) => `${i ? '<div class="lnk"></div>' : ""}<button class="sub ${this.subMeta(n, s) ? "metasub" : ""}" data-sub="${s.id}" title="${esc(s.desc)}"><i>${i + 1}</i><span>${esc(s.title)}</span>${this.subMeta(n, s) ? "<em>Meta</em>" : ""}</button>`).join("")}</div>`;
      if (!C) {
        el.innerHTML = `<div class="hd"><button class="t" title="Open details for ${esc(n.title)}">${esc(n.title)}</button><span class="bd">${n.partners.map((p) => badge(d, p)).join("")}</span></div>
        <p class="d">${esc(n.desc)}</p>${poweredByHtml(poweredByFor(n))}
        ${n.meta_product ? `<div class="bd" style="margin-bottom:6px"><span class="bdg mp" style="--c:var(--m)">Meta · ${esc(n.meta_product)}</span></div>` : ""}
        <div class="stl">${stats.map((s) => statHtml(s)).join("")}</div>
        <div class="now"></div>
        ${subs}
        <div class="ft"><button class="xp" aria-expanded="false">steps (${n.subs.length})</button><button class="pin" aria-pressed="false" title="Keep this step open">pin</button>${STATUS[n.status] ? `<span class="st">${STATUS[n.status]}</span>` : ""}</div>`;
        grid.appendChild(el);
        el.querySelector(".t").onclick = () => this.select(n.id);
        el.querySelector(".xp").onclick = () => this.toggleOpen(n.id);
        el.querySelector(".pin").onclick = (e) => { const on = !this.pins.has(n.id); on ? this.pins.add(n.id) : this.pins.delete(n.id); e.currentTarget.setAttribute("aria-pressed", on); if (on) this.open(n.id, true); else if (!has(this.state[n.id], "on")) this.close(n.id); };
        el.querySelectorAll(".sub").forEach((b) => (b.onclick = () => this.select(n.id, b.dataset.sub)));
        continue;
      }
      const key = n.stats.find((s) => s.v != null) || n.stats[0];
      el.innerHTML = `<div class="hd"><button class="t" aria-expanded="false" aria-controls="subs-${n.id}" title="Expand or collapse ${esc(n.title)}">${esc(n.title)}</button><span class="bd">${n.partners.map((p) => badge(d, p)).join("")}</span></div>
        <button class="sum" data-act="sel" title="Open details for ${esc(n.title)}" aria-label="${esc(key?.label || "value")} for ${esc(n.title)}: open details"><span class="k">${esc(key?.label || "value")}</span><span class="v num">${esc(key ? statVal(key) : "–")}</span>${key?.tag ? pill(key.tag) : ""}</button>
        <p class="d">${esc(n.desc)}</p>${poweredByHtml(poweredByFor(n))}
        ${n.meta_product ? `<div class="bd" style="margin-bottom:6px"><span class="bdg mp" style="--c:var(--m)">Meta · ${esc(n.meta_product)}</span></div>` : ""}
        <div class="stl">${stats.map((s) => statHtml(s, this.barPct(s))).join("")}</div>
        <div class="now"></div>
        ${subs}
        <div class="ft"><button class="xp" aria-expanded="false" aria-controls="subs-${n.id}">steps (${n.subs.length})</button><button class="pin" aria-pressed="false" title="Keep this step open">pin</button>${STATUS[n.status] ? `<span class="st">${STATUS[n.status]}</span>` : ""}</div>`;
      grid.appendChild(el);
      el.querySelector(".t").onclick = () => this.toggleOpen(n.id);
      el.querySelector(".sum").onclick = () => this.select(n.id);
      el.querySelector(".xp").onclick = () => this.toggleOpen(n.id);
      el.querySelector(".pin").onclick = (e) => { const on = !this.pins.has(n.id); on ? this.pins.add(n.id) : this.pins.delete(n.id); e.currentTarget.setAttribute("aria-pressed", on); if (on) this.open(n.id, true); else if (!has(this.state[n.id], "on")) this.close(n.id); };
      el.querySelectorAll(".sub").forEach((b) => (b.onclick = () => this.select(n.id, b.dataset.sub)));
      // Keyboard: the header button toggles natively (Enter/Space fire click).
      // This covers keyboard focus elsewhere in the header chrome itself.
      el.querySelector(".hd").addEventListener("keydown", (e) => {
        if ((e.key === "Enter" || e.key === " ") && !e.target.closest("button")) { e.preventDefault(); this.toggleOpen(n.id); }
      });
    }
    this.layoutEdges();
    this.ro = new ResizeObserver(() => this.layoutEdges()); this.ro.observe(this.root);
    this.applyFilter();
  }
  subMeta(n, s) { return !!s.meta; }
  destroy() { this.stop(); this.ro?.disconnect(); this.paths = null; this.host.innerHTML = ""; }
  el(id) { return this.root.querySelector(`.avn[data-id="${id}"]`); }
  subEl(id, sub) { return this.el(id)?.querySelector(`.sub[data-sub="${sub}"]`); }

  // ---- geometry
  layoutEdges() {
    if (!this.svg) return;
    const R = this.root.getBoundingClientRect(); if (!R.width) return;
    this.svg.setAttribute("viewBox", `0 0 ${R.width} ${R.height}`);
    this.paths = this.paths || new Map(); const seen = new Set();
    for (const e of this.edges) {
      const a = this.el(e.from), b = this.el(e.to); if (!a || !b) continue;
      const A = a.getBoundingClientRect(), B = b.getBoundingClientRect();
      const same = Math.abs(A.left - B.left) < 20;
      let x1, y1, x2, y2, d;
      if (same) { x1 = A.left + A.width / 2 - R.left; y1 = (A.top < B.top ? A.bottom : A.top) - R.top; x2 = B.left + B.width / 2 - R.left; y2 = (A.top < B.top ? B.top : B.bottom) - R.top; const m = (y1 + y2) / 2; d = `M${x1},${y1} C${x1},${m} ${x2},${m} ${x2},${y2}`; }
      else { const fw = A.left < B.left; x1 = (fw ? A.right : A.left) - R.left; y1 = Math.min(A.top + 26, A.bottom) - R.top; x2 = (fw ? B.left : B.right) - R.left; y2 = Math.min(B.top + 26, B.bottom) - R.top; const m = (x1 + x2) / 2; d = `M${x1},${y1} C${m},${y1} ${m},${y2} ${x2},${y2}`; }
      const k = `${e.from}>${e.to}`; seen.add(k);
      let p = this.paths.get(k);
      if (!p) {
        p = document.createElementNS(SVG, "path"); p.dataset.k = k; p.setAttribute("class", "e");
        p.style.setProperty("--c", this.d.partners[e.partner]?.color || "var(--cy)"); this.svg.appendChild(p); this.paths.set(k, p);
      }
      p.setAttribute("d", d);
      if (p._pk) p._pk.firstChild.setAttribute("path", d);   // the travelling dot follows the moved edge
    }
    for (const [k, p] of this.paths) if (!seen.has(k)) { p._pk?.remove(); p.remove(); this.paths.delete(k); }
    this.paintEdges();
  }
  // Marching-ants state of one edge: flow (data moving in), done (static, dim) or idle.
  edgeState(f, t) {
    const sf = this.state[f], st = this.state[t];
    if (has(st, "on") && (has(sf, "on") || has(sf, "done"))) return this.paused ? "flow paused" : "flow";
    if (has(sf, "done") && has(st, "done")) return "done";
    return "idle";
  }
  paintEdges() {
    if (!this.paths) return;
    for (const [k, p] of this.paths) {
      const [f, t] = k.split(">"), e = this.edges.find((x) => x.from === f && x.to === t);
      const s = this.edgeState(f, t);
      p.classList.toggle("flow", s.startsWith("flow")); p.classList.toggle("halt", s === "flow paused"); p.classList.toggle("done", s === "done");
      p.classList.toggle("live", s.startsWith("flow"));
      this.dot(p, s === "flow" && !reduce());
      if (this.filter) { const hit = this.matches(this.node(f)) && this.matches(this.node(t)); p.classList.toggle("hi", hit); p.classList.toggle("fade", !hit); } else { p.classList.remove("hi", "fade"); }
    }
  }
  // a small packet that travels along an edge while data flows on it (SMIL, so it needs no per-frame work)
  dot(p, on) {
    if (on && !p._pk) {
      const c = document.createElementNS(SVG, "circle"); c.setAttribute("r", "3.4"); c.setAttribute("class", "pk");
      c.style.setProperty("--c", p.style.getPropertyValue("--c"));
      const m = document.createElementNS(SVG, "animateMotion"); m.setAttribute("dur", "1.3s"); m.setAttribute("repeatCount", "indefinite"); m.setAttribute("path", p.getAttribute("d"));
      c.appendChild(m); this.svg.appendChild(c); p._pk = c;
    } else if (!on && p._pk) { p._pk.remove(); p._pk = null; }
  }
  // ---- filter by partner
  matches(n) { return !this.filter || n.partners.includes(this.filter); }
  setFilter(p) { this.filter = p || null; this.applyFilter(); }
  applyFilter() {
    for (const n of this.nodes) {
      const el = this.el(n.id), ok = this.matches(n), color = this.filter ? this.d.partners[this.filter]?.color : null;
      el.classList.toggle("dim", !!this.filter && !ok); el.classList.toggle("hi", !!this.filter && ok);
      if (color) el.style.setProperty("--c", color);
      el.querySelectorAll(".sub").forEach((b) => { const s = n.subs.find((x) => x.id === b.dataset.sub); b.style.opacity = this.filter && !(s.partner === this.filter || (!s.partner && ok)) ? ".35" : ""; });
    }
    this.paintEdges();
  }
  // ---- open/close
  isOpen(id) { return this.el(id).classList.contains("open"); }
  toggleOpen(id) { this.isOpen(id) ? this.close(id) : this.open(id, true); }
  syncToggle(id, open) {
    const el = this.el(id); if (!el) return;
    if (this.collapsible) el.querySelector(".t")?.setAttribute("aria-expanded", String(open));
    el.querySelector(".xp")?.setAttribute("aria-expanded", String(open));
  }
  open(id, manual) {
    const el = this.el(id); if (!el || el.classList.contains("open")) { this.syncToggle(id, true); return; }
    el.classList.add("open"); this.syncToggle(id, true);
    this.tween(el.querySelector(".subs"), true);
  }
  close(id) {
    const el = this.el(id); if (!el || this.pins.has(id)) return;
    this.syncToggle(id, false);
    this.tween(el.querySelector(".subs"), false, () => el.classList.remove("open"));
  }
  expandAll() { for (const n of this.nodes) this.open(n.id, true); this.layoutEdges(); }
  collapseAll() { for (const n of this.nodes) this.close(n.id); this.layoutEdges(); }
  tween(sub, open, done) {
    const G = g();
    if (!G || reduce()) { done?.(); this.layoutEdges(); return; }
    G.killTweensOf(sub); const tick = () => this.layoutEdges(); G.ticker.add(tick);
    const fin = () => { G.ticker.remove(tick); sub.style.height = ""; sub.style.opacity = ""; this.layoutEdges(); done?.(); };
    if (open) { sub.style.display = "flex"; G.fromTo(sub, { height: 0, opacity: 0 }, { height: "auto", opacity: 1, duration: 0.3, ease: "power2.out", onComplete: fin }); }
    else G.to(sub, { height: 0, opacity: 0, duration: 0.25, ease: "power2.in", onComplete: () => { sub.style.display = ""; fin(); } });
  }
  // ---- activation (live and replay both call these)
  setState(id, s) { this.state[id] = s; const el = this.el(id); if (!el) return; el.classList.toggle("on", has(s, "on")); el.classList.toggle("done", has(s, "done")); el.classList.toggle("fail", has(s, "fail")); this.paintEdges(); }
  reset() {
    this.stop(); this.state = {}; this.subOn = {};
    this.root.querySelectorAll(".avn").forEach((el) => { el.classList.remove("on", "done", "fail"); el.querySelector(".now").textContent = ""; if (!this.pins.has(el.dataset.id)) { el.classList.remove("open"); el.querySelector(".subs").style.cssText = ""; this.syncToggle(el.dataset.id, false); } });
    this.root.querySelectorAll(".sub").forEach((b) => b.classList.remove("on", "done", "bad"));
    this.root.querySelectorAll(".lnk").forEach((b) => b.classList.remove("live"));
    this.layoutEdges();
  }
  light(id, sub, ok = true) {
    const n = this.node(id); if (!n) return; const idx = n.subs.findIndex((s) => s.id === sub);
    const el = this.el(id); const bs = [...el.querySelectorAll(".sub")], ls = [...el.querySelectorAll(".lnk")];
    bs.forEach((b, i) => { if (i < idx) { b.classList.remove("on"); b.classList.add("done"); } b.classList.toggle("on", i === idx); });
    ls.forEach((l, i) => l.classList.toggle("live", i === idx - 1));
    if (bs[idx] && !ok) bs[idx].classList.add("bad");
    return bs[idx];
  }
  activate(c) {
    if (!this.ids.has(c.node)) return;
    const prev = this.cur;
    if (prev && prev !== c.node) this.finish(prev);
    const fresh = this.cur !== c.node; this.cur = c.node;
    const el = this.el(c.node), failed = (this.state[c.node] || "").includes("fail") || c.ok === false;
    this.setState(c.node, "on" + (failed ? " fail" : ""));
    this.open(c.node);
    if (fresh && this.o.follow !== false) requestAnimationFrame(() => el.scrollIntoView({ block: "nearest", inline: "center", behavior: reduce() ? "auto" : "smooth" }));
    this.light(c.node, c.sub, c.ok !== false);
    el.querySelector(".now").innerHTML = `${esc(c.text)}${c.tag ? " " + pill(c.tag) : ""}`;
    if (c.took != null) this.timing[`${c.node}.${c.sub}`] = c.took;
    this.o.onCue?.(c);
  }
  finish(id) {
    const el = this.el(id); if (!el) return;
    el.querySelectorAll(".sub").forEach((b) => { if (b.classList.contains("on")) { b.classList.remove("on"); b.classList.add("done"); } });
    el.querySelectorAll(".lnk").forEach((l) => l.classList.remove("live"));
    this.setState(id, "done" + ((this.state[id] || "").includes("fail") ? " fail" : ""));
    if (!this.pins.has(id)) this.close(id);
  }
  // ---- replay of cues
  stop() { this.token++; this.paused = false; }
  pause() { this.paused = true; this.paintEdges(); } resume() { this.paused = false; this.paintEdges(); }
  wait(ms, tok) { return new Promise((r) => { const t0 = performance.now(); const f = () => { if (tok !== this.token) return r(false); if (this.paused) { return setTimeout(f, 60); } performance.now() - t0 >= ms ? r(true) : setTimeout(f, 30); }; f(); }); }
  async play(cues, { speed = 1, gap = 950 } = {}) {
    this.reset(); const tok = this.token;
    for (let i = 0; i < cues.length; i++) {
      const c = cues[i]; this.o.onProgress?.(i, cues.length);
      if (!(await this.wait(120 / speed, tok))) return false;
      // light skipped earlier sub-steps in order before the one that happened
      const n = this.node(c.node), idx = n.subs.findIndex((s) => s.id === c.sub);
      if (n && idx > 0 && this.cur !== c.node) for (let k = 0; k < idx; k++) { this.activate({ ...c, sub: n.subs[k].id, text: n.subs[k].title, tag: null }); if (!(await this.wait(150 / speed, tok))) return false; }
      this.activate(c);
      if (!(await this.wait(gap / speed, tok))) return false;
    }
    if (this.cur) this.finish(this.cur); this.cur = null; this.o.onProgress?.(cues.length, cues.length);
    return true;
  }
  select(id, sub) { this.o.onSelect?.(this.node(id), sub ? this.node(id).subs.find((s) => s.id === sub) : null, this); }
}

// ---- the side panel
export function panelHtml(view, n, s) {
  const d = view.d, p = s?.partner || n.partners[0];
  const t = view.timing[`${n.id}.${s?.id}`] ?? view.timing[`${n.id}.*`];
  const subStat = { "agent.call": "a_p50", "baseten_model.send": "p50", "telemetry.probe": "probe_s" }[`${n.id}.${s?.id}`];
  const ts = subStat && n.stats.find((x) => x.k === subStat);
  const timing = t != null ? `${fmt(t)} s ${pill("MEASURED")} <span class="muted">(the run's own stage log)</span>` : ts?.v != null ? `${fmt(ts.v)} s ${pill(ts.tag)} <span class="muted">(${esc(ts.label)})</span>` : `– <span class="muted">not timed per step in the recorded runs</span>`;
  const used = n.used_for?.[p] || n.used_for?.[n.partners[0]] || "";
  const usedMeta = n.used_for?.meta || used;
  const ev = [...new Set(n.stats.map((x) => x.src).filter(Boolean))];
  const link = DEEP[n.id];
  const meta = (n.meta_product && (!s || view.subMeta(n, s))) ? `<div class="metaline"><b>Meta did this:</b> ${esc(n.meta_product)}. ${esc(usedMeta)}</div>` : "";
  const spec = s || n;
  return `<h3>${esc(s ? s.title : n.title)}</h3>
    <div class="bd" style="display:flex;gap:4px;flex-wrap:wrap;margin:4px 0 8px">${(s?.partner ? [s.partner] : n.partners).map((x) => badge(d, x)).join("")}${STATUS[n.status] ? `<span class="bdg" style="--c:var(--dim)">${STATUS[n.status]}</span>` : ""}</div>
    ${s ? `<p class="muted">in ${esc(n.title)}</p>` : ""}
    <p>${esc(spec.desc)}</p>${meta}
    <h5>Code</h5><code>${esc(spec.code || n.code)}</code>
    <div class="row2"><div><h5>Goes in</h5><p>${esc(spec.inputs || "–")}</p></div><div><h5>Comes out</h5><p>${esc(spec.outputs || "–")}</p></div></div>
    <h5>Time</h5><p>${timing}</p>
    <h5>${esc(d.partners[p]?.name || "Partner")}${used ? " · used for" : ""}</h5><p>${esc(used) || "–"}</p>
    <h5>${s ? "Numbers for this step's module" : "Numbers"}</h5>${n.stats.map((s) => statHtml(s)).join("") || "–"}
    ${!s ? `<h5>Sub-steps</h5><ul>${n.subs.map((x, i) => `<li><a href="#" data-sub="${x.id}" style="color:var(--cy);text-decoration:none">${i + 1}. ${esc(x.title)}</a></li>`).join("")}</ul>` : ""}
    <h5>Evidence (last receipts and logs)</h5><ul>${(n.evidence || []).map((x) => `<li>${esc(x)}</li>`).join("")}${ev.map((x) => `<li class="muted">${esc(x)}</li>`).join("") || (n.evidence ? "" : "<li>–</li>")}</ul>
    <div class="go">${link ? `<button class="btn" data-go="${link}">Open in ${link === "power" ? "Power" : "Experts"}</button>` : ""}${n.partners.includes("meta") ? `<button class="btn" data-go="meta">Show everything Meta did</button>` : ""}</div>`;
}
