// Run matrix page: the 12 planned rounds, Spark-vs-deterministic grades, the Meta spend meter
// and the provider credit meters. Pure string builders (node-tested); only mountMatrix() touches
// the DOM. Reads GET /api/dash/matrix (read-only, no model calls). A round with no result file
// is PLANNED; no number is shown for it.
import { esc, num, labelChip } from "./costbudget.js";

const LEG = { baseten: "Baseten", modal: "Modal", h100: "H100", combined: "Combined (routed)" };

export function pct(v) { return v == null ? "n/a" : `${(v * 100).toFixed(1)}%`; }

export function meterBar(value, cap, marks = []) {
  const w = cap > 0 ? Math.min(100, (value / cap) * 100) : 0;
  const ticks = marks.map((m) => `<i class="mx-tick" title="${esc(m.title)}" style="left:${Math.min(100, (m.at / cap) * 100)}%"></i>`).join("");
  return `<div class="mx-bar"><b style="width:${w.toFixed(1)}%"></b>${ticks}</div>`;
}

export function roundRow(r) {
  const planned = r.status === "PLANNED";
  const res = r.results || {};
  const cell = (k) => (planned || res[k] == null ? "–" : esc(res[k]));
  const g = r.grades;
  return `<tr class="${planned ? "mx-planned" : ""}"><td>${esc(r.id)}</td><td>${esc(LEG[r.leg] || r.leg)} #${esc(r.round)}</td>`
    + `<td>${(r.datasets || []).map(esc).join(", ")}</td><td>${(r.arms || []).length === 2 ? "without + with WattNot" : esc((r.arms || []).join(", "))}</td>`
    + `<td>${planned ? labelChip("PLANNED") : `${esc(r.status)} ${labelChip(r.label)}`}</td>`
    + `<td>${cell("accuracy_without")} → ${cell("accuracy_with")}</td><td>${cell("j_per_correct_without")} → ${cell("j_per_correct_with")}</td>`
    + `<td data-feature="spark_grades">${g ? pct(g.agreement) : "–"}</td></tr>`;
}

export function matrixTable(v) {
  const rows = (v.rounds || []).map(roundRow).join("");
  const done = (v.rounds || []).filter((r) => r.status !== "PLANNED").length;
  return `<section class="mx-card"><h2>Run matrix · ${done} of ${(v.rounds || []).length} rounds run</h2>`
    + `<p class="mx-hint">Every round runs each dataset once without and once with WattNot. Rounds without real data are PLANNED and show no numbers.</p>`
    + `<table class="mx-t"><thead><tr><th>Round</th><th>Leg</th><th>Datasets</th><th>Arms</th><th>Status</th><th>Accuracy (without → with)</th><th>J per correct (without → with)</th><th>Spark agreement</th></tr></thead><tbody>${rows}</tbody></table></section>`;
}

export function gradesCard(v) {
  const graded = (v.rounds || []).filter((r) => r.grades);
  const n = graded.reduce((a, r) => a + r.grades.spark_graded, 0);
  const dis = graded.flatMap((r) => r.grades.disagreements.map((d) => ({ ...d, round: r.id })));
  const agree = n ? 1 - dis.length / n : null;
  const list = dis.slice(0, 50).map((d) => `<li><code>${esc(d.round)}/${esc(d.dataset)}/${esc(d.id)}</code> deterministic ${d.deterministic ? "pass" : "fail"}, Spark ${d.spark ? "pass" : "fail"}${d.spark_reason ? ` — ${esc(d.spark_reason)}` : ""}</li>`).join("");
  return `<section class="mx-card"><h2>Spark grades vs deterministic checks</h2>`
    + `<p class="mx-hint">${esc(v.spark_grade_note || "")} ${v.deterministic_decides ? "The GSM8K / HumanEval / our-set check decides the score." : ""}</p>`
    + (n ? `<p>Agreement ${pct(agree)} over ${num(n, 0)} answers ${labelChip("MEASURED")} · ${dis.length} disagreements</p><ul class="mx-dis">${list}</ul>`
         : `<p>${labelChip("PLANNED")} No graded answers yet: grades appear here after the first real round.</p>`)
    + `</section>`;
}

export function metaCard(m) {
  const per = Object.entries(m.per_model || {}).map(([k, p]) =>
    `<tr><td>${esc(p.name)}</td><td>$${num(p.usd, 4)}</td><td>${num(p.calls, 0)}</td><td>${esc((m.per_model_estimate || {})[k] ?? "")}</td></tr>`).join("");
  const est = m.matrix_estimate_usd ? `$${m.matrix_estimate_usd[0]}–${m.matrix_estimate_usd[1]}` : "n/a";
  return `<section class="mx-card"><h2>Meta spend · $${num(m.total_usd)} of $${num(m.cap_usd, 0)} cap ${labelChip(m.label)}</h2>`
    + meterBar(m.total_usd, m.credits_usd, [{ at: m.soft_usd, title: `$${m.soft_usd} warning` }, { at: m.cap_usd, title: `$${m.cap_usd} hard cap` }])
    + `<p class="mx-hint">Bar = $${num(m.credits_usd, 0)} total Meta credits; ticks at the $${num(m.soft_usd, 0)} warning and the $${num(m.cap_usd, 0)} hard cap.${m.warning ? " <b>Over the warning.</b>" : ""} Matrix estimate: ${est} ${labelChip("ESTIMATE")}. ${esc(m.source)}.</p>`
    + `<table class="mx-t"><thead><tr><th>Meta model</th><th>Spent</th><th>Calls</th><th>Matrix estimate ($)</th></tr></thead><tbody>${per}</tbody></table></section>`;
}

export function providerCard(ps) {
  const one = (p) => {
    const cur = p.unit === "INR" ? "₹" : "$";
    return `<div class="mx-prov"><h3>${esc(p.name)}</h3>${meterBar(p.planned, p.credit, [{ at: p.cap, title: "cap" }])}`
      + `<p>Credit ${cur}${num(p.credit)} ${labelChip(p.credit_label)} · cap ${cur}${num(p.cap)} · planned ${cur}${num(p.planned)} ${labelChip(p.planned_label)}</p>`
      + `<p class="mx-hint">${esc(p.credit_note)}</p></div>`;
  };
  return `<section class="mx-card"><h2>Provider credits (plan; nothing launched)</h2><div class="mx-provs">${(ps || []).map(one).join("")}</div></section>`;
}

export function renderMatrix(v) {
  return matrixTable(v) + gradesCard(v) + metaCard(v.meta || {}) + providerCard(v.providers);
}

export async function mountMatrix(el, fetchFn = fetch) {
  try {
    const r = await fetchFn("/api/dash/matrix");
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    el.innerHTML = renderMatrix(await r.json());
  } catch (e) {
    el.innerHTML = `<p class="mx-hint">Run matrix unavailable (${esc(e.message)}).</p>`;
  }
}
