// explainmap.js — Runtime tagging and local fast matcher for Voice Guide "explain" intent.
//
// Automatically tags bento cards, KPIs, and report cards across all WattNot pages
// with data-explain attributes (honouring any existing data-explain attributes).
// Provides an offline fast local matcher for query strings (numbers, units, keywords),
// a lavender-pulsing highlight (respecting prefers-reduced-motion), and a glass
// popover displaying plain-language explanations, source IDs, and data labels.

export const PAGE_SELECTORS = {
  // All-runs dashboard (runs.html)
  "runs.html": [
    { sel: ".kpis .kpi", name: "KPI Card", kind: "kpi" },
    { sel: "#runsTable tbody tr", name: "Runs Table Row", kind: "table-row" },
    { sel: ".charts .card", name: "Chart Card", kind: "chart" },
  ],
  // Run setup (v2 configure / web/run)
  "run-setup": [
    { sel: "#pCfg .card, #pCfg .cfg-group, .cap-slider, [data-step], .cfg-card", name: "Setup Step Card", kind: "config" },
  ],
  // Each run report (report.html)
  "report.html": [
    { sel: ".flow .card, .step .card", name: "Report Step Card", kind: "report-step" },
    { sel: ".meta-line", name: "Report Metadata Header", kind: "meta" },
    { sel: "aside.side .kv", name: "Report KV Details", kind: "kv" },
  ],
  // Compare view (mind-map nodes, branches and leaf cards)
  "compare": [
    { sel: ".cmp.glass, .cmp-cards .cmp, section[data-cmp]", name: "Compare Branch Card", kind: "compare-card" },
    { sel: ".topic, .leaf, .map-hub", name: "Compare Mind Map Node & Leaf Card", kind: "mindmap" },
    { sel: ".rad-card, .rad-leaf", name: "Compare Tree & Leaf Card", kind: "tree-leaf" },
    { sel: ".wr-t tbody tr", name: "Placement Table Row", kind: "placement" },
    { sel: ".wr-gates li", name: "Gate Status Item", kind: "gate" },
    { sel: ".cmp-m li", name: "Method Attribution Item", kind: "method" },
    { sel: ".ov-kpi.glass", name: "Overview KPI Card", kind: "kpi" },
  ],
  // Live watch-run view (web/run graph nodes and live metrics)
  "web/run": [
    { sel: "#savings .cnt", name: "Live Savings Metric", kind: "live-kpi" },
    { sel: "#savings .eff", name: "Live Efficiency Tile", kind: "live-eff" },
    { sel: "#savings .wf li", name: "Waterfall Step Row", kind: "live-wf" },
    { sel: "#nodes .node, #stage .node, .node", name: "Graph Node", kind: "graph-node" },
    { sel: "#caps, #metacalls, #modeChip", name: "Live Status Chip", kind: "live-chip" },
  ],
  // Meta tools preview & Demo (metatools.html)
  "metatools.html": [
    { sel: ".demo-explain-card", name: "Demo KPI Card", kind: "demo-kpi" },
    { sel: "#metatools-mount .kpi-box", name: "Meta Tools Summary Tile", kind: "kpi" },
    { sel: "#hotspot-mount .hotspot-stat-box", name: "Hotspot Cooling Stat Box", kind: "kpi" },
    { sel: "#eu-directive-card .eulink-metric-box", name: "EU Directive Metric", kind: "kpi" },
  ],
};

