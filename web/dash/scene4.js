// Scene 4 demo screens: one panel per narration line, every number read from recorded files.
// Usage: scene4.html?line=2|3|5|6[&lightup=1][&step=ms]. Without ?line all panels show.
import { parseLightup, playLightup } from "./metatools-lineup.js";

const SRC = {
  receipt: "/replays/modal-r1/receipt.json",
  energy: "/replays/modal-r1/energy-receipt.json",
  langchain: "/replays/modal-r1/blocks/r05-langchain-20261102T140740Z/attachment.json",
  eye: "/replays/modal-r1/blocks/r05-eye-hearing-20261102T153519Z/eye/eye_test.json",
  spoken: "/replays/modal-r1/blocks/r05-eye-hearing-20261102T153519Z/spoken/spoken_answer.json",
  proof: "/replays/h100-max-1103/proofs/p2-sweep/proof-de155e.json",
  pr: "./scene4/musecode-pr4.json",
};
const esc = (s) => String(s ?? "—").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);
const chip = (l) => l ? `<span class="s4-chip s4-${esc(String(l).split(/[ (]/)[0].toLowerCase())}">${esc(String(l).split(" (")[0])}</span>` : "";
const n = (v, d = 1) => (v == null || isNaN(v) ? "—" : Number(v).toFixed(d));
const card = (title, body, src) => `<div class="lineup-card s4-card"><div class="lineup-name">${title}</div>${body}<div class="s4-src">${esc(src)}</div></div>`;

