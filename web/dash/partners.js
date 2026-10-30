// Who is behind each step and each call. Pure formatting (no DOM at import
// time, so node can test it) plus two small loaders for the dashboard.
import { esc, dot, numLab } from "./theme.js";

const PCOL = {
  meta: "var(--m)",
  muse_image: "var(--m)",
  muse_voice: "var(--m)",
  baseten: "var(--bt)",
  e2e: "var(--e2)",
  huggingface: "var(--hf)",
  nvidia: "var(--nv)",
  sigstore: "var(--sg)",
  operator: "var(--cy)",
};

// The static facts, mirroring pipeline._powered_by: what a node runs on when
// the run's own data says nothing more specific.
const STATIC_PB = {
  room: [{ id: "meta", name: "Meta SAM 3.1", what: "finds the racks" }],
  baseline: [{ id: "e2e", name: "E2E H100", what: "the GPU it runs on" }],
  sweep: [
    { id: "e2e", name: "E2E H100", what: "the GPU it runs on" },
    { id: "planner", name: "planner", what: "proposes the next cap" },
  ],
  topk: [{ id: "modal", name: "Modal 8x B200", what: "where the pending Spark top-k test runs" }],
  keep: [
    { id: "e2e", name: "E2E H100", what: "the GPU it runs on" },
    { id: "muse_image", name: "Meta Muse Image", what: "generates the SYNTHETIC eye-test images the vision gate checks" },
    { id: "muse_voice", name: "Meta Muse Voice", what: "transcribes the SYNTHETIC spoken-answer questions the watts-per-spoken-answer gate checks" },
  ],
  waste: [{ id: "meta", name: "Meta Muse Spark", what: "the planning agent it watches" }],
  approve: [{ id: "operator", name: "Operator", what: "a person decides" }],
};

// The powered_by list for a node: the run's own list, or the static facts.
// Never throws; [] means the node shows no tag.
export function poweredByFor(node) {
  if (node && Array.isArray(node.powered_by) && node.powered_by.length) return node.powered_by;
  if (node && STATIC_PB[node.id]) return STATIC_PB[node.id];
  return [];
}

// One small "powered by" tag. The partner's name is text, never colour
// alone. A "planner" entry stays a hidden placeholder until the run's events
// say which planner proposed the step (see fillPlannerTags).
export function poweredByHtml(list) {
  const seen = [];
  let placeholder = "";
  for (const p of list || []) {
    if (!p || typeof p !== "object") continue;
    if (p.id === "planner") {
      placeholder = `<span class="pwb pl" data-pp hidden></span>`;
      continue;
    }
    if (!p.name && !p.id) continue;
    seen.push(
      `<span class="pwb" style="--c:${PCOL[p.id] || "var(--dim)"}" title="${esc(p.what || p.name || p.id)}">${esc(p.name || p.id)}</span>`
    );
  }
  if (!seen.length && !placeholder) return "";
  if (!seen.length) return placeholder;
  return `<div class="pw">powered by ${seen.join(" ")}${placeholder}</div>`;
}

// Which planner the run's events named: "meta" or "baseten". Null until then.
export function plannerTag(partner) {
  if (partner === "meta") return { text: "Meta · Muse Spark API", color: "var(--m)" };
  if (partner === "baseten") return { text: "Baseten · Muse Spark Open", color: "var(--bt)" };
  return null;
}

// Fill every planner placeholder under root once an event names the planner.
// Returns how many tags were filled. Safe to call when data.planner is absent.
export function fillPlannerTags(root, partner) {
  const t = plannerTag(partner);
  if (!t || !root || typeof root.querySelectorAll !== "function") return 0;
  let n = 0;
  for (const el of root.querySelectorAll("[data-pp]")) {
    el.hidden = false;
    el.textContent = t.text;
    if (el.style && typeof el.style.setProperty === "function") el.style.setProperty("--c", t.color);
    if (typeof el.setAttribute === "function") el.setAttribute("title", "proposed the next step");
    n++;
  }
  return n;
}

const fmtNum = (v) => Number(v).toLocaleString("en-US", { maximumFractionDigits: 4 });

// The Partners block for the Results screen. A missing number is a dash,
// never a guess; cost without a value shows no label either.
export function partnersHtml(snap) {
  const list = (snap && snap.partners) || [];
  if (!list.length) return "";
  return `<section class="pblock" aria-label="partners"><h2>Partners</h2>`
    + `<div class="pgrid">${list.map((p) => {
      if (p.status === "not_used") {
        return `<div class="pcard idle"><b>${esc(p.name)}</b><p class="muted">not used yet</p></div>`;
      }
      const cost = p.cost || {};
      const costHtml =
        cost.value == null
          ? "–"
          : `${cost.unit === "USD" ? "$" : ""}${esc(fmtNum(cost.value))}${cost.unit ? ` ${esc(cost.unit)}` : ""}`;
      const label = cost.value == null || !cost.label ? "" : dot(cost.label);
      const aw = p.agent_waste && p.agent_waste.usd != null
        ? `<p class="muted">of which Agent waste <b class="num${numLab(p.agent_waste.label || "")}">$${esc(fmtNum(p.agent_waste.usd))}</b>${p.agent_waste.label ? dot(p.agent_waste.label) : ""}</p>`
        : "";
      return `<div class="pcard"><b>${esc(p.name)}</b><p>${esc(p.what || "–")}</p>`
        + `<p>calls <b class="num">${p.calls == null ? "–" : esc(String(p.calls))}</b></p>`
        + `<p>cost <b class="num${numLab(cost.label)}">${costHtml}</b>${label}</p>${aw}</div>`;
    }).join("")}</div></section>`;
}

// Fetch the snapshot and fill el. A 404 or any failure shows nothing.
export async function loadPartners(el, fetchFn) {
  const f = fetchFn || (typeof fetch === "function" ? fetch : null);
  if (!el || !f) return;
  try {
    const r = await f("/api/dash/partners");
    if (!r.ok) return;
    el.innerHTML = partnersHtml(await r.json());
  } catch {
    /* show nothing */
  }
}
