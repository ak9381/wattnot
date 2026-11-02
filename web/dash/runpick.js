// Header run picker: one selected run shared by every page.
// Order of truth: ?run= in the URL, then localStorage, then the newest run.
// Every same-site link is rewritten to carry ?run=, so the pick survives navigation and refresh.
const KEY = "wn.run";
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

export function getRun() {
  return new URL(location.href).searchParams.get("run") || localStorage.getItem(KEY) || null;
}

export function withRun(href, id = getRun()) {
  if (!id || !href) return href;
  try {
    const u = new URL(href, location.href);
    if (u.origin !== location.origin || !/\.html$|\/$/.test(u.pathname)) return href;
    u.searchParams.set("run", id);
    return u.pathname.split("/").pop() + u.search + u.hash;
  } catch { return href; }
}

export function rewriteLinks(root = document, id = getRun()) {
  root.querySelectorAll("a[href]").forEach((a) => {
    const h = a.getAttribute("href");
    if (!h || h.startsWith("#") || h.startsWith("/api/") || /^(mailto|https?):/.test(h) && !h.startsWith(location.origin)) return;
    const n = withRun(h, id); if (n !== h) a.setAttribute("href", n);
  });
}

export function optionLabel(r) {
  const d = (r.date || "").slice(0, 10);
  return [r.name || r.id, r.stage, d, r.label].filter(Boolean).join(" · ");
}

async function list() {
  const [a, b] = await Promise.all([
    fetch("/api/launch/runs").then((x) => x.json()).catch(() => ({ runs: [] })),
    fetch("/api/dash/v2/runs").then((x) => x.json()).catch(() => ({ runs: [] })),
  ]);
  const replays = (b.runs || []).map((r) => ({ id: r.id, name: r.name, stage: "replay", date: r.date, label: "REPLAY", kind: "replay" }));
  return { runs: [...(a.runs || []), ...replays], latest: a.latest || replays[0]?.id || null };
}

export function setRun(id, { reload = true } = {}) {
  localStorage.setItem(KEY, id);
  const u = new URL(location.href); u.searchParams.set("run", id);
  history.replaceState(null, "", u.pathname + u.search + u.hash);
  rewriteLinks(document, id);
  window.__wnRun = id;
  window.dispatchEvent(new CustomEvent("wn:run", { detail: { id } }));
  if (reload) location.reload();
}

export async function mountPicker() {
  const wrap = document.getElementById("wnSideRun") || document.querySelector("nav.wn-nav .wn-nav-inner");
  if (!wrap || document.getElementById("wnRunPick")) return;
  const box = document.createElement("label");
  box.className = "wn-runpick"; box.id = "wnRunPickBox";
  box.innerHTML = `<span>Run</span><select id="wnRunPick" aria-label="Pick the run every page shows"><option>Loading…</option></select>`;
  wrap.insertBefore(box, wrap.querySelector(".wn-links"));
  box.querySelector("span").textContent = "Showing run";
  const hideOnCfg = () => { const c = document.getElementById("pCfg"); box.hidden = /configure\.html$/.test(location.pathname) || location.hash === "#pCfg" || !!(c && c.offsetParent); };
  hideOnCfg(); addEventListener("hashchange", hideOnCfg);
  const { runs, latest } = await list();
  let id = getRun();
  if (!id || !runs.some((r) => r.id === id)) id = latest;
  const sel = box.querySelector("select");
  sel.innerHTML = runs.map((r) => `<option value="${esc(r.id)}" ${r.id === id ? "selected" : ""}>${esc(optionLabel(r))}</option>`).join("");
  window.__wnRuns = runs;
  if (id) setRun(id, { reload: false });
  sel.addEventListener("change", () => setRun(sel.value));
  hideOnCfg(); addEventListener("popstate", hideOnCfg);
  new MutationObserver(() => { rewriteLinks(document, getRun()); hideOnCfg(); }).observe(document.body, { childList: true, subtree: true });
}

if (!globalThis.__WN_TEST__) {
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", () => setTimeout(mountPicker, 0));
  else setTimeout(mountPicker, 0);
}
