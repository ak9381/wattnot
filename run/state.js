// Run event stream -> canvas state. Pure (no DOM), unit-tested with node --test.
// Numbers keep the label they arrived with: MEASURED, ESTIMATE or ADVICE.
// A fake-backend run (host.provider "fake") never shows MEASURED: it becomes SIMULATED.

// Graph model: groups hold nodes; edges join nodes. Positions are world px.
export const GROUPS = [
  { id: "telemetry", title: "TELEMETRY", sub: "NVML · 10 Hz" },
  { id: "baseline", title: "BASELINE", sub: "uncapped reference" },
  { id: "decide", title: "DECIDE · Muse Spark", pill: "META · Muse API", meta: true },
  { id: "apply", title: "APPLY", sub: "one change per try" },
  { id: "workload", title: "WORKLOAD", sub: "same prompts every try" },
  { id: "gate", title: "QUALITY GATE", sub: "offline replay" },
  { id: "keep", title: "KEEP / REVERT" },
  { id: "receipt", title: "RECEIPT", sub: "per run" },
];

export const NODES = [
  { id: "gpu", group: "telemetry", title: "GPU", x: 24, y: 64, icon: "cpu" },
  { id: "power", group: "telemetry", title: "Live power", x: 24, y: 176, icon: "activity" },
  { id: "base", group: "baseline", title: "Baseline run", x: 432, y: 64, icon: "gauge", h: 120 },
  { id: "choice", group: "decide", title: "Next action", x: 840, y: 64, icon: "sparkles", meta: true },
  { id: "reason", group: "decide", title: "Reason", x: 840, y: 176, icon: "file-text", meta: true },
  { id: "cap", group: "apply", title: "Power cap", x: 1248, y: 64, icon: "zap" },
  { id: "knob", group: "apply", title: "vLLM knob", x: 1248, y: 176, icon: "sliders-horizontal" },
  { id: "prompts", group: "workload", title: "Prompt replay", x: 1248, y: 424, icon: "list-checks", h: 104 },
  { id: "quality", group: "gate", title: "Quality Δ ≤ 1 pt", x: 840, y: 424, icon: "shield-check" },
  { id: "latency", group: "gate", title: "p99 ≤ +10%", x: 840, y: 536, icon: "gauge" },
  { id: "verdict", group: "gate", title: "Verdict", x: 840, y: 648, icon: "check" },
  { id: "kept", group: "keep", title: "Keep cap", x: 432, y: 424, icon: "check" },
  { id: "revert", group: "keep", title: "AUTO-REVERT", x: 432, y: 648, icon: "undo-2", danger: true },
  { id: "receipt", group: "receipt", title: "Receipt", x: 24, y: 424, icon: "receipt", h: 136 },
  { id: "recs", group: "receipt", title: "Recommendations", x: 24, y: 608, icon: "clipboard-check" },
];
export const NODE_W = 248, NODE_H = 80;

// kind: data (WattNot) | meta (Meta model) | revert | optional (dashed)
export const EDGES = [
  { id: "e-gpu-power", from: "gpu", to: "power", fs: "b", ts: "t", label: "", kind: "data" },
  { id: "e-power-base", from: "power", to: "base", fs: "r", ts: "l", label: "W samples", kind: "data" },
  { id: "e-base-choice", from: "base", to: "choice", fs: "r", ts: "l", label: "numbers only", kind: "meta", tdy: -12 },
  { id: "e-choice-cap", from: "choice", to: "cap", fs: "r", ts: "l", label: "clamped cap", kind: "data", tdy: -12 },
  { id: "e-reason-knob", from: "reason", to: "knob", fs: "r", ts: "l", label: "knob, if any", kind: "optional" },
  { id: "e-cap-prompts", from: "cap", to: "prompts", fs: "r", ts: "r", label: "run workload", kind: "data", via: { x: 1592 }, labelV: true },
  { id: "e-prompts-quality", from: "prompts", to: "quality", fs: "l", ts: "r", label: "answers", kind: "data" },
  { id: "e-quality-latency", from: "quality", to: "latency", fs: "b", ts: "t", label: "", kind: "data" },
  { id: "e-latency-verdict", from: "latency", to: "verdict", fs: "b", ts: "t", label: "", kind: "data" },
  { id: "e-verdict-kept", from: "verdict", to: "kept", fs: "l", ts: "r", label: "pass", kind: "data", labelV: true },
  { id: "e-verdict-revert", from: "verdict", to: "revert", fs: "l", ts: "r", label: "fail", kind: "revert" },
  { id: "e-loop", from: "kept", to: "choice", fs: "t", ts: "l", label: "next try", kind: "data", via: { y: 332, x2: 760 }, loop: true, tdy: 12 },
  { id: "e-revert-cap", from: "revert", to: "cap", fs: "b", ts: "l", label: "restore last passing cap", kind: "revert", via: { y: 792, x2: 1168 }, tdy: 12 },
  { id: "e-kept-receipt", from: "kept", to: "receipt", fs: "l", ts: "r", label: "final cap", kind: "data" },
  { id: "e-receipt-recs", from: "receipt", to: "recs", fs: "b", ts: "t", label: "", kind: "data" },
];

