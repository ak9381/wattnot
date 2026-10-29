// Voice guide answers built only from the signed RUN2 receipt and its live addenda.
// No model is called: each answer is a REPLAY of MEASURED (or LIVE-recorded) numbers and cites its file.
const BASE = "../receipts/run2/rcpt_20261027_run2_cap350";
const pct = (x, d = 1) => `${(x * 100).toFixed(d)}%`;

export function answerFromReceipts(query, docs) {
  const t = (query || "").toLowerCase();
  const r = docs?.receipt; if (!r) return null;
  const trials = docs.wonder?.data?.trials || [];
  const trial = (w) => trials.find((x) => x.cap_w === w);
  const j = r.joules || {};
  const src = (s) => `Source: ${s} (signed).`;
  const capMatch = t.match(/\b(\d{3})\s*(w|watt)/);
  if (capMatch && trial(+capMatch[1])) {
    const x = trial(+capMatch[1]);
    const verdict = x.accepted_by_gates ? "kept: it passed both gates" : `rejected: p99 latency rose ${pct(x.p99_delta)} against the 10% limit`;
    return { intent: "answer", label: "REPLAY", text: `${x.cap_w} W was ${verdict}. Joules per correct answer ${x.joules_per_correct} J, fixed-check pass rate ${pct(x.quality, 1)} (MEASURED). ${src("RUN2 receipt + Wonder addendum trials")}` };
  }
  if (/reject|fail|why not lower/.test(t) && trial(325)) {
    const x = trial(325);
    return { intent: "answer", label: "REPLAY", text: `325 W was rejected because p99 latency rose ${pct(x.p99_delta)}, over the 10% limit, even though quality held at ${pct(x.quality, 1)} (MEASURED). 350 W (+${pct(trial(350)?.p99_delta ?? 0)}) is the lowest cap that passed. ${src("RUN2 receipt planner trace")}` };
  }
  if (/quality|pass rate|93|accura|gate/.test(t)) {
    return { intent: "answer", label: "REPLAY", text: `Quality is the fixed-check pass rate: ${pct(r.baseline.quality.fixed_pass_rate, 1)} uncapped and ${pct(r.candidate.quality.fixed_pass_rate, 1)} at ${r.candidate.params.power_cap_w} W over ${r.subject.workload.n_requests} checks (MEASURED). The gate allows a drop of at most 1.0 pt; p99 latency may rise at most 10%. ${src("quality_definition addendum")}` };
  }
  if (/wonder|supervis/.test(t) && docs.wonder) {
    const d = docs.wonder.data.decision || {};
    return { intent: "answer", label: "REPLAY", text: `Muse Wonder (live once) proposed: ${d.action || "trial"} at ${d.cap_w} W. The local p99 gate would block it because 325 W already failed. ${src("wonder_live addendum")}` };
  }
  if (/\bpr\b|pull request|muse code|patch/.test(t) && docs.musecode) {
    return { intent: "answer", label: "REPLAY", text: `Muse Code opened a real pull request with the 350 W patch: ${docs.musecode.data?.pr?.url || docs.musecode.data?.pr_url || "see the Live Meta calls card"}. Nothing is deployed. ${src("musecode_pr addendum")}` };
  }
  if (/sam|hotspot|rack|segment/.test(t) && docs.sam) {
    return { intent: "answer", label: "REPLAY", text: `SAM 3.1 (live once) found a box at [${docs.sam.data.mask.bbox.join(", ")}] on a public rack photo (MEASURED). The photo is not thermal, so the hot slot is SIMULATED and the 400 → 350 W cap is ADVICE. ${src("sam_hotspot addendum")}` };
  }
  if (/joule|energy|sav|power|cost|water|350|result|headline/.test(t)) {
    return { intent: "answer", label: "REPLAY", text: `At ${r.candidate.params.power_cap_w} W vs ${r.baseline.params.power_cap_w} W, joules per correct answer fell ${Math.abs(j.change_pct.j_per_good_answer).toFixed(1)}% (${j.baseline.j_per_good_answer.toFixed(2)} → ${j.candidate.j_per_good_answer.toFixed(2)} J, MEASURED). Cost and water follow from these joules and are ESTIMATES. ${src("RUN2 receipt")}` };
  }
  return null;
}

let cache = null;
export async function loadReceiptDocs(base = import.meta.url) {
  if (cache) return cache;
  const get = async (suffix) => { try { const r = await fetch(new URL(`${BASE}${suffix}.json`, base)); return r.ok ? r.json() : null; } catch { return null; } };
  const [receipt, wonder, musecode, sam] = await Promise.all([get(""), get(".wonder_live"), get(".musecode_pr"), get(".sam_hotspot")]);
  cache = { receipt, wonder, musecode, sam };
  return cache;
}
