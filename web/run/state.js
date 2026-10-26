// Run event stream -> graph state. Pure, no DOM, so it is unit-tested with node --test.
// Every number keeps the label it came with (MEASURED / ESTIMATE / ADVICE).

export const NODES = [
  { id: "read", title: "Read telemetry" },
  { id: "baseline", title: "Baseline" },
  { id: "spark", title: "Muse Spark decides" },
  { id: "apply", title: "Apply change" },
  { id: "prompts", title: "Run prompts" },
  { id: "gate", title: "Gate" },
  { id: "keep", title: "Keep or revert" },
  { id: "recs", title: "Recommendations" },
];
export const LOOP = ["spark", "apply", "prompts", "gate", "keep"];

export function initialState() {
  const nodes = {};
  for (const n of NODES) nodes[n.id] = { status: "idle", stats: [], detail: {} };
  return {
    nodes, active: null, tries: [], caps: [], power: [], loop: 0, done: false,
    example: false, meta: { calls: 0, input: 0, output: 0, models: [] },
    baseline: null, final: null, compare: null, recommendations: null, current: null,
  };
}

const stat = (k, v, label) => ({ k, v, label });

function activate(s, id) {
  if (s.active && s.active !== id && s.nodes[s.active].status === "active") {
    s.nodes[s.active].status = "done";
  }
  s.active = id;
  s.nodes[id].status = "active";
}

function tryFor(s, step) {
  let t = s.tries.find((x) => x.step === step);
  if (!t) { t = { step, cap_w: null, gates: [], prompts: [], result: null }; s.tries.push(t); }
  return t;
}

export function reduce(prev, e) {
  const s = structuredClone(prev);
  const d = e.data || {};
  switch (e.type) {
    case "run.started":
      s.example = d.label === "EXAMPLE" || d.mode === "demo";
      s.model = d.model || null;
      activate(s, "read");
      break;
    case "telemetry.sample": {
      const label = d.source === "estimate" ? "ESTIMATE" : "MEASURED";
      s.power.push({ seq: e.seq, w: d.power_w, label });
      if (!s.active) activate(s, "read");
      s.nodes.read.stats = [stat("power", `${d.power_w} W`, label)];
      break;
    }
    case "step.started":
      if (d.kind === "baseline") activate(s, "baseline");
      if (d.kind === "apply") {
        activate(s, "apply");
        tryFor(s, e.step_id).cap_w = d.power_cap_w;
        s.nodes.apply.stats = [stat("cap", `${d.power_cap_w} W`, "MEASURED")];
      }
      if (d.kind === "prompts") {
        activate(s, "prompts");
        tryFor(s, e.step_id).prompts = d.prompts || [];
        s.nodes.prompts.detail = { prompts: d.prompts || [] };
      }
      break;
    case "decision": {
      activate(s, "spark");
      s.loop += 1;
      const t = tryFor(s, e.step_id);
      t.decision = d;
      const tok = d.tokens || { input: 0, output: 0 };
      if (d.model && d.model !== "fallback") {
        s.meta.calls += 1; s.meta.input += tok.input; s.meta.output += tok.output;
        if (!s.meta.models.includes(d.model)) s.meta.models.push(d.model);
      }
      const c = d.choice || {};
      const what = c.kind === "cap" ? `${c.power_cap_w} W` : c.kind === "knob" ? `${c.knob}=${c.value}` : "stop";
      s.nodes.spark.stats = [stat("choice", what, "MEASURED"), stat("model", d.model, null),
        stat("tokens", `${tok.input} in / ${tok.output} out`, "MEASURED")];
      s.nodes.spark.detail = { reason: d.reason, model: d.model, tokens: tok, clamped: !!d.clamped };
      break;
    }
    case "trial.measured": {
      const wh = d.wh_per_req && typeof d.wh_per_req === "object" ? d.wh_per_req.mean : d.wh_per_req;
      const m = { cap_w: d.cap_w, wh, p99_ms: d.p99_ms, label: d.label || "MEASURED" };
      if (e.step_id === "baseline" || d.trial_no === 0) {
        s.baseline = m;
        s.current = m;
        s.nodes.baseline.stats = [stat("Wh/req", wh.toFixed(4), m.label),
          stat("p99", `${Math.round(d.p99_ms)} ms`, m.label), stat("cap", `${d.cap_w} W`, "MEASURED")];
      } else {
        tryFor(s, e.step_id).result = m;
        s.nodes.prompts.stats = [stat("Wh/req", wh.toFixed(4), m.label),
          stat("p99", `${Math.round(d.p99_ms)} ms`, m.label)];
      }
      break;
    }
    case "gate.result": {
      activate(s, "gate");
      const t = tryFor(s, e.step_id);
      t.gates.push({ check: d.check, passed: d.passed, detail: d.detail || {} });
      const ok = t.gates.every((g) => g.passed);
      s.nodes.gate.stats = t.gates.map((g) => stat(g.check, g.passed ? "pass" : "FAIL", "MEASURED"));
      s.nodes.gate.verdict = ok ? "pass" : "fail";
      s.nodes.gate.detail = { gates: t.gates };
      break;
    }
    case "action.applied": {
      activate(s, "keep");
      const cap = d.change && d.change.power_cap_w;
      const t = tryFor(s, e.step_id);
      t.outcome = "kept";
      s.caps.push({ cap_w: cap, passed: true });
      if (t.result) s.current = t.result;
      s.nodes.keep.verdict = "kept";
      s.nodes.keep.stats = [stat("kept", `${cap} W`, "MEASURED")];
      break;
    }
    case "action.rolled_back": {
      activate(s, "keep");
      const t = tryFor(s, e.step_id);
      t.outcome = "reverted";
      s.caps.push({ cap_w: d.change && d.change.power_cap_w, passed: false });
      s.nodes.keep.verdict = "reverted";
      s.nodes.keep.stats = [stat("reverted to", `${d.restored_cap_w} W`, "MEASURED"),
        stat("failed", (d.failed_gates || []).join(", "), null)];
      break;
    }
    case "recommendations":
      activate(s, "recs");
      s.recommendations = d;
      s.final = s.current;
      s.nodes.recs.stats = [stat("verdict", d.verdict, null)];
      break;
    case "pareto.updated":
      if (d.compare) s.compare = d.compare;
      break;
    case "run.finished":
      if (s.active) s.nodes[s.active].status = "done";
      s.active = null;
      s.done = true;
      break;
    default:
      break;
  }
  return s;
}

export function replay(events, upto = events.length) {
  return events.slice(0, upto).reduce(reduce, initialState());
}

// The cap path shown on the loop edge: 700 -> 650 -> 600 -> 550 -> 500 x
export function capPath(s) {
  const start = s.baseline ? s.baseline.cap_w : null;
  const parts = start != null ? [`${start}`] : [];
  for (const c of s.caps) parts.push(c.passed ? `${c.cap_w}` : `${c.cap_w}✕`);
  return parts.join(" → ");
}
