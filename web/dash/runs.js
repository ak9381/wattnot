// runs.js — the "All runs" page for the WattNot static replay.
//
// It plays back recorded logs: four runs, the savings trend, the energy saved
// per lever, and a runs table that links to each run's report. Nothing here
// talks to a live system. Every number on screen carries the data label it
// arrived with (MEASURED / ESTIMATE / REPLAY / ...) so a reader can always tell
// a metered value from an estimate. Values we cannot compute from the data show
// the em dash "—" — never a zero, never a guess.
//
// Honesty note baked into the UI: only the on-prem H100 run (run2-llama-350w)
// reports metered joules. The cloud runs record request counts but no GPU
// energy, so they contribute "—" to the energy/water/cost totals.

const EM = "—"; // em dash for missing values

// ---- constants from story.json (shown with their own labels) ----
const USD_PER_KWH = 0.1387; // ESTIMATE — US commercial electricity price
const WATER_L_PER_KWH = 0.36; // headline ESTIMATE — on-site cooling water factor (WUE L/kWh), LBNL 2024
const WATER_L_PER_KWH_UPPER = 1.8; // upper ESTIMATE — includes power-plant (electricity) water
const LEVER_NAMES = { cap: "Power cap", topk: "Adaptive top-k", hosted: "Hosted routing" };

// ---------- tiny helpers ----------
const $ = (sel, root = document) => root.querySelector(sel);

function esc(s) {
  return String(s == null ? "" : s).replace(/[&<>"']/g, (c) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]
  ));
}

// Numeric formatter: returns the em dash for anything that is not a finite number.
function fmt(v, digits = 2) {
  if (v == null || typeof v !== "number" || !Number.isFinite(v)) return EM;
  return v.toLocaleString("en-US", { minimumFractionDigits: 0, maximumFractionDigits: digits });
}

const isNum = (v) => typeof v === "number" && Number.isFinite(v);

// A label chip that carries the visible word and distinct glyph.
function chip(label) {
  const L = String(label || "pending").toUpperCase();
  const glyphs = { MEASURED: "●", ESTIMATE: "~", SIMULATED: "┄", MOCK: "○", SAMPLE: "○", REPLAY: "↻", ADVICE: "✦" };
  const glyph = glyphs[L] || "•";
  const cls = L.toLowerCase();
  return `<span class="v-chip ${L} v-chip-${cls}"><i class="v-lab ${L}" aria-hidden="true">${glyph}</i>${esc(L)}</span>`;
}

// Every number shows its label word visibly next to it
function dot(label) {
  if (!label) return `<span class="v-chip"><i></i>pending</span>`;
  return chip(label);
}

// A value + its visible label word, or a plain em dash when the value is missing.
function valDot(text, label, missing) {
  if (missing) return `<span class="emdash">${EM}</span> <span class="v-chip"><i></i>pending</span>`;
  return `<span class="v-num">${esc(text)}</span> ${dot(label)}`;
}

async function getJSON(path) {
  // Always fetch the absolute /api/... path: the injected static shim rewrites
  // it to the recorded JSON under the GitHub Pages subpath.
  const r = await window.fetch(path);
  if (!r.ok) throw new Error(`fetch ${path} -> ${r.status}`);
  return r.json();
}

// ---------- side panel wiring ----------
const scrim = $("#scrim");
const side = $("#side");
const sideBody = $("#sideBody");

function openSide(html) {
  sideBody.innerHTML = html;
  scrim.hidden = false;
  side.classList.add("open");
  side.setAttribute("aria-hidden", "false");
}
function closeSide() {
  scrim.hidden = true;
  side.classList.remove("open");
  side.setAttribute("aria-hidden", "true");
}
$("#sideX").addEventListener("click", closeSide);
scrim.addEventListener("click", closeSide);
document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeSide(); });

