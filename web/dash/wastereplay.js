// The "behind real harness stop rules" part of the Agent waste card.
// Pure formatting (no DOM at import time, so node can test it) plus one small wire-up
// for the replay buttons. Every number comes from data/agent-waste/v2/results.json.
import { esc, pill } from "./theme.js";

const LABELS = ["MEASURED", "ESTIMATE", "SIMULATED", "MOCK", "ADVICE"];
export const num = (v, d = 2) => (v == null || Number.isNaN(v) ? "–" : Number(v).toLocaleString("en-US", { maximumFractionDigits: d, minimumFractionDigits: 0 }));
const tagOf = (t) => (LABELS.includes(t) ? pill(t) : "");
const pct = (v) => (v == null ? "–" : `${num(v, 1)}%`);
const chgText = (c) => (c == null ? "–" : `${c > 0 ? "+" : ""}${num(c, 1)}%`);
export const ARMS = ["harness", "harness+saver"];
export const ARM_NAME = { harness: "Harness alone", "harness+saver": "Harness + saver" };
const ORDER = ["calls per run", "tokens per run", "wall time per run (s)", "energy per run (J)", "cost per run ($)"];

// "how a run ended", in plain words
export function endedText(arm) {
  if (!arm) return "no run";
  const by = arm.ended_by || "";
  const n = num(arm.calls, 0);
  if (by === "cap") return `hit its own cap after ${n} calls`;
  if (by === "safety") return `still running at the bench safety stop (${n} calls)`;
  if (by === "final") return `ended by its own stop rule after ${n} calls`;
  if (by === "saver-done") return `the agent called done after a nudge; ${n} calls`;
  if (by === "saver-stop") return `the saver stopped it after ${n} calls`;
  if (by === "error") return `a call failed after ${n} calls`;
  return `${by || "ended"} after ${n} calls`;
}

export function tableHtml(block, title) {
  const rows = ORDER.filter((k) => block.rows && block.rows[k]).map((k) => {
    const r = block.rows[k], d = k.startsWith("cost") ? 4 : 2;
    return `<tr><th scope="row">${esc(k)}</th><td class="num">${num(r.off, d)}</td><td class="num"><b>${num(r.on, d)}</b></td><td class="num chg ${r.change_pct > 0 ? "up" : ""}">${esc(chgText(r.change_pct))}</td><td>${tagOf(r.tag)}</td></tr>`;
  }).join("");
  const s = block.success || {}, m = block.miss_runs || {};
  return `<h3 class="wsub">${esc(title)} <span class="muted">${num(block.runs, 0)} runs per arm-pair</span></h3>
    <table class="cmpt"><thead><tr><th></th><th>Harness alone</th><th>Harness + saver</th><th>Change</th><th></th></tr></thead><tbody>${rows}</tbody></table>
    <div class="qrow"><b>Tasks passed</b><span>${pct(s.off_pct)} alone, ${pct(s.on_pct)} with the saver ${tagOf("MEASURED")}</span></div>
    <div class="qrow"><b>Runs that missed their stop</b><span>${num(m.off, 0)} alone, ${num(m.on, 0)} with the saver ${tagOf("MEASURED")}</span></div>`;
}

export function categoriesHtml(cats) {
  if (!cats || !cats.length) return "";
  const rows = cats.map((c) => `<tr><th scope="row">${esc(c.name)}</th><td class="num">${num(c.calls_off)} → <b>${num(c.calls_on)}</b></td><td class="num chg ${c.tokens_change_pct > 0 ? "up" : ""}">${esc(chgText(c.tokens_change_pct))}</td><td class="num">${pct(c.success.off_pct)} → ${pct(c.success.on_pct)}</td><td class="num">${num(c.miss_runs.off, 0)} → ${num(c.miss_runs.on, 0)}</td></tr>`).join("");
  return `<h3 class="wsub">By kind of miss ${tagOf("MEASURED")}</h3><table class="cmpt"><thead><tr><th></th><th>Calls per run</th><th>Tokens</th><th>Passed</th><th>Missed stop</th></tr></thead><tbody>${rows}</tbody></table>`;
}

