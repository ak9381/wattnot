export const GROUP_COLORS = {
  tier: ["#F2994A", "#5AA9F0", "#4CC38A"],
  kmeans: ["#F2994A", "#5AA9F0", "#4CC38A", "#B79CFF"],
};
export const TOPIC_COLORS = { code: "#5AA9F0", "gpu-ops": "#F2994A", science: "#4CC38A", writing: "#B79CFF",
  math: "#E9C46A", history: "#E07A9A" };
export const topicColor = (t) => TOPIC_COLORS[t] || "#8795A1";
export const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
export const pill = (l) => `<span class="pill ${l}">${l}</span>`;
// Label colours: the number itself carries the colour, the dot names it on
// hover. One legend per view (legendHtml) explains the colours; per-number
// text badges are gone from the cards.
export const LAB_COLOR = { MEASURED: "var(--cy)", ESTIMATE: "#E9C46A", SIMULATED: "var(--viol)", MOCK: "var(--mu)", REPLAY: "#8FE3B0" };
const OK_LABEL = new Set(["MEASURED", "ESTIMATE", "SIMULATED", "MOCK", "REPLAY", "LIVE", "ADVICE"]);
export const dot = (l) => (l && OK_LABEL.has(l) ? `<span class="pdot ${l}" title="${l}" aria-label="label: ${l}"></span>` : "");
// Class + accessible name for a number drawn from a labelled measurement.
export const numLab = (tag) => (tag && OK_LABEL.has(tag) ? ` lab-${tag}" title="${tag}" aria-label="${tag}` : "");
export const legendHtml = () => `<span class="lab-legend" role="note" aria-label="number colours: measured, estimate, simulated, mock, replay">${["MEASURED", "ESTIMATE", "SIMULATED", "MOCK", "REPLAY"].map((l) => `<span class="li" title="${l}"><i class="pdot ${l}"></i>${l}</span>`).join("")}</span>`;
export const pct = (x) => `${Math.round(x * 100)}%`;