// Store side-panel payloads so [data-side] clicks can render them.
const sidePanels = {};
function bindSide(root) {
  root.querySelectorAll("[data-side]").forEach((el) => {
    el.addEventListener("click", (e) => {
      // let a real link inside the row do its own thing
      if (e.target.closest("a")) return;
      const html = sidePanels[el.getAttribute("data-side")];
      if (html) openSide(html);
    });
  });
}

// ========================================================================
// Load everything, then render each block. Each block try/catches on its own
// so one failing card does not blank the rest of the page.
// ========================================================================
async function main() {
  let runsList = [];
  let details = [];
  let story = null;

  // 1) runs list + per-run detail
  try {
    const list = await getJSON("/api/dash/v2/runs");
    runsList = Array.isArray(list.runs) ? list.runs : [];
    details = await Promise.all(runsList.map(async (r) => {
      try { return await getJSON(`/api/dash/v2/runs/${encodeURIComponent(r.id)}`); }
      catch { return null; }
    }));
  } catch (err) {
    $("#kpis").innerHTML = `<div class="err">Could not load runs: ${esc(err.message)}</div>`;
    $("#trend").innerHTML = `<div class="err">Could not load runs.</div>`;
    $("#runsBody").innerHTML = `<tr><td colspan="8" class="err">Could not load runs: ${esc(err.message)}</td></tr>`;
  }

  // 2) story bundle (price / water / levers)
  try {
    const bundle = await getJSON("/api/dash/story");
    story = bundle.story || bundle;
  } catch (err) {
    $("#donut").innerHTML = `<div class="err">Could not load levers: ${esc(err.message)}</div>`;
  }

  if (runsList.length) {
    renderKpis(runsList, details);
    renderTrend(runsList, details);
    renderTable(runsList, details);
  }
  if (story) renderDonut(story);
}

// ---------- KPI tiles ----------
// A run contributes measured joules only if it has both baseline and wattnot
// j_per_req. joulesSaved = Σ (baseline.j_per_req − wattnot.j_per_req) × requests.
function energyContribs(details) {
  const rows = [];
  let total = 0;
  for (const d of details) {
    if (!d) continue;
    const b = d.phases && d.phases.baseline && d.phases.baseline.metrics;
    const w = d.phases && d.phases.wattnot && d.phases.wattnot.metrics;
    if (b && w && isNum(b.j_per_req) && isNum(w.j_per_req) && isNum(w.requests)) {
      const perReq = b.j_per_req - w.j_per_req;
      const j = perReq * w.requests;
      total += j;
      rows.push({ name: d.run_name || d.id, j, perReq, requests: w.requests, label: w.label || "MEASURED" });
    } else {
      rows.push({ name: (d.run_name || d.id), j: null, label: (d.label || "") });
    }
  }
  return { total, rows };
}

function energyText(joules) {
  if (!isNum(joules)) return { text: EM, unit: "" };
  if (Math.abs(joules) >= 1000) return { text: fmt(joules / 1000, 2), unit: "kJ" };
  return { text: fmt(joules, 1), unit: "J" };
}