const EXPLAIN_CSS = `
@keyframes explain-lavender-pulse {
  0% {
    box-shadow: 0 0 0 0 rgba(178, 166, 195, 0.8), 0 0 16px rgba(178, 166, 195, 0.4);
  }
  70% {
    box-shadow: 0 0 0 12px rgba(178, 166, 195, 0), 0 0 24px rgba(178, 166, 195, 0.6);
  }
  100% {
    box-shadow: 0 0 0 0 rgba(178, 166, 195, 0), 0 0 16px rgba(178, 166, 195, 0.4);
  }
}

.explain-highlight {
  outline: 2px solid #b2a6c3 !important;
  outline-offset: 4px !important;
  border-radius: 12px !important;
  animation: explain-lavender-pulse 2s infinite ease-in-out !important;
  transition: box-shadow 0.25s ease, outline 0.25s ease;
  position: relative;
  z-index: 999;
  /* Keep scrolled-to matches clear of the sticky chrome header. */
  scroll-margin-top: 96px;
  scroll-margin-bottom: 24px;
}

.explain-highlight-candidate {
  outline: 2px dashed rgba(178, 166, 195, 0.8) !important;
  outline-offset: 3px !important;
  border-radius: 10px !important;
  cursor: pointer;
  transition: transform 0.15s ease, box-shadow 0.15s ease;
}

.explain-highlight-candidate:hover {
  outline-style: solid !important;
  box-shadow: 0 0 14px rgba(178, 166, 195, 0.6) !important;
}

@media (prefers-reduced-motion: reduce) {
  .explain-highlight {
    animation: none !important;
    box-shadow: 0 0 18px rgba(178, 166, 195, 0.95) !important;
  }
}

.explain-popover {
  position: fixed;
  z-index: 100000;
  max-width: 360px;
  min-width: 270px;
  padding: 16px 18px;
  background: rgba(30, 20, 38, 0.88);
  backdrop-filter: blur(24px) saturate(170%);
  -webkit-backdrop-filter: blur(24px) saturate(170%);
  border: 1px solid rgba(255, 255, 255, 0.16);
  border-radius: 18px;
  box-shadow: 0 16px 40px rgba(0, 0, 0, 0.55), 0 0 20px rgba(178, 166, 195, 0.25);
  color: #f5f0eb;
  font-family: var(--v-body, system-ui, -apple-system, sans-serif);
  font-size: 13.5px;
  line-height: 1.5;
  box-sizing: border-box;
}

.explain-popover-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  margin-bottom: 10px;
  padding-bottom: 8px;
  border-bottom: 1px solid rgba(255, 255, 255, 0.1);
}

.explain-popover-title {
  font-family: var(--v-head, system-ui, sans-serif);
  font-weight: 600;
  font-size: 14px;
  color: #f6dbc0;
  margin: 0;
}

.explain-popover-chip {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 2px 7px;
  border-radius: 999px;
  font-size: 10.5px;
  font-weight: 600;
  letter-spacing: 0.04em;
  background: rgba(255, 255, 255, 0.08);
  border: 1px solid rgba(255, 255, 255, 0.12);
}

.explain-popover-chip.measured { color: #8fbba0; border-color: rgba(143, 187, 160, 0.3); }
.explain-popover-chip.estimate { color: #f6dbc0; border-color: rgba(246, 219, 192, 0.3); }
.explain-popover-chip.simulated { color: #b2a6c3; border-color: rgba(178, 166, 195, 0.3); }
.explain-popover-chip.mock { color: #e0a3b2; border-color: rgba(224, 163, 178, 0.3); }
.explain-popover-chip.replay { color: #9bb5d9; border-color: rgba(155, 181, 217, 0.3); }
.explain-popover-chip.advice { color: #d6b88d; border-color: rgba(214, 184, 141, 0.3); }

.explain-popover-close {
  background: transparent;
  border: none;
  color: rgba(255, 255, 255, 0.6);
  font-size: 18px;
  line-height: 1;
  cursor: pointer;
  padding: 0 4px;
}
.explain-popover-close:hover {
  color: #fff;
}

.explain-popover-body {
  color: rgba(245, 240, 235, 0.92);
  margin-bottom: 12px;
  font-size: 13px;
}

.explain-popover-meta {
  display: flex;
  justify-content: space-between;
  align-items: center;
  font-size: 11px;
  color: rgba(178, 166, 195, 0.85);
  font-family: var(--v-num, monospace);
  padding-top: 6px;
  border-top: 1px solid rgba(255, 255, 255, 0.06);
}

.explain-choices-dialog {
  position: fixed;
  z-index: 100000;
  max-width: 380px;
  width: 90vw;
  left: 50%;
  top: 50%;
  transform: translate(-50%, -50%);
  padding: 20px;
  background: rgba(30, 20, 38, 0.94);
  backdrop-filter: blur(28px) saturate(180%);
  -webkit-backdrop-filter: blur(28px) saturate(180%);
  border: 1px solid rgba(255, 255, 255, 0.2);
  border-radius: 20px;
  box-shadow: 0 24px 60px rgba(0, 0, 0, 0.65), 0 0 30px rgba(178, 166, 195, 0.3);
  color: #f5f0eb;
  font-family: var(--v-body, system-ui, sans-serif);
}

.explain-choices-title {
  margin: 0 0 10px;
  font-size: 16px;
  font-weight: 600;
  color: #f6dbc0;
}

.explain-choices-sub {
  margin: 0 0 14px;
  font-size: 13px;
  color: rgba(245, 240, 235, 0.75);
}

.explain-choice-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  width: 100%;
  padding: 10px 14px;
  margin-bottom: 8px;
  background: rgba(255, 255, 255, 0.06);
  border: 1px solid rgba(255, 255, 255, 0.12);
  border-radius: 12px;
  color: #f5f0eb;
  cursor: pointer;
  text-align: left;
  transition: background 0.15s ease, border-color 0.15s ease;
}

.explain-choice-item:hover {
  background: rgba(178, 166, 195, 0.15);
  border-color: #b2a6c3;
}
`;