// Which node each event lights, and which edge carries the data into it.
const INTO = {
  gpu: [], power: ["e-gpu-power"], base: ["e-power-base"], choice: ["e-base-choice", "e-loop"],
  cap: ["e-choice-cap"], prompts: ["e-cap-prompts"], quality: ["e-prompts-quality"],
  latency: ["e-quality-latency"], verdict: ["e-latency-verdict"],
  kept: ["e-verdict-kept"], revert: ["e-verdict-revert"], receipt: ["e-kept-receipt"],
  recs: ["e-receipt-recs"],
};

export function initialState() {
  const nodes = {};
  for (const n of NODES) nodes[n.id] = { status: "idle", sub: "", pills: [] };
  return {
    nodes, active: null, hot: [], tries: [], caps: [], power: [], loop: 0, done: false,
    example: false, simulated: false, runId: null, gpu: null, range: null, seq: -1, progress: 0,
    meta: { calls: 0, input: 0, output: 0, models: [] },
    baseline: null, final: null, current: null, compare: null, recommendations: null,
    receipt: null, lastEvent: null,
  };
}

function light(s, id) {
  for (const n of Object.values(s.nodes)) if (n.status === "active") n.status = "done";
  s.active = id;
  s.nodes[id].status = "active";
  s.hot = INTO[id] ? [...INTO[id]] : [];
  if (id === "choice" && s.loop <= 1) s.hot = ["e-base-choice"];
  if (id === "choice" && s.loop > 1) s.hot = ["e-loop"];
}

/** True when a run.started payload comes from the fake backend (no GPU was read). */
export function isSimulated(d) { return (d?.host?.provider || "") === "fake" || d?.label === "SIMULATED"; }

/** Honest label for this run: MEASURED and ESTIMATE both become SIMULATED on a fake backend; ADVICE stays. */
export function honest(s, l) { return s.simulated && (l === "MEASURED" || l === "ESTIMATE") ? "SIMULATED" : l; }

function set(s, id, sub, pills = []) { s.nodes[id].sub = sub; s.nodes[id].pills = pills.map((l) => honest(s, l)); }

function tryFor(s, step) {
  let t = s.tries.find((x) => x.step === step);
  if (!t) { t = { step, cap_w: null, gates: [], prompts: [], result: null, decision: null, outcome: null }; s.tries.push(t); }
  return t;
}

const fmtWh = (x) => Number(x).toFixed(4);
const whOf = (d) => (d.wh_per_req && typeof d.wh_per_req === "object" ? d.wh_per_req.mean : d.wh_per_req);

