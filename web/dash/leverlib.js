// Pure helpers for the server-lever panel (tested in leverlib.test.mjs).
export const LABELS = { energy: "MEASURED", usd: "ESTIMATE", water: "ESTIMATE" };
export function armRows(arms) {
  const a = arms || {}, w = a.without || {}, v = a.with || {};
  const pct = (x, y) => (x > 0 && y > 0 ? `${(((y - x) / x) * 100).toFixed(1)}%` : "–");
  return [
    ["", "Stock (without)", "Lever (with)", "Change"],
    [`Energy, Wh (${LABELS.energy})`, (w.wh ?? 0).toFixed(3), (v.wh ?? 0).toFixed(3), pct(w.wh, v.wh)],
    [`Cost, $ (${LABELS.usd})`, (w.usd ?? 0).toFixed(5), (v.usd ?? 0).toFixed(5), pct(w.usd, v.usd)],
    [`Water, L (${LABELS.water}, 0.36 L/kWh)`, (w.water_l ?? 0).toFixed(5), (v.water_l ?? 0).toFixed(5), pct(w.water_l, v.water_l)],
  ];
}
const PHASE = { starting: "starting", without: "stock arm on the GPU", restart: "restarting vLLM", with: "lever arm on the GPU",
  restore: "putting stock BF16 back", done: "finished", idle: "idle" };
export function stateText(s) {
  if (!s) return "";
  if (s.running) return `Running ${s.lever}: ${PHASE[s.phase] || s.phase}.`;
  if (s.phase !== "done") return "Idle. vLLM " + (s.server?.models_ok ? "is serving" : "is NOT answering") +
    (s.server?.stock_problems?.length ? ` (not stock: ${s.server.stock_problems.join("; ")})` : " stock BF16.");
  const r = s.result, rest = s.restore || {};
  const back = rest.verified ? "Stock BF16 restored and verified." : `Restore NOT verified: ${(rest.problems || []).join("; ")}`;
  if (s.error) return `${s.lever} stopped: ${s.error}. ${back}`;
  if (!r) return back;
  const c = r.check || {};
  return (r.kept ? `${r.lever} passed the answer check: energy per answer ${c.energy_change_pct}%, p99 ${c.p99_change_pct}%, answers ${c.answers_same} (MEASURED).`
    : `${r.lever} not kept: ${r.why}.`) + " " + back;
}
