// Compact Room scan node for the Architecture map: the heat map with a visible/thermal toggle
// and a before/after toggle. Plain ES module.
//   import { mountNode } from "../room/node.js";
//   mountNode(el, { room: "arsat", data: "../room/data", href: "../room/index.html" });
// It reads data/<room>/scan.json, or takes a room_scan event payload as opts.scan together with
// opts.base (the folder that holds the photo and thermal images).
const NS = "http://www.w3.org/2000/svg";
const COLOR = { derate: "#F2994A", hold: "#B79CFF", ok: "#22C7D9", none: "#5b6a77" };

export async function mountNode(root, opts = {}) {
  const room = opts.room || "arsat", base = opts.base || `${opts.data || "../room/data"}/${room}/`;
  const scan = opts.scan || await (await fetch(base + "scan.json")).json();
  const S = { view: "thermal", phase: "before" };
  const mk = (t, a = {}, ...k) => { const n = document.createElement(t); for (const [x, v] of Object.entries(a)) n.setAttribute(x, v); n.append(...k); return n; };
  const btn = (key, v, text) => { const b = mk("button", { type: "button" }, text); b.onclick = () => { S[key] = v; draw(); }; b.dataset.k = key; b.dataset.v = v; return b; };
  const img = mk("img", { alt: "room", style: "width:100%;display:block" });
  const svg = document.createElementNS(NS, "svg");
  svg.setAttribute("viewBox", `0 0 ${scan.size[0]} ${scan.size[1]}`);
  svg.setAttribute("style", "position:absolute;inset:0;width:100%;height:100%");
  const stage = mk("div", { style: "position:relative;line-height:0" }, img, svg);
  const bar = mk("div", { class: "room-node-bar" }, btn("view", "visible", "Visible"), btn("view", "thermal", "Thermal"),
    btn("phase", "before", "Before"), btn("phase", "after", "After caps"));
  const note = mk("div", { class: "room-node-note" });
  root.replaceChildren(bar, stage, note);
  function draw() {
    const th = S.view === "thermal";
    img.src = base + (th ? (S.phase === "after" && scan.thermal_after_image ? scan.thermal_after_image : scan.thermal_image) : scan.image);
    bar.querySelectorAll("button").forEach((b) => b.classList.toggle("on", S[b.dataset.k] === b.dataset.v));
    svg.replaceChildren();
    for (const r of scan.racks) {
      const p = document.createElementNS(NS, "polygon");
      p.setAttribute("points", r.polygon.map((q) => q.join(",")).join(" "));
      p.setAttribute("fill", "none"); p.setAttribute("stroke", COLOR[r.decision] || COLOR.none);
      p.setAttribute("stroke-width", "2"); p.setAttribute("vector-effect", "non-scaling-stroke");
      svg.append(p);
    }
    const a = S.phase === "after" ? scan.after : scan.before;
    note.textContent = `${scan.sam.racks_found} racks (MEASURED, SAM 3.1) - hottest ${a.max_c} C - ${a.hot_racks} over the line - `
      + `${th ? scan.labels.thermal_frame : "MEASURED"} ${th ? "thermal frame" : "photo"}; the H100 rack is measured, the rest are examples`;
  }
  draw();
  return { state: S, draw };
}