export function reduce(prev, e) {
  const s = structuredClone(prev);
  const d = e.data || {};
  s.seq = e.seq;
  s.lastEvent = { seq: e.seq, type: e.type, step: e.step_id || null };
  switch (e.type) {
    case "run.started": {
      s.example = d.label === "EXAMPLE" || d.mode === "demo";
      s.simulated = isSimulated(d);
      s.runId = e.run_id;
      s.model = d.model || null;
      const h = d.host || {};
      s.gpu = { index: h.gpu_index ?? 0, name: h.gpu || "GPU" };
      s.range = h.cap_range_w || null;
      set(s, "gpu", `gpu ${s.gpu.index} · ${s.gpu.name}${s.range ? ` · ${s.range[0]}–${s.range[1]} W` : ""}`);
      light(s, "gpu");
      break;
    }
    case "telemetry.sample": {
      const label = d.source === "estimate" ? "ESTIMATE" : "MEASURED";
      s.power.push({ seq: e.seq, w: d.power_w, label });
      set(s, "power", `${d.power_w} W`, [label]);
      break;
    }
    case "step.started":
      if (d.kind === "baseline") { light(s, "base"); s.progress = 0; set(s, "base", "measuring at the default cap"); }
      if (d.kind === "apply") {
        light(s, "cap");
        const t = tryFor(s, e.step_id);
        t.cap_w = d.power_cap_w;
        const from = s.current ? s.current.cap_w : null;
        set(s, "cap", `${from ?? "?"} → ${d.power_cap_w} W`, ["MEASURED"]);
      }
      if (d.kind === "prompts") {
        light(s, "prompts");
        s.progress = 0;
        tryFor(s, e.step_id).prompts = d.prompts || [];
        s.promptSet = d.prompt_set || "prompts";
        set(s, "prompts", `${s.promptSet} · ${(d.prompts || []).length} prompts`);
      }
      break;
    case "step.progress":
      s.progress = d.pct;
      break;
    case "decision": {
      s.loop += 1;
      light(s, "choice");
      const t = tryFor(s, e.step_id);
      t.decision = d;
      const tok = d.tokens || { input: 0, output: 0 };
      const real = d.model && d.model !== "fallback";
      if (real) {
        s.meta.calls += 1; s.meta.input += tok.input; s.meta.output += tok.output;
        if (!s.meta.models.includes(d.model)) s.meta.models.push(d.model);
      }
      const c = d.choice || {};
      const what = c.kind === "cap" ? `cap ${c.power_cap_w} W` : c.kind === "knob" ? `${c.knob}=${c.value}` : "stop";
      set(s, "choice", `${what} · ${real ? d.model : "fallback rules"} · ${tok.input}/${tok.output} tok${d.clamped ? " · clamped" : ""}`);
      set(s, "reason", d.reason || "");
      if (c.kind === "knob") set(s, "knob", `${c.knob} = ${c.value}`, ["MEASURED"]);
      else if (!s.nodes.knob.sub) set(s, "knob", "unchanged this try");
      break;
    }
    case "trial.measured": {
      const wh = whOf(d);
      const m = { cap_w: d.cap_w, wh, p99_ms: d.p99_ms, pass_rate: d.pass_rate ?? null, label: honest(s, d.label || "MEASURED") };
      if (e.step_id === "baseline" || d.trial_no === 0) {
        s.baseline = m;
        s.current = m;
        const pr = m.pass_rate != null ? ` · pass ${Math.round(m.pass_rate * 100)}%` : "";
        set(s, "base", `${fmtWh(wh)} Wh/req · p99 ${Math.round(d.p99_ms)} ms${pr} · ${d.cap_w} W`, [m.label]);
      } else {
        tryFor(s, e.step_id).result = m;
        set(s, "prompts", `${s.promptSet || "prompts"} · ${fmtWh(wh)} Wh/req · p99 ${Math.round(d.p99_ms)} ms`, [m.label]);
      }
      break;
    }
    case "gate.result": {
      const t = tryFor(s, e.step_id);
      const g = { check: d.check, passed: d.passed, detail: d.detail || {} };
      t.gates.push(g);
      const dt = g.detail;
      if (d.check === "fixed" || d.check === "judge") {
        light(s, "quality");
        set(s, "quality", `−${dt.score} pt vs ≤ ${dt.threshold} · ${d.passed ? "pass" : "FAIL"}`, ["MEASURED"]);
      } else if (d.check === "p99") {
        light(s, "latency");
        set(s, "latency", `${dt.score} / ${dt.threshold} ms · ${d.passed ? "pass" : "FAIL"}`, ["MEASURED"]);
      }
      t.verdict = t.gates.every((x) => x.passed) ? "pass" : "fail";
      break;
    }
    case "action.applied": {
      const t = tryFor(s, e.step_id);
      light(s, "verdict");
      set(s, "verdict", `PASS · ${t.cap_w ?? ""} W`);
      s.nodes.verdict.verdict = "pass";
      light(s, "kept");
      s.hot = ["e-verdict-kept"];
      const cap = d.change && d.change.power_cap_w;
      t.outcome = "kept";
      s.caps.push({ cap_w: cap, passed: true });
      if (t.result) s.current = t.result;
      set(s, "kept", `kept ${cap} W · try ${s.loop}`, ["MEASURED"]);
      break;
    }
    case "action.rolled_back": {
      const t = tryFor(s, e.step_id);
      t.outcome = "reverted";
      const cap = d.change && d.change.power_cap_w;
      s.caps.push({ cap_w: cap, passed: false });
      set(s, "verdict", `FAIL · ${(d.failed_gates || []).join(", ")}`);
      s.nodes.verdict.verdict = "fail";
      light(s, "revert");
      s.hot = ["e-verdict-revert", "e-revert-cap"];
      s.reverted = { from: cap, to: d.restored_cap_w, failed: d.failed_gates || [] };
      set(s, "revert", `${cap} W → back to ${d.restored_cap_w} W`, ["MEASURED"]);
      break;
    }
    case "recommendations":
      s.recommendations = d;
      s.final = s.current;
      break;
    case "pareto.updated":
      if (d.compare) s.compare = d.compare;
      break;
    case "receipt.signed": {
      light(s, "receipt");
      s.receipt = d;
      const b = s.baseline, f = s.final || s.current;
      const usd = (s.recommendations?.actions || []).find((a) => a.key === "usd_saved");
      const wl = (s.recommendations?.actions || []).find((a) => a.key === "water_saved_l");
      s.receiptView = {
        wh: b && f ? b.wh - f.wh : null, usd: usd ? usd.value : null, water: wl ? wl.value : null,
        signed: d.signed !== false, id: d.receipt_id,
      };
      set(s, "receipt", `${d.receipt_id} · ${d.signed === false ? "hashed, not signed (demo)" : "signed"}`, ["ESTIMATE"]);
      break;
    }
    case "run.finished":
      if (s.recommendations) {
        light(s, "recs");
        set(s, "recs", `${s.recommendations.verdict} · ${s.recommendations.actions.length} actions`);
      }
      for (const n of Object.values(s.nodes)) if (n.status === "active") n.status = "done";
      s.active = null;
      s.hot = [];
      s.done = true;
      break;
    default:
      break;
  }
  // the loop edge always reports the try count and the cap under test
  const last = s.tries[s.tries.length - 1];
  s.loopLabel = s.loop ? `try ${s.loop} · ${last && last.cap_w ? last.cap_w + " W" : ""}`.trim() : "next try";
  return s;
}