const passText = (a) => (a && a.runs ? `${num(a.success, 0)}/${num(a.runs, 0)}` : "–");
const cell = (a) => (a && a.runs ? `${num(a.calls)} calls · ${num(a.tokens, 0)} tok · ${passText(a)} passed` : "–");

// After the two fixes: same cases, harness alone, the v2 saver and the v3 saver side by side.
export function afterFixesHtml(af) {
  if (!af) return "";
  const rowOf = (name, b) => `<tr><th scope="row">${esc(name)}</th><td class="num">${esc(cell(b.harness))}</td><td class="num">${esc(cell(b.saver_v2))}</td><td class="num"><b>${esc(cell(b.saver_v3))}</b></td></tr>`;
  const rows = [rowOf("All 6 fixed-up cases", af.fixed), rowOf("All 18 normal tasks (regression check)", af.normal), ...(af.cases || []).map((c) => rowOf(c.id, c))].join("");
  const notes = (af.notes || []).map((n) => `<p class="hnt">${esc(n)}</p>`).join("");
  const fx = (af.fixes || []).map((f) => `<li>${esc(f)}</li>`).join("");
  return `<h3 class="wsub">After the two fixes (v3) ${tagOf("MEASURED")}</h3>
    <ul class="wfix">${fx}</ul>
    <table class="cmpt"><thead><tr><th></th><th>Harness alone (v3 run)</th><th>Saver, before (v2)</th><th>Saver, after (v3)</th></tr></thead><tbody>${rows}</tbody></table>
    ${notes}<p class="hnt">${num(af.reps, 0)} reps per case. v2 numbers come from the earlier run on the same cases; the model is not deterministic, so small gaps are noise.</p>`;
}

// the steps of one arm that are visible after k steps of the replay
export const visible = (arm, k) => ((arm && arm.trace) || []).slice(0, k);
export const stepCount = (sc) => Math.max(0, ...ARMS.map((a) => ((sc.arms[a] || {}).trace || []).length));

export function eventHtml(ev) {
  const calls = (ev.calls || []).map((c) => `<li><code>${esc(c[0])}</code> <span class="muted">${esc(String(c[1] || "").slice(0, 70))}</span> <span class="ret">→ ${esc(String(c[2] || "").slice(0, 80))}</span></li>`).join("");
  const text = ev.text ? `<span class="say">${esc(ev.text)}</span>` : "";
  const note = ev.note ? `<span class="wnote">${esc(ev.note)}</span>` : "";
  return `<li class="ev ${ev.note ? "sv" : ""}"><span class="en">${esc(ev.agent || "main")} · call ${num(ev.n, 0)} · ${num(ev.tokens, 0)} tokens</span>${text}${calls ? `<ul>${calls}</ul>` : ""}${note}</li>`;
}

export function armHtml(sc, name, k) {
  const arm = sc.arms[name];
  if (!arm) return `<div class="wcol"><h4>${esc(ARM_NAME[name])}</h4><p class="hnt">Not recorded.</p></div>`;
  const total = (arm.trace || []).length;
  const done = k >= total;
  const end = done ? `<p class="wend ${arm.miss ? "bad" : "ok"}">${esc(endedText(arm))}. Task ${arm.success ? "passed" : "failed"}; ${num(arm.total_tokens, 0)} tokens; ${num(arm.wall_s, 1)} s.</p>` : `<p class="wend run">running…</p>`;
  return `<div class="wcol"><h4>${esc(ARM_NAME[name])}</h4><ol class="wtrace">${visible(arm, k).map(eventHtml).join("")}</ol>${end}</div>`;
}

// did the saver step in at all on this recorded run? (its trace notes start with "saver:")
export const saverActed = (sc) => ((sc.arms["harness+saver"] || {}).trace || []).some((e) => /^saver:/.test(e.note || "") || /then saver:/.test(e.note || ""));