function renderKpis(runsList, details) {
  const el = $("#kpis");
  const { total, rows } = energyContribs(details);
  const hasEnergy = rows.some((r) => isNum(r.j));
  const joules = hasEnergy ? total : null;
  const kWh = isNum(joules) ? joules / 3.6e6 : null;
  const litres = isNum(kWh) ? kWh * WATER_L_PER_KWH : null;
  const usd = isNum(kWh) ? kWh * USD_PER_KWH : null;

  const eng = energyText(joules);
  const measuredRows = rows.filter((r) => isNum(r.j));
  const missedRows = rows.filter((r) => !isNum(r.j));

  // --- side-panel bodies ---
  const contribList = (fn) => rows.map((r) => {
    const miss = !isNum(r.j);
    return `<li><span class="k">${esc(r.name)}</span><span class="vv">${miss ? `<span class="emdash">${EM}</span>` : `${esc(fn(r))}${dot(r.label)}`}</span></li>`;
  }).join("");

  sidePanels.k_energy = `
    <h3>Energy saved</h3>
    <p>Joules kept out of the room by running WattNot instead of the stock baseline.</p>
    <div class="formula">Σ over runs of (baseline J/req − WattNot J/req) × requests</div>
    <h4>Per-run contribution</h4>
    <ul class="rowlist">${contribList((r) => `${fmt(r.j, 0)} J`)}</ul>
    <p class="note">Only the on-prem H100 run is metered for GPU energy, so it is the
      one run that contributes measured joules. The cloud runs record request
      counts but no GPU power, so they show ${EM}.</p>`;

  sidePanels.k_water = `
    <h3>Water saved</h3>
    <p>Cooling water not evaporated, estimated from the energy saved.</p>
    <div class="formula">litres = (joules saved ÷ 3.6e6) kWh × ${WATER_L_PER_KWH} L/kWh</div>
    <ul class="rowlist">
      <li><span class="k">Energy saved</span><span class="vv">${isNum(joules) ? `${esc(eng.text)} ${eng.unit}${dot("MEASURED")}` : `<span class="emdash">${EM}</span>`}</span></li>
      <li><span class="k">kWh saved</span><span class="vv">${isNum(kWh) ? `${fmt(kWh, 4)}${dot("MEASURED")}` : `<span class="emdash">${EM}</span>`}</span></li>
      <li><span class="k">Water factor</span><span class="vv">${fmt(WATER_L_PER_KWH, 2)} L/kWh${dot("ESTIMATE")}</span></li>
    </ul>
    <p class="note">The energy is measured; the water factor is 0.36 L/kWh
      (on-site cooling, ESTIMATE); up to ${fmt(WATER_L_PER_KWH_UPPER, 2)} L/kWh
      including power-plant water (ESTIMATE).</p>`;

  sidePanels.k_cost = `
    <h3>Cost saved</h3>
    <p>Electricity bill avoided, estimated from the energy saved.</p>
    <div class="formula">dollars = (joules saved ÷ 3.6e6) kWh × $${USD_PER_KWH}/kWh</div>
    <ul class="rowlist">
      <li><span class="k">Energy saved</span><span class="vv">${isNum(joules) ? `${esc(eng.text)} ${eng.unit}${dot("MEASURED")}` : `<span class="emdash">${EM}</span>`}</span></li>
      <li><span class="k">kWh saved</span><span class="vv">${isNum(kWh) ? `${fmt(kWh, 4)}${dot("MEASURED")}` : `<span class="emdash">${EM}</span>`}</span></li>
      <li><span class="k">Price</span><span class="vv">$${fmt(USD_PER_KWH, 4)}/kWh${dot("ESTIMATE")}</span></li>
    </ul>
    <p class="note">The energy is measured; the price is a published estimate, so
      the result is an ESTIMATE.</p>`;

  sidePanels.k_runs = `
    <h3>Runs</h3>
    <p>How many runs are recorded in this replay.</p>
    <div class="formula">count of recorded runs</div>
    <ul class="rowlist">
      ${runsList.map((r) => `<li><span class="k">${esc(r.name || r.id)}</span><span class="vv">${esc(r.model || "")}</span></li>`).join("")}
    </ul>
    <p class="note">${measuredRows.length} of ${runsList.length} run(s) report metered GPU energy; the rest show ${EM} for energy.</p>`;

  el.innerHTML = `
    <div class="kpi v-glass" data-side="k_energy" data-explain="runs.total.energy" data-explain-value="${esc(isNum(joules) ? eng.text + ' ' + eng.unit : EM)}" data-explain-label="MEASURED" data-explain-source="phases.wattnot.metrics.j_per_req" role="button" tabindex="0">
      <span class="more">Details &rarr;</span>
      <div class="name">Energy saved</div>
      <div class="big">${isNum(joules)
        ? `<span class="v-num">${esc(eng.text)}</span><span class="unit">${eng.unit}</span>${dot("MEASURED")}`
        : `<span class="emdash">${EM}</span>`}</div>
      <div class="foot">${isNum(joules) ? "Measured GPU joules, H100 run" : "No metered run"}</div>
    </div>
    <div class="kpi v-glass" data-side="k_water" data-explain="runs.total.water" data-explain-value="${esc(isNum(litres) ? fmt(litres, 3) + ' L' : EM)}" data-explain-label="ESTIMATE" data-explain-source="water_factor" role="button" tabindex="0">
      <span class="more">Details &rarr;</span>
      <div class="name">Water saved</div>
      <div class="big">${isNum(litres)
        ? `<span class="v-num">${fmt(litres, 3)}</span><span class="unit">L</span>`
        : `<span class="emdash">${EM}</span>`}</div>
      <div class="foot">${isNum(litres) ? `From energy &times; ${WATER_L_PER_KWH} L/kWh (on-site, ESTIMATE); up to ${WATER_L_PER_KWH_UPPER} L/kWh incl. power-plant water` : "Needs metered energy"}</div>
    </div>
    <div class="kpi v-glass" data-side="k_cost" data-explain="runs.total.cost" data-explain-value="${esc(isNum(usd) ? '$' + fmt(usd, 4) : EM)}" data-explain-label="ESTIMATE" data-explain-source="usd_per_kwh" role="button" tabindex="0">
      <span class="more">Details &rarr;</span>
      <div class="name">Cost saved</div>
      <div class="big">${isNum(usd)
        ? `<span class="unit">$</span><span class="v-num">${fmt(usd, 4)}</span>`
        : `<span class="emdash">${EM}</span>`}</div>
      <div class="foot">${isNum(usd) ? "From energy &times; $0.1387/kWh" : "Needs metered energy"}</div>
    </div>
    <div class="kpi v-glass" data-side="k_runs" data-explain="runs.total.runs" data-explain-value="${esc(String(runsList.length))}" data-explain-label="MEASURED" data-explain-source="runs_count" role="button" tabindex="0">
      <span class="more">Details &rarr;</span>
      <div class="name">Runs</div>
      <div class="big"><span class="v-num">${fmt(runsList.length, 0)}</span>${dot("MEASURED")}</div>
      <div class="foot">Recorded in this replay</div>
    </div>`;

  // keyboard activation for the tiles
  el.querySelectorAll(".kpi").forEach((k) => k.addEventListener("keydown", (e) => {
    if (e.key === "Enter" || e.key === " ") { e.preventDefault(); k.click(); }
  }));
  bindSide(el);
  void missedRows;
}

