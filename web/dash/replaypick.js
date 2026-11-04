// "Replay a run": pick a recorded run (read from replays/ on disk by /api/dash/replays) and play
const NM = { "h100-stitched": "H100 run", "modal-stitched": "Modal run", "baseten-stitched": "Baseten run" };
// it back through a live power graph. No keys, no GPU, no network call beyond this server.
// Every number keeps the label it was recorded with.
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const GAP_MAX_S = 3; // idle stretches longer than this are shortened, and the page says so
const $ = (s, h) => h.querySelector(s);

export function timeline(events) {
  // [{ev, at}] with `at` in seconds of playback at 1x: real gaps between timestamps, long idle gaps cut to GAP_MAX_S
  let at = 0, prev = null, cut = 0;
  const out = events.map((ev) => {
    const t = Date.parse(ev.ts);
    if (prev != null && !Number.isNaN(t)) { const g = Math.max(0, (t - prev) / 1000); if (g > GAP_MAX_S) cut += g - GAP_MAX_S; at += Math.min(g, GAP_MAX_S); }
    if (!Number.isNaN(t)) prev = t;
    return { ev, at };
  });
  return { items: out, total: at, cut };
}

export function phrase(ev) {
  const d = ev.data || {};
  if (ev.type === "telemetry.sample" && d.power_w != null) return `GPU power ${d.power_w} W`;
  if (ev.type === "gate.result") return `Check ${d.check || ""}: ${d.passed === false ? "failed, change rolled back" : "passed"}`;
  return d.summary || d.label && d.label !== "MEASURED" && d.label || d.kind || d.sub || ev.type;
}

const STYLE = `
#wn-replay{max-width:1160px;margin:0 auto;padding:40px 48px 64px}
#wn-replay h2{font-weight:400;font-size:34px;letter-spacing:-.01em}
#wn-replay .muted{color:var(--mu);font-size:15px}
.rp-list{display:grid;grid-template-columns:repeat(auto-fill,minmax(250px,1fr));gap:14px;margin:20px 0}
.rp-card{text-align:left;background:rgba(255,255,255,.03);border:1px solid var(--ln);border-radius:14px;padding:16px;color:var(--tx);font:inherit;cursor:pointer;display:flex;flex-direction:column;align-items:flex-start;justify-content:flex-start;gap:4px}
.rp-card:hover,.rp-card.on{border-color:rgba(140,200,255,.55);background:rgba(140,200,255,.06)}
.rp-card b{display:block;font-weight:500}.rp-card .t{font-size:14px;color:var(--mu);margin:6px 0}
.rp-pf{display:inline-block;font-size:12px;padding:2px 8px;border-radius:99px;border:1px solid var(--ln);margin-right:6px}
.rp-lab{font-size:11px;letter-spacing:.06em;padding:2px 6px;border-radius:4px;background:rgba(80,200,120,.15);color:#8fe3a8}
.rp-banner{margin:18px 0 10px;padding:10px 14px;border-radius:10px;background:rgba(80,200,120,.1);border:1px solid rgba(80,200,120,.35);font-weight:500}
.rp-ctl{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin:8px 0}
.rp-ctl button{font:inherit;font-size:14px;color:var(--tx);background:rgba(255,255,255,.06);border:1px solid var(--ln);border-radius:8px;padding:6px 12px;cursor:pointer}
.rp-ctl button.on{border-color:rgba(140,200,255,.7)}
.rp-graph{width:100%;height:220px;background:rgba(255,255,255,.02);border:1px solid var(--ln);border-radius:12px}
.rp-now{margin-top:8px;font-size:15px}.rp-log{font-size:13px;color:var(--mu);max-height:150px;overflow:auto;margin-top:6px;font-family:ui-monospace,monospace}
body.wn-replay-first .hero{height:auto;min-height:0;padding:56px 0 24px}
@media (max-width:900px){#wn-replay{padding:32px 24px}}`;

export async function mount(host) {
  if (!host) return;
  const st = document.createElement("style"); st.textContent = STYLE; document.head.append(st);
  let list = [], health = {};
  try { [list, health] = await Promise.all([fetch("/api/dash/replays").then((r) => r.json()).then((j) => (j.replays || []).filter((r) => NM[r.id]).map((r) => ({ ...r, name: NM[r.id] }))), fetch("/api/dash/health").then((r) => r.json()).catch(() => ({}))]); }
  catch { host.innerHTML = `<p class="muted">The recorded runs need the local server (./start.sh).</p>`; return; }
  const liveActive = health.mode === "live" && !health.replay_only;
  if (!liveActive) document.body.classList.add("wn-replay-first"); // no live run: the picker is the first screen
  host.innerHTML = `<h2>Replay a run</h2>
    <p class="muted">Recorded runs, read from <code>replays/</code> on this machine. No keys, no GPU, no live calls. Every number keeps the label it was recorded with.</p>
    <div class="rp-list">${list.map((r) => `<button class="rp-card" data-id="${esc(r.id)}">
      <b>${esc(r.name || r.id)}</b><span class="rp-pf">${esc(r.platform)}${r.gpu && r.platform !== "H100" ? " · " + esc(r.gpu) : ""}</span><span class="muted">${esc(r.date || "")}</span>
      <div class="t">${esc(r.title)}</div>
      <span class="rp-lab">${r.measured ? "MEASURED" : esc(Object.keys(r.labels)[0] || "UNLABELLED")}</span>
      <span class="muted"> ${r.events} events · ${r.power_samples ? r.power_samples + " power samples" : "no GPU power meter (hosted API)"}</span></button>`).join("") || `<p class="muted">No recorded runs found under replays/.</p>`}</div>
    <div id="rp-player" hidden></div>`;
  host.querySelectorAll(".rp-card").forEach((b) => (b.onclick = () => { host.querySelectorAll(".rp-card").forEach((x) => x.classList.toggle("on", x === b)); play(list.find((r) => r.id === b.dataset.id), $("#rp-player", host)); }));
  const want = new URL(location.href).searchParams.get("replay");
  if (want && list.some((r) => r.id === want)) host.querySelector(`.rp-card[data-id="${globalThis.CSS?.escape ? globalThis.CSS.escape(want) : want}"]`)?.click();
  document.querySelectorAll("[data-replay-jump]").forEach((a) => (a.onclick = (e) => { e.preventDefault(); host.scrollIntoView({ behavior: "smooth" }); }));
}