export function injectStyles(doc = document) {
  if (!doc || doc.getElementById("wattnot-explain-styles")) return;
  const style = doc.createElement("style");
  style.id = "wattnot-explain-styles";
  style.textContent = EXPLAIN_CSS;
  (doc.head || doc.body).appendChild(style);
}

/**
 * Extract active run ID or receipt ID from current location or DOM.
 */
function detectCurrentSource(doc = document) {
  if (typeof window !== "undefined" && window.location) {
    const params = new URLSearchParams(window.location.search);
    const fromQuery = params.get("run") || params.get("receipt");
    if (fromQuery) return fromQuery;
  }
  const codeEl = doc.querySelector(".meta-line code, #runid");
  if (codeEl && codeEl.textContent.trim()) {
    return codeEl.textContent.trim();
  }
  return "rcpt_llama_350w";
}

/**
 * Extract label from element or its children.
 */
function extractLabel(el) {
  const labEl = el.querySelector(".v-lab, .v-chip, .pill, [title*='MEASURED'], [title*='ESTIMATE'], [title*='MOCK']");
  if (labEl) {
    const text = (labEl.getAttribute("title") || labEl.textContent || "").toUpperCase();
    for (const l of ["MEASURED", "ESTIMATE", "SIMULATED", "MOCK", "REPLAY", "ADVICE"]) {
      if (text.includes(l)) return l;
    }
  }
  const rawText = el.textContent.toUpperCase();
  for (const l of ["MEASURED", "ESTIMATE", "SIMULATED", "MOCK", "REPLAY", "ADVICE"]) {
    if (rawText.includes(l)) return l;
  }
  // No label found on the element — never invent MEASURED; fall back to MOCK.
  return "MOCK";
}

/**
 * Build a grounded explanation straight from the element's own value + label.
 * Never fabricates figures or claims the receipt does not carry: it restates the
 * value, names what kind of metric it is, and explains what the provenance label means.
 */