// ---------- savings trend (inline SVG) ----------
function renderTrend(runsList, details) {
  const el = $("#trend");
  // One point per run that has a measured saved_pct, ordered by date then index.
  const pts = [];
  const notMeasured = [];
  details.forEach((d, i) => {
    const w = d && d.phases && d.phases.wattnot && d.phases.wattnot.metrics;
    const name = (d && (d.run_name || d.id)) || (runsList[i] && (runsList[i].name || runsList[i].id)) || `run ${i + 1}`;
    const date = (d && d.date) || (runsList[i] && runsList[i].date) || "";
    if (w && isNum(w.saved_pct)) {
      pts.push({ name, date, idx: i, pct: Math.abs(w.saved_pct), label: w.label || "MEASURED" });
    } else {
      notMeasured.push(name);
    }
  });
  pts.sort((a, b) => String(a.date).localeCompare(String(b.date)) || a.idx - b.idx);

  if (!pts.length) {
    el.innerHTML = `<p class="cap">No run reports measured energy savings yet, so there is nothing to plot. ${EM}</p>`;
    return;
  }

  const W = 520, H = 230, mL = 48, mR = 20, mT = 18, mB = 42;
  const iw = W - mL - mR, ih = H - mT - mB;
  const maxY = Math.max(10, Math.ceil(Math.max(...pts.map((p) => p.pct)) / 10) * 10);
  const x = (k) => pts.length === 1 ? mL + iw / 2 : mL + (iw * k) / (pts.length - 1);
  const y = (v) => mT + ih - (ih * v) / maxY;

  // y gridlines / ticks
  let grid = "";
  for (let t = 0; t <= maxY; t += maxY / 4) {
    const yy = y(t);
    grid += `<line x1="${mL}" y1="${yy}" x2="${W - mR}" y2="${yy}" stroke="var(--v-line)" stroke-width="1"/>`;
    grid += `<text class="axislab" x="${mL - 8}" y="${yy + 4}" text-anchor="end">${fmt(t, 0)}%</text>`;
  }

  // line + area (only meaningful with 2+ points)
  let area = "", line = "";
  if (pts.length >= 2) {
    const dLine = pts.map((p, k) => `${k ? "L" : "M"}${x(k).toFixed(1)},${y(p.pct).toFixed(1)}`).join(" ");
    line = `<path d="${dLine}" fill="none" stroke="var(--v-mauve)" stroke-width="2.5" stroke-linejoin="round"/>`;
    const dArea = `${dLine} L${x(pts.length - 1).toFixed(1)},${y(0).toFixed(1)} L${x(0).toFixed(1)},${y(0).toFixed(1)} Z`;
    area = `<path d="${dArea}" fill="var(--v-mauve)" opacity="0.18"/>`;
  }

  // points + x labels
  let dots = "", xlabs = "";
  pts.forEach((p, k) => {
    dots += `<circle cx="${x(k).toFixed(1)}" cy="${y(p.pct).toFixed(1)}" r="5.5" fill="var(--v-peach)" stroke="var(--v-bg)" stroke-width="1.5" style="filter:drop-shadow(0 0 6px var(--v-glow))"><title>${esc(p.name)}: ${fmt(p.pct, 1)}% saved</title></circle>`;
    dots += `<text class="axislab" x="${x(k).toFixed(1)}" y="${y(p.pct) - 12}" text-anchor="middle" fill="var(--v-cream)">${fmt(p.pct, 1)}%</text>`;
    const short = String(p.date || p.name).slice(0, 10);
    xlabs += `<text class="axislab" x="${x(k).toFixed(1)}" y="${H - mB + 18}" text-anchor="middle">${esc(short)}</text>`;
  });

  const single = pts.length === 1
    ? `<p class="cap">Only measured runs appear. One run reports metered savings so far; more will plot as a trend line.</p>`
    : "";
  const missed = notMeasured.length
    ? `<p class="cap">Not measured: ${notMeasured.map((n) => `${esc(n)} ${EM}`).join(", ")}</p>`
    : "";

  el.innerHTML = `
    <svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Energy saved per run, percent">
      ${grid}
      <line x1="${mL}" y1="${mT}" x2="${mL}" y2="${H - mB}" stroke="var(--v-line2)" stroke-width="1"/>
      <line x1="${mL}" y1="${H - mB}" x2="${W - mR}" y2="${H - mB}" stroke="var(--v-line2)" stroke-width="1"/>
      ${area}${line}${dots}${xlabs}
      <text class="axislab" x="${mL}" y="12">% energy saved</text>
    </svg>
    ${single}${missed}
    <p class="cap">${dot("MEASURED")} Points are metered GPU energy savings. Higher is better.</p>`;
}

