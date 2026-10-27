// A tiny infinite pan/zoom canvas: one inner layer moved with a CSS
// translate + scale transform. No libraries. The report mind map can reuse
// it through init(container, inner, opts) -> { fit, focus, setZoom, ... }.
const MIN = 0.25, MAX = 2;

const reduce = () => window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

export function init(container, inner, opts = {}) {
  const state = { x: 0, y: 0, scale: 1, follow: opts.follow ?? true, minimap: false };
  let destroyed = false;

  container.classList.add("pc-view");
  container.tabIndex = container.tabIndex < 0 ? 0 : container.tabIndex;
  inner.classList.add("pc-inner");

  const label = () => `${Math.round(state.scale * 100)}%`;
  const paint = () => {
    inner.style.transform = `translate(${state.x}px, ${state.y}px) scale(${state.scale})`;
    if (controls) controls.querySelector("#pc-zoom-label").textContent = label();
    if (opts.onChange) opts.onChange({ ...state });
    scheduleMini();
  };

  const clampZ = (z) => Math.min(MAX, Math.max(MIN, z));

  // Zoom around a viewport point (client coords relative to container).
  function zoomAt(factor, cx, cy) {
    const r = container.getBoundingClientRect();
    const px = cx ?? r.width / 2, py = cy ?? r.height / 2;
    const next = clampZ(state.scale * factor);
    if (next === state.scale) return;
    const k = next / state.scale;
    state.x = px - (px - state.x) * k;
    state.y = py - (py - state.y) * k;
    state.scale = next;
    paint();
  }

  function setZoom(z, cx, cy) {
    const next = clampZ(z);
    if (cx == null || cy == null) {
      const k = next / state.scale;
      state.x *= k; state.y *= k;
      state.scale = next;
    } else {
      const k = next / state.scale;
      state.x = cx - (cx - state.x) * k;
      state.y = cy - (cy - state.y) * k;
      state.scale = next;
    }
    paint();
  }

  // Fit the whole content into the viewport and center it.
  function fit() {
    const r = container.getBoundingClientRect();
    if (!r.width || !r.height) return;
    const w = inner.offsetWidth || inner.scrollWidth || r.width;
    const h = inner.offsetHeight || inner.scrollHeight || r.height;
    if (!w || !h) return;
    const pad = opts.pad ?? 48;
    const z = clampZ(Math.min((r.width - pad) / w, (r.height - pad) / h));
    state.scale = z;
    state.x = (r.width - w * z) / 2;
    state.y = (r.height - h * z) / 2;
    paint();
  }

  // Center an element in the viewport, optionally zooming to it.
  function focus(el, o = {}) {
    if (!el) return false;
    const r = container.getBoundingClientRect();
    const er = el.getBoundingClientRect();
    if (!r.width || !er.width) return false;
    // Content point under the element center, in unscaled inner coords.
    const cx = (er.left + er.width / 2 - r.left - state.x) / state.scale;
    const cy = (er.top + er.height / 2 - r.top - state.y) / state.scale;
    let z = state.scale;
    if (o.zoom != null) z = clampZ(o.zoom);
    else if (er.width * 1.4 > r.width * state.scale / state.scale) z = state.scale;
    // Slightly zoom in on small cards so the step is readable.
    if (o.zoom == null && state.scale < 1 && er.width < 420) z = clampZ(1);
    const animate = o.animate ?? true;
    const apply = () => {
      state.scale = z;
      state.x = r.width / 2 - cx * z;
      state.y = Math.max(12, r.height / 2 - cy * z - (o.above ?? 0));
      paint();
    };
    if (animate && !reduce()) {
      inner.style.transition = "transform 400ms ease";
      apply();
      const done = () => { inner.style.transition = ""; inner.removeEventListener("transitionend", done); };
      inner.addEventListener("transitionend", done);
      setTimeout(done, 450);
    } else {
      apply();
    }
    return true;
  }

  function setFollow(on) {
    state.follow = !!on;
    if (controls) {
      const b = controls.querySelector("#pc-follow");
      b.setAttribute("aria-pressed", String(state.follow));
      b.classList.toggle("on", state.follow);
    }
    if (opts.onFollow) opts.onFollow(state.follow);
  }

  const manual = () => { if (state.follow) setFollow(false); };

  // ---- wheel: zoom around the cursor (ctrl / trackpad pinch included)
  const onWheel = (e) => {
    if (destroyed) return;
    e.preventDefault();
    manual();
    const f = Math.exp(-e.deltaY * (e.ctrlKey ? 0.01 : 0.0015));
    const r = container.getBoundingClientRect();
    zoomAt(f, e.clientX - r.left, e.clientY - r.top);
  };

  // ---- drag empty space to pan; clicks on cards and buttons still work
  const pointers = new Map();
  let panStart = null, moved = false, pinchD0 = 0, pinchS0 = 1;
  const interactive = (t) => t && t.closest && t.closest("button, a, select, input, textarea, [contenteditable], summary");
  const onDown = (e) => {
    if (e.pointerType === "mouse" && e.button !== 0) return;
    if (interactive(e.target)) return; // let toggles / expand buttons get the click
    container.setPointerCapture?.(e.pointerId);
    pointers.set(e.pointerId, { x: e.clientX, y: e.clientY });
    if (pointers.size === 1) {
      panStart = { x: e.clientX, y: e.clientY, ox: state.x, oy: state.y };
      moved = false;
    } else if (pointers.size === 2) {
      const [a, b] = [...pointers.values()];
      pinchD0 = Math.hypot(a.x - b.x, a.y - b.y) || 1;
      pinchS0 = state.scale;
      panStart = { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2, ox: state.x, oy: state.y };
    }
  };
  const onMove = (e) => {
    if (!pointers.has(e.pointerId)) return;
    pointers.set(e.pointerId, { x: e.clientX, y: e.clientY });
    if (pointers.size === 2) {
      const [a, b] = [...pointers.values()];
      const d = Math.hypot(a.x - b.x, a.y - b.y) || 1;
      const mx = (a.x + b.x) / 2, my = (a.y + b.y) / 2;
      const r = container.getBoundingClientRect();
      manual();
      setZoom(pinchS0 * (d / pinchD0), mx - r.left, my - r.top);
      if (panStart) {
        state.x = panStart.ox + (mx - panStart.x);
        state.y = panStart.oy + (my - panStart.y);
        paint();
      }
      moved = true;
      return;
    }
    if (!panStart) return;
    const dx = e.clientX - panStart.x, dy = e.clientY - panStart.y;
    if (Math.abs(dx) + Math.abs(dy) > 4) {
      if (!moved) { moved = true; manual(); }
      state.x = panStart.ox + dx;
      state.y = panStart.oy + dy;
      paint();
    }
  };
  const onUp = (e) => {
    pointers.delete(e.pointerId);
    if (pointers.size === 0) panStart = null;
    else if (pointers.size === 1) {
      const [a] = [...pointers.values()];
      panStart = { x: a.x, y: a.y, ox: state.x, oy: state.y };
    }
  };

  const onKey = (e) => {
    if (e.target.closest && e.target.closest("input, textarea, select, [contenteditable]")) return;
    if (e.key === "0") { e.preventDefault(); fit(); }
    else if (e.key === "+" || e.key === "=") { e.preventDefault(); manual(); zoomAt(1.2); }
    else if (e.key === "-" || e.key === "_") { e.preventDefault(); manual(); zoomAt(1 / 1.2); }
  };

  container.addEventListener("wheel", onWheel, { passive: false });
  container.addEventListener("pointerdown", onDown);
  container.addEventListener("pointermove", onMove);
  container.addEventListener("pointerup", onUp);
  container.addEventListener("pointercancel", onUp);
  container.addEventListener("keydown", onKey);

  // ---- floating controls, bottom-right
  let controls = null;
  if (opts.controls !== false) {
    controls = document.createElement("div");
    controls.className = "pc-controls";
    controls.setAttribute("role", "group");
    controls.setAttribute("aria-label", "canvas controls");
    controls.innerHTML = `
      <button id="pc-zoom-out" title="zoom out (-)" aria-label="zoom out">−</button>
      <button id="pc-zoom-label" title="current zoom" aria-label="current zoom">100%</button>
      <button id="pc-zoom-in" title="zoom in (+)" aria-label="zoom in">+</button>
      <button id="pc-fit" title="fit to screen (0)">Fit</button>
      <button id="pc-minimap-toggle" title="toggle minimap" aria-pressed="false">Map</button>
      <button id="pc-goto" title="go to current step">Go to step</button>
      <button id="pc-follow" title="follow the active step" aria-pressed="true" class="on">Follow</button>`;
    container.appendChild(controls);
    controls.querySelector("#pc-zoom-out").onclick = (e) => { e.stopPropagation(); manual(); zoomAt(1 / 1.25); };
    controls.querySelector("#pc-zoom-in").onclick = (e) => { e.stopPropagation(); manual(); zoomAt(1.25); };
    controls.querySelector("#pc-zoom-label").onclick = (e) => { e.stopPropagation(); setZoom(1); };
    controls.querySelector("#pc-fit").onclick = (e) => { e.stopPropagation(); fit(); };
    controls.querySelector("#pc-minimap-toggle").onclick = (e) => { e.stopPropagation(); toggleMini(); };
    controls.querySelector("#pc-goto").onclick = (e) => { e.stopPropagation(); opts.goto?.(); };
    controls.querySelector("#pc-follow").onclick = (e) => { e.stopPropagation(); setFollow(!state.follow); };
    // Clicks on the cluster must never start a canvas pan.
    controls.addEventListener("pointerdown", (e) => e.stopPropagation());
    controls.addEventListener("wheel", (e) => e.stopPropagation());
  }

  // ---- minimap, bottom-left, off by default
  let mini = null, miniRect = null, miniDots = null, raf = 0;
  function buildMini() {
    mini = document.createElement("div");
    mini.className = "pc-minimap";
    mini.id = "pc-minimap";
    mini.hidden = true;
    mini.setAttribute("role", "group");
    mini.setAttribute("aria-label", "minimap");
    mini.innerHTML = `<div class="pc-mini-map" id="pc-mini-map"></div><div class="pc-mini-rect" id="pc-mini-rect"></div>`;
    container.appendChild(mini);
    miniDots = mini.querySelector("#pc-mini-map");
    miniRect = mini.querySelector("#pc-mini-rect");
    mini.addEventListener("pointerdown", (e) => e.stopPropagation());
    mini.addEventListener("click", (e) => {
      const dots = miniDots.getBoundingClientRect();
      const fx = (e.clientX - dots.left) / dots.width;
      const fy = (e.clientY - dots.top) / dots.height;
      if (!Number.isFinite(fx) || !Number.isFinite(fy)) return;
      const w = inner.offsetWidth || 1, h = inner.offsetHeight || 1;
      const r = container.getBoundingClientRect();
      state.x = r.width / 2 - fx * w * state.scale;
      state.y = r.height / 2 - fy * h * state.scale;
      manual();
      paint();
    });
    scheduleMini();
  }
  function scheduleMini() {
    if (!mini || mini.hidden) return;
    cancelAnimationFrame(raf);
    raf = requestAnimationFrame(drawMini);
  }
  function drawMini() {
    if (!mini || mini.hidden || destroyed) return;
    const W = 168, H = 108;
    const w = inner.offsetWidth || 1, h = inner.offsetHeight || 1;
    const s = Math.min(W / w, H / h);
    const mw = w * s, mh = h * s;
    miniDots.style.width = `${mw}px`;
    miniDots.style.height = `${mh}px`;
    const irect = inner.getBoundingClientRect();
    const cards = inner.querySelectorAll(opts.miniSelector || ".avn");
    let html = "";
    cards.forEach((c) => {
      const r = c.getBoundingClientRect();
      const dx = (r.left - irect.left) / (state.scale || 1);
      const dy = (r.top - irect.top) / (state.scale || 1);
      const dw = Math.max(2, (r.width / (state.scale || 1)) * s);
      const dh = Math.max(2, (r.height / (state.scale || 1)) * s);
      const live = c.classList.contains("on") ? " live" : "";
      html += `<i class="pc-dot${live}" style="left:${(dx * s).toFixed(1)}px;top:${(dy * s).toFixed(1)}px;width:${dw.toFixed(1)}px;height:${dh.toFixed(1)}px"></i>`;
    });
    miniDots.innerHTML = html;
    // Viewport rect in minimap coords.
    const r = container.getBoundingClientRect();
    const vx = (-state.x / (state.scale || 1)) * s;
    const vy = (-state.y / (state.scale || 1)) * s;
    const vw = (r.width / (state.scale || 1)) * s;
    const vh = (r.height / (state.scale || 1)) * s;
    miniRect.style.left = `${miniDots.offsetLeft + vx}px`;
    miniRect.style.top = `${miniDots.offsetTop + vy}px`;
    miniRect.style.width = `${Math.min(mw, vw)}px`;
    miniRect.style.height = `${Math.min(mh, vh)}px`;
  }
  function toggleMini(force) {
    if (!mini) buildMini();
    const on = force ?? mini.hidden;
    mini.hidden = !on;
    state.minimap = on;
    controls?.querySelector("#pc-minimap-toggle")?.setAttribute("aria-pressed", String(on));
    if (on) scheduleMini();
  }

  if (opts.minimap) buildMini();

  const ro = new ResizeObserver(() => { scheduleMini(); if (opts.onResize) opts.onResize({ ...state }); });
  ro.observe(container);

  paint();

  return {
    state,
    fit,
    focus,
    setZoom,
    setFollow,
    getFollow: () => state.follow,
    toggleMini,
    update: paint,
    refreshMinimap: scheduleMini,
    destroy() {
      destroyed = true;
      ro.disconnect();
      cancelAnimationFrame(raf);
      container.removeEventListener("wheel", onWheel);
      container.removeEventListener("pointerdown", onDown);
      container.removeEventListener("pointermove", onMove);
      container.removeEventListener("pointerup", onUp);
      container.removeEventListener("pointercancel", onUp);
      container.removeEventListener("keydown", onKey);
      controls?.remove();
      mini?.remove();
      inner.classList.remove("pc-inner");
      container.classList.remove("pc-view");
    },
  };
}
