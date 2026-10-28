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
function plan(scan, side) {
  const racks = [...scan.racks].sort((a, b) => a.box[0] - b.box[0]);
  const lo = 22, hi = 50, rw = 70, rd = 110, gap = 14, pad = 60, W = pad * 2 + racks.length * (rw + gap) - gap, H = 420, y = 150;
  const t = (r) => (side === "after" ? r.after?.temp_c : r.before?.temp_c) ?? r.temp_c;
  const p = (r) => (side === "after" ? r.after?.power_w : r.before?.power_w) ?? r.power_w;
  const g = racks.map((r, i) => {
    const x = pad + i * (rw + gap), f = (t(r) - lo) / (hi - lo), c = ironbow(f);
    return `<g class="th-r" tabindex="0"><title>${esc(r.id)} · ${t(r).toFixed(1)} °C (simulated) · ${p(r)} W per GPU (replay)${r.decision ? ` · ${esc(r.decision)}` : ""}</title>
      <ellipse cx="${x + rw / 2}" cy="${y + rd / 2}" rx="${rw * (0.6 + f)}" ry="${rd * (0.55 + f * 0.6)}" fill="${c}" opacity="${(0.25 + f * 0.5).toFixed(2)}" filter="url(#thb)"/>
      <rect x="${x}" y="${y}" width="${rw}" height="${rd}" rx="5" fill="${c}" stroke="rgba(255,255,255,.35)"/>
      <text x="${x + rw / 2}" y="${y + rd + 20}" text-anchor="middle" class="th-id">${esc(r.id)}${r.id === scan.real_rack ? " ★" : ""}</text>
      <text x="${x + rw / 2}" y="${y + rd / 2 + 5}" text-anchor="middle" class="th-t">${t(r).toFixed(1)}°</text></g>`;
  }).join("");
  const grad = Array.from({ length: 11 }, (_, i) => `<stop offset="${i * 10}%" stop-color="${ironbow(i / 10)}"/>`).join("");
  return `<svg class="th-svg" viewBox="0 0 ${W} ${H}" width="${W}" height="${H}" role="img" aria-label="Thermal floor plan, ${side}">
    <defs><filter id="thb" x="-100%" y="-100%" width="300%" height="300%"><feGaussianBlur stdDeviation="22"/></filter><linearGradient id="thg">${grad}</linearGradient></defs>
    <rect width="${W}" height="${H}" fill="#0b0716"/>
    <rect x="${pad - 20}" y="${y - 70}" width="${W - 2 * pad + 40}" height="50" fill="rgba(90,169,240,.08)"/><text x="${pad - 12}" y="${y - 40}" class="th-aisle">Cold aisle (inlet ${scan.thermal?.t_in_c ?? 22} °C)</text>
    <rect x="${pad - 20}" y="${y + rd + 34}" width="${W - 2 * pad + 40}" height="50" fill="rgba(240,80,74,.08)"/><text x="${pad - 12}" y="${y + rd + 64}" class="th-aisle">Hot aisle</text>
    ${g}
    <rect x="${pad}" y="${H - 34}" width="240" height="10" fill="url(#thg)"/><text x="${pad}" y="${H - 40}" class="th-aisle">${lo} °C</text><text x="${pad + 240}" y="${H - 40}" text-anchor="end" class="th-aisle">${hi} °C</text>
  </svg>`;
}
export async function openThermal() {
  const scan = await fetch("/room/data/arsat/scan.json").then((r) => r.json());
  const s = (k) => scan[k] || {};
  const head = (side) => `<div class="th-head"><span class="bdg mp">Meta · SAM 3.1</span><span class="thl n-measured">rack outlines MEASURED</span><span class="thl n-simulated">heat SIMULATED</span><span class="thl n-replay">power REPLAY</span>
    <span class="th-sum">${side === "after" ? `After WattNot: hottest ${s("after").max_c ?? "?"} °C, ${s("after").hot_racks ?? "?"} hot rack` : `Before: hottest ${s("before").max_c ?? "?"} °C, ${s("before").hot_racks ?? "?"} hot racks`}</span></div>`;
  let side = "before", pc;
  popup("Thermal floor plan", `<div class="th-wrap"><div class="th-tg"><button class="btn on" data-side="before">Before</button><button class="btn" data-side="after">After WattNot</button></div><div data-th>${head(side)}${plan(scan, side)}</div><p class="muted th-note">Top-down view, racks left to right as SAM 3.1 found them in the photo (★ is the rack with a real GPU). Heat = inlet + 0.05 °C per watt: a simulation, not a camera.</p></div>`, {
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