// ---------- donut: energy saved by lever (inline SVG) ----------
function renderDonut(story) {
  const el = $("#donut");
  const levers = Array.isArray(story.levers) ? story.levers : [];

  // Measured energy slices: levers with wh_before & wh_after -> J/req saved.
  const sliced = [];
  const unmetered = [];
  for (const lv of levers) {
    const name = LEVER_NAMES[lv.id] || lv.title || lv.id;
    if (isNum(lv.wh_before) && isNum(lv.wh_after)) {
      const jSaved = (lv.wh_before - lv.wh_after) * 3600; // Wh -> J
      sliced.push({ id: lv.id, name, jSaved, lv });
    } else {
      unmetered.push({ id: lv.id, name, lv });
    }
  }

  if (!sliced.length) {
    el.innerHTML = `<p class="cap">No lever has measured before/after energy. ${EM}</p>`;
    return;
  }

  const colors = ["var(--v-mauve)", "var(--v-peach)", "var(--v-violet)"];
  const total = sliced.reduce((s, x) => s + x.jSaved, 0);

  // donut geometry
  const S = 200, cx = S / 2, cy = S / 2, R = 78, rInner = 50;
  let a0 = -Math.PI / 2; // start at top
  let segs = "";
  sliced.forEach((s, i) => {
    const frac = total > 0 ? s.jSaved / total : 0;
    const a1 = a0 + frac * 2 * Math.PI;
    const large = (a1 - a0) > Math.PI ? 1 : 0;
    const p = (ang, r) => `${(cx + r * Math.cos(ang)).toFixed(2)},${(cy + r * Math.sin(ang)).toFixed(2)}`;
    const d = `M${p(a0, R)} A${R},${R} 0 ${large} 1 ${p(a1, R)} L${p(a1, rInner)} A${rInner},${rInner} 0 ${large} 0 ${p(a0, rInner)} Z`;
    s.color = colors[i % colors.length];
    segs += `<path d="${d}" fill="${s.color}" stroke="var(--v-bg2)" stroke-width="2"><title>${esc(s.name)}: ${fmt(s.jSaved, 3)} J/req</title></path>`;
    a0 = a1;
  });

  // legend rows (measured slices) + unmetered levers (no slice, em dash)
  const legSliced = sliced.map((s) => `
    <li data-side="lev_${esc(s.id)}" role="button" tabindex="0">
      <span class="sw" style="background:${s.color}"></span>
      <span class="lname">${esc(s.name)}</span>
      <span class="lval"><span class="v-num">${fmt(s.jSaved, 3)}</span> J/req${dot("MEASURED")}</span>
    </li>`).join("");
  const legUnmetered = unmetered.map((u) => `
    <li class="flat">
      <span class="sw" style="background:transparent;border:1px dashed var(--v-line2)"></span>
      <span class="lname">${esc(u.name)} ${EM} not metered</span>
      <span class="lval"><span class="emdash">${EM}</span></span>
    </li>`).join("");

  // side-panel bodies for each sliced lever
  sliced.forEach((s) => {
    const lv = s.lv;
    sidePanels[`lev_${s.id}`] = `
      <h3>${esc(s.name)}</h3>
      <p>Measured GPU energy saved per request by this technique.</p>
      <ul class="rowlist">
        <li><span class="k">Energy saved</span><span class="vv"><span class="v-num">${fmt(s.jSaved, 3)}</span> J/req${dot("MEASURED")}</span></li>
        <li><span class="k">Saved</span><span class="vv">${isNum(lv.saved_pct) ? `${fmt(lv.saved_pct, 1)}%${dot("MEASURED")}` : `<span class="emdash">${EM}</span>`}</span></li>
        <li><span class="k">Wh before</span><span class="vv">${isNum(lv.wh_before) ? `${fmt(lv.wh_before, 5)}${dot("MEASURED")}` : `<span class="emdash">${EM}</span>`}</span></li>
        <li><span class="k">Wh after</span><span class="vv">${isNum(lv.wh_after) ? `${fmt(lv.wh_after, 5)}${dot("MEASURED")}` : `<span class="emdash">${EM}</span>`}</span></li>
      </ul>
      <div class="formula">J/req saved = (Wh before − Wh after) × 3600</div>
      <p class="note">This is metered GPU energy, measured with NVML on an H100.</p>`;
  });
  unmetered.forEach((u) => {
    const lv = u.lv;
    sidePanels[`lev_${u.id}`] = `
      <h3>${esc(u.name)}</h3>
      <p>This lever runs on hosted GPUs that are not metered, so it has no measured
         energy slice and shows ${EM} here.</p>
      <ul class="rowlist">
        <li><span class="k">Energy saved</span><span class="vv"><span class="emdash">${EM}</span></span></li>
        <li><span class="k">Saved</span><span class="vv">${isNum(lv.saved_pct) ? `${fmt(lv.saved_pct, 1)}%${dot("MEASURED")}` : `<span class="emdash">${EM}</span>`}</span></li>
      </ul>
      <p class="note">The percentage above is a measured cost/effort saving; GPU
         energy on the hosted path is not metered.</p>`;
  });

  el.innerHTML = `
    <div style="display:flex;justify-content:center">
      <svg viewBox="0 0 ${S} ${S}" width="200" role="img" aria-label="Energy saved by lever">
        ${segs}
        <text x="${cx}" y="${cy - 6}" text-anchor="middle" fill="var(--v-cream)" class="axislab" style="font-size:9px">total J/req</text>
        <text x="${cx}" y="${cy + 12}" text-anchor="middle" fill="var(--v-peach)" style="font-family:var(--v-num);font-size:17px">${fmt(total, 3)}</text>
      </svg>
    </div>
    <ul class="legend">${legSliced}${legUnmetered}</ul>
    <p class="cap">${dot("MEASURED")} Metered GPU energy per request. Click a lever for detail.</p>`;

  // keyboard activation + side wiring
  el.querySelectorAll(".legend li[data-side]").forEach((li) => li.addEventListener("keydown", (e) => {
    if (e.key === "Enter" || e.key === " ") { e.preventDefault(); li.click(); }
  }));
  bindSide(el);
}

