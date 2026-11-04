// Proof tab: the judge path. Spark's own decisions, why a gate failed,
// which endpoints fell back, the stacked saving, and how to check a receipt.
import { esc, badge } from "./v2lib.js";

const fmt = (v, d = 1) => (v == null ? "–" : Number(v).toLocaleString("en-US", { maximumFractionDigits: d }));
// Each proof section is a collapsible glass card: only the title (and a short
// summary) shows until clicked. The open/close is handled by v2.js's shared
// .card>.hd click/keyboard delegation, so the heavy text stays behind a click.
const slug = (t) => t.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");
const sec = (title, body, cls = "", { open = false, sum = "" } = {}) =>
  `<div class="card glass pf-card ${cls} ${open ? "open" : ""}" data-card="pf-${slug(title)}">
    <div class="hd" tabindex="0" role="button" aria-expanded="${open}"><span class="ic">◆</span>
      <span class="t"><h3>${title}</h3>${sum ? `<small>${sum}</small>` : ""}</span><span class="chev">›</span></div>
    <div class="bd">${body}</div></div>`;

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
  return `<p>Every call the Spark planner made in a recorded H100 sweep, in order: what it saw, what it chose, and what the gates said.</p><ol class="pf-trace">${rows}</ol>`;
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
  return `<p>Anyone can check a receipt without trusting us. The signed receipts are published with this page: <a href="https://ak9381.github.io/wattnot/receipts/">ak9381.github.io/wattnot/receipts/</a>.</p><pre class="pf-code">pip install -e . &amp;&amp; ${esc(v.command)}</pre>
    <table class="pf-t"><tr><th>Receipt</th><th>Checks</th><th>Grade</th></tr>${v.receipts.map((r) => `<tr><td>${esc(r.file)}</td><td>${r.steps.map((s) => `<span class="${s.ok ? "ok" : "bad"}">${esc(s.name)} ${s.ok ? "✓" : s.name === "rekor" ? "not logged" : "✗"}</span>`).join(" · ")}</td><td>${esc(r.grade)}</td></tr>`).join("")}</table>
    <p class="pf-meta">Hash chain: ${v.chain_ok ? "intact ✓" : "broken ✗"} ${badge(v.label)}. Receipts are signed offline with the public test key, so grades stop at ESTIMATE; publishing to the public Rekor log is a one-time owner step, so the rekor column says "not logged" rather than pretending.</p>`;
}

function lbHtml(L) {
  if (!L) return "";
  const pct = (v) => (v == null ? "–" : `${v > 0 ? "+" : ""}${fmt(v, 1)}%`);
  const rows = L.rows.map((r) => `<tr class="${r.status === "pending" ? "pf-pend" : ""}${r.best ? " pf-best" : ""}">
    <td>${esc(r.setup)}${r.best ? " ★" : ""}<div class="pf-meta">${esc(r.hardware)}${r.note ? ` · ${esc(r.note)}` : ""}</div></td>
    <td>${r.j_per_correct ? `<b>${fmt(r.j_per_correct.mean, 2)}</b>${r.j_per_correct.sd != null ? ` ± ${fmt(r.j_per_correct.sd, 2)}` : ""}` : "pending"} ${badge(r.status === "pending" ? "" : r.label)}${r.status === "pending" ? ` <span class="pf-tag">${esc(r.label)} when run</span>` : ""}</td>
    <td>${pct(r.vs_default_pct)}</td><td>${r.pass_pct == null ? "–" : `${fmt(r.pass_pct, 1)}%`}</td><td>${pct(r.p99_change_pct)}</td>
    <td>${r.escalation_rate_pct == null ? "–" : `${fmt(r.escalation_rate_pct)}%`}</td><td>${r.small_model_pass_pct == null ? "–" : `${fmt(r.small_model_pass_pct)}%`}</td>
    <td>${esc(r.gate || "–")}</td></tr>`).join("");
  return `<p>${esc(L.metric)}. Lower is better. Mean ± sd over ${L.rows[0]?.repeats ?? 3} repeats, ${L.rows[0]?.n_checked ?? ""} checked answers each. RUN2 rows used fixed answer checks; Meta-judge rows are pending and kept apart.</p>
    <table class="pf-t pf-lb"><tr><th>Setup</th><th>J / correct</th><th>vs default</th><th>Pass</th><th>p99</th><th>Escalation</th><th>Small model pass</th><th>Gate</th></tr>${rows}</table>
    <p class="pf-meta">Reproduce: <code>${esc(L.reproduce)}</code> · method: <a href="https://github.com/ak9381/wattnot/blob/main/${esc(L.doc)}">${esc(L.doc)}</a> · submit: a JSON file like <a href="https://github.com/ak9381/wattnot/blob/main/data/leaderboard/submission.example.json">submission.example.json</a> plus a signed receipt, as a pull request.</p>`;
}