export function replay(events, upto = events.length) {
  return events.slice(0, upto).reduce(reduce, initialState());
}

export function capPath(s) {
  const start = s.baseline ? s.baseline.cap_w : null;
  const parts = start != null ? [`${start}`] : [];
  for (const c of s.caps) parts.push(c.passed ? `${c.cap_w}` : `${c.cap_w}✕`);
  return parts.join(" → ");
}

// Orthogonal edge route between two node ports. Returns SVG path and label point.
export function port(n, side) {
  const w = n.w || NODE_W, h = n.h || NODE_H;
  return { l: [n.x, n.y + h / 2], r: [n.x + w, n.y + h / 2], t: [n.x + w / 2, n.y], b: [n.x + w / 2, n.y + h] }[side];
}

export function route(edge, byId) {
  const a = port(byId[edge.from], edge.fs), b = port(byId[edge.to], edge.ts);
  if (edge.tdy) b[1] += edge.tdy;
  let pts;
  const v = edge.via || {};
  if (v.y != null && v.x2 != null) pts = [a, [a[0], v.y], [v.x2, v.y], [v.x2, b[1]], b];
  else if (v.y != null) pts = [a, [a[0], v.y], [b[0], v.y], b];
  else if (v.x != null) pts = [a, [v.x, a[1]], [v.x, b[1]], b];
  else if ((edge.fs === "r" || edge.fs === "l") && (edge.ts === "l" || edge.ts === "r")) {
    const mx = (a[0] + b[0]) / 2;
    pts = Math.abs(a[1] - b[1]) < 1 ? [a, b] : [a, [mx, a[1]], [mx, b[1]], b];
  } else {
    const my = (a[1] + b[1]) / 2;
    pts = Math.abs(a[0] - b[0]) < 1 ? [a, b] : [a, [a[0], my], [b[0], my], b];
  }
  // label on the longest segment; horizontal runs win so text never sits on a vertical line
  let best = 0, lp = a;
  for (let i = 1; i < pts.length; i++) {
    const flat = pts[i][1] === pts[i - 1][1];
    const len = Math.hypot(pts[i][0] - pts[i - 1][0], pts[i][1] - pts[i - 1][1]) * (flat && !edge.labelV ? 4 : 1);
    if (len > best) { best = len; lp = [(pts[i][0] + pts[i - 1][0]) / 2, (pts[i][1] + pts[i - 1][1]) / 2]; }
  }
  return { d: "M" + pts.map((p) => p.join(" ")).join(" L"), label: lp, pts };
}

// Group bounds follow their nodes (so dragging a node grows its group).
export function groupBounds(groupId, nodes, pad = 24, head = 40) {
  const ns = nodes.filter((n) => n.group === groupId);
  const x0 = Math.min(...ns.map((n) => n.x)) - pad, y0 = Math.min(...ns.map((n) => n.y)) - pad - head;
  const x1 = Math.max(...ns.map((n) => n.x + (n.w || NODE_W))) + pad;
  const y1 = Math.max(...ns.map((n) => n.y + (n.h || NODE_H))) + pad;
  return { x: x0, y: y0, w: x1 - x0, h: y1 - y0 };
}
