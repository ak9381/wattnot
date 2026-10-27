export const GROUP_COLORS = {
  tier: ["#F2994A", "#5AA9F0", "#4CC38A"],
  kmeans: ["#F2994A", "#5AA9F0", "#4CC38A", "#B79CFF"],
};
export const TOPIC_COLORS = { code: "#5AA9F0", "gpu-ops": "#F2994A", science: "#4CC38A", writing: "#B79CFF",
  math: "#E9C46A", history: "#E07A9A" };
export const topicColor = (t) => TOPIC_COLORS[t] || "#8795A1";
export const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
export const pill = (l) => `<span class="pill ${l}">${l}</span>`;
export const pct = (x) => `${Math.round(x * 100)}%`;