function generateTechnicalExplanation(title, value, label) {
  const v = String(value || "—").trim();
  const t = String(title || "metric").trim();
  const lab = String(label || "MOCK").trim().toUpperCase();
  const tl = t.toLowerCase();

  let kind = "This value is read directly from the run's signed receipt.";
  if (tl.includes("saving") || tl.includes("cut") || tl.includes("saved")) {
    kind = "This is the before/after reduction reported for the run.";
  } else if (tl.includes("cap") || tl.includes("power") || v.toLowerCase().includes("w")) {
    kind = "This is the GPU power cap applied during the run.";
  } else if (tl.includes("energy") || v.toLowerCase().includes("wh") || v.toLowerCase().includes("kwh")) {
    kind = "This is the electrical energy accounted for the run.";
  } else if (tl.includes("water") || v.toLowerCase().includes("ml") || v.toLowerCase().includes("l")) {
    kind = "This is the cooling-water figure derived from the run's energy.";
  } else if (tl.includes("gate") || tl.includes("quality") || v.toLowerCase().includes("pass")) {
    kind = "This is the quality-gate outcome for the run.";
  }

  const PROV = {
    MEASURED: "MEASURED means it was metered directly.",
    ESTIMATE: "ESTIMATE means it was modelled from measured inputs, not metered.",
    SIMULATED: "SIMULATED means it comes from a simulated/replayed run, not live hardware.",
    MOCK: "MOCK means it is placeholder data, not a real measurement.",
    REPLAY: "REPLAY means it is replayed from previously recorded logs.",
    ADVICE: "ADVICE means it is a planner recommendation, not a measured result.",
  };
  const prov = PROV[lab] || `${lab} is the provenance label it carries.`;

  return `${t}: ${v} [${lab}]. ${kind} The [${lab}] tag states its provenance — ${prov} No figures here are invented; the value and label are taken as-is from the receipt.`;
}

/**
 * Tag DOM elements with data-explain attributes at runtime.
 * Honours any data-explain or data-explain-id attributes already present.
 */