function metaHtml(m) {
  return `<table class="pf-t"><tr><th>Model</th><th>Job</th><th>Calls</th><th>Cost</th></tr>${(m || []).map((r) => `<tr><td>${esc(r.model)}</td><td>${esc(r.job)}</td><td>${r.calls ?? "–"}</td><td>${r.usd == null ? esc(r.source) : `$${fmt(r.usd, r.usd < 0.1 ? 3 : 2)} ${badge(r.label)}`}</td></tr>`).join("")}</table>`;
}

function receiptHtml(r) {
  if (!r) return "";
  return `<p>Spark trace ID <code>${esc(r.spark_trace_id)}</code>: the sha256 of the planner's event log, so a receipt points at the exact decisions above.</p>
    <table class="pf-t"><tr><th>Model</th><th>J per answer</th><th>J total</th></tr>${r.per_model_joules.map((x) => `<tr><td>${esc(x.model)}</td><td>${x.j_per_answer == null ? "not metered" : fmt(x.j_per_answer, 2)} ${badge(x.label === "PENDING" ? "" : x.label)}</td><td>${x.j_total == null ? "–" : fmt(x.j_total, 0)}</td></tr>`).join("")}</table>
    <p class="pf-meta">${esc(r.per_model_joules[0]?.how || "")}. Hosted planner calls report tokens, not joules, so their energy is not in the net figure yet.</p>`;
}

