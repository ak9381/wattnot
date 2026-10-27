// A room heat map drawn from a wattnot.room/1 scan: one polygon per rack, coloured by temperature.
// The scan comes from a room module (or the labelled example). Nothing here segments an image.
import { esc, pill } from "./theme.js";

const T0 = 22, T1 = 45;                       // colour scale, in °C
const lerp = (a, b, t) => a + (b - a) * t;
const stops = [[0, [38, 92, 168]], [0.35, [46, 160, 140]], [0.62, [226, 180, 60]], [0.85, [226, 100, 50]], [1, [200, 40, 60]]];
export function heatColor(t) {
  if (t == null) return "#2a313b";
  const x = Math.max(0, Math.min(1, (t - T0) / (T1 - T0)));
  for (let i = 1; i < stops.length; i++) if (x <= stops[i][0]) {
    const [p, a] = stops[i - 1], [q, b] = stops[i], k = (x - p) / (q - p);
    return `rgb(${a.map((v, j) => Math.round(lerp(v, b[j], k))).join(",")})`;
  }
  return "rgb(200,40,60)";
}
const centroid = (poly) => poly.reduce((a, p) => [a[0] + p[0] / poly.length, a[1] + p[1] / poly.length], [0, 0]);
export const rackLine = (r) => `${r.id}: ${r.temp_c == null ? "no temperature" : `${r.temp_c.toFixed(1)} °C ${r.temp_label || ""}`}; ${r.power_w == null ? "no power" : `${Math.round(r.power_w)} W ${r.power_label || ""}`}; ${r.decision}${r.reason ? `. ${r.reason}` : ""}`;

// returns svg markup; `opts.img` puts a picture under the racks, `opts.target` rings the chosen rack
export function heatSvg(scan, opts = {}) {
  const [w, h] = scan.size || [400, 240];
  const img = opts.img ? `<image href="${esc(opts.img)}" x="0" y="0" width="${w}" height="${h}" preserveAspectRatio="xMidYMid slice" opacity=".55"></image>` : "";
  const racks = scan.racks.map((r) => {
    const pts = r.polygon.map((p) => p.join(",")).join(" "), [cx, cy] = centroid(r.polygon);
    return `<g class="rk ${esc(r.decision)} ${r.id === opts.target ? "tgt" : ""}" data-id="${esc(r.id)}" tabindex="0" role="img" aria-label="${esc(rackLine(r))}">
      <polygon points="${pts}" style="fill:${heatColor(r.temp_c)}" fill-opacity="${opts.img ? 0.7 : 0.92}"></polygon>
      <text x="${cx}" y="${cy - 3}" class="rid">${esc(r.id)}</text><text x="${cx}" y="${cy + 11}" class="rt">${r.temp_c == null ? "–" : r.temp_c.toFixed(0) + "°"}</text></g>`;
  }).join("");
  return `<svg class="heat" viewBox="0 0 ${w} ${h}" role="group" aria-label="room heat map, ${scan.racks.length} racks">${img}${racks}</svg>`;
}
export const heatLegend = () => `<div class="hleg" aria-hidden="true"><span>${T0} °C</span><i style="background:linear-gradient(90deg,${[0, 0.25, 0.5, 0.75, 1].map((x) => heatColor(T0 + x * (T1 - T0))).join(",")})"></i><span>${T1} °C</span></div>`;
export const scanTag = (scan) => (scan.label ? pill(scan.label) : "");

// wire hover/focus on racks to a details line; returns a function to pulse one rack
export function wireHeat(root, scan, detailEl) {
  const show = (id) => { const r = (root.__scan || scan).racks.find((x) => x.id === id); if (detailEl && r) detailEl.textContent = rackLine(r); };
  root.querySelectorAll(".rk").forEach((g) => { g.onmouseenter = g.onfocus = () => show(g.dataset.id); });
}
export function pulse(root, id) {
  const g = root.querySelector(`.rk[data-id="${CSS.escape(id)}"]`); if (!g) return;
  g.classList.remove("pulse"); void g.getBoundingClientRect(); g.classList.add("pulse");
}
export function recolor(root, scan) {
  for (const r of scan.racks) {
    const g = root.querySelector(`.rk[data-id="${CSS.escape(r.id)}"]`); if (!g) continue;
    g.querySelector("polygon").style.fill = heatColor(r.temp_c);
    g.querySelector("text.rt").textContent = r.temp_c == null ? "–" : r.temp_c.toFixed(0) + "°";
    g.setAttribute("aria-label", rackLine(r));
  }
  root.__scan = scan;
}
