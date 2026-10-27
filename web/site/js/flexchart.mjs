// Flex chart: power, p99, power limit and answer quality over one grid event.
// Input is a `<id>.trace.json` file written by `wattnot flex run` (see docs/moonshot/API-SHAPES.md).
import { svg, el, reduced } from "./ui.mjs";

const COL = { power: "#5FE3F5", target: "#9B8CFF", cap: "#7C8599", p99: "#4D8DFF", sla: "#F5C56B", pass: "#5CE6A8", ev: "rgba(155,140,255,.09)", grid: "rgba(255,255,255,.07)", tx: "#8B94A8" };

function scale(d0, d1, r0, r1) { const f = (v) => r0 + ((v - d0) / (d1 - d0)) * (r1 - r0); f.d = [d0, d1]; return f; }
function nice(lo, hi, n = 4) {
  const span = hi - lo || 1, step0 = span / n, mag = 10 ** Math.floor(Math.log10(step0));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => s >= step0);
  const a = Math.floor(lo / step) * step, b = Math.ceil(hi / step) * step, t = [];
  for (let v = a; v <= b + 1e-9; v += step) t.push(+v.toPrecision(10));
  return { lo: a, hi: b, ticks: t };
}
function path(pts, x, y, step = false) {
  let d = "";
  pts.forEach((p, i) => {
    const X = x(p[0]).toFixed(1), Y = y(p[1]).toFixed(1);
    if (!i) d += `M${X},${Y}`;
    else if (step) d += `H${X}V${Y}`;
    else d += `L${X},${Y}`;
  });
  return d;
}

