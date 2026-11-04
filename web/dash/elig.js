// Shared eligibility: one check (POST /api/launch/eligibility) for the configuration saved on the
// Configure screen. Every page greys what this configuration cannot run, with the reason on hover,
// and gets a replay toggle between the without-WattNot and with-WattNot arms.
const KEY = "wn.config", ARM = "wn.arm";
export const DEFAULT = { provider: "baseten" };
// The selected recorded run decides the provider, so the bar never shows another run's setup.
export const REPLAY_PROVIDER = (id) => (!id ? null : id.startsWith("modal") ? "modal_a100" : id.startsWith("baseten") ? "baseten" : id.startsWith("h100") ? "e2e_h100" : null);
function replayId() {
  const v = new URLSearchParams(location.search).get("run") || localStorage.getItem("wn.run") || "";
  return v.startsWith("replay:") ? v.slice(7) : null;
}
export function loadConfig() {
  const q = new URLSearchParams(location.search).get("provider") || REPLAY_PROVIDER(replayId());
  try { const c = JSON.parse(localStorage.getItem(KEY) || "null"); return q ? { ...(c || {}), provider: q } : c || { ...DEFAULT }; }
  catch { return { ...DEFAULT }; }
}
export function saveConfig(c) { localStorage.setItem(KEY, JSON.stringify(c)); }
export function getArm() {
  const q = new URLSearchParams(location.search).get("arm");
  if (q === "off") return "without_wattnot"; if (q === "on") return "with_wattnot";
  return localStorage.getItem(ARM) || "with_wattnot";
}
export function setArm(a) {
  localStorage.setItem(ARM, a); document.body.dataset.arm = a;
  window.dispatchEvent(new CustomEvent("wn:arm", { detail: a }));
}
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
export const lab = (l) => (l ? `<span class="wn-lab ${esc(l)}">${esc(l)}</span>` : "");

// Meta strip ids (metastrip.js / archpage) -> eligibility model ids
export const MODEL_ALIAS = { glimmer: "glimmer-30b", sam: "sam-3.1", voice: "muse-voice", code: "muse-code", open: "spark-open", image: "muse-image" };

// Compare-tree and graph node ids -> runner feature ids, so those nodes grey out with the same verdict.
export const NODE_FEATURE = { waste: "waste_guard", loopstop: "waste_guard", bt_waste: "waste_guard", md_waste: "waste_guard",
  smallfirst: "router", bt_routing: "router", md_routing: "router", context: "context_saver", memcomp: "context_saver", dupcut: "context_saver",
  prefix: "vllm_prefix", prefixcache: "vllm_prefix", caps: "power_caps", capsweep: "power_caps", clocks: "clock_sidecar",
  think: "thinking_budget", quant: "fp8", gate: "gates", bt_gate: "gates", md_gate: "gates", q: "spark_grades", speed: "joule_slo",
  budget: "spend_guard", sam: "sam_hotspot" };
export function applyGrey(root, elig) {
  if (!elig) return 0;
  const models = Object.fromEntries(elig.models.map((m) => [m.id, m]));
  const feats = Object.fromEntries(elig.runner.map((r) => [r.id, r]));
  const rows = Object.fromEntries(elig.features.map((f) => [String(f.n), f]));
  let n = 0;
  const mark = (el, ok, why) => {
    el.classList.toggle("wn-inel", !ok);
    if (!ok) { el.title = `Ineligible for ${elig.provider_label}: ${why}`; el.setAttribute("aria-disabled", "true"); n++; }
    else if (el.title.startsWith("Ineligible")) { el.removeAttribute("title"); el.removeAttribute("aria-disabled"); }
  };
  root.querySelectorAll("[data-model]").forEach((el) => {
    const m = models[el.dataset.model] || models[MODEL_ALIAS[el.dataset.model]]; if (m) mark(el, m.eligible, m.reason);
  });
  root.querySelectorAll("[data-feature]").forEach((el) => { const f = feats[el.dataset.feature]; if (f) mark(el, f.eligible, f.reason); });
  root.querySelectorAll("[data-node]").forEach((el) => { const f = feats[NODE_FEATURE[el.dataset.node]]; if (f) mark(el, f.eligible, f.reason); });
  root.querySelectorAll("[data-feature-row]").forEach((el) => { const f = rows[el.dataset.featureRow]; if (f) mark(el, f.eligible, f.reason); });
  return n;
}

export async function fetchElig(config, f = fetch) {
  const r = await f("/api/launch/eligibility", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ config }) });
  if (!r.ok) throw new Error(`eligibility ${r.status}`);
  return r.json();
}

function bar(elig, arm) {
  const el = document.createElement("div");
  el.className = "wn-elig"; el.setAttribute("role", "region"); el.setAttribute("aria-label", "Run configuration");
  const rid = replayId();
  el.innerHTML = `<span>${rid ? `Run <b>${esc(rid)}</b> ${lab("REPLAY")} · ` : ""}Configuration <b>${esc(elig.provider_label)}</b> · main model <b>${esc(elig.model)}</b> · energy ${lab(elig.energy_label)}</span>
    <span><b>${elig.counts.eligible}</b> eligible · <b>${elig.counts.ineligible}</b> ineligible of ${elig.counts.total} features</span>
    <span class="seg" role="group" aria-label="Replay arm">
      <button data-arm="without_wattnot" aria-pressed="${arm === "without_wattnot"}">Without WattNot</button>
      <button data-arm="with_wattnot" aria-pressed="${arm === "with_wattnot"}">With WattNot</button></span>
    <a href="v2.html#pCfg">Change configuration</a>`;
  el.querySelectorAll("[data-arm]").forEach((b) => b.addEventListener("click", () => {
    el.querySelectorAll("[data-arm]").forEach((x) => x.setAttribute("aria-pressed", String(x === b)));
    setArm(b.dataset.arm);
    if (location.pathname.endsWith("graph.html")) { const u = new URL(location.href); u.searchParams.set("arm", b.dataset.arm === "with_wattnot" ? "on" : "off"); location.href = u.toString(); }
  }));
  return el;
}

export async function mount() {
  if (document.querySelector(".wn-elig") || document.body.dataset.noElig != null) return;
  let elig;
  try { elig = await fetchElig(loadConfig()); } catch { return; } // static site: nothing to grey
  window.__wnElig = elig;
  const arm = getArm(); document.body.dataset.arm = arm;
  const nav = document.querySelector("nav.wn-nav");
  const b = bar(elig, arm);
  if (nav && nav.parentNode) nav.after(b); else document.body.prepend(b);
  const run = () => applyGrey(document, elig);
  run(); new MutationObserver(() => { clearTimeout(mount._t); mount._t = setTimeout(run, 120); }).observe(document.body, { childList: true, subtree: true });
  window.dispatchEvent(new CustomEvent("wn:eligibility", { detail: elig }));
}
if (typeof window !== "undefined" && typeof document !== "undefined" && !globalThis.__WN_TEST__) {
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", () => setTimeout(mount, 50)); else setTimeout(mount, 50);
}