export function buildPanels(D) {
  const r = D.receipt, res = r.results, P = D.proof, lc = D.langchain.results.langchain;
  const feat = Object.fromEntries(r.features.map((f) => [f.id, f.status]));
  const caps = P.steps.map((s) => `<tr class="${s.accepted ? "s4-keep" : "s4-back"}"><td>${s.cap_w} W</td><td>${n(s.energy_change_pct?.mean)}%</td>
      <td>${s.quality?.cap_pct}% vs ${s.quality?.stock_pct}%</td><td>${s.accepted ? "kept" : "rolled back: " + esc(s.why_not.join("; "))}</td></tr>`).join("");
  const checks = Object.entries(r.approvals.review.checks).map(([k, c]) => `<li>${c.ok ? "✓" : "✗"} ${esc(c.rule || k)}</li>`).join("");
  const w = D.energy.sci.arms;
  const eyeA = D.eye.energy_arms?.with_wattnot || {}, spA = D.spoken.energy_arms?.with_wattnot || {};
  const pr = D.pr;
  return {
    2: { title: "Not just power caps: every saver, one score, rollback", cards: [
      card("Closest tools", `<div class="lineup-role"><b>Pebble</b>: caps GPU power; checks SLA, throughput, latency.<br><b>Neuralwatt</b>: caps power every 100 ms; tunes tokens per watt.<br>Answer-quality check: not found for either.</div>`, "competitor scan, Nov 3 (public sites)"),
      card(`Power caps (H100) ${chip("MEASURED")}`, `<table class="s4-t"><tr><th>cap</th><th>energy</th><th>answers</th><th>decision</th></tr>${caps}</table>`, "h100-max-1103 p2-sweep proof-de155e"),
      card(`Batching, caching, agent waste ${chip("MEASURED")}`, `<ul class="s4-l"><li>Concurrency batching: ${feat.concurrency_batching}, KV batch: ${feat.kv_batch}</li><li>Prefix caching: ${feat.vllm_prefix}</li>
        <li>Agent saver (LangChain): ${lc.arms.saver_on.correct}/${lc.arms.saver_on.n} vs ${lc.arms.saver_off.correct}/${lc.arms.saver_off.n}, ${n(lc.arms.saver_off.j_per_correct, 0)} → ${n(lc.arms.saver_on.j_per_correct, 0)} J/correct</li></ul>`, "modal-r1 receipt + r05 LangChain block"),
      card(`Joules per correct answer ${chip(res.j_per_correct_saved_pct.label)}`, `<div class="s4-big">${n(res.j_per_correct_without)} → ${n(res.j_per_correct_with)} J</div>
        <div class="lineup-role">accuracy ${n(res.accuracy_without * 100)}% → ${n(res.accuracy_with * 100)}% · ${n(res.j_per_correct_saved_pct.value, 2)}% less energy per correct answer</div>`, "modal-r1 receipt (A100, NVML)"),
      card(`Bad change rolled back ${chip(P.control.label)}`, `<div class="lineup-role">Deliberately bad setting rejected: <b>${P.control.bad_setting_rejected ? "yes" : "no"}</b><br>${P.steps.filter((s) => !s.accepted).length} of ${P.steps.length} caps rolled back to stock; best kept: ${P.best_cap_w} W</div>`, "proof-de155e control"),
    ] },
    3: { title: "Text, images and voice, and a look at the rack first", cards: [
      card(`Eye check (Muse Image test set) ${chip(D.eye.label)} ${chip(D.eye.answers_label)}`, `<div class="s4-big">${esc(D.eye.reason)}</div><div class="lineup-role">${n(eyeA.j_per_correct)} J per correct image answer</div>`, "r05 eye_test.json"),
      card(`Hearing check (Muse Voice Transcribe) ${chip(D.spoken.transcripts_label)} ${chip(D.spoken.answers_label)}`, `<div class="s4-big">${esc(D.spoken.reason)}</div><div class="lineup-role">${n(spA.j_per_correct)} J per correct spoken answer</div>`, "r05 spoken_answer.json"),
      card(`SAM 3.1 rack check ${chip(P.room.labels.rack_count)}`, `<img class="s4-img" src="./scene4/rackmap.sam.png" alt="SAM 3.1 rack map"><div class="lineup-role">${P.room.sam.racks_found} racks found (prompt "${esc(P.room.sam.prompt)}"). Rule in code: a rack not visible in the photo is not touched.</div>`, "proof-de155e room.sam · node/agent.py rack_check"),
    ] },
    5: { title: "Safe cuts, water counted, EU report", cards: [
      card(`Quality gate: ${esc(r.approvals.review.decision)} ${chip("MEASURED")}`, `<ul class="s4-l">${checks}</ul>`, "modal-r1 receipt approvals.review"),
      card(`Water per request ${chip(D.energy.estimates.water_ml_per_req.label)}`, `<div class="s4-big">${n(w.baseline.water_ml_per_request, 3)} → ${n(w.candidate.water_ml_per_request, 3)} mL</div><div class="lineup-role">${esc(D.energy.estimates.water_ml_per_req.source)}</div>`, "modal-r1 energy receipt sci.water"),
      card(`EU efficiency report ${chip("MEASURED")}`, `<div class="lineup-role">Built from the signed receipt of this run.</div><a class="s4-btn" href="/replays/modal-r1/eu_report.html">Open EU report</a>`, "replays/modal-r1/eu_report.html"),
    ] },
    6: { title: "Signed receipt, and the fix as a pull request", cards: [
      card(`Signed receipt ${chip("MEASURED")}`, `<div class="s4-mono">${esc(D.energy.receipt_id)}<br>sha256 ${esc(r.integrity.sha256.slice(0, 24))}…<br>signer ${esc(r.integrity.signer)}</div>
        <div class="lineup-role">Re-seal check: <b>${r.approvals.review.checks.receipt_hash?.ok ? "✓ verified" : "✗"}</b> · sigstore bundle: receipt.sigstore.json</div>`, "modal-r1 receipt.json integrity"),
      card(`Pay only for what's proven ${chip("MEASURED")}`, `<div class="s4-big">${n(D.energy.result.billed_saving_pct, 2)}% billed</div><div class="lineup-role">measured saving ${n(D.energy.result.saving_pct.mean, 2)}%; billing uses the low end of the confidence interval (${n(D.energy.result.saving_pct.ci_low, 2)}–${n(D.energy.result.saving_pct.ci_high, 2)}%)</div>`, "modal-r1 energy receipt result"),
      card(`Muse Code pull request ${chip(pr.label)}`, `<div class="s4-mono">${esc(pr.pr_url.replace("https://github.com/", ""))}</div><div class="lineup-role">Change: power cap ${pr.change.power_cap_w} W (${esc(pr.change.label)}) · energy ${n(pr.evidence.energy_change_pct)}% · answers ${pr.evidence.quality_cap_pct}% vs ${pr.evidence.quality_stock_pct}% on ${pr.evidence.checked}</div>`, "musecode p2 pr.json"),
    ] },
  };
}

export async function loadData(fetcher = fetch) {
  const D = {};
  await Promise.all(Object.entries(SRC).map(async ([k, u]) => { D[k] = await (await fetcher(u)).json(); }));
  return D;
}

export async function mountScene4(el, search) {
  const panels = buildPanels(await loadData());
  const line = new URLSearchParams(search).get("line");
  const keys = line && panels[line] ? [line] : Object.keys(panels);
  el.innerHTML = keys.map((k) => `<section class="lineup s4-panel" data-line="${k}"><div class="lineup-head"><h2><span class="s4-line">Line ${k}</span> ${panels[k].title}</h2>
    <button type="button" class="lineup-play">&#9654; Play</button></div><div class="s4-grid">${panels[k].cards.join("")}</div></section>`).join("");
  const { on, step } = parseLightup(search);
  el.querySelectorAll(".s4-panel").forEach((p) => {
    p.querySelector(".lineup-play").addEventListener("click", () => playLightup(p, step));
    if (on) { p.classList.add("lineup-dim"); playLightup(p, step); }
  });
}
