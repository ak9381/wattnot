// Live savings: running counters and a per-step attribution waterfall, built only from events.
const isSimulated = (d) => (d?.host?.provider || "") === "fake" || d?.label === "SIMULATED";
// Labels: power, energy and tokens/Wh are MEASURED; cost and water are ESTIMATE (impact maths).

// Same defaults as src/wattnot/impact.py and recommend.py.
export const ASSUME = { reqPerGpuDay: 100_000, gpus: 8, days: 30, pue: 1.0, tariff: 0.12, wue: 1.8 };
export const P99_BUDGET = 0.10;

const whOf = (d) => (d.wh_per_req && typeof d.wh_per_req === "object" ? d.wh_per_req.mean : d.wh_per_req);

/** kWh per month for a Wh/request figure across the assumed fleet. */
export function monthlyKwh(wh, a = ASSUME) { return (wh * a.reqPerGpuDay * a.gpus * a.days * a.pue) / 1000; }
export const usd = (wh, a = ASSUME) => monthlyKwh(wh, a) * a.tariff;
export const litres = (wh, a = ASSUME) => monthlyKwh(wh, a) * a.wue;

/** Convert a bar into the chosen unit: wh | usd | water | w. Positive = saved. */
export function inUnit(bar, unit, a = ASSUME) {
  const k = { wh: 1, usd: null, water: null, w: null }[unit];
  const pick = (wh, w) => (unit === "wh" ? wh * k : unit === "usd" ? usd(wh, a) : unit === "water" ? litres(wh, a) : w);
  return { tried: pick(bar.triedWh, bar.triedW), net: pick(bar.netWh, bar.netW) };
}

/**
 * Walk the events and attribute savings to each try.
 * A try's saving is measured against the last kept state, so kept tries add up to the total
 * and a reverted try shows its saving, then gives it all back (net 0).
 */
export function attribute(events) {
  let base = null, kept = null, curCap = null;
  const tries = new Map(), order = [], hist = { power: [], wh: [], usd: [], water: [] };
  const get = (step) => { if (!tries.has(step)) { tries.set(step, { step }); order.push(step); } return tries.get(step); };
  const snap = () => {
    if (!base || !kept) return;
    hist.power.push(curCap); hist.wh.push(kept.wh);
    hist.usd.push(usd(kept.wh)); hist.water.push(litres(kept.wh));
  };
  let sim = false;
  for (const e of events) {
    const d = e.data || {};
    if (e.type === "run.started") sim = isSimulated(d);
    if (e.type === "decision" && e.step_id) {
      const t = get(e.step_id);
      t.reason = d.reason || ""; t.model = d.model || "fallback";
      t.cap = d.choice?.power_cap_w ?? t.cap;
    } else if (e.type === "trial.measured") {
      const m = { cap: d.cap_w, wh: whOf(d), p99: d.p99_ms, pass: d.pass_rate ?? null, tok: d.tokens_per_req ?? null };
      if (!base) { base = m; kept = m; curCap = m.cap; snap(); continue; }
      const t = get(e.step_id);
      Object.assign(t, { cap: m.cap, wh: m.wh, p99: m.p99, pass: m.pass, tok: m.tok, from: kept, outcome: "pending" });
    } else if (e.type === "gate.result" && e.step_id && tries.has(e.step_id)) {
      const t = get(e.step_id);
      (t.gates ||= []).push({ check: d.check, passed: d.passed });
    } else if (e.type === "action.applied" && d.change?.power_cap_w != null) {
      curCap = d.change.power_cap_w;
      const t = tries.get(e.step_id);
      if (t && t.wh != null) { t.outcome = "kept"; kept = { cap: t.cap, wh: t.wh, p99: t.p99, pass: t.pass, tok: t.tok }; snap(); }
    } else if (e.type === "action.rolled_back") {
      curCap = d.restored_cap_w ?? curCap;
      const t = tries.get(e.step_id);
      if (t) { t.outcome = "reverted"; t.failed = d.failed_gates || []; t.restored = d.restored_cap_w; snap(); }
    }
  }
  const bars = order.map((s) => tries.get(s)).filter((t) => t.wh != null).map((t) => {
    const triedWh = t.from.wh - t.wh, triedW = t.from.cap - t.cap;
    const live = t.outcome !== "reverted";
    return {
      step: t.step, cap: t.cap, outcome: t.outcome, triedWh, triedW,
      netWh: live ? triedWh : 0, netW: live ? triedW : 0,
      node: t.outcome === "reverted" ? "revert" : t.outcome === "kept" ? "kept" : "verdict",
      why: why(t, base),
    };
  });
  let top = null;
  for (const b of bars) if (b.outcome === "kept" && b.netWh > 0 && (!top || b.netWh > top.netWh * 1.01)) top = b;
  if (top) top.top = true;
  const totalWh = base && kept ? base.wh - kept.wh : 0;
  return {
    base, kept, cap: curCap, bars, hist, totalWh,
    totalW: base && curCap != null ? base.cap - curCap : 0,
    counters: base && kept ? {
      power: { v: curCap, base: base.cap, label: sim ? "SIMULATED" : "MEASURED" },
      wh: { v: kept.wh, base: base.wh, label: sim ? "SIMULATED" : "MEASURED" },
      usd: { v: usd(kept.wh), base: usd(base.wh), label: "ESTIMATE" },
      water: { v: litres(kept.wh), base: litres(base.wh), label: "ESTIMATE" },
      tokPerWh: kept.tok != null ? { v: kept.tok / kept.wh, base: base.tok / base.wh, tok: kept.tok, label: sim ? "SIMULATED" : "MEASURED" } : null,
    } : null,
  };
}

const sgn = (x, d) => (x > 0 ? "−" : x < 0 ? "+" : "±") + Math.abs(x).toFixed(d);

/** One plain line per bar, from event data only. */
export function why(t, base) {
  const parts = [`${t.cap} W cap: ${sgn(t.from.wh - t.wh, 4)} Wh/req`];
  if (t.p99 != null && base?.p99) {
    const pct = (100 * (t.p99 - base.p99)) / base.p99;
    const ok = pct <= 100 * P99_BUDGET;
    parts.push(`p99 ${pct >= 0 ? "+" : "−"}${Math.abs(pct).toFixed(1)}% ${ok ? "still in budget" : "over the +10% budget"}`);
  }
  if (t.outcome === "reverted") parts.push(`gave it back: reverted to ${t.restored ?? t.from.cap} W`);
  if (t.outcome === "pending") parts.push("waiting for the gate");
  if (t.reason) parts.push(`${t.model && t.model !== "fallback" ? "Spark reason" : "rule"}: ${t.reason}`);
  return parts.join(", ");
}

/** Waterfall geometry: each bar starts where the running total ended. Values in the chosen unit. */
export function waterfall(bars, unit) {
  let run = 0;
  return bars.map((b) => {
    const v = inUnit(b, unit);
    const start = run;
    run += v.net;
    return { ...b, v, start, end: run };
  });
}
