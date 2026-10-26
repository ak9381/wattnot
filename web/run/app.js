import { GROUPS, NODES, EDGES, NODE_W, NODE_H, initialState, reduce, capPath, route, groupBounds } from "./state.js";
import { buildChecklist, loadDone, toggle, progress, actionPayload } from "./checklist.js";
import { icon } from "./vendor/icons/icons.js";

const q = new URLSearchParams(location.search);
const $ = (id) => document.getElementById(id);
const esc = (x) => String(x ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const pill = (l) => (l ? `<span class="pill ${l}">${l}</span>` : "");
const store = (() => { try { localStorage.setItem("_t", "1"); return localStorage; } catch { const m = {}; return { getItem: (k) => m[k] ?? null, setItem: (k, v) => { m[k] = v; } }; } })();

// ---------- data / playback ----------
let events = [], idx = 0, state = initialState(), playing = false, timer = null;
let selected = q.get("node"), showResults = false;
const layout = NODES.map((n) => ({ ...n }));
const byId = () => Object.fromEntries(layout.map((n) => [n.id, n]));

function seek(i) {
  idx = Math.max(0, Math.min(events.length, i));
  state = events.slice(0, idx).reduce(reduce, initialState());
  if (state.done && !q.has("node") && showResults !== "dismissed") showResults = true;
  render();
}
function play(on = !playing) {
  playing = on && idx < events.length;
  clearTimeout(timer);
  if (playing) {
    const step = () => {
      if (idx >= events.length) { playing = false; renderTimeline(); return; }
      seek(idx + 1);
      timer = setTimeout(step, 650 / Number($("speed").value));
    };
    timer = setTimeout(step, 200);
  }
  renderTimeline();
}

// ---------- canvas: pan / zoom / fit ----------
const view = { x: 0, y: 0, k: 1 };
const vp = $("viewport"), world = $("world");
function applyView() {
  world.style.transform = `translate(${view.x}px,${view.y}px) scale(${view.k})`;
  const g = 24 * view.k;
  vp.style.backgroundSize = `${g}px ${g}px`;
  vp.style.backgroundPosition = `${view.x}px ${view.y}px`;
  $("zoomv").textContent = `${Math.round(view.k * 100)}%`;
  renderMinimap();
}
function worldBounds() {
  const bs = GROUPS.map((g) => groupBounds(g.id, layout));
  const x0 = Math.min(...bs.map((b) => b.x)), y0 = Math.min(...bs.map((b) => b.y));
  const x1 = Math.max(...bs.map((b) => b.x + b.w)) + 96, y1 = Math.max(...bs.map((b) => b.y + b.h)) + 56;
  return { x: x0, y: y0, w: x1 - x0, h: y1 - y0 };
}
function fit() {
  const b = worldBounds(), r = vp.getBoundingClientRect();
  const padB = 72; // keep clear of the legend and controls
  view.k = Math.min(1.25, Math.min((r.width - 64) / b.w, (r.height - 48 - padB) / b.h));
  view.x = (r.width - b.w * view.k) / 2 - b.x * view.k;
  view.y = 24 + (r.height - padB - 24 - b.h * view.k) / 2 - b.y * view.k;
  applyView();
}
function zoomAt(f, cx, cy) {
  const k = Math.max(0.3, Math.min(2.5, view.k * f));
  view.x = cx - ((cx - view.x) * k) / view.k;
  view.y = cy - ((cy - view.y) * k) / view.k;
  view.k = k;
  applyView();
}
vp.addEventListener("wheel", (e) => {
  e.preventDefault();
  const r = vp.getBoundingClientRect();
  if (e.ctrlKey || e.metaKey || Math.abs(e.deltaY) > Math.abs(e.deltaX)) zoomAt(Math.exp(-e.deltaY * 0.0015), e.clientX - r.left, e.clientY - r.top);
  else { view.x -= e.deltaX; applyView(); }
}, { passive: false });

let drag = null;
vp.addEventListener("pointerdown", (e) => {
  const nodeEl = e.target.closest(".node");
  vp.focus();
  drag = { sx: e.clientX, sy: e.clientY, moved: false, node: nodeEl ? nodeEl.dataset.id : null,
    ox: view.x, oy: view.y, nx: 0, ny: 0 };
  if (drag.node) { const n = byId()[drag.node]; drag.nx = n.x; drag.ny = n.y; }
  vp.setPointerCapture(e.pointerId);
});
vp.addEventListener("pointermove", (e) => {
  if (!drag) { hover(e); return; }
  const dx = e.clientX - drag.sx, dy = e.clientY - drag.sy;
  if (!drag.moved && Math.hypot(dx, dy) < 4) return;
  drag.moved = true;
  $("tip").hidden = true;
  if (drag.node) {
    const n = byId()[drag.node];
    n.x = Math.round((drag.nx + dx / view.k) / 8) * 8; // snap to the 8 px grid
    n.y = Math.round((drag.ny + dy / view.k) / 8) * 8;
    renderCanvas();
  } else {
    vp.classList.add("panning");
    view.x = drag.ox + dx; view.y = drag.oy + dy; applyView();
  }
});
vp.addEventListener("pointerup", () => {
  if (drag && !drag.moved && drag.node) { selected = drag.node; renderDrawer(); renderCanvas(); }
  if (drag && !drag.moved && !drag.node && selected) { selected = null; renderDrawer(); renderCanvas(); }
  drag = null; vp.classList.remove("panning");
});

function hover(e) {
  const el = e.target.closest(".node"), tip = $("tip");
  if (!el) { tip.hidden = true; return; }
  const n = NODES.find((x) => x.id === el.dataset.id), st = state.nodes[n.id];
  tip.innerHTML = `<b>${esc(n.title)}</b><span class="mono">${esc(st.sub || "waiting for this step")}</span><em>${esc(HINT[n.id] || "")} · click for details · drag to move</em>`;
  tip.style.left = `${e.clientX + 16}px`; tip.style.top = `${e.clientY + 16}px`;
  tip.hidden = false;
}
const HINT = {
  gpu: "Device under test and the cap range its driver allows",
  power: "Board power from NVML; fake backend reports an ESTIMATE",
  base: "Reference run at the default cap; every try is compared to this",
  choice: "Muse Spark sees numbers only and picks one cap or one knob",
  reason: "One line from the model, quoting the number that drove it",
  cap: "Our code clamps the cap to the driver range and one step",
  knob: "Only allow-listed vLLM knobs can change",
  prompts: "The same prompt set replays on every try",
  quality: "Fails if the pass rate drops more than 1 point",
  latency: "Fails if p99 is more than 10% over baseline",
  verdict: "Both checks must pass to keep the change",
  kept: "Keeps the cap and loops back for the next try",
  revert: "Restores the last cap that passed, immediately",
  receipt: "Hash of the run log plus the saving, per run",
  recs: "What this run means for the data centre",
};

// ---------- render: canvas ----------
const DOTS = (id, kind) => [0, 0.33, 0.66].map((o) => `<circle r="3" class="dot" fill="${kind === "revert" ? "#F0504A" : "#22C7D9"}">
  <animateMotion dur="1.4s" begin="${-o * 1.4}s" repeatCount="indefinite"><mpath href="#${id}"/></animateMotion></circle>`).join("");

function edgeColour(e, hot, used) {
  if (e.kind === "revert") return hot ? "#F0504A" : used ? "rgba(240,80,74,.7)" : "rgba(240,80,74,.35)";
  if (hot) return "#22C7D9";
  if (e.kind === "meta") return used ? "rgba(174,184,193,.7)" : "#33414C";
  return used ? "rgba(34,199,217,.5)" : "#33414C";
}

function renderCanvas() {
  const B = byId();
  $("groups").innerHTML = GROUPS.map((g) => {
    const b = groupBounds(g.id, layout);
    return `<div class="grp${g.meta ? " meta" : ""}" style="left:${b.x}px;top:${b.y}px;width:${b.w}px;height:${b.h}px">
      <div class="gh">${esc(g.title)}${g.sub ? ` <small>· ${esc(g.sub)}</small>` : ""}${g.pill ? `<span class="gpill">${esc(g.pill)}</span>` : ""}</div></div>`;
  }).join("");
  $("nodes").innerHTML = layout.map((n) => {
    const st = state.nodes[n.id];
    const fail = (n.id === "verdict" && st.verdict === "fail");
    const cls = ["node", st.status, n.meta ? "meta" : "", n.danger ? "danger" : "", fail ? "fail" : "", selected === n.id ? "selected" : ""].join(" ");
    let body = `<div class="ns${st.sub ? "" : " idle"}">${esc(st.sub || "waiting")}</div>`;
    if (n.id === "prompts") body += `<div class="bar2"><i style="width:${st.status === "active" || state.progress === 100 ? state.progress : 0}%"></i></div>`;
    if (n.id === "receipt") {
      const r = state.receiptView;
      body = `<div class="rcpt"><div>Energy<b>${r && r.wh != null ? "−" + r.wh.toFixed(4) : "–"}</b>Wh / req</div><div>Cost<b>${r && r.usd != null ? "$" + r.usd.toFixed(2) : "$ –"}</b>per 30 d</div><div>Water<b>${r && r.water != null ? r.water + " L" : "– L"}</b>per 30 d</div></div>
        <div class="ns${st.sub ? "" : " idle"}" style="margin-top:8px">${esc(st.sub || "written when the run ends")}</div>`;
    }
    const pills = [...st.pills, ...(n.id === "receipt" ? ["ESTIMATE"] : [])].filter((v, i, a) => a.indexOf(v) === i);
    return `<div class="${cls}" data-id="${n.id}" style="left:${n.x}px;top:${n.y}px;width:${n.w || NODE_W}px;min-height:${n.h || NODE_H}px">
      <div class="nh">${icon(n.icon, 16)}<span class="nt">${esc(n.title)}</span></div>${body}
      ${pills.length ? `<div class="pills">${pills.map(pill).join("")}</div>` : ""}</div>`;
  }).join("");

  const used = new Set();
  for (const id of Object.keys(state.nodes)) if (state.nodes[id].status !== "idle") used.add(id);
  let defs = `<defs>
    <filter id="eglow" x="-20%" y="-20%" width="140%" height="140%"><feGaussianBlur stdDeviation="3"/></filter>
    ${["cy", "rd", "mu", "mt"].map((c) => `<marker id="ah-${c}" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0 0 L10 5 L0 10 z" fill="${{ cy: "#22C7D9", rd: "#F0504A", mu: "#4A5864", mt: "#AEB8C1" }[c]}"/></marker>`).join("")}</defs>`;
  let paths = "", labels = "";
  for (const e of EDGES) {
    const r = route(e, B);
    const hot = state.hot.includes(e.id);
    const u = used.has(e.from) && used.has(e.to);
    const col = edgeColour(e, hot, u);
    const m = e.kind === "revert" ? "rd" : hot || (u && e.kind !== "meta") ? "cy" : e.kind === "meta" && u ? "mt" : "mu";
    const dash = e.kind === "optional" ? ` stroke-dasharray="5 5"` : "";
    const pid = `p-${e.id}`;
    if (hot) paths += `<path d="${r.d}" stroke="${col}" stroke-width="6" fill="none" opacity=".35" filter="url(#eglow)"/>`;
    paths += `<path id="${pid}" d="${r.d}" stroke="${col}" stroke-width="${hot ? 2 : 1.5}" fill="none"${dash} marker-end="url(#ah-${m})"/>`;
    if (hot) paths += DOTS(pid, e.kind);
    const text = e.loop ? state.loopLabel : e.label;
    if (text) {
      const w = text.length * 7 + 12;
      const cls = e.kind === "revert" ? "rv" : hot ? "hot" : "";
      labels += `<rect class="elbg" x="${r.label[0] - w / 2}" y="${r.label[1] - 10}" width="${w}" height="20" rx="4"/>
        <text class="elbl ${cls}" x="${r.label[0]}" y="${r.label[1] + 4}" text-anchor="middle">${esc(text)}</text>`;
    }
  }
  $("edges").innerHTML = defs + paths + labels;
  renderMinimap();
}

function renderMinimap() {
  const mm = $("minimap"), b = worldBounds(), W = 208, H = 128;
  const k = Math.min((W - 16) / b.w, (H - 16) / b.h), ox = 8 - b.x * k + ((W - 16) - b.w * k) / 2, oy = 8 - b.y * k + ((H - 16) - b.h * k) / 2;
  const r = vp.getBoundingClientRect();
  const vx = (-view.x / view.k) * k + ox, vy = (-view.y / view.k) * k + oy, vw = (r.width / view.k) * k, vh = (r.height / view.k) * k;
  mm.innerHTML = GROUPS.map((g) => { const gb = groupBounds(g.id, layout); return `<rect x="${gb.x * k + ox}" y="${gb.y * k + oy}" width="${gb.w * k}" height="${gb.h * k}" rx="2" fill="none" stroke="${g.meta ? "#6B7680" : "rgba(34,199,217,.5)"}"/>`; }).join("")
    + layout.map((n) => { const st = state.nodes[n.id].status; return `<rect x="${n.x * k + ox}" y="${n.y * k + oy}" width="${(n.w || NODE_W) * k}" height="${(n.h || NODE_H) * k}" rx="1" fill="${st === "active" ? (n.danger ? "#F0504A" : "#22C7D9") : st === "done" ? "#2A3A44" : "#1A232A"}"/>`; }).join("")
    + `<rect x="${vx}" y="${vy}" width="${vw}" height="${vh}" fill="rgba(34,199,217,.06)" stroke="#8D9AA6" stroke-width="1"/>`;
  mm.onclick = (e) => {
    const rr = mm.getBoundingClientRect();
    const wx = (e.clientX - rr.left - ox) / k, wy = (e.clientY - rr.top - oy) / k;
    view.x = r.width / 2 - wx * view.k; view.y = r.height / 2 - wy * view.k; applyView();
  };
}

// ---------- render: drawer ----------
function lastTry() { return state.tries[state.tries.length - 1] || null; }
function renderDrawer() {
  const d = $("drawer");
  if (!selected) { d.hidden = true; return; }
  const n = NODES.find((x) => x.id === selected), st = state.nodes[selected], t = lastTry();
  let h = `<div class="k" style="margin-top:0">${esc(GROUPS.find((g) => g.id === n.group).title)}</div><h2>${esc(n.title)}</h2>
    <div class="ns">${esc(st.sub || "Not reached yet in this run.")}</div>${st.pills.length ? `<div class="pills">${st.pills.map(pill).join("")}</div>` : ""}`;
  if (t && t.decision) {
    const dc = t.decision, tok = dc.tokens || { input: 0, output: 0 };
    h += `<div class="k">Muse Spark reasoning · ${esc(t.step)}</div><div class="quote">${esc(dc.reason)}</div>
      <div class="k">Logged model call</div><div class="call">model&nbsp;&nbsp;&nbsp;${esc(dc.model)}${dc.model === "fallback" ? " (fixed rules, no Meta call)" : ""}<br>
      tokens&nbsp;&nbsp;${tok.input} in / ${tok.output} out<br>clamped&nbsp;${dc.clamped ? "yes, by our code" : "no"}<br>
      choice&nbsp;&nbsp;${esc(JSON.stringify(dc.choice))}</div>`;
  }
  if (t && t.prompts.length) h += `<div class="k">Prompts that ran · ${t.prompts.length}</div><ol class="plist">${t.prompts.map((p) => `<li>${esc(p)}</li>`).join("")}</ol>`;
  if (t && t.gates.length) {
    h += `<div class="k">Gate verdict · ${t.verdict === "pass" ? '<span class="ok">PASS</span>' : '<span class="bad">FAIL</span>'}</div><ul class="gl">${t.gates.map((g) => `<li><span>${g.check === "p99" ? "p99 ≤ +10%" : "Quality Δ ≤ 1 pt"}</span><span class="mono ${g.passed ? "ok" : "bad"}">${esc(g.detail.score)} / ${esc(g.detail.threshold)} · ${g.passed ? "pass" : "fail"}</span></li>`).join("")}</ul>`;
  }
  if (state.tries.length) {
    h += `<div class="k">Every try</div><table class="tries"><tr><th>try</th><th>cap</th><th>Wh/req</th><th>p99</th><th>result</th></tr>
      ${state.tries.map((x, i) => `<tr><td>${i + 1}</td><td>${x.cap_w ?? "–"} W</td><td>${x.result ? x.result.wh.toFixed(4) : "–"}</td><td>${x.result ? Math.round(x.result.p99_ms) + " ms" : "–"}</td><td class="${x.outcome === "reverted" ? "bad" : x.outcome ? "ok" : ""}">${x.outcome || "running"}</td></tr>`).join("")}</table>`;
  }
  $("dbody").innerHTML = h;
  d.hidden = false;
}

// ---------- render: timeline + power line ----------
function renderTimeline() {
  $("play").innerHTML = icon(playing ? "pause" : "play", 16);
  const sc = $("scrub"); sc.max = events.length; sc.value = idx;
  const ev = events[idx - 1];
  $("evlabel").textContent = ev ? `event ${idx} / ${events.length} · ${ev.type}${ev.step_id ? " · " + ev.step_id : ""}` : `event 0 / ${events.length}`;
  $("caps").textContent = capPath(state) ? `cap path ${capPath(state)} W` : "";
  // power line over the whole run, playhead at the current event
  const svg = $("powerline"), W = svg.clientWidth || 1200, H = 48, N = Math.max(events.length, 1);
  const all = events.filter((e) => e.type === "telemetry.sample");
  const xOf = (seq) => (events.findIndex((e) => e.seq === seq) / N) * W, yOf = (w) => H - 6 - (w / 720) * (H - 14);
  let pts = "", last = null;
  for (const e of all) { const x = xOf(e.seq), y = yOf(e.data.power_w); pts += `${last ? `${x},${last} ` : ""}${x},${y} `; last = y; }
  const px = (idx / N) * W;
  const marks = events.map((e, i) => (e.type === "action.rolled_back" ? `<line x1="${(i / N) * W}" x2="${(i / N) * W}" y1="0" y2="${H}" stroke="#F0504A" stroke-dasharray="3 3"/>` : e.type === "decision" ? `<line x1="${(i / N) * W}" x2="${(i / N) * W}" y1="${H - 6}" y2="${H}" stroke="#4A5864"/>` : "")).join("");
  svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
  svg.innerHTML = `<line x1="0" x2="${W}" y1="${yOf(700)}" y2="${yOf(700)}" stroke="#1E2932" stroke-dasharray="2 4"/>
    <text x="4" y="${yOf(700) - 4}" font-size="11" fill="#6B7884" font-family="JetBrains Mono">700 W</text>${marks}
    <clipPath id="past"><rect x="0" y="0" width="${px}" height="${H}"/></clipPath>
    <polyline points="${pts}" fill="none" stroke="#2B3843" stroke-width="1.5"/>
    <polyline points="${pts}" fill="none" stroke="#22C7D9" stroke-width="1.75" clip-path="url(#past)"/>
    <line x1="${px}" x2="${px}" y1="0" y2="${H}" stroke="#E9EEF2" stroke-width="1"/>
    <text x="${W - 4}" y="12" font-size="11" fill="#8D9AA6" text-anchor="end" font-family="JetBrains Mono">GPU power · ${state.power.length ? state.power[state.power.length - 1].w + " W · " + state.power[state.power.length - 1].label : "no samples yet"}</text>`;
}

// ---------- render: results ----------
function toast(msg) { const t = document.createElement("div"); t.className = "toast"; t.textContent = msg; document.body.appendChild(t); setTimeout(() => t.remove(), 1800); }
function deliver(p) {
  if (p.type === "copy") { navigator.clipboard?.writeText(p.text).catch(() => {}); toast(`Copied: ${p.text}`); }
  if (p.type === "download") {
    const blob = new Blob([p.json ? JSON.stringify(p.json, null, 2) : p.text], { type: p.json ? "application/json" : "text/calendar" });
    const a = document.createElement("a"); a.href = URL.createObjectURL(blob); a.download = p.filename; a.click();
    toast(`Downloaded ${p.filename}`);
  }
}
function renderResults() {
  const r = $("results");
  $("toResults").hidden = !state.done;
  $("toResults").innerHTML = showResults === true ? `${icon("activity", 16)} Back to canvas` : `${icon("clipboard-check", 16)} Results`;
  if (showResults !== true || !state.done) { r.hidden = true; return; }
  const b = state.baseline, f = state.final || b, c = state.compare, rec = state.recommendations;
  const items = buildChecklist(rec);
  const done = loadDone(store, state.runId), pg = progress(items, done);
  const ch = (x, y) => `${(((y - x) / x) * 100).toFixed(1)}%`;
  const rows = [["Power cap", `${b.cap_w} W`, `${f.cap_w} W`, ch(b.cap_w, f.cap_w)],
    ["Energy per request", `${b.wh.toFixed(4)} Wh`, `${f.wh.toFixed(4)} Wh`, ch(b.wh, f.wh)],
    ["p99 latency", `${Math.round(b.p99_ms)} ms`, `${Math.round(f.p99_ms)} ms`, `+${(((f.p99_ms - b.p99_ms) / b.p99_ms) * 100).toFixed(1)}%`],
    ["Quality pass rate", `${Math.round(b.pass_rate * 1000) / 10}%`, `${Math.round(f.pass_rate * 1000) / 10}%`, `${((f.pass_rate - b.pass_rate) * 100).toFixed(1)} pt`]];
  r.innerHTML = `<div class="rgrid">
   <div class="card"><div class="rhead"><div><h3>RECOMMENDED ACTIONS · ${esc(rec.verdict.toUpperCase())}</h3><h2>Set ${f.cap_w} W on this workload, then work down the list</h2></div>
     <div class="prog"><span>${pg.text}</span><div class="bar2"><i style="width:${pg.pct}%"></i></div></div></div>
     <ul class="chk">${items.map((it) => `<li class="${done.has(it.id) ? "done" : ""}">
       <input type="checkbox" class="cb" data-id="${esc(it.id)}" ${done.has(it.id) ? "checked" : ""} aria-label="Mark done">
       <div class="txt">${pill(it.label)}${esc(it.text)}</div><div class="val">${esc(it.value)}</div>
       <button class="act${it.kind === "apply_cap" ? " primary" : ""}" data-act="${esc(it.id)}">${icon(it.icon, 14)}${esc(it.button)}</button></li>`).join("")}</ul>
     <p class="muted" style="font-size:12.5px;margin:16px 0 0">Buttons copy a command or download a file. Nothing runs on your servers from this page.</p></div>
   <div>
    <div class="card"><h3>BEFORE / AFTER ${state.example ? '<span class="chip ex" style="margin-left:8px">EXAMPLE</span>' : ""}</h3>
      <table class="ba"><tr><th></th><th>Before</th><th>After</th><th>Δ</th></tr>${rows.map((x) => `<tr><td>${x[0]} ${pill("MEASURED")}</td><td class="n">${x[1]}</td><td class="n">${x[2]}</td><td class="n">${x[3]}</td></tr>`).join("")}</table>
      ${c ? `<div class="cmp"><div class="win"><b>WattNot, gated</b><div class="mono">${f.cap_w} W<br>${f.wh.toFixed(4)} Wh/req<br>p99 ${Math.round(f.p99_ms)} ms<br><span class="ok">gate pass</span></div></div>
        <div class="lose"><b>Plain fixed cap</b><div class="mono">${c.cap_w} W, no gate<br>${c.wh_per_req.toFixed(4)} Wh/req<br>p99 ${c.p99_ms} ms<br><span class="${c.gate_passed ? "ok" : "bad"}">${c.gate_passed ? "pass" : "p99 +" + Math.round((c.p99_ms / b.p99_ms - 1) * 100) + "%: fails"}</span></div></div></div>` : ""}
      <div class="slot">${q.get("rack") ? `<img src="${esc(q.get("rack"))}" alt="SAM 3.1 rack map: servers outlined, GPUs this run touched lit">` : "Rack map (SAM 3.1) appears here when the run includes a rack photo"}</div></div>
    <div class="card" style="margin-top:16px"><h3>WHERE THIS APPLIES</h3><ul class="applies">${rec.applies.map((a) => `<li>${esc(a)}</li>`).join("")}</ul></div>
   </div></div>`;
  r.querySelectorAll(".cb").forEach((cb) => cb.addEventListener("change", () => { toggle(store, state.runId, loadDone(store, state.runId), cb.dataset.id); renderResults(); }));
  r.querySelectorAll(".act").forEach((bt) => bt.addEventListener("click", () => {
    const it = items.find((x) => x.id === bt.dataset.act);
    deliver(actionPayload(it, { gpuIndex: state.gpu?.index, runId: state.runId, receipt: { receipt: state.receipt, recommendations: rec } }));
  }));
  r.hidden = false;
}

function renderHeader() {
  $("ex").hidden = !state.example;
  $("runid").textContent = state.runId || "";
  const m = state.meta;
  $("metacalls").innerHTML = m.calls ? `META · ${m.calls} call${m.calls > 1 ? "s" : ""} · ${m.input + m.output} tok` : "META · 0 calls · fallback rules";
}
function render() { renderHeader(); renderCanvas(); renderDrawer(); renderTimeline(); renderResults(); }

// ---------- wiring ----------
$("zin").innerHTML = icon("zoom-in", 16); $("zout").innerHTML = icon("zoom-out", 16); $("fit").innerHTML = icon("maximize", 16);
$("back").innerHTML = icon("skip-back", 16); $("fwd").innerHTML = icon("skip-forward", 16); $("dclose").innerHTML = icon("x", 16);
const centre = () => { const r = vp.getBoundingClientRect(); return [r.width / 2, r.height / 2]; };
$("zin").onclick = () => zoomAt(1.2, ...centre()); $("zout").onclick = () => zoomAt(1 / 1.2, ...centre()); $("fit").onclick = fit;
$("play").onclick = () => play(); $("back").onclick = () => { play(false); seek(idx - 1); }; $("fwd").onclick = () => { play(false); seek(idx + 1); };
$("scrub").oninput = (e) => { play(false); seek(Number(e.target.value)); };
$("dclose").onclick = () => { selected = null; render(); };
$("toResults").onclick = () => { showResults = showResults === true ? "dismissed" : true; render(); };
window.addEventListener("resize", () => { fit(); renderTimeline(); });
window.addEventListener("keydown", (e) => {
  if (e.target.closest && e.target.closest("select,input[type=checkbox]")) return;
  if (e.code === "Space") { e.preventDefault(); play(); }
  else if (e.key === "f" || e.key === "F") fit();
  else if (e.key === "ArrowRight") { play(false); seek(idx + 1); }
  else if (e.key === "ArrowLeft") { play(false); seek(idx - 1); }
  else if (e.key === "+" || e.key === "=") zoomAt(1.2, ...centre());
  else if (e.key === "-") zoomAt(1 / 1.2, ...centre());
  else if (e.key === "Escape") { selected = null; if (showResults === true) showResults = "dismissed"; render(); }
});

const run = q.get("run");
if (run) {
  const src = new EventSource(`/api/runs/${encodeURIComponent(run)}/events`);
  const push = (m) => { events.push(JSON.parse(m.data)); seek(events.length); };
  src.onmessage = push;
  for (const t of ["run.started", "telemetry.sample", "step.started", "step.progress", "decision", "trial.measured", "gate.result",
    "action.applied", "action.rolled_back", "recommendations", "pareto.updated", "receipt.signed", "run.finished"]) src.addEventListener(t, push);
} else {
  const text = await (await fetch(q.get("src") || "demo-events.jsonl")).text();
  events = text.trim().split("\n").map((l) => JSON.parse(l));
}
fit();
if (q.has("results") && q.get("results") === "0") showResults = "dismissed";
seek(q.has("at") ? Number(q.get("at")) : 0);
if (!q.has("at") && !run) play(true);
