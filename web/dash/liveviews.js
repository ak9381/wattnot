// Live previews, floating windows and solo pop-outs for the run page.
// The run view shows three titled mini canvases side by side (Architecture,
// Experts, Power), each naming the currently executing step. All three read
// the same replay/run clock and phase (Phase 1 baseline / Phase 2 WattNot).
// Clicking a preview opens it as a floating window; each window can pop out
// to ?view=arch&solo=1 (or experts/power), which stays in sync through
// BroadcastChannel('wattnot-clock') and still works with no broadcaster.
import { CLOCK_CHANNEL, listen } from "./clockbus.js";
import { esc } from "./theme.js";

export const VIEWS = ["arch", "experts", "power"];
export const VIEW_TITLE = { arch: "Live graph", experts: "Experts", power: "Power" };
export const PHASE_LABEL = { 1: "Phase 1 of 2 · Without WattNot (baseline)", 2: "Phase 2 of 2 · With WattNot" };

let zTop = 100;

// ---- mini preview drawing: one small canvas per view, all from one state ----
// state = { phase, step, text, archActive, expertActive, powerSamples }
export function drawPreview(view, cv, state = {}) {
  if (!cv) return;
  const dpr = window.devicePixelRatio || 1;
  const W = cv.clientWidth || 300, H = cv.clientHeight || 90;
  if (!W || !H) return;
  cv.width = W * dpr; cv.height = H * dpr;
  const x = cv.getContext("2d");
  x.setTransform(dpr, 0, 0, dpr, 0, 0);
  x.clearRect(0, 0, W, H);
  if (view === "arch") {
    const n = 8, gw = (W - 16) / n;
    for (let i = 0; i < n; i++) {
      const on = i === (state.archActive ?? -1);
      x.fillStyle = on ? "#B2A6C3" : "#2B2637";
      x.strokeStyle = on ? "#B2A6C3" : "#3B3A54";
      x.lineWidth = 1;
      x.beginPath();
      x.roundRect(8 + i * gw + 2, H / 2 - 14, gw - 4, 28, 3);
      x.fill(); x.stroke();
    }
  } else if (view === "experts") {
    const g = state.expertBars || [0.5, 0.8, 0.35, 0.6];
    const bw = (W - 16) / g.length;
    g.forEach((v, i) => {
      const h = Math.max(3, v * (H - 24));
      x.fillStyle = i === (state.expertActive ?? -1) ? "#8E7A9E" : "#3B3A54";
      x.fillRect(8 + i * bw + 2, H - 8 - h, bw - 4, h);
    });
  } else {
    const s = state.powerSamples || [];
    x.strokeStyle = "#B2A6C3"; x.lineWidth = 1.5; x.beginPath();
    if (s.length < 2) { x.moveTo(8, H - 12); x.lineTo(W - 8, H - 12); }
    else {
      const lo = Math.min(...s), hi = Math.max(...s, lo + 1);
      s.forEach((v, i) => {
        const px = 8 + (i / (s.length - 1)) * (W - 16);
        const py = H - 10 - ((v - lo) / (hi - lo)) * (H - 24);
        i ? x.lineTo(px, py) : x.moveTo(px, py);
      });
    }
    x.stroke();
  }
  if (state.step != null) {
    x.fillStyle = "#A79BB9"; x.font = "10px JetBrains Mono, monospace";
    x.fillText(String(state.text || state.step || ""), 8, 12);
  }
}

export function updatePreviews(state = {}) {
  for (const v of VIEWS) {
    const cap = document.getElementById(`preview-${v}-step`);
    if (cap) cap.textContent = state.text ? `${state.step || ""} · ${state.text}` : (state.step || "waiting for the run…");
    const ph = document.getElementById(`preview-${v}-phase`);
    if (ph) ph.textContent = PHASE_LABEL[state.phase] || "";
    drawPreview(v, document.getElementById(`preview-${v}-canvas`), state);
    const winCap = document.getElementById(`win-${v}-step`);
    if (winCap) winCap.textContent = cap ? cap.textContent : (state.text || "");
    drawPreview(v, document.getElementById(`win-${v}-canvas`), { ...state, archActive: state.archActive });
  }
}

