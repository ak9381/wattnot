// Room view: the room photo, SAM's rack outlines, a thermal frame, and one decision per rack.
// Plain ES module, no build step. Data: data/<room>/scan.json (python scripts/room_scan.py).
// Embed: import { mount } from "../room/room.js"; mount(rootEl, { room: "arsat" }).

const ANCHORS = [[0, [0, 0, 4]], [.12, [40, 0, 110]], [.30, [120, 10, 140]], [.48, [200, 40, 90]],
  [.64, [245, 100, 20]], [.80, [255, 175, 15]], [.92, [255, 235, 90]], [1, [255, 255, 225]]];
const NS = "http://www.w3.org/2000/svg";
const COLOR = { derate: "#F2994A", hold: "#B79CFF", ok: "#22C7D9", none: "#5b6a77" };
const WORD = { derate: "lower cap", hold: "hold: cooling", ok: "ok", none: "no reading" };

const el = (tag, attrs = {}, ...kids) => {
  const n = tag.startsWith("svg:") ? document.createElementNS(NS, tag.slice(4)) : document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") n.setAttribute("class", v); else if (k === "text") n.textContent = v;
    else if (k.startsWith("on")) n.addEventListener(k.slice(2), v); else n.setAttribute(k, v);
  }
  for (const c of kids.flat()) if (c != null) n.append(c);
  return n;
};
const pill = (label) => el("span", { class: `pill ${label}`, text: label });
const fmt = (v, d = 1) => (v == null ? "-" : Number(v).toFixed(d));

export function palette(f) {
  for (let i = 1; i < ANCHORS.length; i++) {
    if (f <= ANCHORS[i][0]) {
      const [a, ca] = ANCHORS[i - 1], [b, cb] = ANCHORS[i], t = (f - a) / (b - a);
      return ca.map((x, k) => Math.round(x + (cb[k] - x) * t));
    }
  }
  return ANCHORS[ANCHORS.length - 1][1];
}

