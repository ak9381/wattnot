// Pure helpers for the v2 dashboard. No DOM here, so node tests can import it.
export const LABELS = ["MEASURED", "ESTIMATE", "SIMULATED", "MOCK", "REPLAY", "ADVICE", "SAMPLE"];

export const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
export const badge = (t) => (t ? `<span class="b b-${esc(String(t).toLowerCase())}">${esc(t)}</span>` : "");

export function num(v, d = 2) {
  if (v == null || Number.isNaN(+v)) return "n/a";
  const a = Math.abs(+v);
  return (+v).toLocaleString("en-US", { maximumFractionDigits: a >= 100 ? 0 : a >= 10 ? 1 : d });
}

// Which pipeline stage is active after `n` events have played.
export function stageState(pipeline, events, n) {
  const st = Object.fromEntries(pipeline.map((p) => [p.id, "todo"]));
  let active = null;
  for (const f of events.slice(0, n)) {
    const e = f.event || f, node = e.data?.node;
    if (!node || !(node in st)) continue;
    if (active && active !== node && st[active] === "run") st[active] = "done";
    if (e.type === "step.finished" && e.data?.status === "failed") st[node] = st[node] === "fail" ? "fail" : "run";
    if (st[node] === "todo") st[node] = "run";
    active = node;
  }
  if (n >= events.length && active) st[active] = "done";
  return { st, active: n >= events.length ? null : active };
}

// Every backend action as one readable row (the "no black box" ledger).
export function ledgerRows(events) {
  const out = [];
  for (const f of events) {
    const e = f.event || f, d = e.data || {};
    if (e.type === "step.finished") out.push({ t: e.ts, node: d.node, kind: "step", ok: d.status !== "failed", text: d.summary, label: d.label, out: d.outputs });
    else if (e.type === "gate.result") out.push({ t: e.ts, node: d.node, kind: "gate", ok: d.passed, text: `${d.check} check ${d.passed ? "passed" : "failed"}${d.detail?.score != null ? ` (${d.detail.score} vs limit ${d.detail.threshold})` : d.detail?.note ? ` (${d.detail.note})` : ""}`, label: "MEASURED" });
    else if (e.type === "decision") out.push({ t: e.ts, node: d.node, kind: "decision", ok: true, text: d.reason || d.kind, label: d.model ? "ADVICE" : null });
    else if (e.type === "action.applied") out.push({ t: e.ts, node: d.node, kind: "apply", ok: true, text: `Applied ${JSON.stringify(d.change)} (was ${JSON.stringify(d.from)})`, label: "REPLAY" });
    else if (e.type === "action.rolled_back") out.push({ t: e.ts, node: d.node, kind: "undo", ok: true, text: "Rolled back", label: "REPLAY" });
  }
  return out;
}

// Headline RUN2 before/after rows (Llama 3.1 8B, 350 W cap vs 700 W stock).
// `r` is the /api/dash/v2/run2 payload (measured joules, p99, quality);
// `price`/`water` are the bundle's {usd_per_kwh} / {factor_l_per_kwh}.
// Energy, p99 and quality are MEASURED; cost and water are ESTIMATES
// derived from measured joules. Shape matches reportRows.
export function run2Rows(r, price, water) {
  if (!r) return [];
  const rows = [];
  if (r.j_per_req_stock != null && r.j_per_req_cap != null) {
    rows.push({
      id: "energy", dim: "Energy per request",
      stock: `${num(r.j_per_req_stock)} J`, wattnot: `${num(r.j_per_req_cap)} J`,
      delta: `${num(r.saved_pct, 1)}%`, tag: "MEASURED",
      win: r.saved_pct < 0 ? "w" : "tie",
      note: `Llama 3.1 8B at a ${r.cap_w} W cap, RUN2 (${r.source})`,
    });
  }
  const kwh = (j) => (j * 1e6) / 3.6e6;
  if (r.j_per_req_stock != null && price?.usd_per_kwh != null) {
    const s = kwh(r.j_per_req_stock) * price.usd_per_kwh, w = kwh(r.j_per_req_cap) * price.usd_per_kwh;
    rows.push({
      id: "cost", dim: "Electricity per 1M requests",
      stock: `$${num(s)}`, wattnot: `$${num(w)}`, delta: `-$${num(s - w)}`,
      tag: "ESTIMATE", win: "w",
      note: `Measured joules x $${price.usd_per_kwh}/kWh (${price.name || "bundle price"})`,
    });
  }
  if (r.j_per_req_stock != null && water?.factor_l_per_kwh != null) {
    const s = kwh(r.j_per_req_stock) * water.factor_l_per_kwh, w = kwh(r.j_per_req_cap) * water.factor_l_per_kwh;
    rows.push({
      id: "water", dim: "Cooling water per 1M requests",
      stock: `${num(s)} L`, wattnot: `${num(w)} L`, delta: `-${num(s - w)} L`,
      tag: "ESTIMATE", win: "w",
      note: `Measured joules x ${water.factor_l_per_kwh} L/kWh (${water.name || "bundle water factor"})`,
    });
  }
  if (r.p99_s_stock != null && r.p99_s_cap != null) {
    rows.push({
      id: "p99", dim: "Slowest 1% (p99)",
      stock: `${num(r.p99_s_stock)} s`, wattnot: `${num(r.p99_s_cap)} s`,
      delta: `+${num(r.p99_change_pct, 1)}%`, tag: "MEASURED",
      win: r.p99_change_pct <= 0 ? "w" : "s",
      note: "The capped run is slower here; the gate decides if that is acceptable",
    });
  }
  if (r.quality_stock_pct != null && r.quality_cap_pct != null) {
    const same = r.quality_stock_pct === r.quality_cap_pct;
    rows.push({
      id: "quality", dim: "Answer quality",
      stock: `${num(r.quality_stock_pct, 1)}%`, wattnot: `${num(r.quality_cap_pct, 1)}%`,
      delta: same ? "unchanged" : `${num(r.quality_cap_pct - r.quality_stock_pct, 1)} pts`,
      tag: "MEASURED", win: "tie",
      note: same ? "Same pass rate capped as stock: quality held" : null,
    });
  }
  return rows;
}