// ---------- runs table ----------
function renderTable(runsList, details) {
  const body = $("#runsBody");
  if (!runsList.length) {
    body.innerHTML = `<tr><td colspan="8" class="emdash">No runs recorded ${EM}</td></tr>`;
    return;
  }

  body.innerHTML = runsList.map((r, i) => {
    const d = details[i];
    const w = d && d.phases && d.phases.wattnot && d.phases.wattnot.metrics;
    const label = (d && d.label) || r.label || "";
    const name = (d && d.run_name) || r.name || r.id;
    const model = (d && d.model) || r.model || "";
    const date = (d && d.date) || r.date || "";

    // energy saved: saved_pct is negative when energy dropped -> show magnitude as "X% less"
    const savedCell = (w && isNum(w.saved_pct))
      ? `<span class="v-num">${fmt(Math.abs(w.saved_pct), 1)}% less</span>${dot(w.label || "MEASURED")}`
      : `<span class="emdash">${EM}</span>`;

    const p99Cell = (w && isNum(w.p99_change_pct))
      ? `<span class="v-num">${w.p99_change_pct >= 0 ? "+" : ""}${fmt(w.p99_change_pct, 1)}%</span>${dot(w.label || "MEASURED")}`
      : `<span class="emdash">${EM}</span>`;

    const qCell = (w && isNum(w.quality_pct))
      ? `<span class="v-num">${fmt(w.quality_pct, 1)}%</span>${dot(w.label || "MEASURED")}`
      : `<span class="emdash">${EM}</span>`;

    const reportHref = `report.html?run=${encodeURIComponent(r.id)}`;

    // Framework detection: from receipt, run detail, or fallback to 'framework: none recorded'
    const fw = d?.receipt?.framework || d?.receipt?.frameworks?.[0] || d?.framework || r.framework;
    const isReal = label === "MEASURED" && fw;
    const fwName = fw ? (fw === "plain" ? "plain API" : fw) : "framework: none recorded";
    const fwTag = isReal ? "MEASURED" : "MOCK";

    // side-panel body for the run (opened by clicking name/model cells)
    sidePanels[`run_${r.id}`] = `
      <h3>${esc(name)}</h3>
      <p>${esc(model)} &middot; ${esc(date)} &nbsp; ${chip(label)} &nbsp; <span class="v-chip ${fwTag}"><i class="v-lab ${fwTag}"></i>${esc(fwName)}</span></p>
      <ul class="rowlist">
        <li><span class="k">Framework</span><span class="vv">${esc(fwName)} ${chip(fwTag)}</span></li>
      </ul>
      ${w ? `<ul class="rowlist">
        <li><span class="k">Energy saved</span><span class="vv">${isNum(w.saved_pct) ? `${fmt(Math.abs(w.saved_pct), 1)}% less${dot(w.label || "MEASURED")}` : `<span class="emdash">${EM}</span>`}</span></li>
        <li><span class="k">p99 change</span><span class="vv">${isNum(w.p99_change_pct) ? `${w.p99_change_pct >= 0 ? "+" : ""}${fmt(w.p99_change_pct, 1)}%${dot(w.label || "MEASURED")}` : `<span class="emdash">${EM}</span>`}</span></li>
        <li><span class="k">Quality</span><span class="vv">${isNum(w.quality_pct) ? `${fmt(w.quality_pct, 1)}%${dot(w.label || "MEASURED")}` : `<span class="emdash">${EM}</span>`}</span></li>
        <li><span class="k">Requests</span><span class="vv">${isNum(w.requests) ? `${fmt(w.requests, 0)}${dot(w.label || "MEASURED")}` : `<span class="emdash">${EM}</span>`}</span></li>
      </ul>` : `<p class="note">This run recorded a baseline only (no WattNot phase), so its savings show ${EM}.</p>`}
      <p class="note"><a href="${esc(reportHref)}">Open the full report &rarr;</a></p>`;

    const savedVal = isNum(w?.saved_pct) ? `${fmt(Math.abs(w.saved_pct), 1)}%` : EM;
    const p99Val = isNum(w?.p99_change_pct) ? `${w.p99_change_pct >= 0 ? "+" : ""}${fmt(w.p99_change_pct, 1)}%` : EM;
    const qVal = isNum(w?.quality_pct) ? `${fmt(w.quality_pct, 1)}%` : EM;
    const metricLabel = w?.label || (label === "MEASURED" ? "MEASURED" : "pending");

    return `
      <tr data-side="run_${esc(r.id)}" data-explain="runs.${esc(r.id)}" data-explain-value="${esc(savedVal)}" data-explain-label="${esc(metricLabel)}" data-explain-source="phases.wattnot.metrics.saved_pct" role="button" tabindex="0" style="cursor:pointer">
        <td><div class="runname">${esc(name)}</div><div class="runsub">${esc(r.id)} &middot; <span class="v-chip ${fwTag}"><i class="v-lab ${fwTag}"></i>${esc(fwName)}</span></div></td>
        <td>${esc(model)}</td>
        <td class="num">${esc(date) || `<span class="emdash">${EM}</span>`}</td>
        <td class="num" data-explain="runs.${esc(r.id)}.saving" data-explain-value="${esc(savedVal)}" data-explain-label="${esc(metricLabel)}" data-explain-source="phases.wattnot.metrics.saved_pct">${savedCell}</td>
        <td class="num" data-explain="runs.${esc(r.id)}.p99" data-explain-value="${esc(p99Val)}" data-explain-label="${esc(metricLabel)}" data-explain-source="phases.wattnot.metrics.p99_change_pct">${p99Cell}</td>
        <td class="num" data-explain="runs.${esc(r.id)}.quality" data-explain-value="${esc(qVal)}" data-explain-label="${esc(metricLabel)}" data-explain-source="phases.wattnot.metrics.quality_pct">${qCell}</td>
        <td>${chip(label)}</td>
        <td><a class="report-link" href="${esc(reportHref)}">Report &rarr;</a></td>
      </tr>`;
  }).join("");

  body.querySelectorAll("tr[data-side]").forEach((tr) => tr.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.target.closest("a")) { e.preventDefault(); tr.click(); }
  }));
  bindSide(body);
}

main();
