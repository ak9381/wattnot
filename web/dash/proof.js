// Proof tab: the judge path. Spark's own decisions, why a gate failed,
// which endpoints fell back, the stacked saving, and how to check a receipt.
import { esc, badge } from "./v2lib.js";

const fmt = (v, d = 1) => (v == null ? "–" : Number(v).toLocaleString("en-US", { maximumFractionDigits: d }));
const sec = (title, body, cls = "") => `<section class="pf-card glass ${cls}"><h3>${title}</h3>${body}</section>`;

function traceHtml(t) {
  if (!t?.calls?.length) return "<p>No planner trace in this bundle.</p>";
  const rows = t.calls.map((c) => {
    const pass = c.gate ? c.gate.passed : null;
    const fail = (c.gate?.checks || []).filter((k) => !k.passed).map((k) => `${esc(k.check)} ${fmt(k.score, 2)} vs ${fmt(k.threshold, 1)}`).join(", ");
    return `<li class="pf-step ${pass === false ? "bad" : pass ? "ok" : ""}">
      <span class="pf-n">${c.n}</span>
      <div><b>${esc(c.choice || c.step)}</b> ${badge(c.label)}
      <p class="pf-why">"${esc(c.reasoning || "")}"</p>
      <p class="pf-meta">${esc(c.model)} · ${fmt(c.latency_s?.value, 2)} s ${badge("MEASURED")} · ${c.tokens?.input ?? "–"} in / ${c.tokens?.output ?? "–"} out tokens · $${fmt(c.usd?.value, 4)} ${badge("ESTIMATE")}</p>
      ${pass == null ? "" : `<p class="pf-gate">${pass ? "Gates passed" : `Gate failed: ${fail}`} ${pass ? "" : `<button class="pf-i" data-explain="1" aria-label="Why did this fail?">i</button>`}</p>`}</div></li>`;
  }).join("");
  return `<p>Every call the Spark planner made in RUN2, in order: what it saw, what it chose, and what the gates said.</p><ol class="pf-trace">${rows}</ol>`;
}

function explainHtml(x) {
  if (!x) return "<p>No explanation recorded.</p>";
  return `${x.fallback ? `<p class="pf-fb">${esc(x.fallback)}</p>` : ""}${(x.items || []).map((i) => `<h4>${esc(i.setting)} · ${esc(i.check)}</h4><p>${esc(i.reason)} ${badge(i.label)}</p><p class="pf-meta">Most affected prompt class: <b>${esc(i.prompt_class)}</b> · source: ${esc(i.source)}</p>`).join("")}`;
}

function healthHtml(h) {
  if (!h?.entries) return "<p>No health check recorded.</p>";
  return `<table class="pf-t"><tr><th>Endpoint</th><th>Status</th><th>Latency</th><th>If it fails</th></tr>${h.entries.map((e) => `<tr><td>${esc(e.name)}</td><td class="${e.ok ? "ok" : "bad"}">${esc(e.ok ? "ok" : `FALLBACK · ${e.status}`)}</td><td>${e.latency_s == null ? "–" : `${fmt(e.latency_s, 2)} s`}</td><td>${esc(e.fallback || e.why || "–")}</td></tr>`).join("")}</table><p class="pf-meta">Checked ${esc((h.entries[0]?.checked_at || "").replace("T", " ").replace("Z", " UTC"))} ${badge(h.label)}. A failed endpoint is written to this ledger, never hidden.</p>`;
}

function stackHtml(s) {
  if (!s) return "<p>No stack row.</p>";
  const e = s.energy_saved_pct;
  return `<div class="pf-stack"><div><b>${fmt(e.tokens_only)}%</b><span>fewer tokens (${esc(s.trimmer)}) ${badge(s.tokens.label)}</span></div><div class="pf-x">×</div>
    <div><b>${fmt(e.caps_only)}%</b><span>less energy per token from the 350 W cap ${badge(s.caps.label)}</span></div><div class="pf-x">=</div>
    <div><b>${fmt(e.stacked)}%</b><span>less energy, stacked ${badge(e.label)}</span></div></div>
    <p class="pf-meta">${esc(s.how)}. ${esc(e.note || "")}. On normal tasks the stop rule changes tokens by ${fmt(s.normal_tasks?.tokens_change_pct)}% ${badge("MEASURED")}.</p>`;
}

