// Headline numbers for one recorded run under replays/<id>/, read from its own files.
// Every value keeps the label it was recorded with; a missing value comes with a reason.
const J_PER_KWH = 3.6e6;
const pct = (a, b) => (a != null && b ? (a / b - 1) * 100 : null);

async function firstTs(fetcher, u) { try { const r = await fetcher(u); if (!r.ok) return null; const t = await r.text(); return JSON.parse(t.slice(0, t.indexOf("\n") > 0 ? t.indexOf("\n") : undefined)).ts?.slice(0, 10) || null; } catch { return null; } }
const pts = (a, f = (x) => x) => (a || []).map((y, i) => ({ x: i + 1, y: f(y), label: `#${i + 1}` }));
async function getJSON(fetcher, u) { try { const r = await fetcher(u); return r.ok ? await r.json() : null; } catch { return null; } }

export function selectedReplay(search, stored) {
  const v = new URLSearchParams(search || "").get("run") || stored || "";
  return v.startsWith("replay:") ? v.slice(7) : null;
}

// From an energy receipt / proof sci.arms block (MEASURED energy, ESTIMATE water and cost).
function fromArms(arms, tariff) {
  const b = arms?.baseline, c = arms?.candidate; if (!b || !c) return {};
  const out = {
    j_per_req_stock: b.E_kwh_per_request * J_PER_KWH, j_per_req_cap: c.E_kwh_per_request * J_PER_KWH,
    j_per_correct_stock: b.E_kwh_per_correct * J_PER_KWH, j_per_correct_cap: c.E_kwh_per_correct * J_PER_KWH,
    energy_label: b.E_label || "MEASURED",
    water_ml_per_req_stock: b.water_ml_per_request, water_ml_per_req_cap: c.water_ml_per_request, water_label: "ESTIMATE",
  };
  if (tariff?.value != null) {
    out.usd_per_1k_saved = (b.E_kwh_per_request - c.E_kwh_per_request) * 1000 * tariff.value;
    out.cost_label = tariff.label || "ESTIMATE"; out.cost_note = `electricity at $${tariff.value}/kWh (${tariff.label || "ESTIMATE"})`;
  } else out.cost_why = "no electricity tariff in this run's proof, so cost is not computed";
  return out;
}

