import { RUN_NAMES as NM, REPLAY_IDS, DEFAULT_RUN } from "./runset.js";
// Header run picker: one selected run shared by every page.
// Order of truth: ?run= in the URL, then localStorage, then the newest run.
// Every same-site link is rewritten to carry ?run=, so the pick survives navigation and refresh.
const KEY = "wn.run";
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

export function getRun() {
  const st = localStorage.getItem(KEY);
  return new URL(location.href).searchParams.get("run") || (st && REPLAY_IDS.includes(st.replace(/^replay:/, "")) ? st : null);
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
  if (r.kind === "recorded") {
    // Short enough to read in full in the side panel: "Plugin H100 · Nov 4".
    const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(d);
    const md = m ? `${["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"][+m[2] - 1]} ${+m[3]}` : "";
    return [r.name || r.id, md].filter(Boolean).join(" · ");
  }
  return [r.name || r.id, r.stage, d, r.label].filter(Boolean).join(" · ");
}

// Which run the picker starts on: the asked-for one when this server lists it, the newest only
// when nothing was asked for. An asked-for run that is not listed comes back as missing.
export function pickInitial(asked, runs, latest) {
  if (!asked) return { id: latest || null, missing: false };
  return runs.some((r) => r.id === asked) ? { id: asked, missing: false } : { id: asked, missing: true };
}

async function list() {
  const [a, b, c, h] = await Promise.all([
    fetch("/api/launch/runs").then((x) => x.json()).catch(() => ({ runs: [] })),
    fetch("/api/dash/v2/runs").then((x) => x.json()).catch(() => ({ runs: [] })),
    fetch("/api/dash/replays").then((x) => x.json()).catch(() => ({ replays: [] })),
    fetch("/api/dash/health").then((x) => x.json()).catch(() => ({})),
  ]);
  // The recorded runs under replays/ come first: they are the ones with full data (graph, receipt, EU report).
  const rank = (id) => REPLAY_IDS.indexOf(id);
  const recorded = (c.replays || []).filter((r) => NM[r.id]).slice().sort((x, y) => rank(x.id) - rank(y.id)).map((r) => ({ id: `replay:${r.id}`, name: NM[r.id], stage: "recorded", date: r.date, label: r.measured ? "MEASURED" : "REPLAY", kind: "recorded", group: r.id.startsWith("plugin-") ? "Plugin runs (Nov 4)" : "Test runs (Nov 2-3)" }));
  const replays = [].map((r) => ({ id: r.id, name: r.name, stage: "replay", date: r.date, label: "REPLAY", kind: "replay" }));
  // With nothing asked for, start on the recorded run this server was started with (./start.sh,
  // ./run-local.sh replay <id>), not the newest dashboard launch job, which may hold no data.
  const served = h.replay_id ? `replay:${h.replay_id}` : null;
  const stitched = recorded.find((r) => r.id === `replay:${DEFAULT_RUN}`)?.id;
  const latest = stitched || (served && recorded.some((r) => r.id === served) ? served : null) || (h.replay_only ? recorded[0]?.id : null) || a.latest || recorded[0]?.id || replays[0]?.id || null;
  // Exactly six runs are listed (runset.js); the rest stay on disk.
  return { runs: recorded.length ? recorded : [...(a.runs || []), ...replays], latest };
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
  const { id, missing } = pickInitial(getRun(), runs, latest);
  const sel = box.querySelector("select");
  // A run named in the URL or saved earlier that this server does not list (e.g. ?run=baseten:r04
  // on a server that only has Modal rounds) stays named and flagged, never silently swapped for
  // the newest run: the page must not show a different run's numbers under the asked-for name.
  const opts = missing ? [{ id, name: `${id} (not on this server)`, missing: true }, ...runs] : runs;
  sel.innerHTML = opts.map((r) => `<option value="${esc(r.id)}" ${r.id === id ? "selected" : ""}>${esc(r.missing ? r.name : optionLabel(r))}</option>`).join("");
  window.__wnRuns = runs;
  if (missing) { box.classList.add("wn-runpick-missing"); box.title = `Run ${id} is not on this server. Pick another run.`; }
  else if (id) setRun(id, { reload: false });
  sel.addEventListener("change", () => setRun(sel.value));
  hideOnCfg(); addEventListener("popstate", hideOnCfg);
  new MutationObserver(() => { rewriteLinks(document, getRun()); hideOnCfg(); }).observe(document.body, { childList: true, subtree: true });
}

if (!globalThis.__WN_TEST__) {
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", () => setTimeout(mountPicker, 0));
  else setTimeout(mountPicker, 0);
}
