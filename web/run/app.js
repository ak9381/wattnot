import { NODES, LOOP, initialState, reduce, capPath } from "./state.js";

const q = new URLSearchParams(location.search);
const $ = (id) => document.getElementById(id);
const NS = "http://www.w3.org/2000/svg";
let state = initialState();
let selected = q.get("node");
const esc = (x) => String(x ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const lbl = (l) => (l ? `<span class="lbl ${l}">${l}</span>` : "");

// layout: one row, loop arc from Keep back to Spark
const W = 138, H = 118, GAP = 14, Y = 120;
const pos = Object.fromEntries(NODES.map((n, i) => [n.id, { x: 10 + i * (W + GAP), y: Y }]));

function el(tag, attrs, parent) {
  const e = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v);
  if (parent) parent.appendChild(e);
  return e;
}

function drawGraph() {
  const g = $("graph");
  g.innerHTML = `<defs><filter id="glow" x="-40%" y="-40%" width="180%" height="180%"><feGaussianBlur stdDeviation="6" result="b"/>
    <feFlood flood-color="#5FE3F5" flood-opacity=".55"/><feComposite in2="b" operator="in"/><feMerge><feMergeNode/><feMergeNode in="SourceGraphic"/></feMerge></filter></defs>`;
  NODES.slice(0, -1).forEach((n, i) => {
    const a = pos[n.id], b = pos[NODES[i + 1].id];
    const hot = state.nodes[NODES[i + 1].id].status !== "idle";
    el("path", { d: `M${a.x + W} ${a.y + H / 2} L${b.x} ${b.y + H / 2}`, class: `edge${hot ? " hot" : ""}` }, g);
  });
  const s = pos.spark, k = pos.keep;
  const running = !state.done && LOOP.includes(state.active) && state.loop > 0;
  el("path", { d: `M${k.x + W / 2} ${k.y} C${k.x + W / 2} 18, ${s.x + W / 2} 18, ${s.x + W / 2} ${s.y}`, class: `loop${running ? " run" : ""}` }, g);
  const lt = el("text", { x: (s.x + k.x + W) / 2, y: 40, "text-anchor": "middle", "font-size": 12, fill: "#5FE3F5" }, g);
  lt.textContent = state.loop ? `try ${state.loop} · lower the cap until the gate fails` : "loop";
  for (const n of NODES) {
    const st = state.nodes[n.id], p = pos[n.id];
    const fail = (n.id === "gate" && st.verdict === "fail") || (n.id === "keep" && st.verdict === "reverted");
    const grp = el("g", { class: `node ${st.status}${fail ? " fail" : ""}`, transform: `translate(${p.x},${p.y})`, "data-id": n.id }, g);
    el("rect", { width: W, height: H, rx: 12 }, grp);
    const t = el("text", { x: 12, y: 24, class: "t" }, grp); t.textContent = n.title;
    st.stats.slice(0, 3).forEach((x, i) => {
      const y = 50 + i * 22;
      const a = el("text", { x: 12, y, class: "s" }, grp); a.textContent = `${x.k} ${x.v}`;
      if (x.label) { const l = el("text", { x: 12, y: y + 11, class: `l ${x.label}` }, grp); l.textContent = x.label; }
    });
    grp.addEventListener("click", () => { selected = n.id; drawPanel(); });
  }
  $("cappath").textContent = capPath(state) ? `cap (W): ${capPath(state)}` : "";
}

function drawPower() {
  const pts = state.power;
  const svg = $("power");
  if (!pts.length) { svg.innerHTML = ""; return; }
  const max = 720, n = Math.max(pts.length - 1, 1);
  const xy = pts.map((p, i) => `${(i / n) * 1240},${90 - (p.w / max) * 80}`).join(" ");
  svg.innerHTML = `<defs><linearGradient id="pg" x1="0" x2="0" y1="0" y2="1"><stop offset="0" stop-color="#4D8DFF" stop-opacity=".35"/><stop offset="1" stop-color="#4D8DFF" stop-opacity="0"/></linearGradient></defs>
    <polygon points="0,90 ${xy} 1240,90" fill="url(#pg)"/><polyline points="${xy}" fill="none" stroke="#5FE3F5" stroke-width="2" vector-effect="non-scaling-stroke"/>`;
  const last = pts[pts.length - 1];
  $("powlabel").outerHTML = `<span id="powlabel"><span class="m"> ${last.w} W</span>${lbl(last.label)}</span>`;
}

function drawPanel() {
  const p = $("panel");
  document.body.classList.toggle("panel", !!selected);
  if (!selected) { p.hidden = true; return; }
  const n = NODES.find((x) => x.id === selected), st = state.nodes[selected];
  const last = state.tries[state.tries.length - 1] || {};
  let h = `<div class="k">Node</div><h3>${esc(n.title)}</h3>`;
  h += `<ul>${st.stats.map((x) => `<li>${esc(x.k)}: <b class="m">${esc(x.v)}</b>${lbl(x.label)}</li>`).join("")}</ul>`;
  const d = last.decision;
  if (d) {
    h += `<div class="k">Muse Spark reasoning (latest try)</div><pre>${esc(d.reason)}</pre>`;
    h += `<div class="k">Logged model call</div><pre>model: ${esc(d.model)}\ntokens: ${d.tokens.input} in / ${d.tokens.output} out\nclamped by our code: ${d.clamped ? "yes" : "no"}${d.model === "fallback" ? "\n(fixed rules decided; no Meta call)" : ""}</pre>`;
  }
  if (last.prompts && last.prompts.length) h += `<div class="k">Prompts that ran</div><ul>${last.prompts.map((x) => `<li>${esc(x)}</li>`).join("")}</ul>`;
  if (last.gates && last.gates.length) {
    h += `<div class="k">Gate verdict</div><ul>${last.gates.map((g) => `<li class="${g.passed ? "pass" : "failc"}">${esc(g.check)}: ${g.passed ? "pass" : "FAIL"} <span class="m mu">(${esc(g.detail.score)} vs ${esc(g.detail.threshold)})</span>${lbl("MEASURED")}</li>`).join("")}</ul>`;
  }
  if (last.outcome) h += `<div class="k">Outcome</div><pre>${last.cap_w} W ${last.outcome}</pre>`;
  $("pbody").innerHTML = h;
  p.hidden = false;
}

