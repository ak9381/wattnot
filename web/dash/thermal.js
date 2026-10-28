// Thermal (infrared-style) floor plan of the scanned room, in a pan/zoom popup.
// Racks come from the SAM 3.1 scan (outlines MEASURED, ordered left to right as
// in the photo); their heat is SIMULATED from REPLAY power with the scan's
// thermal model, so the plan is labelled that way.
import { popup } from "./r7.js";
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
// ironbow: black -> purple -> red -> orange -> yellow -> white
const STOPS = [[0, [20, 10, 40]], [0.25, [90, 20, 130]], [0.5, [200, 40, 70]], [0.7, [240, 120, 30]], [0.88, [250, 210, 60]], [1, [255, 250, 220]]];
export function ironbow(f) {
  f = Math.max(0, Math.min(1, f));
  for (let i = 1; i < STOPS.length; i++) if (f <= STOPS[i][0]) {
    const [a, ca] = STOPS[i - 1], [b, cb] = STOPS[i], t = (f - a) / (b - a);
    return `rgb(${ca.map((v, k) => Math.round(v + (cb[k] - v) * t)).join(",")})`;
  }
  return "rgb(255,250,220)";
}
// Data-hall plan: Row B holds the scanned racks, placed along the row by the
// x-position SAM 3.1 found (box centres from the photo, scaled to the row).
// Rows A, C, D, the CRAC units and the room shell are ILLUSTRATIVE (no data:
// drawn grey, no temperatures). Cold aisles face rack fronts; hot aisles the backs.
function plan(scan, side) {
  const racks = [...scan.racks].sort((a, b) => a.box[0] - b.box[0]);
  const lo = 22, hi = 50, M = 64;            // px per metre
  const rackW = 0.6 * M, rackD = 1.2 * M, aisle = 1.2 * M;
  const nPer = Math.max(12, racks.length + 1), rowL = nPer * rackW;
  const x0 = 130, W = x0 + rowL + 330, top = 90;
  const t = (r) => (side === "after" ? r.after?.temp_c : r.before?.temp_c) ?? r.temp_c;
  const pw = (r) => (side === "after" ? r.after?.power_w : r.before?.power_w) ?? r.power_w;
  // layout top->bottom: cold A | row A | hot 1 | row B (scanned) | cold 2 | row C | hot 2 | row D | cold 3
  const rows = [], aisles = []; let y = top;
  const push = (kind, label) => { if (kind === "row") { rows.push({ y, label }); y += rackD; } else { aisles.push({ y, kind, label }); y += aisle; } };
  push("cold", "Cold aisle 1"); push("row", "Row A"); push("hot", "Hot aisle 1"); push("row", "Row B · scanned");
  push("cold", "Cold aisle 2"); push("row", "Row C"); push("hot", "Hot aisle 2"); push("row", "Row D"); push("cold", "Cold aisle 3");
  const H = y + 110;
  const bx = racks.map((r) => (r.box[0] + r.box[2]) / 2), bmin = Math.min(...bx), bmax = Math.max(...bx);
  const pos = (i) => x0 + rackW * (i + 0.5); // scan order, one slot each (photo spacing overlaps at this scale)
  const ill = (ry) => Array.from({ length: nPer }, (_, k) => `<rect class="th-ill" x="${x0 + k * rackW + 1}" y="${ry + 1}" width="${rackW - 2}" height="${rackD - 2}" rx="2"/>`).join("");
  const B = rows[1];
  const glow = [], real = [];
  racks.forEach((r, i) => {
    const cx = pos(i), f = (t(r) - lo) / (hi - lo), c = ironbow(f), x = cx - rackW / 2;
    glow.push(`<ellipse cx="${cx}" cy="${B.y + rackD / 2 + (f > 0.6 ? 18 : 0)}" rx="${rackW * (0.6 + 0.8 * f)}" ry="${rackD * (0.5 + 0.5 * f)}" fill="${c}" opacity="${(0.25 + f * 0.55).toFixed(2)}" filter="url(#thb)"/>`);
    real.push(`<g class="th-r" tabindex="0"><title>${esc(r.id)} · ${t(r).toFixed(1)} °C (simulated) · ${pw(r)} W per GPU (replay)${r.decision ? ` · ${esc(r.decision)}` : ""}</title>
      <rect x="${x}" y="${B.y}" width="${rackW}" height="${rackD}" rx="2" fill="${c}" stroke="#fff" stroke-width="1.2"/>
      <text x="${cx}" y="${B.y + rackD / 2 + 4}" text-anchor="middle" class="th-t">${t(r).toFixed(0)}°</text>
      <text x="${cx}" y="${B.y - 5}" text-anchor="middle" class="th-id" font-size="12">${esc(r.id)}${r.id === scan.real_rack ? "★" : ""}</text></g>`);
  });
  const crac = (cx, cy, id) => `<g class="th-crac"><rect x="${cx}" y="${cy}" width="${1.0 * M}" height="${2.2 * M}" rx="4"/><text x="${cx + 0.5 * M}" y="${cy + 1.1 * M + 4}" text-anchor="middle">${id}</text></g>`;
  const cracs = [aisles[0], aisles[2], aisles[4]].map((a, k) => crac(20, a.y - 0.5 * M, `CRAC ${k + 1}`)).join("") + [aisles[0], aisles[2], aisles[4]].map((a, k) => crac(W - 20 - M, a.y - 0.5 * M, `CRAC ${k + 4}`)).join("");
  const grad = Array.from({ length: 11 }, (_, i) => `<stop offset="${i * 10}%" stop-color="${ironbow(i / 10)}"/>`).join("");
  const sb = 5 * M;
  return `<svg class="th-svg" viewBox="0 0 ${W} ${H}" width="${W}" height="${H}" role="img" aria-label="Thermal data-hall floor plan, ${side}">
    <defs><filter id="thb" x="-150%" y="-150%" width="400%" height="400%"><feGaussianBlur stdDeviation="16"/></filter><linearGradient id="thg">${grad}</linearGradient>
      <pattern id="tile" width="${0.6 * M}" height="${0.6 * M}" patternUnits="userSpaceOnUse"><path d="M ${0.6 * M} 0 L 0 0 0 ${0.6 * M}" fill="none" stroke="rgba(255,255,255,.05)"/></pattern></defs>
    <rect width="${W}" height="${H}" fill="#07050f"/><rect x="10" y="${top - 30}" width="${W - 20}" height="${y - top + 60}" fill="url(#tile)" stroke="rgba(255,255,255,.25)" stroke-width="2" rx="6"/>
    ${aisles.map((a) => `<rect x="${x0 - 10}" y="${a.y}" width="${rowL + 20}" height="${aisle}" class="${a.kind === "hot" ? "th-hot" : "th-cold"}"/><text x="${x0 + rowL + 20}" y="${a.y + aisle / 2 + 4}" class="th-aisle">${a.label}${a.kind === "cold" ? " · perforated tiles" : " · exhaust"}</text>`).join("")}
    ${rows.map((r, k) => `<text x="${x0 - 14}" y="${r.y + rackD / 2 + 4}" text-anchor="end" class="th-row">${r.label.split(" · ")[0]}</text>${k === 1 ? "" : ill(r.y)}`).join("")}
    ${glow.join("")}${real.join("")}
    ${cracs}
    <text x="${x0}" y="${top - 40}" class="th-aisle">Row B: SAM 3.1 rack outlines, placed in scan order (measured) · other rows, CRAC units and room: illustrative, no data</text>
    <g transform="translate(${x0},${H - 60})"><rect width="${sb}" height="6" fill="#fff"/><rect width="${sb / 5}" height="6" fill="#07050f" x="${sb / 5}"/><rect width="${sb / 5}" height="6" fill="#07050f" x="${(3 * sb) / 5}"/><text y="22" class="th-aisle">0</text><text x="${sb}" y="22" text-anchor="end" class="th-aisle">5 m</text></g>
    <g transform="translate(${x0 + sb + 60},${H - 60})"><rect width="260" height="10" fill="url(#thg)"/><text y="26" class="th-aisle">${lo} °C</text><text x="260" y="26" text-anchor="end" class="th-aisle">${hi} °C (simulated)</text></g>
  </svg>`;
}
export async function openThermal(opts = {}) {
  const scan = await fetch("/room/data/arsat/scan.json").then((r) => r.json());
  const s = (k) => scan[k] || {};
  const head = (side) => `<div class="th-head"><span class="bdg mp">Meta · SAM 3.1</span><span class="thl n-measured">rack outlines MEASURED</span><span class="thl n-simulated">heat SIMULATED</span><span class="thl n-replay">power REPLAY</span>
    <span class="th-sum">${side === "after" ? `After WattNot: hottest ${s("after").max_c ?? "?"} °C, ${s("after").hot_racks ?? "?"} hot rack` : `Before: hottest ${s("before").max_c ?? "?"} °C, ${s("before").hot_racks ?? "?"} hot racks`}</span></div>`;
  const ms = opts.measuredSaving;
  const honest = `<div class="th-honest"><p><b>Measured:</b> ${scan.racks.length} rack outlines found by SAM 3.1 in the room photo${scan.real_rack ? `, and the GPU power of rack ${esc(scan.real_rack)}` : ""}. <b>Hotspot saving:</b> ${ms ? `<span class="n-measured">${esc(ms.text)} MEASURED</span>` : `<span class="r7miss">— not measured in this run</span>`}.</p><p><b>Illustration:</b> the before/after heat (hot racks ${s("before").hot_racks ?? "?"} → ${s("after").hot_racks ?? "?"}, hottest ${s("before").max_c ?? "?"} → ${s("after").max_c ?? "?"} °C) is <span class="n-simulated">SIMULATED</span> from replayed power, not a camera reading.</p></div>`;
  let side = "before", pc;
  popup(opts.measuredSaving !== undefined || opts.fromCard ? "Cooling hotspots (SAM 3.1)" : "Thermal floor plan", `<div class="th-wrap">${honest}<div class="th-tg"><button class="btn on" data-side="before">Before</button><button class="btn" data-side="after">After WattNot</button></div><div data-th>${head(side)}${plan(scan, side)}</div><p class="muted th-note">Top-down view of a hot-aisle / cold-aisle hall. Row B holds the racks SAM 3.1 found, left to right as in the photo (★ has a real GPU). Grey racks, CRAC units and the room shell are illustrative. Heat = inlet + 0.05 °C per watt: a simulation, not a camera.</p></div>`, {
    pan: true, sub: "Drag to pan, scroll to zoom. Hover a rack for its numbers.",
    onMount: (root, pan) => {
      pc = pan;
      root.querySelectorAll("[data-side]").forEach((b) => b.addEventListener("click", () => {
        side = b.dataset.side; root.querySelectorAll("[data-side]").forEach((x) => x.classList.toggle("on", x === b));
        root.querySelector("[data-th]").innerHTML = head(side) + plan(scan, side);
      }));
    },
  });
  return pc;
}
