// Voice guide answers built from the signed Oct 31 Glimmer rerun receipt, plus the live Meta call
// addenda saved next to the older H100 receipt (Meta call evidence only, no numbers from that run).
// No model is called: each answer is a REPLAY of MEASURED (or LIVE-recorded) numbers and cites its file.
const RECEIPT = "../receipts/rerun-1031/rcpt_20261031_rerun_cap325";
const BASE = "../receipts/run2/rcpt_20261027_run2_cap350";
const pct = (x, d = 1) => `${(x * 100).toFixed(d)}%`;

export function answerFromReceipts(query, docs) {
  const t = (query || "").toLowerCase();
  const r = docs?.receipt; if (!r) return null;
  const trials = docs.wonder?.data?.trials || [];
  const trial = (w) => trials.find((x) => x.cap_w === w);
  const j = r.joules || {};
  const src = (s) => `Source: ${s} (signed).`;
  // Cap-by-cap trial answers (325 W, 350 W ...) came from the older RUN2 trials, which served a
  // non-Meta model; they are no longer answered here.
  if (/quality|pass rate|93|accura|gate/.test(t)) {
    return { intent: "answer", label: "REPLAY", text: `Quality is the fixed-check pass rate: ${pct(r.baseline.quality.fixed_pass_rate, 1)} uncapped and ${pct(r.candidate.quality.fixed_pass_rate, 1)} at ${r.candidate.params.power_cap_w} W over ${r.subject.workload.n_requests} checks (MEASURED). The gate allows a drop of at most 1.0 pt; p99 latency may rise at most 10%. ${src("quality_definition addendum")}` };
  }
  if (/wonder|supervis/.test(t) && docs.wonder) {
    const d = docs.wonder.data.decision || {};
    return { intent: "answer", label: "REPLAY", text: `Muse Wonder (live once) proposed: ${d.action || "trial"} at ${d.cap_w} W. It is advice only: the local gates decide. ${src("wonder_live addendum")}` };
  }
  if (/\bpr\b|pull request|muse code|patch/.test(t) && docs.musecode) {
    return { intent: "answer", label: "REPLAY", text: `Muse Code opened a real pull request with a cap patch: ${docs.musecode.data?.pr?.url || docs.musecode.data?.pr_url || "see the Live Meta calls card"}. Nothing is deployed. ${src("musecode_pr addendum")}` };
  }
  if (/sam|hotspot|rack|segment/.test(t) && docs.sam) {
    return { intent: "answer", label: "REPLAY", text: `SAM 3.1 (live once) found a box at [${docs.sam.data.mask.bbox.join(", ")}] on a public rack photo (MEASURED). The photo is not thermal, so the hot slot is SIMULATED and the 400 → 350 W cap is ADVICE. ${src("sam_hotspot addendum")}` };
  }
  if (/joule|energy|sav|power|cost|water|350|result|headline/.test(t)) {
    return { intent: "answer", label: "REPLAY", text: `At ${r.candidate.params.power_cap_w} W vs ${r.baseline.params.power_cap_w} W, joules per correct answer fell ${Math.abs(j.change_pct.j_per_good_answer).toFixed(1)}% (${j.baseline.j_per_good_answer.toFixed(2)} → ${j.candidate.j_per_good_answer.toFixed(2)} J, MEASURED). Cost and water follow from these joules and are ESTIMATES. ${src("Oct 31 rerun receipt")}` };
  }
  return null;
}

let cache = null;
export async function loadReceiptDocs(base = import.meta.url) {
  if (cache) return cache;
  const get = async (suffix) => { try { const r = await fetch(new URL(`${BASE}${suffix}.json`, base)); return r.ok ? r.json() : null; } catch { return null; } };
  const getR = async () => { try { const r = await fetch(new URL(`${RECEIPT}.json`, base)); return r.ok ? r.json() : null; } catch { return null; } };
  const [receipt, wonder, musecode, sam] = await Promise.all([getR(), get(".wonder_live"), get(".musecode_pr"), get(".sam_hotspot")]);
  cache = { receipt, wonder, musecode, sam };
  return cache;
}