export async function mount(root, opts = {}) {
  const q = new URLSearchParams(location.search);
  const room = opts.room || q.get("room") || "arsat";
  const base = (opts.data || q.get("data") || "data") + "/" + room + "/";
  const scan = opts.scan || await (await fetch(base + "scan.json")).json();
  const $ = (id) => root.querySelector("#" + id);
  const S = { view: q.get("view") || "thermal", phase: q.get("phase") || "before", sam: q.get("sam") !== "off", sel: q.get("rack") };
  const racks = scan.racks, [W, H] = scan.size;
  const cur = (r) => (S.phase === "after" ? r.after : r.before);

  const seg = (id, key) => $(id).querySelectorAll("button").forEach((b) => {
    b.onclick = () => { S[key] = b.dataset.v; render(); };
  });
  seg("mode", "view"); seg("phase", "phase");
  $("sam").onclick = () => { S.sam = !S.sam; if (!S.sam) S.sel = null; render(); };

  function drawLegend() {
    const [lo, hi] = scan.thermal.range_c, c = el("canvas", { width: 380, height: 12 });
    const g = c.getContext("2d");
    for (let x = 0; x < 380; x++) { const [r, gg, b] = palette(x / 379); g.fillStyle = `rgb(${r},${gg},${b})`; g.fillRect(x, 0, 1, 12); }
    const hot = scan.thermal.hot_c, pos = ((hot - lo) / (hi - lo)) * 100;
    const wrap = el("div", { style: "position:relative" }, c,
      el("div", { style: `position:absolute;top:-3px;bottom:-3px;left:${pos}%;border-left:2px solid #fff` }));
    $("legend").replaceChildren(el("span", { text: `${lo} \u00b0C` }), wrap, el("span", { text: `${hi} \u00b0C` }),
      el("span", {}, `white line = hot line ${hot} \u00b0C `, pill(scan.labels.rack_temp_c)));
  }

  function drawOverlay() {
    const svg = $("ov"); svg.setAttribute("viewBox", `0 0 ${W} ${H}`); svg.replaceChildren();
    if (!S.sam) return;
    for (const r of racks) {
      const col = COLOR[r.decision] || COLOR.none, pts = r.polygon.map((p) => p.join(",")).join(" ");
      svg.append(el("svg:polygon", { points: pts, stroke: col, "stroke-dasharray": r.decision === "ok" ? "6 5" : "0" }));
      svg.append(el("svg:path", { d: r.path, class: "m" + (S.sel === r.id ? " sel" : ""), onclick: () => { S.sel = r.id; render(); } }));
      if (r.box[2] - r.box[0] >= 60 || S.sel === r.id) svg.append(el("svg:text", { x: r.box[0] + 6, y: r.box[1] + 26, text: r.id }));
    }
  }

  function drawHot() {
    const list = $("hot"); list.replaceChildren();
    const off = scan.sam_off;
    if (!S.sam) {
      $("hsub").textContent = "- SAM off";
      list.append(el("li", { style: "--c:#F2994A;grid-template-columns:1fr" },
        el("span", {}, `hottest pixel ${fmt(scan.before.max_c)} \u00b0C, rack unknown`)));
      return;
    }
    $("hsub").textContent = `- ${racks.length} racks, ranked by camera \u00b0C`;
    const ranked = [...racks].filter((r) => cur(r).temp_c != null).sort((a, b) => cur(b).temp_c - cur(a).temp_c);
    for (const r of ranked.slice(0, 6)) {
      list.append(el("li", { style: `--c:${COLOR[r.decision]}`, class: S.sel === r.id ? "sel" : "", onclick: () => { S.sel = r.id; render(); } },
        el("span", { class: "id", text: r.id + (r.source === "h100-run1" ? "\u2605" : "") }), el("span", { text: `${fmt(cur(r).temp_c)} \u00b0C` }),
        el("span", { class: "w", text: `${cur(r).power_w} W/GPU` }), el("span", { class: `act ${r.decision}`, text: WORD[r.decision] })));
    }
  }

  function spark(r) {
    const s = r.series_w, lo = Math.min(...s, r.cap_w || 1e9) - 20, hi = Math.max(...s) + 20, w = 340, h = 46;
    const y = (v) => h - ((v - lo) / (hi - lo)) * h, x = (i) => (i / (s.length - 1)) * w;
    const svg = el("svg:svg", { class: "spark", viewBox: `0 0 ${w} ${h}`, preserveAspectRatio: "none" });
    svg.append(el("svg:polyline", { points: s.map((v, i) => `${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(" "), fill: "none", stroke: "#5AA9F0", "stroke-width": 1.6 }));
    if (r.cap_w) svg.append(el("svg:line", { x1: 0, x2: w, y1: y(r.cap_w), y2: y(r.cap_w), stroke: "#F2994A", "stroke-dasharray": "4 3" }));
    return svg;
  }

  function drawDrawer() {
    const d = $("drawer"); d.replaceChildren();
    if (!S.sam) {
      const o = scan.sam_off;
      d.append(el("div", { class: "blunt" }, el("b", { text: "Without SAM masks, " }),
        `${o.reason}. It cuts every GPU by ${o.cut_w} W: ${o.racks_cut} racks lose power, ${o.racks_cut_needlessly} of them did not need it, `
        + `${o.cooling_faults_missed} cooling fault is missed, ${o.watts_lost.toLocaleString()} W lost against ${o.watts_lost_with_sam.toLocaleString()} W with SAM. `,
        pill("SIMULATED")));
      return;
    }
    const r = racks.find((x) => x.id === S.sel);
    if (!r) { d.append(el("div", { class: "muted", text: "Click a rack on the photo, or a row above." })); return; }
    const c = cur(r), after = S.phase === "after";
    d.append(el("h4", {}, `${r.id} `, el("span", { class: `act ${r.decision}`, text: WORD[r.decision] }),
      r.cap_w ? el("span", { class: "muted", text: `  cap ${r.cap_w} W` }) : null));
    d.append(el("div", { class: "why" }, r.reason + " ", pill("ADVICE")));
    d.append(el("div", { class: "kv" },
      el("span", { text: "camera" }), el("span", {}, `${fmt(c.temp_c)} \u00b0C `, pill(scan.labels.rack_temp_c),
        after ? ` (was ${fmt(r.before.temp_c)})` : ""),
      el("span", { text: "power / GPU" }), el("span", {}, `${c.power_w} W `, pill(after && r.cap_w ? "SIMULATED" : scan.labels.gpu_power_w),
        after ? ` (was ${r.before.power_w})` : ""),
      el("span", { text: "rack" }), el("span", {}, r.source === "h100-run1" ? "H100 rack: one real GPU " : "example rack ",
        pill(r.source === "h100-run1" ? "MEASURED" : "SIMULATED")),
      el("span", { text: "rack to GPUs" }), el("span", {}, `${r.gpus.length} GPUs `, pill(scan.labels.rack_to_gpu_map))));
    const cap = after && r.cap_w ? r.cap_w : 1e9;
    d.append(el("table", {}, el("tr", {}, el("th", { text: "GPU" }), el("th", { text: "W" }), el("th", { text: "\u00b0C" })),
      ...r.gpus.map((g) => el("tr", {}, el("td", { text: `GPU ${g.id}${g.measured ? " \u2605" : ""}` }), el("td", { text: Math.min(g.power_w, cap) }), el("td", { text: g.gpu_temp_c })))));
    d.append(el("div", { class: "muted", style: "font:11px var(--mono)" }, "GPU W ", pill(after && r.cap_w ? "SIMULATED" : "REPLAY"), "  GPU \u00b0C ", pill("SIMULATED"), r.gpus.some((g) => g.measured) ? "  \u2605 = the real H100 (RUN1 NVML log, replayed)" : ""));
    d.append(spark(r), el("div", { class: "muted", style: "font:11px var(--mono)" }, "power over the run ", pill("REPLAY"), r.cap_w ? " dashed = new cap" : ""));
  }

  function drawSum() {
    const a = scan.before, b = scan.after, aft = S.phase === "after", x = aft ? b : a;
    const off = scan.sam_off, card = (k, v, u, d) => el("div", { class: "card" }, el("div", { class: "k", text: k }),
      el("div", { class: "v" }, v, el("small", { text: u })), el("div", { class: "d", text: d || "" }));
    $("sum").replaceChildren(
      card("Hottest rack", fmt(x.max_c), "\u00b0C", aft ? `was ${fmt(a.max_c)}` : `hot line ${scan.thermal.hot_c} \u00b0C`),
      card("Racks over the line", x.hot_racks, "of " + racks.length, aft ? `was ${a.hot_racks}` : "from the thermal frame"),
      card("GPU power in the room", x.room_power_w.toLocaleString(), "W", aft ? `${b.saved_w.toLocaleString()} W saved by caps` : "REPLAY x SIMULATED load"),
      S.sam ? card("SAM rack masks", scan.sam.racks_found, "racks", "MEASURED, prompt \"" + scan.sam.prompt + "\"")
        : card("Lost without SAM", off.watts_lost.toLocaleString(), "W", `vs ${off.watts_lost_with_sam.toLocaleString()} W with SAM`));
  }

  function render() {
    const isTh = S.view === "thermal", file = isTh ? (S.phase === "after" && scan.thermal_after_image ? scan.thermal_after_image : scan.thermal_image) : scan.image;
    $("img").src = base + file;
    root.querySelectorAll("#mode button").forEach((b) => b.classList.toggle("on", b.dataset.v === S.view));
    root.querySelectorAll("#phase button").forEach((b) => b.classList.toggle("on", b.dataset.v === S.phase));
    const sb = $("sam"); sb.setAttribute("aria-pressed", String(S.sam)); sb.querySelector("b").textContent = S.sam ? "on" : "off";
    root.querySelectorAll(".stamp").forEach((n) => n.remove());
    $("frame").append(el("div", { class: "stamp" }, isTh ? [pill(scan.labels.thermal_frame), ` thermal frame, ${S.phase === "after" ? "after caps" : "before"}`] : [pill("MEASURED"), " visible photo"]));
    drawOverlay(); drawHot(); drawDrawer(); drawSum();
  }

  drawLegend();
  $("src").textContent = `${scan.room_id} - SAM 3.1 "${scan.sam.prompt}" - thermal ${scan.thermal.mode}`;
  $("foot").replaceChildren(...[["MEASURED", "SAM rack count and outlines"], ["REPLAY", "H100 RUN1 power, played back for every GPU"],
    ["SIMULATED", "rack-to-GPU map, load factors, GPU \u00b0C, the thermal frame"], ["ESTIMATE", "colour to \u00b0C from a real IR still"],
    ["ADVICE", "decisions; the planner and gate still decide"]].map(([l, t]) => el("span", {}, pill(l), " " + t)),
    el("span", { text: "Heat comes from the thermal frame, power from GPU telemetry. A normal CCTV camera sees no heat." }),
    el("span", {}, "Photo: ", el("a", { href: "../../rack/room/" + room + "/ATTRIBUTION.md", style: "color:inherit", text: "credit and licence" })));
  render();
  return { state: S, render };
}

if (typeof document !== "undefined" && document.getElementById("app") && !window.__roomNoAuto) mount(document.body.querySelector("#app").parentElement);
