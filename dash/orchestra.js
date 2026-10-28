// The orchestra: every expert is a seat. Sections are groups (tiers or k-means clusters), rows are
// layers. Seat colour = its group, brightness = how busy it is, flash = routed to just now.
import * as C from "./core.js";
import { GROUP_COLORS, esc, dot, pct } from "./theme.js";

export class Orchestra {
  constructor(host, ctx) {
    this.host = host; this.ctx = ctx; this.cv = null; this.geo = null; this.mask = null;
    new ResizeObserver(() => { this.layout(); }).observe(host);
  }

  build() {
    const { st } = this.ctx, colors = GROUP_COLORS[st.mode];
    this.host.innerHTML = `<h3>orchestra · every expert is a seat</h3>
      <div class="legend">${st.names.map((n, g) => `<span data-g="${g}"><i style="background:${colors[g]}"></i>${esc(n)}</span>`).join("")}
        <span>brightness = activation</span><span>rows = layers</span></div>
      <canvas id="stage"></canvas>
      <div class="note" id="o-note"></div>`;
    this.cv = this.host.querySelector("#stage");
    this.cv.onmousemove = (ev) => this.onMove(ev);
    this.cv.onmouseleave = () => { this.ctx.hoverGroup(null); this.ctx.tipUnit(null); };
    this.cv.onclick = (ev) => { const h = this.hit(ev); if (h) this.ctx.selectGroup(h.g); };
    this.host.querySelectorAll(".legend span[data-g]").forEach((s) => {
      s.style.cursor = "pointer"; const g = +s.dataset.g;
      s.onmouseenter = () => this.ctx.hoverGroup(g); s.onmouseleave = () => this.ctx.hoverGroup(null);
      s.onclick = () => this.ctx.selectGroup(g);
    });
    this.layout();
  }

  layout() {
    if (!this.cv) return;
    const { st } = this.ctx, dpr = window.devicePixelRatio || 1;
    const W = this.cv.clientWidth, H = this.cv.clientHeight; if (!W || !H) return;
    this.cv.width = W * dpr; this.cv.height = H * dpr;
    const L = st.L, E = st.E, k = st.k;
    const per = Array.from({ length: k }, () => 0);
    for (let l = 0; l < L; l++) { const c = new Array(k).fill(0); for (let e = 0; e < E; e++) c[st.assign[l * E + e]]++; c.forEach((v, g) => (per[g] = Math.max(per[g], v))); }
    const gap = 14, left = 22, top = 34, bottom = 8;
    const seatsTotal = per.reduce((s, v) => s + v, 0);
    const cell = Math.min((W - left - gap * (k - 1) - 4) / seatsTotal, (H - top - bottom) / L * 0.82);
    const rowH = (H - top - bottom) / L;
    const sec = []; let x = left;
    for (let g = 0; g < k; g++) { sec.push({ g, x, w: per[g] * cell, n: per[g] }); x += per[g] * cell + gap; }
    // seat position per unit
    const pos = new Array(st.U);
    for (let l = 0; l < L; l++) {
      const idx = new Array(k).fill(0);
      for (let e = 0; e < E; e++) {
        const u = l * E + e, g = st.assign[u], i = idx[g]++;
        pos[u] = [sec[g].x + i * cell + cell / 2, top + l * rowH + rowH / 2];
      }
    }
    this.geo = { W, H, dpr, cell, rowH, sec, pos, top, left, r: Math.max(1.6, Math.min(cell, rowH) * 0.4) };
  }

  hit(ev) {
    const { st } = this.ctx, g = this.geo; if (!g) return null;
    const r = this.cv.getBoundingClientRect(), x = ev.clientX - r.left, y = ev.clientY - r.top;
    const l = Math.floor((y - g.top) / g.rowH); if (l < 0 || l >= st.L) { const s = g.sec.find((s) => x >= s.x && x <= s.x + s.w && y < g.top - 4); return s ? { g: s.g, u: null } : null; }
    let best = null, bd = 1e9;
    for (let e = 0; e < st.E; e++) { const u = l * st.E + e, p = g.pos[u], d = Math.hypot(p[0] - x, p[1] - y); if (d < bd) { bd = d; best = u; } }
    return bd < g.cell ? { g: st.assign[best], u: best } : null;
  }

