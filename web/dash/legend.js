// The one shared number legend (same as the EU Compliance tab): MEASURED green, ESTIMATE amber,
// SIMULATED blue, MOCK rose, REPLAY lavender, pending dashed. Inline colours, so it looks the
// same on every page whatever stylesheets that page loads.
export const LEGEND = [
  ["MEASURED", "#2DD4A7", "Read from a meter or a real run."],
  ["ESTIMATE", "#F5C451", "Measured numbers plus a stated assumption."],
  ["SIMULATED", "#5B9FE8", "From a model or simulator, not measured."],
  ["MOCK", "#FB7185", "Sample data that shows the layout only."],
  ["REPLAY", "#A78BFA", "Replayed from saved, signed records."],
];
const DOT = "display:inline-block;width:9px;height:9px;border-radius:50%;box-sizing:border-box;flex:none";
export const sharedLegend = (extra = "") => `<div class="wn-shared-legend r7legend" role="note" aria-label="Legend: measured, estimate, simulated, mock, replay, pending" style="display:flex;flex-wrap:wrap;gap:14px;align-items:center;font-size:12px">${LEGEND.map(([l, c, t]) => `<span title="${l}: ${t}" style="display:inline-flex;align-items:center;gap:6px"><i style="${DOT};background:${c}"></i>${l}</span>`).join("")}<span title="Pending: not measured yet; shown dimmed with a dot, never hidden." style="display:inline-flex;align-items:center;gap:6px"><i style="${DOT};border:1.5px dashed #9AA0AE;background:transparent"></i>pending</span>${extra}</div>`;