export function replayHtml(sc, k) {
  const honest = saverActed(sc) ? "" : `<p class="hnt wwarn">The saver did not step in on this recorded run. The two runs simply took different paths, because the model is not deterministic. Do not read the gap as the saver's doing.</p>`;
  return `${honest}<p class="hnt">${esc(sc.prompt || "")}</p>
    <p class="hnt"><b>What was injected:</b> ${esc(sc.injected || "")}. <b>Harness:</b> ${esc(sc.harness)} loop. Replay of a recorded real Muse Spark run (rep ${num(sc.rep, 0)}) ${pill("MEASURED")} <span class="srcb REPLAY"><i></i>REPLAY</span></p>
    <div class="wpair">${ARMS.map((a) => armHtml(sc, a, k)).join("")}</div>`;
}

export function secondLayerHtml(sl, sel = 0) {
  if (!sl) return "";
  const sc = sl.scenarios || [];
  const tabs = sc.map((s, i) => `<button type="button" class="wtab ${i === sel ? "on" : ""}" data-i="${i}">${esc(s.title || s.case)}</button>`).join("");
  const notes = (sl.notes || []).map((n) => `<p class="hnt">${esc(n)}</p>`).join("");
  const replay = sc.length ? `<h3 class="wsub">Replay a miss</h3>
    <div class="wtabs" role="tablist">${tabs}</div>
    <div class="wctl"><button type="button" class="wplay">Play</button><button type="button" class="wreset">Reset</button></div>
    <div class="wreplay" aria-live="polite">${replayHtml(sc[sel], stepCount(sc[sel]))}</div>` : "";
  return `<div class="wsl"><h3 class="wsub2">Behind real harness stop rules</h3>
    <p class="note2">${esc(sl.headline || "")}</p>
    ${tableHtml(sl.normal || {}, `Normal tasks (should tie; ${num(sl.normal_cases, 0)} cases)`)}
    ${tableHtml(sl.injected || {}, `Injected misses (${num(sl.cases, 0)} cases)`)}
    ${categoriesHtml(sl.categories)}
    ${afterFixesHtml(sl.after_fixes)}
    ${replay}
    <div class="wnotes">${notes}<p class="hnt">${num(sl.reps, 0)} reps per case. Spend on this benchmark: $${num(sl.spend_usd, 2)} ${tagOf("ESTIMATE")}</p></div></div>`;
}

// buttons: pick a scenario, play it one step at a time, or reset. Reduced motion shows it all at once.
export function wireReplay(root, sl, { reduce = () => false, delay = 900 } = {}) {
  const sc = (sl && sl.scenarios) || [];
  if (!root || !sc.length) return;
  let sel = 0, k = stepCount(sc[0]), timer = null;
  const view = root.querySelector(".wreplay"), play = root.querySelector(".wplay");
  const paint = () => { view.innerHTML = replayHtml(sc[sel], k); };
  const stop = () => { if (timer) clearInterval(timer); timer = null; play.textContent = "Play"; };
  const tick = () => { k += 1; paint(); if (k >= stepCount(sc[sel])) stop(); };
  const start = () => {
    if (reduce()) { k = stepCount(sc[sel]); paint(); return; }
    if (k >= stepCount(sc[sel])) k = 0;
    paint(); play.textContent = "Pause"; timer = setInterval(tick, delay);
  };
  play.addEventListener("click", () => (timer ? stop() : start()));
  root.querySelector(".wreset").addEventListener("click", () => { stop(); k = 0; paint(); });
  root.querySelectorAll(".wtab").forEach((b) => b.addEventListener("click", () => {
    stop(); sel = Number(b.dataset.i); k = stepCount(sc[sel]);
    root.querySelectorAll(".wtab").forEach((x) => x.classList.toggle("on", x === b)); paint();
  }));
}