export function tagElementsWithExplain(root = document) {
  if (!root || typeof root.querySelectorAll !== "function") return [];
  injectStyles(root.ownerDocument || root);

  const doc = root.ownerDocument || root;
  const currentSource = detectCurrentSource(doc);
  const tagged = [];

  // Helper to safely tag an element without overwriting existing data-explain
  function applyTag(el, fallbackTitle, fallbackValue, fallbackLabel, fallbackId) {
    if (!el) return;
    if (el.hasAttribute("data-explain") || el.hasAttribute("data-explain-id")) {
      tagged.push(el);
      return;
    }

    const title = fallbackTitle || "Metric";
    const value = fallbackValue || "—";
    const label = fallbackLabel || extractLabel(el);
    const id = fallbackId || `exp_${title.toLowerCase().replace(/[^a-z0-9]+/g, "_")}`;
    const summary = generateTechnicalExplanation(title, value, label);

    el.setAttribute("data-explain-id", id);
    el.setAttribute("data-explain-value", value);
    el.setAttribute("data-explain-label", label);
    el.setAttribute("data-explain-source", currentSource);
    el.setAttribute("data-explain-summary", summary);
    el.setAttribute(
      "data-explain",
      JSON.stringify({ id, value, label, source: currentSource, summary, title })
    );
    tagged.push(el);
  }

  // 1. All-runs dashboard (runs.html) KPI cards
  root.querySelectorAll(".kpis .kpi").forEach((el) => {
    const title = el.querySelector(".name")?.textContent.trim() || "KPI";
    const valEl = el.querySelector(".big .v-num") || el.querySelector(".big");
    const unitEl = el.querySelector(".unit");
    const valText = valEl ? (valEl.textContent.trim() + (unitEl ? " " + unitEl.textContent.trim() : "")) : "—";
    applyTag(el, title, valText, extractLabel(el), `runs_kpi_${title.toLowerCase().replace(/\s+/g, "_")}`);
  });

  // Table rows in runsTable
  root.querySelectorAll("#runsTable tbody tr, table tbody tr").forEach((tr) => {
    const nameEl = tr.querySelector(".runname");
    const numEl = tr.querySelector("td.num");
    if (nameEl) {
      const runName = nameEl.textContent.trim();
      const numVal = numEl ? numEl.textContent.trim() : "—";
      applyTag(tr, `Run ${runName}`, numVal, extractLabel(tr), `run_row_${runName.replace(/[^a-z0-9]+/gi, "_")}`);
    }
  });

  // 2. Report step cards (report.html)
  root.querySelectorAll(".flow .card, .step .card").forEach((el) => {
    const title = el.querySelector(".k")?.textContent.trim() || el.querySelector("h2, h3")?.textContent.trim() || "Step";
    const val = el.querySelector(".val")?.textContent.trim() || el.querySelector(".v-num")?.textContent.trim() || "—";
    applyTag(el, title, val, extractLabel(el), `report_step_${title.toLowerCase().replace(/[^a-z0-9]+/g, "_")}`);
  });

  // 3. Compare view & mindmap cards (v2.html / comparetree.html)
  root.querySelectorAll(".cmp.glass, .cmp-cards .cmp, section[data-cmp]").forEach((el) => {
    const title = el.querySelector(".cmp-t, h2")?.textContent.trim() || el.getAttribute("data-cmp") || "Compare";
    const val = el.querySelector(".cmp-v")?.textContent.trim() || "—";
    applyTag(el, `Compare ${title}`, val, extractLabel(el), `cmp_${title.toLowerCase().replace(/[^a-z0-9]+/g, "_")}`);
  });

  root.querySelectorAll(".topic, .leaf, .map-hub").forEach((el) => {
    const title = el.querySelector(".tt b, b, h1, .leaf-title")?.textContent.trim() || "Compare Node";
    const val = el.querySelector(".rad-val, span, b, p")?.textContent.trim() || "—";
    applyTag(el, `Compare ${title}`, val, extractLabel(el), `cmp_mindmap_${title.toLowerCase().replace(/[^a-z0-9]+/g, "_")}`);
  });

  root.querySelectorAll(".rad-card, .rad-leaf").forEach((el) => {
    const title = el.querySelector(".rad-head b, .rad-leaf-title")?.textContent.trim() || el.getAttribute("data-node") || el.getAttribute("data-leaf") || "Compare Tree";
    const val = el.querySelector(".rad-val, .rad-leaf-val")?.textContent.trim() || "—";
    applyTag(el, `Compare ${title}`, val, extractLabel(el), `cmp_tree_${title.toLowerCase().replace(/[^a-z0-9]+/g, "_")}`);
  });

  root.querySelectorAll(".ov-kpi.glass").forEach((el) => {
    const title = el.querySelector(".lvl")?.textContent.trim() || "Metric";
    const val = el.querySelector("b")?.textContent.trim() || "—";
    applyTag(el, title, val, extractLabel(el), `ov_kpi_${title.toLowerCase().replace(/[^a-z0-9]+/g, "_")}`);
  });

  // 4. Run setup (v2 configure / web/run)
  root.querySelectorAll("#pCfg .card, #pCfg .cfg-group, .cap-slider, [data-step]").forEach((el) => {
    const title = el.querySelector("h3, label, .step-title")?.textContent.trim() || el.getAttribute("data-step") || "Setup";
    const val = el.querySelector("input, select, .val")?.value || el.querySelector(".val")?.textContent.trim() || "350 W";
    applyTag(el, `Setup ${title}`, String(val), "ADVICE", `cfg_${title.toLowerCase().replace(/[^a-z0-9]+/g, "_")}`);
  });

  // 5. Live watch-run view (web/run graph nodes and live metrics)
  root.querySelectorAll("#savings .cnt").forEach((el) => {
    const title = el.querySelector(".ch span")?.textContent.trim() || "Live Metric";
    const val = el.querySelector("b.mono")?.textContent.trim() || "—";
    applyTag(el, `Live ${title}`, val, extractLabel(el), `live_cnt_${title.toLowerCase().replace(/[^a-z0-9]+/g, "_")}`);
  });

  root.querySelectorAll("#nodes .node, #stage .node, .node").forEach((el) => {
    const title = el.querySelector(".nt, h3, .name")?.textContent.trim() || el.getAttribute("data-id") || "Node";
    const val = el.querySelector(".ns, .status, .val")?.textContent.trim() || "Active";
    applyTag(el, `Graph ${title}`, val, extractLabel(el), `node_${title.toLowerCase().replace(/[^a-z0-9]+/g, "_")}`);
  });

  root.querySelectorAll("#caps, #metacalls, #modeChip").forEach((el) => {
    const title = el.id || "Live Status";
    const val = el.textContent.trim() || "—";
    applyTag(el, `Live ${title}`, val, extractLabel(el), `live_status_${title.toLowerCase()}`);
  });

  // 6. Meta tools demo KPI cards
  root.querySelectorAll(".demo-explain-card").forEach((el) => {
    const title = el.querySelector(".demo-kpi-title")?.textContent.trim() || "Metric";
    const val = el.querySelector(".demo-kpi-val")?.textContent.trim() || "—";
    applyTag(el, title, val, extractLabel(el), el.id || `demo_${title.toLowerCase().replace(/[^a-z0-9]+/g, "_")}`);
  });

  return tagged;
}