function escHtml(x, illus) {
  const p = x?.preview;
  return `<p>A small Meta model (${esc(x.small)}) answers first. A reference-free check (cut off, no final answer, code that does not parse, refusal) sends only the failures to ${esc(x.big)}.</p>
    <p><b>Not measured yet.</b> ${esc(x.note)}.</p>
    ${p ? `<div class="pf-prev" data-illus="1"><p>Illustration only ${badge(p.label)}: escalation rate ${fmt(p.escalation_rate_pct)}%, small model passes on its own ${fmt(p.small_model_pass_pct)}%, net saving ${fmt(p.net_saved_pct)}%.</p></div>` : ""}`;
}

function verifyHtml(v) {
  return `<p>Anyone can check a receipt without trusting us:</p><pre class="pf-code">pip install -e . &amp;&amp; ${esc(v.command)}</pre>
    <table class="pf-t"><tr><th>Receipt</th>${(v.receipts[0]?.steps || []).map((s) => `<th>${esc(s.name)}</th>`).join("")}</tr>${v.receipts.map((r) => `<tr><td>${esc(r.file)}</td>${r.steps.map((s) => `<td class="${s.ok ? "ok" : "bad"}">${s.ok ? "✓" : s.name === "rekor" ? "not logged" : "✗"}</td>`).join("")}</tr>`).join("")}</table>
    <p class="pf-meta">Hash chain: ${v.chain_ok ? "intact ✓" : "broken ✗"} ${badge(v.label)}. These samples are signed offline; publishing to the public Rekor log is a one-time owner step, so the rekor column says "not logged" rather than pretending.</p>`;
}

export async function renderProof(el) {
  if (el.dataset.done) return;
  const d = await fetch("/api/dash/v2/judge-path").then((r) => (r.ok ? r.json() : null)).catch(() => null);
  if (!d) { el.innerHTML = `<p>The proof bundle is not available.</p>`; return; }
  el.dataset.done = "1";
  const r = d.run2;
  el.innerHTML = `<div class="pf">
    ${sec("The result, per correct answer", r ? `<p class="pf-big"><b>${fmt(r.j_per_correct.change_pct)}%</b> joules per correct answer at a ${r.cap_w} W cap (${fmt(r.j_per_correct.stock, 2)} → ${fmt(r.j_per_correct.cap, 2)} J), pass rate unchanged at ${fmt(r.pass_pct.cap)}% on ${r.checked} checked answers ${badge(r.label)}</p><p class="pf-meta">${esc(r.era)} · quality judged by ${esc(r.judge)}. Meta-judge results (${esc(d.meta_judge.judge)}) are reported separately: ${esc(d.meta_judge.note)}.</p>` : "", "pf-wide")}
    ${sec("What Spark decided, step by step", traceHtml(d.trace), "pf-wide")}
    ${sec("Small model first", escHtml(d.escalation))}
    ${sec("Stack: fewer tokens and cheaper tokens", stackHtml(d.stack))}
    ${sec("Endpoint health and fallbacks", healthHtml(d.health))}
    ${sec("Verify it yourself", verifyHtml(d.verify))}
  </div>`;
  el.querySelectorAll("[data-explain]").forEach((b) => (b.onclick = () => {
    document.querySelector("#sideBody").innerHTML = `<h2>Why the gate failed</h2>${explainHtml(d.explain)}`;
    document.querySelector("#side").classList.add("on"); document.querySelector("#side").setAttribute("aria-hidden", "false"); document.querySelector("#scrim").hidden = false;
  }));
}

// Illustration toggle: SIMULATED and MOCK content is hidden unless asked for.
const ILL = ".b-simulated, .b-mock, .n-simulated, .n-mock";
export function markIllus(root = document) {
  root.querySelectorAll(ILL).forEach((b) => {
    if (b.closest('[id*="h-labels"]')) return;
    const box = b.closest(".pf-prev, dd, li, tr, .kpi, .card, p") ;
    if (!box || box.dataset.illus) return;
    box.dataset.illus = "1";
    if (box.tagName === "DD" && box.previousElementSibling?.tagName === "DT") box.previousElementSibling.dataset.illus = "1";
  });
}
export function wireIllus(btn) {
  const set = (on) => { document.body.classList.toggle("illus", on); btn.setAttribute("aria-pressed", String(on)); btn.textContent = on ? "Illustrations: on" : "Illustrations: off"; };
  set(localStorage.getItem("wn-illus") === "1");
  btn.onclick = () => { const on = !document.body.classList.contains("illus"); localStorage.setItem("wn-illus", on ? "1" : "0"); set(on); };
  let q = false;
  new MutationObserver(() => { if (q) return; q = true; requestAnimationFrame(() => { q = false; markIllus(); }); }).observe(document.querySelector("main"), { childList: true, subtree: true });
  markIllus();
}