// Step-through of how the H100 run's receipt is proven, like the How it works diagram.
// Every value is read from the recorded proof files of the H100 run (proof-de155e).
const PF = "/replays/h100-stitched/proofs/p2-sweep/";
function flowSteps(r, sig, ver) {
  const st = r.steps.find((x) => x.cap_w === r.best_cap_w) || r.steps[0];
  const avg = (a) => a.reduce((x, y) => x + y, 0) / a.length;
  const js = avg(st.j_per_req_stock), jc = avg(st.j_per_req_cap), q = st.quality;
  const jcs = js / (q.stock_pct / 100), jcc = jc / (q.cap_pct / 100);
  const short = (h) => `${h.slice(0, 12)}…${h.slice(-6)}`;
  return [
    ["Prompts run without and with", `${r.workload.prompts} prompts (${esc(r.workload.name)}), ${esc(r.method.design)}: stock 700 W vs a ${st.cap_w} W cap on ${esc(r.hardware.gpu)}, model ${esc(r.model)}.`, "MEASURED"],
    ["NVML energy measured", `${fmt(js, 1)} → ${fmt(jc, 1)} J per request (${fmt(st.energy_change_pct.mean, 1)}%). Mean power ${fmt(st.mean_w.stock, 0)} → ${fmt(st.mean_w.cap, 0)} W. Method: ${esc(r.method.energy)}.`, "MEASURED"],
    ["Answers checked", `${q.checked} answers checked: pass rate ${fmt(q.stock_pct, 1)}% → ${fmt(q.cap_pct, 1)}%, ${q.passed ? "gate passed" : "gate failed"}. p99 ${st.p99_change_pct.mean > 0 ? "+" : ""}${fmt(st.p99_change_pct.mean, 1)}% (limit +10%).`, "MEASURED"],
    ["Joules per correct answer", `${fmt(jcs, 1)} → ${fmt(jcc, 1)} J per correct answer (${fmt((jcc / jcs - 1) * 100, 1)}%). J per request divided by the pass rate.`, "MEASURED"],
    ["Receipt hashed and signed", `Canonical sha256 <code>${esc(short(sig.sha256))}</code>, signed with Ed25519, key id <code>${esc(sig.key_id)}</code> (offline key). Receipt ${esc(r.receipt_id)}, created ${esc(r.created)}.`, "MEASURED"],
    ["Signature verified", ver ? `Hash recomputed ${ver.hash_match ? "matches ✓" : "does not match ✗"}, signature ${ver.signature_ok ? "valid ✓" : "invalid ✗"} (${esc(ver.message)}). Checked ${esc(ver.checked_at)} with ${esc(ver.how)}.` : "No verification record saved for this receipt.", ver ? "MEASURED" : ""],
    ["Public transparency log (Rekor)", sig.rekor ? `Logged in Rekor: ${esc(JSON.stringify(sig.rekor))}` : "Not logged. This receipt is signed offline; publishing to the public Rekor log is a one-time owner step that has not been done.", ""],
  ];
}
function flowHtml(steps) {
  return `<div class="pf-flow" data-i="0"><ol class="pf-fl-blocks">${steps.map(([t], i) => `<li><button class="pf-fl-b${i ? "" : " on"}" data-i="${i}"><span class="pf-fl-n">${i + 1}</span>${esc(t)}</button></li>`).join("")}</ol>
    <div class="pf-fl-body" aria-live="polite"></div>
    <div class="pf-fl-nav"><button class="btn" data-step="-1">← Prev</button><span class="pf-fl-pos"></span><button class="btn" data-step="1">Next →</button></div></div>`;
}
function wireFlow(root, steps) {
  const f = root.querySelector(".pf-flow"); if (!f) return;
  const show = (i) => {
    i = Math.max(0, Math.min(steps.length - 1, i)); f.dataset.i = i;
    f.querySelectorAll(".pf-fl-b").forEach((b, k) => { b.classList.toggle("on", k === i); b.classList.toggle("done", k < i); });
    const [t, body, lab] = steps[i];
    f.querySelector(".pf-fl-body").innerHTML = `<h4>${i + 1}. ${esc(t)} ${lab ? badge(lab) : ""}</h4><p>${body}</p><p class="pf-meta">Source: H100 run, proof-de155e (2026-11-03).</p>`;
    f.querySelector(".pf-fl-pos").textContent = `${i + 1} of ${steps.length}`;
  };
  f.querySelectorAll(".pf-fl-b").forEach((b) => (b.onclick = () => show(+b.dataset.i)));
  f.querySelectorAll("[data-step]").forEach((b) => (b.onclick = () => show(+f.dataset.i + +b.dataset.step)));
  show(0);
}
const FLOW_CSS = `.pf-flow{margin:0 0 18px;padding:16px;border-radius:14px;background:var(--v-bg2,#1F1A2F);border:1px solid var(--v-line,rgba(255,255,255,.14))}
.pf-fl-blocks{list-style:none;margin:0;padding:0;display:flex;flex-wrap:wrap;gap:8px}
.pf-fl-b{display:flex;gap:8px;align-items:center;font:600 13px/1.3 var(--v-body,inherit);color:var(--v-mut,#CBC4D6);background:var(--v-surf-17,#171724);border:1px solid var(--v-line,rgba(255,255,255,.14));border-radius:10px;padding:8px 12px;cursor:pointer;opacity:.6}
.pf-fl-b.done{opacity:.85}.pf-fl-b.on{opacity:1;color:var(--v-cream,#fff);border-color:var(--v-peach,#B2A6C3);box-shadow:0 0 12px rgba(178,166,195,.45)}
.pf-fl-n{display:inline-grid;place-items:center;width:20px;height:20px;border-radius:50%;background:var(--v-violet,#3B3A54);font-size:11px}
.pf-fl-body{margin:14px 0 8px;min-height:90px}.pf-fl-nav{display:flex;gap:10px;align-items:center}.pf-fl-pos{color:var(--v-mut,#aaa);font-size:13px}`;
const noLlama = (x) => !/llama/i.test(JSON.stringify(x ?? ""));