/**
 * Local fast matcher: matches numbers, units, and keywords against value+label+title.
 */
export function matchExplainElement(query, root = document) {
  if (!query || typeof query !== "string") {
    return { match: null, ambiguous: false, candidates: [] };
  }

  // Ensure elements are tagged
  tagElementsWithExplain(root);

  const rawElements = Array.from(root.querySelectorAll("[data-explain], [data-explain-id]"));
  if (!rawElements.length) {
    return { match: null, ambiguous: false, candidates: [] };
  }

  const q = query.toLowerCase().trim();

  // Extract numeric tokens (e.g. "25%", "9.4%", "350", "0.0432")
  const numMatches = q.match(/(\d+(?:\.\d+)?%?|\bpass\b|\bfail\b)/gi) || [];
  const queryTokens = q
    .replace(/[?,!.:;()]/g, " ")
    .split(/\s+/)
    .filter((tok) => tok.length > 1 && !["what", "does", "mean", "this", "tell", "about", "show", "is", "the", "for"].includes(tok));

  const scored = [];

  for (const el of rawElements) {
    let data = null;
    try {
      const attr = el.getAttribute("data-explain");
      data = attr ? JSON.parse(attr) : null;
    } catch {
      data = null;
    }
    if (!data) {
      // Some cards (dashboard KPIs, report steps) pre-set data-explain as a plain
      // id string plus data-explain-value/-label. Derive a human title from the
      // element's own visible label so the query can actually match it, and take
      // value + label straight from its attributes (never defaulting to MEASURED).
      const rawExplain = el.getAttribute("data-explain") || "";
      const labelEl = typeof el.querySelector === "function"
        ? el.querySelector(".name, .k, .lvl, .demo-kpi-title, .cmp-t, .nt, h2, h3")
        : null;
      const titleText = (
        (labelEl && labelEl.textContent) ||
        el.getAttribute("data-explain-title") ||
        rawExplain ||
        el.getAttribute("data-explain-id") ||
        ""
      ).trim();
      data = {
        id: el.getAttribute("data-explain-id") || rawExplain || "",
        value: el.getAttribute("data-explain-value") || el.textContent.trim(),
        label: el.getAttribute("data-explain-label") || "MOCK",
        source: el.getAttribute("data-explain-source") || "rcpt_llama_350w",
        summary: el.getAttribute("data-explain-summary") || "",
        title: titleText,
      };
    }

    const val = String(data.value || "").toLowerCase();
    const title = String(data.title || data.id || "").toLowerCase();
    const label = String(data.label || "").toLowerCase();
    const summary = String(data.summary || "").toLowerCase();

    let score = 0;

    // 1. Numeric / Value matches
    for (const num of numMatches) {
      const cleanNum = num.toLowerCase();
      if (val === cleanNum) {
        score += 120;
      } else if (val.includes(cleanNum)) {
        score += 85;
      } else if (summary.includes(cleanNum)) {
        score += 40;
      }
    }

    // 2. Keyword matches on title, id, label, summary
    for (const tok of queryTokens) {
      if (val.includes(tok)) score += 35;
      if (title.includes(tok)) score += 40;
      if (label.includes(tok)) score += 20;
      if (summary.includes(tok)) score += 15;
    }

    if (score > 25) {
      scored.push({ el, data, score });
    }
  }

  scored.sort((a, b) => b.score - a.score);

  if (!scored.length) {
    return { match: null, ambiguous: false, candidates: [] };
  }

  // Check ambiguity: multiple candidates with identical or very close scores (within 10%)
  const top = scored[0];
  const closeCandidates = scored.filter((c) => c.score >= top.score * 0.9 && c.score >= 50);

  if (closeCandidates.length > 1) {
    return {
      match: null,
      ambiguous: true,
      candidates: closeCandidates.map((c) => ({ el: c.el, data: c.data, score: c.score })),
    };
  }

  return {
    match: top.el,
    candidate: top.data,
    ambiguous: false,
    candidates: [top],
  };
}