let stopPrev = null;
async function play(run, el) {
  stopPrev?.(); el.hidden = false; el.innerHTML = `<p class="muted">Loading ${esc(run.id)}…</p>`;
  const { events } = await fetch(`/api/dash/replays/${encodeURIComponent(run.id)}/events`).then((r) => r.json());
  const tl = timeline(events);
  const lab = run.measured ? "MEASURED" : Object.keys(run.labels)[0] || "UNLABELLED";
  el.innerHTML = `<div class="rp-banner" role="status">REPLAY of ${esc(lab)} run ${esc(NM[run.id] || run.id)} <span class="muted">· ${esc(run.platform)} · recorded ${esc(run.date || "")}</span></div>
    <div class="rp-ctl"><button id="rp-pp">Pause</button><button id="rp-re">Restart</button><span class="muted">Speed</span><button data-s="1" class="on">Real speed</button><button data-s="4">4x</button><span class="muted" id="rp-clock"></span></div>
    <svg class="rp-graph" viewBox="0 0 1000 220" preserveAspectRatio="none"><polyline id="rp-line" fill="none" stroke="#8cc8ff" stroke-width="2" points=""/><text id="rp-w" x="12" y="24" fill="#F2F5FA" font-size="16"></text></svg>
    <div class="rp-now" id="rp-now"></div><div class="rp-log" id="rp-log"></div>
    <p class="muted">Playback uses the real gaps between recorded events; idle stretches over ${GAP_MAX_S} s are shortened (${Math.round(tl.cut / 60)} min cut). ${run.power_samples ? "Power: NVML samples, MEASURED." : "This platform is a hosted API with no GPU power meter, so there is no power line."} CLI: <code>${esc(run.cli)}</code></p>`;
  const pts = [], maxW = Math.max(100, ...events.map((e) => (e.data || {}).power_w || 0));
  let speed = 1, paused = false, t = 0, i = 0, last = performance.now(), raf = 0;
  const line = $("#rp-line", el), now = $("#rp-now", el), log = $("#rp-log", el), clock = $("#rp-clock", el), wv = $("#rp-w", el);
  const reset = () => { t = 0; i = 0; pts.length = 0; line.setAttribute("points", ""); log.innerHTML = ""; now.textContent = ""; wv.textContent = ""; };
  const step = (ts) => {
    const dt = (ts - last) / 1000; last = ts; if (!paused) t += dt * speed;
    while (i < tl.items.length && tl.items[i].at <= t) {
      const { ev } = tl.items[i++], d = ev.data || {};
      if (ev.type === "telemetry.sample" && d.power_w != null) { pts.push(d.power_w); wv.textContent = `${d.power_w} W · ${d.label || ""}`; }
      else { now.textContent = phrase(ev); log.insertAdjacentHTML("afterbegin", `<div>${esc(ev.ts || "")} ${esc(ev.type)} ${esc(d.label || "")}</div>`); if (log.childElementCount > 60) log.lastElementChild.remove(); }
    }
    const view = pts.slice(-200);
    line.setAttribute("points", view.map((w, k) => `${(k / 199) * 1000},${210 - (w / maxW) * 180}`).join(" "));
    clock.textContent = `${Math.floor(t)} s / ${Math.floor(tl.total)} s${i >= tl.items.length ? " · finished" : ""}`;
    raf = requestAnimationFrame(step);
  };
  raf = requestAnimationFrame(step);
  $("#rp-pp", el).onclick = (e) => { paused = !paused; e.target.textContent = paused ? "Play" : "Pause"; };
  $("#rp-re", el).onclick = () => reset();
  el.querySelectorAll("[data-s]").forEach((b) => (b.onclick = () => { speed = +b.dataset.s; el.querySelectorAll("[data-s]").forEach((x) => x.classList.toggle("on", x === b)); }));
  stopPrev = () => cancelAnimationFrame(raf);
  el.scrollIntoView({ behavior: "smooth", block: "start" });
}

mount(document.getElementById("wn-replay"));

// Inline player for any page (Results "Play it back"): plays one recorded run in `el`.
export async function playInline(id, el) {
  if (!el) return;
  if (!document.getElementById("rp-style")) { const st = document.createElement("style"); st.id = "rp-style"; st.textContent = STYLE; document.head.append(st); }
  const list = await fetch("/api/dash/replays").then((r) => r.json()).then((j) => j.replays || []).catch(() => []);
  const run = list.find((r) => r.id === id);
  if (!run) { el.hidden = false; el.innerHTML = `<p class="muted">Run ${esc(id)} is not under replays/ on this server.</p>`; return; }
  return play(run, el);
}