export async function replaySummary(id, fetcher = fetch) {
  const base = `/replays/${id}`;
  const [man, rec, er] = await Promise.all([getJSON(fetcher, `${base}/manifest.json`), getJSON(fetcher, `${base}/receipt.json`), getJSON(fetcher, `${base}/energy-receipt.json`)]);
  if (!man) return null;
  const S = { id, title: man.title || man.note, provider: man.provider || "e2e", label: man.label || "REPLAY", model: man.model || rec?.model || null,
    date: await firstTs(fetcher, `${base}/events.jsonl`) };
  const res = rec?.results;
  if (res) {
    const pa = res.per_arm || {}, w0 = pa.without_wattnot || {}, w1 = pa.with_wattnot || {};
    Object.assign(S, {
      quality_stock_pct: res.accuracy_without * 100, quality_cap_pct: res.accuracy_with * 100, quality_label: "MEASURED",
      j_per_correct_stock: res.j_per_correct_without, j_per_correct_cap: res.j_per_correct_with,
      energy_label: w1.j_per_correct?.label || res.energy_saved_pct?.label || "MEASURED",
      energy_saved_pct: res.energy_saved_pct?.value, energy_saved_label: res.energy_saved_pct?.label,
      p99_stock_s: w0.item_latency_s?.p99, p99_cap_s: w1.item_latency_s?.p99, p99_label: w1.item_latency_s?.label || "MEASURED",
      setting: "WattNot savers vs stock settings", requests: w1.answers,
    });
    const wh = (a) => a?.wh_per_item_samples?.values;
    if (wh(w0) && wh(w1)) S.series = { unit: "chunk of 32 answers", label: w1.wh_per_item_samples.label, note: w1.wh_per_item_samples.method,
      sets: [{ name: "Without WattNot", cls: "without", pts: pts(wh(w0), (v) => v * 3600) }, { name: "With WattNot", cls: "with", pts: pts(wh(w1), (v) => v * 3600) }] };
    else S.series_why = "hosted API: no per-chunk power samples were recorded for this run";
    S.p99_change_pct = pct(S.p99_cap_s, S.p99_stock_s);
    if (w0.usd?.value != null && w1.usd?.value != null) { S.usd_saved_pct = pct(w1.usd.value, w0.usd.value) * -1; S.usd_saved = w0.usd.value - w1.usd.value; S.cost_label = w1.usd.label; S.cost_note = w1.usd.method; }
    if (er) { const keep = { energy_label: S.energy_label, j_per_correct_stock: S.j_per_correct_stock, j_per_correct_cap: S.j_per_correct_cap }; Object.assign(S, fromArms(er.sci?.arms, er.result?.tariff_usd_per_kwh), keep); }
    else { S.water_why = "hosted API: no GPU power meter, so water (from energy) is not computed"; }
    if (S.j_per_correct_stock != null && S.j_per_req_stock == null && w0.energy_wh?.value != null && w0.answers) {
      S.j_per_req_stock = w0.energy_wh.value * 3600 / w0.answers; S.j_per_req_cap = w1.energy_wh.value * 3600 / w1.answers;
    }
    return S;
  }
  // GPU power-cap sweep (h100-max-1103): the kept cap in the signed proof.
  const proof = await getJSON(fetcher, `${base}/proofs/p2-sweep/proof-de155e.json`);
  const st = proof?.steps?.find((s) => s.accepted);
  if (proof && st) {
    const avg = (a) => (a && a.length ? a.reduce((x, y) => x + y, 0) / a.length : null);
    Object.assign(S, fromArms(proof.sci?.arms, null), {
      quality_stock_pct: st.quality.stock_pct, quality_cap_pct: st.quality.cap_pct, quality_label: st.label,
      j_per_req_stock: avg(st.j_per_req_stock), j_per_req_cap: avg(st.j_per_req_cap), energy_label: st.label,
      energy_saved_pct: -st.energy_change_pct.mean, energy_saved_label: st.label,
      p99_change_pct: st.p99_change_pct.mean, p99_label: st.label,
      cap_w: st.cap_w, stock_w: proof.steps.length ? Math.round(st.mean_w?.stock) : null, setting: `${st.cap_w} W cap vs stock`,
      requests: proof.workload?.prompts,
    });
    S.series = { unit: "repeat", label: st.label, note: `${st.cap_w} W step of the signed sweep, one point per repeat`,
      sets: [{ name: "Without WattNot", cls: "without", pts: pts(st.j_per_req_stock) }, { name: "With WattNot", cls: "with", pts: pts(st.j_per_req_cap) }] };
    S.j_per_correct_stock = S.j_per_req_stock / (S.quality_stock_pct / 100); S.j_per_correct_cap = S.j_per_req_cap / (S.quality_cap_pct / 100);
  }
  return S;
}

// One run per provider in the UI: the stitched runs (best valid take per block, each block source-tagged).
// The original rounds stay on disk under replays/ but are not listed.
export const RUN_NAMES = { "h100-stitched": "H100 run", "modal-stitched": "Modal run", "baseten-stitched": "Baseten run" };
export const REPLAY_IDS = Object.keys(RUN_NAMES);
export const runName = (id) => RUN_NAMES[String(id).replace(/^replay:/, "")] || id;
export async function replayRuns(fetcher = fetch) {
  const out = await Promise.all(REPLAY_IDS.map((id) => replaySummary(id, fetcher).catch(() => null)));
  return out.filter(Boolean).map((x) => ({ id: x.id, name: x.id, model: (x.model || "").replace("meta-models/", ""), date: x.date || "", label: x.label, title: x.title }));
}