// ---- floating windows: draggable by the title bar, resizable with CSS ----
// ---- (resize: both plus a corner handle), z-order on focus, close, ----
// ---- minimize and 'Go to current step'. All three fit at 1920 wide. ----
const WIN_POS = { arch: [56, 110], experts: [668, 132], power: [1280, 110] };

export function openLiveWindow(view, getState) {
  if (!VIEWS.includes(view)) return null;
  let win = document.getElementById(`win-${view}`);
  if (win) { front(win); win.classList.remove("min"); return win; }
  const host = document.getElementById("livewins") || document.body;
  win = document.createElement("div");
  win.className = "livewin";
  win.id = `win-${view}`;
  win.setAttribute("role", "dialog");
  win.setAttribute("aria-label", `${VIEW_TITLE[view]} live view`);
  const [left, top] = WIN_POS[view] || [120 + zTop * 4, 120];
  win.style.left = `${Math.min(left, Math.max(8, innerWidth - 620))}px`;
  win.style.top = `${top}px`;
  win.innerHTML = `<header class="livewin-bar" id="win-${view}-bar">
      <span class="livewin-title">${esc(VIEW_TITLE[view])} · live</span>
      <span class="livewin-btns">
        <button class="btn" id="win-${view}-goto" title="go to current step">Go to current step</button>
        <button class="btn" id="win-${view}-pop" title="pop out to its own window">Pop out</button>
        <button class="btn" id="win-${view}-min" title="minimize" aria-label="minimize">–</button>
        <button class="btn" id="win-${view}-close" title="close" aria-label="close">×</button>
      </span></header>
    <div class="livewin-body"><canvas id="win-${view}-canvas"></canvas>
      <p class="livewin-step" id="win-${view}-step">waiting for the run…</p>
      <p class="livewin-phase" id="win-${view}-phase"></p>
      <span class="livewin-grip" aria-hidden="true"></span></div>`;
  host.appendChild(win);
  front(win);
  // drag by the title bar; buttons keep their clicks
  const bar = win.querySelector(".livewin-bar");
  bar.addEventListener("pointerdown", (e) => {
    if (e.target.closest("button")) return;
    front(win);
    bar.setPointerCapture?.(e.pointerId);
    const sx = e.clientX - win.offsetLeft, sy = e.clientY - win.offsetTop;
    const move = (m) => { win.style.left = `${Math.max(0, m.clientX - sx)}px`; win.style.top = `${Math.max(0, m.clientY - sy)}px`; };
    const up = () => { bar.removeEventListener("pointermove", move); bar.removeEventListener("pointerup", up); };
    bar.addEventListener("pointermove", move);
    bar.addEventListener("pointerup", up);
  });
  win.addEventListener("pointerdown", () => front(win), true);
  win.querySelector(`#win-${view}-close`).onclick = () => win.remove();
  win.querySelector(`#win-${view}-min`).onclick = (e) => {
    win.classList.toggle("min");
    e.currentTarget.setAttribute("aria-pressed", win.classList.contains("min"));
  };
  win.querySelector(`#win-${view}-goto`).onclick = () => {
    const s = getState?.() || {};
    const cap = win.querySelector(`#win-${view}-step`);
    if (cap) {
      cap.textContent = s.text ? `${s.step || ""} · ${s.text}` : "already at the current step";
      cap.classList.remove("flash"); void cap.offsetWidth; cap.classList.add("flash");
    }
    drawPreview(view, win.querySelector(`#win-${view}-canvas`), s);
  };
  win.querySelector(`#win-${view}-pop`).onclick = () => {
    window.open(`?view=${view}&solo=1`, "_blank", "width=1200,height=800");
  };
  if (getState) drawPreview(view, win.querySelector(`#win-${view}-canvas`), getState());
  return win;
}

function front(win) {
  zTop += 1;
  win.style.zIndex = String(zTop);
}