export async function renderProof(el) {
  if (el.dataset.done) return;
  const J = (u) => fetch(u).then((r) => (r.ok ? r.json() : null)).catch(() => null);
  const [d, pr, sig, ver] = await Promise.all([J("/api/dash/v2/judge-path"), J(PF + "proof-de155e.json"), J(PF + "proof-de155e.sigstore.json"), J(PF + "verify-de155e.json")]);
  if (!d && !pr) { el.innerHTML = `<p>The proof bundle is not available.</p>`; return; }
  el.dataset.done = "1";
  if (!document.getElementById("pf-flow-css")) { const c = document.createElement("style"); c.id = "pf-flow-css"; c.textContent = FLOW_CSS; document.head.append(c); }
  const steps = pr && sig ? flowSteps(pr, sig, ver) : null;
  const st = pr ? (pr.steps.find((x) => x.cap_w === pr.best_cap_w) || pr.steps[0]) : null;
  const result = st ? `<p class="pf-big"><b>${fmt(st.energy_change_pct.mean, 1)}%</b> energy per request at a ${st.cap_w} W cap on the H100 run, pass rate ${fmt(st.quality.stock_pct, 1)}% → ${fmt(st.quality.cap_pct, 1)}% on ${st.quality.checked} checked answers ${badge("MEASURED")}</p><p class="pf-meta">Model ${esc(pr.model)} · receipt ${esc(pr.receipt_id)} · 2026-11-03.</p>` : "<p>No H100 proof receipt found.</p>";
  const health = d?.health ? { ...d.health, entries: (d.health.entries || []).filter(noLlama) } : null;
  const meta = (d?.leaderboard?.meta_models || []).filter(noLlama);
  el.innerHTML = `<div class="pf">
    ${steps ? `<div class="pf-wide" style="grid-column:1/-1"><h3 style="margin:0 0 10px">How this receipt is proven, step by step</h3>${flowHtml(steps)}</div>` : ""}
    <div class="ctrl noprint" style="grid-column:1/-1"><button class="btn" data-all="open">Expand all</button><button class="btn" data-all="close">Collapse all</button><span style="color:var(--mu);font-size:13px">Each proof below opens on click. Start with the result.</span></div>
    ${sec("The result, H100 run", result, "", { open: true, sum: st ? `${fmt(st.energy_change_pct.mean, 1)}% energy at ${st.cap_w} W` : "" })}
    ${d?.trace && noLlama(d.trace) ? sec("What Spark decided, step by step", traceHtml(d.trace), "pf-wide") : ""}
    ${d?.verify ? sec("Verify it yourself", verifyHtml(d.verify)) : ""}
    ${health ? sec("Endpoint health and fallbacks", healthHtml(health)) : ""}
    ${meta.length ? sec("Meta models used", metaHtml(meta)) : ""}
  </div>`;
  if (steps) wireFlow(el, steps);
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
  if (!btn) return;
  const set = (on) => { document.body.classList.toggle("illus", on); btn.setAttribute("aria-pressed", String(on)); btn.textContent = on ? "Illustrations: on" : "Illustrations: off"; };
  set(localStorage.getItem("wn-illus") === "1");
  btn.onclick = () => { const on = !document.body.classList.contains("illus"); localStorage.setItem("wn-illus", on ? "1" : "0"); set(on); };
  let q = false;
  new MutationObserver(() => { if (q) return; q = true; requestAnimationFrame(() => { q = false; markIllus(); }); }).observe(document.querySelector("main"), { childList: true, subtree: true });
  markIllus();
}
