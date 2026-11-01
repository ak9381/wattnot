// Live arm: a live run.started event names its arm. The without-WattNot arm greys every WattNot
// node (only its baseline_nodes stay lit); the with-WattNot arm lights the whole map.
// Returns [phase, baselineIds] or null when the event says nothing about an arm.
export function armPhase(ev) {
  const d = (ev && ev.data) || {};
  if (!ev || ev.type !== "run.started" || !d.arm) return null;
  if (d.arm === "without_wattnot") return [1, Array.isArray(d.baseline_nodes) ? d.baseline_nodes : null];
  return [2, null];
}

// Stage feature table: a stage.features event (written before the run starts) names the map nodes whose
// features are OFF on this platform, with a reason each. Returns {nodeId: reason} or null.
export function stageOff(ev) {
  const d = (ev && ev.data) || {};
  if (!ev || ev.type !== "stage.features" || !d.off_nodes || typeof d.off_nodes !== "object") return null;
  return d.off_nodes;
}

// What a step did, from its own event data only (nothing invented). Returns [[name, value, label|null], ...]
// for the inline block that opens inside the active node and folds away when the next step lights.
const FACTS = [
  ["effort", "effort"], ["step_up", "step-up"], ["tokens", "tokens"], ["completion_tokens", "tokens out"],
  ["cached_tokens", "cached tokens"], ["latency_s", "latency s"], ["passed", "gate"], ["score", "score"],
  ["grade", "Spark grade"], ["verdict", "verdict"], ["decision", "decision"], ["result", "result"],
  ["pr_url", "PR"], ["wh", "Wh"], ["usd", "cost $"], ["model", "model"],
];
export function stepFacts(ev) {
  const d = (ev && ev.data) || {}, out = [];
  for (const [k, name] of FACTS) {
    if (d[k] == null || d[k] === "") continue;
    let v = d[k];
    if (k === "passed") v = v ? "pass" : "fail";
    if (k === "step_up" && typeof v === "object") v = `${v.from}→${v.to}`;
    const lab = k === "usd" ? d.usd_label || d.label : k === "wh" ? d.wh_label || "ESTIMATE" : (k === "model" ? null : d.label || null);
    out.push([name, String(v), lab]);
  }
  return out;
}
