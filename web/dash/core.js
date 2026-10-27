// Pure state and maths for the expert views. No DOM here, so node can test it.
// A "unit" is one expert in one layer: unit = layer * E + expert.

export const ARMS = ["off", "on"];

export function createState(meta, mode = "tier") {
  const { layers: L, experts: E } = meta.meta;
  const U = L * E;
  const mk = () => ({ ticks: 0, tokens: 0, gen: 0, joules: 0, slots: 0, kept: 0, ent: 0, top1: 0,
    units: new Float64Array(U), unitJ: new Float64Array(U) });
  const st = { meta, L, E, U, mode, arm: { off: mk(), on: mk() }, steps: new Map(), recent: new Float64Array(U),
    stepOrder: meta.meta.steps.map((s) => s.id), lastTick: null };
  setMode(st, mode);
  return st;
}

export function setMode(st, mode) {
  st.mode = mode;
  const m = st.meta.modes[mode];
  st.assign = m.assign; st.names = m.names; st.k = m.names.length; st.report = m.report;
}

// Energy per routed expert-slot. Window energy is split by each unit's share of routed tokens
// (equal FLOPs per routed token in one model), so shares add up to the window total exactly.
// With an energy meter in the tick (energy_j) that is used instead of the priced rate.
export function jPerSlot(st, arm) {
  const e = st.meta.energy, t = st.meta.totals[arm];
  if (!e || e[arm] == null || !t || !t.routed_slots) return null;
  return (e[arm] * t.generated) / t.routed_slots;
}

export function stepRec(st, step, arm) {
  let s = st.steps.get(step);
  if (!s) { s = {}; st.steps.set(step, s); }
  if (!s[arm]) s[arm] = { tokens: 0, gen: 0, joules: 0, units: new Float64Array(st.U), tok: new Map(), ticks: 0 };
  return s[arm];
}

export function applyTick(st, t) {
  const a = st.arm[t.arm];
  const sr = stepRec(st, t.step, t.arm);
  let slots = 0;
  for (const [, n] of t.r) slots += n;
  let per = null;
  if (t.energy_j != null && slots) per = t.energy_j / slots;
  else { const j = jPerSlot(st, t.arm); if (j != null) per = j; }
  const joules = per == null ? null : per * slots;
  a.ticks++; a.tokens += t.tokens; a.slots += t.slots; a.kept += t.kept_slots;
  a.ent += t.ent * t.slots; a.top1 += t.top1 * t.slots;
  sr.ticks++; sr.tokens += t.tokens;
  if (t.phase === "decode") { a.gen++; sr.gen++; }
  for (const [u, n] of t.r) {
    a.units[u] += n; sr.units[u] += n;
    if (per != null) a.unitJ[u] += n * per;
  }
  if (joules != null) { a.joules += joules; sr.joules += joules; }
  for (const [, tok, n] of t.tt) sr.tok.set(tok, (sr.tok.get(tok) || 0) + n);
  st.lastTick = t;
  return { joules, slots };
}

export function bump(st, t, gain = 1) {
  for (const [u, n] of t.r) st.recent[u] = Math.min(1.5, st.recent[u] + gain * Math.min(1, 0.45 + n / 8));
}
export function decay(st, dtSeconds, halfLife = 0.7) {
  const f = Math.pow(0.5, dtSeconds / halfLife);
  for (let i = 0; i < st.recent.length; i++) st.recent[i] *= f;
}

export function groupTotals(st, arm, stepIds = null) {
  const out = Array.from({ length: st.k }, () => ({ routed: 0, joules: 0, units: 0 }));
  const unitsArr = stepIds ? sumSteps(st, stepIds, arm) : st.arm[arm].units;
  const per = jPerSlot(st, arm);
  for (let u = 0; u < st.U; u++) {
    const g = out[st.assign[u]];
    g.units++;
    g.routed += unitsArr[u];
    g.joules += stepIds ? (per == null ? 0 : unitsArr[u] * per) : st.arm[arm].unitJ[u];
  }
  const tr = out.reduce((s, g) => s + g.routed, 0) || 1;
  const tj = out.reduce((s, g) => s + g.joules, 0) || 1;
  for (const g of out) { g.share = g.routed / tr; g.jshare = g.joules / tj; }
  return out;
}

export function sumSteps(st, stepIds, arm) {
  const out = new Float64Array(st.U);
  for (const id of stepIds) {
    const r = st.steps.get(id)?.[arm];
    if (r) for (let u = 0; u < st.U; u++) out[u] += r.units[u];
  }
  return out;
}

// Baseline from the whole capture: routed slots per generated-and-prompt token, per group, for
// each arm. Edge widths use this so flipping adaptive top-k visibly thins the edges.
export function staticPerToken(st, arm) {
  const c = st.meta.counts[arm], tok = st.meta.totals[arm].tokens || 1;
  const g = new Array(st.k).fill(0);
  for (let u = 0; u < st.U; u++) g[st.assign[u]] += c[u];
  return g.map((x) => x / tok);
}

// How much each seat is used by the adaptive arm compared with the normal arm (1 = same).
export function skipRatio(st) {
  const off = st.meta.counts.off, on = st.meta.counts.on;
  const to = st.meta.totals.off.tokens || 1, tn = st.meta.totals.on.tokens || 1;
  const out = new Float64Array(st.U);
  for (let u = 0; u < st.U; u++) {
    const a = off[u] / to, b = on[u] / tn;
    out[u] = a > 0 ? Math.min(1, b / a) : 1;
  }
  return out;
}