// opts: {compact, onHover(point|null), label}
export function flexChart(host, trace, opts = {}) {
  const pts = trace.points, T = pts[pts.length - 1].t_s;
  const ev = pts.filter((p) => p.phase === "event");
  const evStart = ev.length ? ev[0].t_s : 0, evEnd = ev.length ? ev[ev.length - 1].t_s + trace.dt_s : 0;
  const W = 1000, L = 62, R = 18, PL = L, PR = W - R;
  const x = scale(0, T, PL, PR);
  const panels = opts.compact
    ? [{ key: "power", h: 210, title: "GPU power (W)", fmt: (v) => Math.round(v) }]
    : [{ key: "power", h: 230, title: "GPU power (W)", fmt: (v) => Math.round(v) },
       { key: "p99", h: 130, title: "p99 latency (ms)", fmt: (v) => Math.round(v) },
       { key: "cap", h: 74, title: "Power limit set (W)", fmt: (v) => Math.round(v) },
       { key: "pass", h: 74, title: "Answers passing", fmt: (v) => (v * 100).toFixed(0) + "%" }];
  const GAP = 30, TOP = 8, BOT = 30;
  let y0 = TOP; for (const p of panels) { p.y0 = y0; y0 += p.h + GAP; }
  const H = y0 - GAP + BOT;

  const baseW = trace.points.filter((p) => p.phase === "before").reduce((a, p, _, arr) => a + p.power_w / arr.length, 0);
  const target = ev.find((p) => p.target_w) ? ev.find((p) => p.target_w).target_w : null;
  const sla = ev.find((p) => p.sla_ms) ? ev.find((p) => p.sla_ms).sla_ms : null;

  const dom = {
    power: nice(Math.min(...pts.map((p) => p.power_w), target ?? 1e9) * 0.94, Math.max(...pts.map((p) => p.power_w)) * 1.04, 3),
    p99: nice(Math.min(...pts.map((p) => p.p99_ms)) * 0.6, Math.max(sla ?? 0, ...pts.map((p) => p.p99_ms)) * 1.06, 3),
    cap: nice(Math.min(...pts.map((p) => p.cap_w)) - 60, Math.max(...pts.map((p) => p.cap_w)) + 40, 2),
    pass: nice(0.5, 1, 2),
  };
  const root = svg("svg", { viewBox: `0 0 ${W} ${H}`, class: "fx", role: "img", "aria-label": opts.label || "Chart of GPU power, p99 latency, power limit and answer quality during a simulated grid event", preserveAspectRatio: "xMidYMid meet" });
  const clipId = "clip" + Math.random().toString(36).slice(2, 7);
  const clipRect = svg("rect", { x: 0, y: 0, width: W, height: H });
  root.append(svg("defs", {}, svg("clipPath", { id: clipId }, clipRect)));

  const reveal = svg("g", { "clip-path": `url(#${clipId})` });
  for (const p of panels) {
    const y = scale(dom[p.key].hi, dom[p.key].lo, p.y0, p.y0 + p.h);
    p.y = y;
    const g = svg("g", {});
    // event window and grid
    if (ev.length) g.append(svg("rect", { x: x(evStart), y: p.y0, width: x(evEnd) - x(evStart), height: p.h, fill: COL.ev }));
    for (const t of dom[p.key].ticks) {
      if (t < dom[p.key].lo || t > dom[p.key].hi) continue;
      g.append(svg("line", { x1: PL, x2: PR, y1: y(t), y2: y(t), stroke: COL.grid }),
        svg("text", { x: PL - 8, y: y(t) + 3.5, "text-anchor": "end", class: "ax" }, p.fmt(t)));
    }
    g.append(svg("text", { x: PL, y: p.y0 - 9, class: "ax ttl" }, p.title));
    root.append(g);
    // series
    const S = (key) => pts.map((q) => [q.t_s, q[key]]);
    const add = (n) => reveal.append(n);
    if (p.key === "power") {
      add(svg("line", { x1: PL, x2: PR, y1: y(baseW), y2: y(baseW), stroke: COL.tx, "stroke-dasharray": "2 5", opacity: .7 }));
      add(svg("text", { x: ev.length ? (x(evStart) + x(evEnd)) / 2 : PR, y: y(baseW) - 6, "text-anchor": ev.length ? "middle" : "end", class: "ax" }, `power before the event: ${baseW.toFixed(0)} W`));
      if (target) {
        add(svg("line", { x1: x(evStart), x2: x(evEnd), y1: y(target), y2: y(target), stroke: COL.target, "stroke-width": 1.4, "stroke-dasharray": "7 5" }));
        add(svg("text", { x: x(evEnd) - 6, y: y(target) + 15, "text-anchor": "end", class: "ax", fill: COL.target }, `target ${target.toFixed(0)} W`));
      }
      add(svg("path", { d: path(S("power_w"), x, y), fill: "none", stroke: COL.power, "stroke-width": 2, "stroke-linejoin": "round" }));
    }
    if (p.key === "p99") {
      if (sla) {
        add(svg("line", { x1: PL, x2: PR, y1: y(sla), y2: y(sla), stroke: COL.sla, "stroke-dasharray": "7 5", "stroke-width": 1.2 }));
        add(svg("text", { x: PR, y: y(sla) - 6, "text-anchor": "end", class: "ax", fill: COL.sla }, `latency budget ${Math.round(sla)} ms`));
      }
      add(svg("path", { d: path(S("p99_ms"), x, y), fill: "none", stroke: COL.p99, "stroke-width": 1.8, "stroke-linejoin": "round" }));
    }
    if (p.key === "cap") add(svg("path", { d: path(S("cap_w"), x, y, true), fill: "none", stroke: COL.cap, "stroke-width": 1.6 }));
    if (p.key === "pass") add(svg("path", { d: path(S("pass_rate"), x, y), fill: "none", stroke: COL.pass, "stroke-width": 1.8 }));
  }
  root.append(reveal);
  // time axis
  const axY = H - BOT + 16;
  for (let t = 0; t <= T + 0.1; t += 60) root.append(svg("text", { x: x(t), y: axY, "text-anchor": "middle", class: "ax" }, `${t}s`));
  if (ev.length) {
    root.append(svg("text", { x: (x(evStart) + x(evEnd)) / 2, y: panels[0].y0 + 16, "text-anchor": "middle", class: "ax", fill: COL.target },
      `GRID ASKS FOR ${trace.event.target_pct}% LESS · SIGNAL IS ${trace.label}`));
  }
  // hover
  const cursor = svg("line", { y1: panels[0].y0, y2: H - BOT, stroke: "#fff", "stroke-opacity": .5, visibility: "hidden" });
  const hit = svg("rect", { x: PL, y: 0, width: PR - PL, height: H - BOT, fill: "transparent", style: "cursor:crosshair" });
  root.append(cursor, hit);
  const at = (ev2) => {
    const r = root.getBoundingClientRect(), px = ((ev2.clientX - r.left) / r.width) * W;
    const t = Math.max(0, Math.min(T, ((px - PL) / (PR - PL)) * T));
    return pts[Math.min(pts.length - 1, Math.round(t / trace.dt_s))];
  };
  const move = (e) => { const q = at(e); cursor.setAttribute("x1", x(q.t_s)); cursor.setAttribute("x2", x(q.t_s)); cursor.setAttribute("visibility", "visible"); opts.onHover && opts.onHover(q); };
  hit.addEventListener("pointermove", move);
  hit.addEventListener("pointerdown", move);
  hit.addEventListener("pointerleave", () => { cursor.setAttribute("visibility", "hidden"); opts.onHover && opts.onHover(null); });

  host.replaceChildren(root);
  let raf = 0;
  const api = {
    set(p) { clipRect.setAttribute("width", String(PL + (PR - PL) * p + (p >= 1 ? R : 0))); },
    play(ms = 5200) {
      cancelAnimationFrame(raf);
      if (reduced()) return api.set(1);
      const t0 = performance.now();
      const tick = (now) => { const p = Math.min(1, (now - t0) / ms); api.set(1 - (1 - p) ** 2); if (p < 1) raf = requestAnimationFrame(tick); };
      api.set(0); raf = requestAnimationFrame(tick);
    },
  };
  api.set(reduced() ? 1 : 0);
  return api;
}