// ---- solo pop-out: ?view=arch&solo=1 (or experts/power) renders only that ----
// ---- canvas full screen. It listens on BroadcastChannel('wattnot-clock') ----
// ---- for the main page's tick + phase + active step, and draws its own ----
// ---- overview from the served data when no broadcaster is talking. ----
export function soloParams() {
  const q = new URLSearchParams(location.search);
  const view = q.get("view");
  if (q.get("solo") !== "1" || !VIEWS.includes(view)) return null;
  return view;
}

export function initSoloIfNeeded() {
  const view = soloParams();
  if (!view) return false;
  document.body.classList.add("solo");
  for (const sel of [".top", "#drawer", "#rail", "#spanel", "#pop"]) document.querySelector(sel)?.setAttribute("hidden", "");
  const stage = document.getElementById("stage");
  stage.innerHTML = `<div class="solo-wrap" aria-label="${esc(VIEW_TITLE[view])} solo view">
    <header class="solo-head"><h1>${esc(VIEW_TITLE[view])} · live</h1>
      <span class="solo-phase" id="solo-phase">${esc(PHASE_LABEL[2])}</span>
      <span class="sp"></span><span class="muted">synced through ${esc(CLOCK_CHANNEL)}</span></header>
    <canvas id="solo-canvas" aria-label="${esc(VIEW_TITLE[view])} canvas"></canvas>
    <p class="solo-step" id="solo-step">waiting for the broadcaster…</p></div>`;
  const state = { view, phase: 2, step: null, text: null, heard: false };
  const paint = () => {
    const cap = document.getElementById("solo-step");
    if (cap && state.heard) cap.textContent = state.text ? `${state.step || ""} · ${state.text}` : (state.step || "");
    const ph = document.getElementById("solo-phase");
    if (ph) ph.textContent = PHASE_LABEL[state.phase] || "";
    drawPreview(view, document.getElementById("solo-canvas"), state);
  };
  // the main page broadcasts { type: 'tick', phase, step, text, progress }; follow it
  listen((msg) => {
    if (!msg || typeof msg !== "object") return;
    if (msg.type !== "tick" && msg.type !== "phase" && msg.type !== "progress") return;
    state.heard = true;
    if (msg.phase === 1 || msg.phase === 2) state.phase = msg.phase;
    if (msg.step != null) state.step = msg.step;
    if (msg.text != null) state.text = msg.text;
    if (msg.view === view || msg.view === "all") {
      if (msg.archActive != null) state.archActive = msg.archActive;
      if (msg.expertActive != null) state.expertActive = msg.expertActive;
      if (msg.powerSamples) state.powerSamples = msg.powerSamples;
    }
    paint();
  });
  // standalone: no broadcaster yet after a beat, so draw this view's own data
  setTimeout(() => { if (!state.heard) soloStandalone(view, state, paint); }, 900);
  window.addEventListener("resize", paint);
  paint();
  return true;
}

async function soloStandalone(view, state, paint) {
  const cap = document.getElementById("solo-step");
  try {
    if (view === "arch") {
      const d = await fetch("/api/dash/story").then((r) => { if (!r.ok) throw new Error(r.status); return r.json(); });
      state.archActive = 0;
      state.text = `${d.nodes.length} steps in the recorded run · showing the map overview`;
    } else if (view === "experts") {
      const ticks = await fetch("/api/dash/moe/ticks").then((r) => { if (!r.ok) throw new Error(r.status); return r.json(); });
      state.expertActive = 1;
      state.text = `${ticks.length} routed ticks in the capture · showing the group overview`;
    } else {
      const info = await fetch("/api/dash/gpu").then((r) => { if (!r.ok) throw new Error(r.status); return r.json(); });
      const first = info.scenes?.[0];
      const sc = first ? await fetch(`/api/dash/gpu/scene/${encodeURIComponent(first.id)}`).then((r) => r.json()) : null;
      state.powerSamples = (sc?.samples || []).slice(0, 120).map((s) => s.power_w).filter((v) => v != null);
      state.text = first ? `${first.title} · showing the recorded trace` : "no recorded scene";
    }
  } catch {
    state.text = "standalone: the served data could not load, still listening for the broadcaster…";
  }
  if (cap && !state.heard) cap.textContent = state.text || "";
  paint();
}