export function topTokens(st, tokMap, n = 5) {
  const skip = /^<\|.*\|>$/;
  return [...tokMap.entries()].map(([id, c]) => [st.meta.vocab[String(id)] ?? `#${id}`, c])
    .filter(([s]) => s.trim() && !skip.test(s)).sort((a, b) => b[1] - a[1]).slice(0, n)
    .map(([s, c]) => [s.replace(/\n/g, "↵").trim(), c]);
}

export function stepSummary(st, stepIds, arm) {
  const units = sumSteps(st, stepIds, arm);
  let n = 0, slots = 0;
  for (let u = 0; u < st.U; u++) if (units[u] > 0) { n++; slots += units[u]; }
  // how many seats carried 80% of this step's routed tokens (the rest are a long thin tail)
  const sorted = Array.from(units).filter((v) => v > 0).sort((a, b) => b - a);
  let acc = 0, core = 0;
  for (const v of sorted) { if (acc >= 0.8 * slots) break; acc += v; core++; }
  const per = jPerSlot(st, arm);
  const tok = new Map(); let tokens = 0, gen = 0, joules = 0;
  const topics = {};
  for (const id of stepIds) {
    const r = st.steps.get(id)?.[arm]; if (!r) continue;
    tokens += r.tokens; gen += r.gen; joules += r.joules;
    for (const [k, v] of r.tok) tok.set(k, (tok.get(k) || 0) + v);
    const tp = st.meta.meta.steps[id]?.topic; if (tp) topics[tp] = (topics[tp] || 0) + r.tokens;
  }
  const groups = groupTotals(st, arm, stepIds);
  const topic = Object.entries(topics).sort((a, b) => b[1] - a[1])[0]?.[0] ?? null;
  return { units, seats: n, core, slots, tokens, gen, joules, per, groups, topic, topics, top: topTokens(st, tok) };
}

const fmtJ = (j) => (j >= 100 ? j.toFixed(0) : j >= 10 ? j.toFixed(1) : j.toFixed(2));
export { fmtJ };

export function captionForSteps(st, stepIds, arm) {
  if (!stepIds.length) return "";
  const s = stepSummary(st, stepIds, arm);
  if (!s.tokens) return `Step ${stepIds.map((i) => i + 1).join(", ")} has not run yet.`;
  const lead = s.groups.map((g, i) => [g, i]).sort((a, b) => b[0].share - a[0].share)[0];
  const many = stepIds.length > 1;
  const what = many ? `These ${stepIds.length} steps` : `Step ${stepIds[0] + 1}`;
  const topic = s.topic ? `mostly ${s.topic} tokens` : "";
  const words = s.top.length ? ` (${s.top.slice(0, 3).map(([w]) => `“${w}”`).join(", ")})` : "";
  const j = s.per == null ? "" : `, about ${fmtJ(s.joules)} J (ESTIMATE)`;
  return `${what} leaned on ${s.core} of ${st.U} expert seats (80% of its routed tokens), ${topic}${words}; ${Math.round(lead[0].share * 100)}% went to ${st.names[lead[1]]}${j}.`;
}

export function captionForGroup(st, g, arm) {
  const t = groupTotals(st, arm)[g];
  const rep = st.report[g];
  const per = jPerSlot(st, arm);
  const j = per == null ? "" : ` and about ${fmtJ(t.joules)} J (ESTIMATE)`;
  const steps = stepsUsingGroup(st, g, arm).filter((x) => x.lift > 1.15).slice(0, 4).map((x) => x.step + 1);
  const when = steps.length ? ` It worked hardest on step${steps.length > 1 ? "s" : ""} ${steps.join(", ")}.` : "";
  return `${st.names[g]}: ${rep.units} seats, ${Math.round(t.share * 100)}% of routed tokens${j}.${when}`;
}

// For each step, the share of its routed tokens that went to group g, and how that compares
// with the group's overall share (lift above 1 means this step leaned on the group).
export function stepsUsingGroup(st, g, arm) {
  const overall = groupTotals(st, arm)[g].share || 1e-9;
  const out = [];
  for (const id of st.stepOrder) {
    const r = st.steps.get(id)?.[arm]; if (!r) continue;
    let tot = 0, mine = 0;
    for (let u = 0; u < st.U; u++) { tot += r.units[u]; if (st.assign[u] === g) mine += r.units[u]; }
    if (tot) out.push({ step: id, share: mine / tot, lift: mine / tot / overall });
  }
  return out.sort((a, b) => b.lift - a.lift);
}

export function reset(st) {
  for (const arm of ARMS) {
    const a = st.arm[arm];
    for (const k of ["ticks", "tokens", "gen", "joules", "slots", "kept", "ent", "top1"]) a[k] = 0;
    a.units.fill(0); a.unitJ.fill(0);
  }
  st.steps.clear(); st.recent.fill(0); st.lastTick = null;
}

// Rebuild everything from a list of ticks (used when a grouping change needs fresh totals).
export function replayAll(st, ticks) {
  reset(st);
  for (const t of ticks) applyTick(st, t);
}

export function activePerToken(st, arm) {
  const a = st.arm[arm];
  return a.slots ? a.kept / a.slots : st.meta.totals[arm].active_per_token;
}