  onMove(ev) {
    const h = this.hit(ev);
    this.ctx.hoverGroup(h ? h.g : null); this.ctx.tipUnit(h && h.u != null ? h.u : null, ev);
  }

  draw() {
    const { st, arm } = this.ctx, g = this.geo, cv = this.cv; if (!g || !cv) return;
    const x = cv.getContext("2d"); x.setTransform(g.dpr, 0, 0, g.dpr, 0, 0);
    x.clearRect(0, 0, g.W, g.H);
    const colors = GROUP_COLORS[st.mode], counts = this.ctx.live() ? st.arm[arm].units : st.meta.counts[arm], max = Math.max(...counts) || 1;
    const ratio = C.skipRatio(st), f = this.ctx.focus();
    const gt = C.groupTotals(st, arm);
    // section headers
    x.textBaseline = "alphabetic";
    for (const s of g.sec) {
      const dim = f.group != null && f.group !== s.g;
      x.globalAlpha = dim ? 0.35 : 1; x.fillStyle = colors[s.g]; x.font = "500 11px JetBrains Mono, monospace";
      x.fillText(st.names[s.g], s.x, 14);
      x.fillStyle = "#A79BB9"; x.font = "10px JetBrains Mono, monospace";
      const e = C.jPerSlot(st, arm) == null ? "" : ` · ~${C.fmtJ(gt[s.g].joules)} J`;
      x.fillText(s.w < 100 ? pct(gt[s.g].share) : `${pct(gt[s.g].share)} tokens${e}`, s.x, 26);
      x.strokeStyle = colors[s.g]; x.globalAlpha = dim ? 0.1 : 0.35; x.beginPath(); x.moveTo(s.x, g.top - 3); x.lineTo(s.x + s.w, g.top - 3); x.stroke();
      if (this.ctx.pickedGroup() === s.g) { x.globalAlpha = 0.9; x.lineWidth = 1.5; x.strokeRect(s.x - 4, g.top - 6, s.w + 8, st.L * g.rowH + 8); x.lineWidth = 1; }
    }
    x.globalAlpha = 1;
    // row labels
    x.fillStyle = "#6b4a70"; x.font = "9px JetBrains Mono, monospace";
    for (let l = 0; l < st.L; l += 4) x.fillText(`L${l}`, 0, g.top + l * g.rowH + g.rowH * 0.8);
    // seats
    const mask = f.mask;
    for (let u = 0; u < st.U; u++) {
      const [px, py] = g.pos[u], gi = st.assign[u], col = colors[gi];
      const use = (counts[u] / max) ** 0.55;
      let a = 0.16 + 0.5 * use;
      if (mask) a = a * (0.12 + 0.88 * mask[u]) + 0.3 * mask[u] * mask[u];
      const r = st.recent[u] * (mask ? 0.2 + 0.8 * mask[u] : 1);
      x.globalAlpha = Math.min(1, a + r * 0.55);
      x.fillStyle = col;
      if (r > 0.25) { x.shadowColor = col; x.shadowBlur = 5 + 8 * Math.min(1, r); }
      x.beginPath(); x.arc(px, py, g.r * (1 + 0.25 * Math.min(1, r)), 0, 6.283); x.fill();
      x.shadowBlur = 0;
      // seats the adaptive arm skips most: a dark slash
      if (arm === "on" && ratio[u] < 0.55) { x.globalAlpha = 0.85; x.strokeStyle = "#080B10"; x.lineWidth = 1.1; x.beginPath(); x.moveTo(px - g.r, py + g.r); x.lineTo(px + g.r, py - g.r); x.stroke(); x.lineWidth = 1; }
    }
    x.globalAlpha = 1;
  }

  note() {
    const { st, arm } = this.ctx;
    const el = this.host.querySelector("#o-note"); if (!el) return;
    el.innerHTML = arm === "on"
      ? `adaptive top-k is on: seats crossed out are used far less (${dot("MEASURED")} routing).`
      : `normal top-k: each token uses ${st.meta.meta.top_k} seats per layer ${dot("MEASURED")}`;
  }
}
