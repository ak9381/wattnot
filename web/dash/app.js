import { esc, pill } from "./theme.js";
import { Product } from "./product.js";
import { ArchPage } from "./archpage.js";
import { panelHtml } from "./arch.js";

const $ = (id) => document.getElementById(id);
const params = new URLSearchParams(location.search);

// The Experts and Power/Room tabs were merged into the Architecture page.
// Old URLs (?tab=experts, ?tab=power, ?tab=room, and hash routes like
// #experts, #power, #room) redirect here: Architecture with the matching
// drill-down open (expert details, or the rack map).
const LEGACY_TABS = {
  experts: { tab: "arch", expert: true },
  expert: { tab: "arch", expert: true },
  power: { tab: "arch", room: true },
  room: { tab: "arch", room: true },
  rooms: { tab: "arch", room: true },
  arch: { tab: "arch" },
  architecture: { tab: "arch" },
};
function legacyTarget() {
  const q = new URLSearchParams(location.search);
  const tab = (q.get("tab") || "").toLowerCase();
  const h = (location.hash || "").toLowerCase().replace(/^#\/?/, "");
  const hm = h.match(/(?:^|[?&#;])tab=([a-z]+)/) || h.match(/^([a-z]+)/);
  const key = tab || (hm && hm[1]) || "";
  const base = LEGACY_TABS[key] || { tab: "arch" };
  return {
    ...base,
    expert: base.expert || q.get("expert") === "1",
    room: base.room ? "sam" : q.get("room") || null,
    filter: q.get("filter") || null,
    legacy: !!LEGACY_TABS[key] || !!location.hash,
  };
}
function normalizeUrl(t) {
  const q = new URLSearchParams();
  q.set("tab", "arch");
  if (t.expert) q.set("expert", "1");
  if (t.room) q.set("room", "sam");
  if (t.filter) q.set("filter", t.filter);
  history.replaceState(null, "", `?${q}${location.hash ? "" : ""}`);
  if (location.hash) history.replaceState(null, "", `?${q}`);
}

// ---- tabs: Architecture is the only page now
let archPage = null, healthData = {};
function showTab() {
  document.querySelectorAll(".tabs button").forEach((b) => b.classList.toggle("on", b.dataset.tab === "arch"));
  $("arch").classList.add("on");
  ensureArch().then(() => archPage?.show());
}
function ensureArch() {
  if (archPage || !bundle) return Promise.resolve();
  archPage = new ArchPage($("arch"), bundle, { openDrawer, health: healthData });
  return Promise.resolve();
}
document.querySelectorAll(".tabs button").forEach((b) => (b.onclick = () => openDrawer("arch")));

// ---- drawer (the Architecture deep dive)
let drawerOpen = false, opener = null;
function openDrawer(tab, o = {}) {
  const t = typeof tab === "string" ? { ...(LEGACY_TABS[tab.toLowerCase()] || { tab: "arch" }), ...o } : { tab: "arch", ...o };
  const d = $("drawer"); opener = document.activeElement; closePanel();
  showTab();
  // legacy ?tab=power/experts/room (or hash routes) land here with the drill-down open
  if (t.filter) ensureArch().then(() => archPage?.setFilter(t.filter));
  if (t.expert) ensureArch().then(() => archPage?.setExpert(true));
  if (t.room) ensureArch().then(() => archPage?.openRoom());
  if (!drawerOpen) {
    drawerOpen = true; d.classList.add("open");
    if (window.gsap && !matchMedia("(prefers-reduced-motion: reduce)").matches && !o.instant) window.gsap.fromTo(d, { yPercent: 100, y: 0 }, { yPercent: 100 - 100, y: 0, duration: 0.45, ease: "power3.out", onComplete: () => { archPage?.show(); } });
    else { d.style.transform = "none"; }
  }
  normalizeUrl(t);
  $("dClose").focus({ preventScroll: true });
}
function closeDrawer() {
  if (!drawerOpen) return; const d = $("drawer"); drawerOpen = false; archPage?.closePanel();
  const done = () => { d.classList.remove("open"); d.style.transform = ""; opener?.focus?.({ preventScroll: true }); };
  if (window.gsap && !matchMedia("(prefers-reduced-motion: reduce)").matches) window.gsap.to(d, { yPercent: 100, duration: 0.35, ease: "power2.in", onComplete: () => { done(); window.gsap.set(d, { clearProps: "transform" }); } }); else done();
  history.replaceState(null, "", location.pathname);
}
$("dClose").onclick = closeDrawer;
$("dArch").onclick = () => openDrawer("arch");
document.querySelectorAll(".tabs button").forEach((b) => (b.onclick = () => openDrawer("arch")));

// ---- side panel on the run screens
function closePanel() { $("spanel").classList.remove("open"); }
function openPanel(n, s, view, extra = "") {
  const h = panelHtml(view, n, s);
  $("spbody").innerHTML = extra ? h.replace(/(<h3>.*?<\/h3>)/, `$1${extra}`) : h;
  $("spanel").classList.add("open"); $("spx").focus({ preventScroll: true }); $("spanel")._ctx = { n, view, extra };
}
$("spx").onclick = closePanel;
$("spanel").addEventListener("click", (e) => {
  const go = e.target.closest("[data-go]"); if (go) { closePanel(); openArchGo(go.dataset.go); return; }
  const sb = e.target.closest("a[data-sub]"); if (sb) { e.preventDefault(); const c = $("spanel")._ctx; openPanel(c.n, c.n.subs.find((x) => x.id === sb.dataset.sub), c.view, product?.runBlock(c.n, c.n.subs.find((x) => x.id === sb.dataset.sub)) || ""); }
});
// Card links from the merged tabs: experts -> the Expert toggle, power/room -> the rack map.
function openArchGo(go) {
  if (go === "meta") return openDrawer("arch", { filter: "meta" });
  if (go === "experts") return openDrawer("arch", { expert: true });
  if (go === "power" || go === "room") return openDrawer("arch", { room: true });
  return openDrawer("arch");
}

// ---- tooltips (water and cost factors, assumptions)
const tt = $("tt");
document.addEventListener("mouseover", (e) => { const t = e.target.closest("[data-tt]"); if (!t) return; tt.innerHTML = t.dataset.tt; tt.hidden = false; const r = t.getBoundingClientRect(); tt.style.left = `${Math.min(innerWidth - 440, Math.max(8, r.left))}px`; tt.style.top = `${Math.max(8, r.top - tt.offsetHeight - 8)}px`; if (r.top - tt.offsetHeight - 8 < 8) tt.style.top = `${r.bottom + 8}px`; });
document.addEventListener("mouseout", (e) => { if (e.target.closest("[data-tt]")) tt.hidden = true; });
document.addEventListener("focusin", (e) => { const t = e.target.closest("[data-tt]"); if (t) t.dispatchEvent(new MouseEvent("mouseover", { bubbles: true })); });

// ---- health chips
const chipSet = (el, cls, text) => { el.classList.remove("ok", "warn", "bad"); if (cls) el.classList.add(cls); el.querySelector("span").textContent = text; };
async function loadHealth() {
  try {
    const h = await fetch("/api/dash/health").then((r) => r.json());
    chipSet($("modeChip"), h.replay_only ? "warn" : h.mode === "live" ? "ok" : "", h.replay_only ? "REPLAY · saved run · no GPU, no live Meta calls" : h.mode === "live" ? "Live run" : `Replay of recorded runs${h.gpu_attached ? "" : " · no GPU attached"}`);
    document.body.classList.toggle("replay-only", !!h.replay_only);
    $("modeChip").title = h.replay_only ? "Hosted replay for judges: everything is read from a saved run. No GPU is used and no Meta or SAM call is made." : h.mode === "live" ? "Following a run that is writing events now" : "Every number is read from a recorded run. Nothing here needs a GPU or a network connection.";
    $("signout").hidden = !h.auth_required; return h;
  } catch { chipSet($("modeChip"), "bad", "Server not reachable"); return null; }
}
const META_TEXT = { replay_only: ["", "Meta API: not called (hosted replay)"], ok: ["ok", "Meta API: connected"], key_missing: ["warn", "Meta API: reachable, no key"], key_rejected: ["bad", "Meta API: key rejected"], blocked: ["bad", "Meta API: blocked from here"], unreachable: ["bad", "Meta API: unreachable"], degraded: ["warn", "Meta API: degraded"], error: ["bad", "Meta API: check failed"] };
async function checkMeta(fresh) {
  const chip = $("metaChip"); chipSet(chip, "", "Meta API: checking…");
  let r; try { r = await fetch(`/api/dash/meta/health${fresh ? "?fresh=1" : ""}`).then((x) => { if (!x.ok) throw new Error(x.status); return x.json(); }); }
  catch (e) { r = { state: "error", steps: [], advice: "The health check could not be reached. The story still works from recorded runs.", spends_money: false }; }
  const [cls, text] = META_TEXT[r.state] || ["warn", `Meta API: ${r.state}`]; chipSet(chip, cls, text); renderPop(r); return r;
}
function renderPop(r) {
  $("pop").innerHTML = `<h4>Meta API connection</h4><ul>${(r.steps || []).map((s) => `<li class="${s.ok ? "" : "no"}"><b>${s.ok ? "ok" : "no"}</b><span>${esc(s.text)}</span></li>`).join("") || "<li><span>No steps ran.</span></li>"}</ul><p>${esc(r.advice || "")}</p><p>This check makes no model call and costs nothing. Replay mode needs no connection.</p><div class="go" style="display:flex;gap:8px;margin-top:8px"><button class="btn" id="popAgain">Check again</button><button class="btn" id="popClose">Close</button></div>`;
  $("popAgain").onclick = () => checkMeta(true); $("popClose").onclick = () => togglePop(false);
}
function togglePop(on) { $("pop").hidden = !on; $("metaChip").setAttribute("aria-expanded", on); if (on) $("popClose")?.focus(); }
$("metaChip").onclick = () => togglePop($("pop").hidden);

// ---- start the product flow
let product = null, bundle = null;
async function boot() {
  const stage = $("stage"); stage.innerHTML = "";
  const [health, sb] = await Promise.all([loadHealth(), fetch("/api/dash/story").then((r) => { if (!r.ok) throw new Error(r.status === 404 ? "The run data has not been built yet: run scripts/build_dash_arch.py." : `HTTP ${r.status}`); return r.json(); }).catch((e) => ({ error: e.message }))]);
  checkMeta(false);
  if (sb.error || !sb.config || !sb.config.models) {
    stage.innerHTML = `<div class="pf"><div class="err"><b>The run configuration could not load.</b><p>${esc(sb.error || "The bundle has no recorded run in it. Rebuild it with scripts/build_dash_arch.py.")}</p><button class="btn" id="retry">Try again</button> <button class="btn" id="goArch">Open Architecture instead</button></div></div>`;
    $("retry").onclick = boot; $("goArch").onclick = () => openDrawer("arch"); return;
  }
  bundle = sb; healthData = health;
  product = new Product(stage, $("rail"), { bundle: sb, health, open: openPanel, drawer: openDrawer });
  // Legacy tab URLs and hash routes open Architecture with the matching drill-down.
  const t = legacyTarget();
  if (t.legacy || params.get("tab") || t.expert || t.room || t.filter) openDrawer("arch", { ...t, instant: true });
  else if (params.get("filter") === "meta") ensureArch().then(() => archPage?.setFilter("meta"));
}
window.addEventListener("keydown", (e) => {
  if (e.key === "Escape") { if (!$("pop").hidden) return togglePop(false); if ($("spanel").classList.contains("open")) return closePanel(); if (drawerOpen) { if ($("apanel")?.classList.contains("open")) return archPage.closePanel(); return closeDrawer(); } return; }
});
window.__app = {};
boot();