function drawResults() {
  const r = $("results");
  if (!state.done || q.get("results") === "0") { r.hidden = true; return; }
  const b = state.baseline, f = state.final || b, c = state.compare, rec = state.recommendations;
  const pct = (x, y) => (((y - x) / x) * 100).toFixed(1);
  const row = (name, bv, fv, unit, lower = true, label = "MEASURED") => {
    const better = lower ? fv <= bv : fv >= bv;
    return `<tr><td>${name}${lbl(label)}</td><td class="n">${bv}${unit}</td><td class="n">${fv}${unit}</td><td class="n ${better ? "better" : "worse"}">${pct(bv, fv)}%</td></tr>`;
  };
  const acts = rec ? rec.actions.map((a) => `<li>${lbl(a.label)} ${esc(a.text)}</li>`).join("") : "";
  const applies = rec ? rec.applies.map((a) => `<li>· ${esc(a)}</li>`).join("") : "";
  const rack = q.get("rack");
  r.innerHTML = `<div class="rw">
   <div class="gl2 card"><div class="k">Results ${state.example ? '<span class="tag ex">EXAMPLE</span>' : ""}</div><h1>${f.cap_w < b.cap_w ? `${b.cap_w} W → ${f.cap_w} W, gate passed` : `kept ${b.cap_w} W: no lower cap passed the gate`}</h1>
    <table><tr><th></th><th>Before</th><th>After</th><th>Change</th></tr>
     ${row("Power cap", b.cap_w, f.cap_w, " W")}
     ${row("Energy per request", b.wh.toFixed(4), f.wh.toFixed(4), " Wh")}
     ${row("p99 latency", Math.round(b.p99_ms), Math.round(f.p99_ms), " ms")}</table>
    ${c ? `<div class="k" style="margin-top:18px">Compared with a plain fixed cap</div>
    <table><tr><th></th><th>Cap</th><th>Wh/req</th><th>p99</th><th>Gate</th></tr>
     <tr><td>WattNot (gated)${lbl("MEASURED")}</td><td class="n">${f.cap_w} W</td><td class="n">${f.wh.toFixed(4)}</td><td class="n">${Math.round(f.p99_ms)} ms</td><td class="pass">pass</td></tr>
     <tr><td>${esc(c.name)}${lbl("MEASURED")}</td><td class="n">${c.cap_w} W</td><td class="n">${c.wh_per_req.toFixed(4)}</td><td class="n">${c.p99_ms} ms</td><td class="${c.gate_passed ? "pass" : "failc"}">${c.gate_passed ? "pass" : "FAIL: p99 over +10%"}</td></tr></table>` : ""}
    <div class="k" style="margin-top:18px">Rack map</div>
    <div class="slot">${rack ? `<img src="${esc(rack)}" alt="SAM rack map">` : "SAM rack map goes here when the run has one"}</div>
   </div>
   <div class="gl2 card recs"><div class="k">What this means for your data centre</div><h1 style="font-size:22px">${rec ? esc(rec.verdict) : "no recommendations event"}</h1>
    <div class="k">Recommended actions</div><ul>${acts}</ul><div class="k">Where this applies</div><ul>${applies}</ul>
    <div class="k">Meta API</div><pre class="m mu">${metaLine()}</pre></div></div>`;
  r.hidden = false;
}

function metaLine() {
  const m = state.meta;
  return m.calls ? `${m.calls} call(s) to ${m.models.join(", ")} · ${m.input} in / ${m.output} out tokens`
    : "0 calls: this run used the fixed fallback policy";
}

function render() {
  $("ex").hidden = !state.example;
  $("metacalls").textContent = metaLine();
  const act = NODES.find((n) => n.id === state.active);
  $("headline").textContent = state.done ? "Run finished" : act ? `${act.title}…` : "Waiting for events…";
  drawGraph(); drawPower(); drawPanel(); drawResults();
}

$("close").onclick = () => { selected = null; drawPanel(); };
const push = (e) => { state = reduce(state, e); $("runid").textContent = e.run_id; render(); };

const run = q.get("run");
if (run) {
  const src = new EventSource(`/api/runs/${encodeURIComponent(run)}/events`);
  src.onmessage = (m) => push(JSON.parse(m.data));
  for (const t of ["run.started", "telemetry.sample", "step.started", "decision", "trial.measured", "gate.result",
    "action.applied", "action.rolled_back", "recommendations", "pareto.updated", "run.finished"]) {
    src.addEventListener(t, (m) => push(JSON.parse(m.data)));
  }
} else {
  const text = await (await fetch(q.get("src") || "demo-events.jsonl")).text();
  const events = text.trim().split("\n").map((l) => JSON.parse(l));
  const at = q.has("at") ? Number(q.get("at")) : null;
  if (at !== null) { events.slice(0, at).forEach(push); render(); }
  else {
    const ms = Number(q.get("ms") || 700);
    let i = 0; const tick = () => { if (i < events.length) { push(events[i++]); setTimeout(tick, ms); } };
    tick();
  }
}
render();