let activePopover = null;
let activeHighlightedElements = [];

export function clearHighlights(doc = document) {
  activeHighlightedElements.forEach((el) => {
    el.classList.remove("explain-highlight");
    el.classList.remove("explain-highlight-candidate");
  });
  activeHighlightedElements = [];

  if (activePopover && activePopover.parentNode) {
    activePopover.parentNode.removeChild(activePopover);
    activePopover = null;
  }

  const existingDialog = doc.querySelector(".explain-choices-dialog");
  if (existingDialog && existingDialog.parentNode) {
    existingDialog.parentNode.removeChild(existingDialog);
  }
}

/**
 * Open small glass popover beside matched element, keeping it strictly on screen.
 */
export function showExplainPopover(targetElement, data, doc = document) {
  clearHighlights(doc);
  if (!targetElement) return null;

  targetElement.classList.add("explain-highlight");
  activeHighlightedElements.push(targetElement);

  // Scroll element into view smoothly if needed
  if (typeof targetElement.scrollIntoView === "function") {
    targetElement.scrollIntoView({ behavior: "smooth", block: "center" });
  }

  const popover = doc.createElement("div");
  popover.className = "explain-popover";
  popover.id = "wattnot-explain-popover";

  // Value and label come straight from the matched element's data-explain fields;
  // we never substitute a MEASURED label the data did not claim.
  const label = data.label || targetElement.getAttribute("data-explain-label") || "MOCK";
  const labelClass = label.toLowerCase();
  const title = data.title || targetElement.getAttribute("data-explain-title") || "Metric Explanation";
  const value = data.value != null ? data.value : (targetElement.getAttribute("data-explain-value") || "—");
  const summary = data.summary || targetElement.getAttribute("data-explain-summary") || generateTechnicalExplanation(title, value, label);

  popover.innerHTML = `
    <div class="explain-popover-header">
      <h4 class="explain-popover-title">${title} (${value || "—"})</h4>
      <span class="explain-popover-chip ${labelClass}">${label}</span>
      <button class="explain-popover-close" aria-label="Close explanation">&times;</button>
    </div>
    <div class="explain-popover-body">${summary}</div>
    <div class="explain-popover-meta">
      <span>Source: ${data.source || "rcpt_llama_350w"}</span>
      <span>[${label}]</span>
    </div>
  `;

  doc.body.appendChild(popover);
  activePopover = popover;

  popover.querySelector(".explain-popover-close")?.addEventListener("click", () => {
    clearHighlights(doc);
  });

  // Calculate position kept on-screen
  const rect = targetElement.getBoundingClientRect();
  const popRect = popover.getBoundingClientRect();

  const margin = 12;
  const viewportW = (typeof window !== "undefined" ? window.innerWidth : 1280);
  const viewportH = (typeof window !== "undefined" ? window.innerHeight : 800);

  // The shared chrome header is sticky at the top; never let the popover sit under it.
  let headerBottom = 0;
  const hdr = (typeof doc.querySelector === "function")
    ? (doc.querySelector("nav.wn-nav.gl") || doc.querySelector("header.top"))
    : null;
  if (hdr && typeof hdr.getBoundingClientRect === "function") {
    const hr = hdr.getBoundingClientRect();
    if (hr && hr.bottom > 0) headerBottom = hr.bottom;
  }
  const topMin = Math.max(margin, headerBottom + margin);

  let left = rect.right + 12;
  let top = rect.top;

  // If overflows right, position to left of element
  if (left + popRect.width > viewportW - margin) {
    left = rect.left - popRect.width - 12;
  }
  // If still out of bounds, center horizontally
  if (left < margin) {
    left = Math.max(margin, Math.min(rect.left, viewportW - popRect.width - margin));
  }

  // Keep vertically on-screen and clear of the fixed header
  if (top + popRect.height > viewportH - margin) {
    top = Math.max(topMin, viewportH - popRect.height - margin);
  }
  if (top < topMin) {
    top = topMin;
  }

  popover.style.left = `${Math.round(left)}px`;
  popover.style.top = `${Math.round(top)}px`;

  return popover;
}

