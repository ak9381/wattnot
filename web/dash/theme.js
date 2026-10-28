export const GROUP_COLORS = {
  tier: ["#8E7A9E", "#8E7A9E", "#7FA88A"],
  kmeans: ["#8E7A9E", "#8E7A9E", "#7FA88A", "#B49BDB"],
};
export const TOPIC_COLORS = { code: "#8E7A9E", "gpu-ops": "#8E7A9E", science: "#7FA88A", writing: "#B49BDB",
  math: "#C4B4D6", history: "#D094A0" };
export const topicColor = (t) => TOPIC_COLORS[t] || "#A79BB9";
export const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
export const pill = (l) => `<span class="pill ${l}">${l}</span>`;
// Label colours: the number itself carries the colour, the dot names it on
// hover. One legend per view (legendHtml) explains the colours; per-number
// text badges are gone from the cards.
export const LAB_COLOR = { MEASURED: "var(--cy)", ESTIMATE: "#C4B4D6", SIMULATED: "var(--viol)", MOCK: "var(--mu)", REPLAY: "#8FBBA0" };
const OK_LABEL = new Set(["MEASURED", "ESTIMATE", "SIMULATED", "MOCK", "REPLAY", "LIVE", "ADVICE"]);
export const dot = (l) => (l && OK_LABEL.has(l) ? `<span class="pdot ${l}" title="${l}" aria-label="label: ${l}"></span>` : "");
// Class + accessible name for a number drawn from a labelled measurement.
export const numLab = (tag) => (tag && OK_LABEL.has(tag) ? ` lab-${tag}" title="${tag}" aria-label="${tag}` : "");
export const legendHtml = () => `<span class="lab-legend" role="note" aria-label="number colours: measured, estimate, simulated, mock, replay">${["MEASURED", "ESTIMATE", "SIMULATED", "MOCK", "REPLAY"].map((l) => `<span class="li" title="${l}"><i class="pdot ${l}"></i>${l}</span>`).join("")}</span>`;
export const pct = (x) => `${Math.round(x * 100)}%`;