// Rows for the two-path signed report. Each: dim, stock, wattnot, tag, win ("w" | "s" | "tie" | null).
export function reportRows(results, extra = {}) {
  const lv = (results?.levers || [])[0] || {};
  const rows = [];
  const pct = (a, b) => (a && b ? ((b - a) / a) * 100 : null);
  if (lv.wh_before != null) rows.push({ id: "energy", dim: "Energy per request", stock: `${(lv.wh_before * 1000).toFixed(2)} mWh`, wattnot: `${(lv.wh_after * 1000).toFixed(2)} mWh`, delta: `${num(-lv.saved_pct, 1)}%`, tag: "MEASURED", win: lv.saved_pct > 0 ? "w" : "tie" });
  if (lv.p99_before_s != null) rows.push({ id: "p99", dim: "Slowest 1% (p99)", stock: `${num(lv.p99_before_s)} s`, wattnot: `${num(lv.p99_after_s)} s`, delta: `+${num(lv.p99_change_pct, 1)}%`, tag: "MEASURED", win: lv.p99_change_pct <= 0 ? "w" : "s", note: lv.limit_p99_s ? `limit ${num(lv.limit_p99_s)} s` : null });
  const q = lv.quality || {};
  rows.push({ id: "quality", dim: "Answer quality", stock: "reference", wattnot: q.text || "n/a", tag: q.state === "passed" ? "MEASURED" : null, win: q.state === "passed" ? "tie" : null, note: q.why || null });
  if (lv.per_million?.usd != null) rows.push({ id: "cost", dim: "Electricity per 1M requests", stock: `$${num(lv.usd_before * 1e6)}`, wattnot: `$${num(lv.usd_after * 1e6)}`, delta: `-$${num(lv.per_million.usd)}`, tag: "ESTIMATE", win: "w" });
  if (lv.litres_before != null) rows.push({ id: "water", dim: "Cooling water per 1M requests", stock: `${num(lv.litres_before * 1e6)} L`, wattnot: `${num(lv.litres_after * 1e6)} L`, delta: `-${num(lv.per_million?.litres)} L`, tag: "ESTIMATE", win: "w" });
  const room = results?.room;
  if (room?.target && room.before && room.after) {
    const b = room.before.racks.find((r) => r.id === room.target), a = room.after.racks.find((r) => r.id === room.target);
    if (b && a) rows.push({ id: "heat", dim: `Rack ${room.target} heat`, stock: `${num(b.temp_c, 1)} °C`, wattnot: `${num(a.temp_c, 1)} °C`, tag: "SIMULATED", win: "w" });
  }
  rows.push({ id: "carbon", dim: "Carbon", stock: "n/a", wattnot: "n/a", tag: null, win: null, note: "Needs the site's grid intensity (--grid-gco2). Not claimed." });
  rows.push({ id: "risk", dim: "Risk and approvals", stock: "no review", wattnot: extra.approval || "a person approves high-risk changes", tag: "MOCK", win: "w" });
  rows.push({ id: "undo", dim: "Undo", stock: "manual", wattnot: "one click, logged", tag: "MOCK", win: "w" });
  return { rows, lever: lv, pct };
}

export function verifySummary(v) {
  const r = (v?.receipts || []).map((x) => {
    const s = Object.fromEntries(x.steps.map((k) => [k.name, k]));
    return { sample: x.sample, path: x.path, grade: x.grade, hash: s.sha256?.ok, sig: s.signature?.ok, signer: s.signature?.signer || null, sha: s.sha256?.expected || null, rekor: s.rekor?.ok };
  });
  return r;
}