/**
 * Handle ambiguous matches: highlight all candidates and ask "Which one?"
 */
export function showAmbiguousChoices(candidates, doc = document) {
  clearHighlights(doc);
  if (!candidates || !candidates.length) return null;

  candidates.forEach((c) => {
    c.el.classList.add("explain-highlight-candidate");
    activeHighlightedElements.push(c.el);
  });

  const dialog = doc.createElement("div");
  dialog.className = "explain-choices-dialog";

  let choicesHtml = "";
  candidates.forEach((c, idx) => {
    const d = c.data;
    choicesHtml += `
      <button class="explain-choice-item" data-idx="${idx}">
        <span><strong>${d.title || d.id}:</strong> ${d.value}</span>
        <span class="explain-popover-chip ${(d.label || "MOCK").toLowerCase()}">${d.label || "MOCK"}</span>
      </button>
    `;
  });

  dialog.innerHTML = `
    <h3 class="explain-choices-title">Which one?</h3>
    <p class="explain-choices-sub">Multiple metrics matched your query. Select the element you'd like explained:</p>
    <div class="explain-choices-list">${choicesHtml}</div>
    <button class="explain-choice-item" id="explain-choices-cancel" style="justify-content:center;color:#b2a6c3;">Cancel</button>
  `;

  doc.body.appendChild(dialog);

  dialog.querySelectorAll(".explain-choice-item[data-idx]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const idx = Number(btn.getAttribute("data-idx"));
      const chosen = candidates[idx];
      dialog.remove();
      showExplainPopover(chosen.el, chosen.data, doc);
    });
  });

  dialog.querySelector("#explain-choices-cancel").addEventListener("click", () => {
    dialog.remove();
    clearHighlights(doc);
  });

  return dialog;
}

/**
 * Main entrance called by Voice Guide for explain queries.
 */
export function handleExplainQuery(query, doc = document) {
  const result = matchExplainElement(query, doc);
  if (result.ambiguous) {
    showAmbiguousChoices(result.candidates, doc);
    return {
      status: "ambiguous",
      count: result.candidates.length,
      text: "Multiple elements matched on the page. Please choose which one you would like explained.",
    };
  }
  if (result.match) {
    showExplainPopover(result.match, result.candidate, doc);
    return {
      status: "matched",
      candidate: result.candidate,
      text: `Found ${result.candidate.title || result.candidate.id}: ${result.candidate.value} [${result.candidate.label}]. Highlighting element with explanation.`,
    };
  }
  return {
    status: "none",
    text: `Could not locate a metric or element matching '${query}' on this page.`,
  };
}

// Auto-tag on DOM ready
if (typeof window !== "undefined" && typeof document !== "undefined") {
  window.WattNotExplain = {
    PAGE_SELECTORS,
    tagElementsWithExplain,
    matchExplainElement,
    showExplainPopover,
    showAmbiguousChoices,
    clearHighlights,
    handleExplainQuery,
  };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", () => tagElementsWithExplain(document));
  } else {
    tagElementsWithExplain(document);
  }
}
