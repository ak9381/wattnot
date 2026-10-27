import { el, chip, clear, getJSON, num, usd, sig, pct } from "./ui.mjs";

const $ = (id) => document.getElementById(id);
let replay, decisions, grid, region = true;

const WHY = { "failed: fixed": "failed the fixed-prompt check", "failed: judge": "failed the judge check", "failed: p99": "was too slow (p99)" };
const why = (w) => WHY[w] || w.replace(/^failed: /, "failed the ") + "";

async function boot() {
  [replay, decisions, grid] = await Promise.all([getJSON("data/route/replay.json"), getJSON("data/route/decisions.json"), getJSON("data/route/grid.json")]);
  $("srcchip").append(chip(replay.source_receipt.label));
  $("src").textContent = `${replay.requests} requests · ${replay.classes.length} classes · source receipt ${replay.source_receipt.sha256.slice(0, 12)}…`;
  $("m-effort").onclick = () => set(false);
  $("m-region").onclick = () => set(true);
  gridTable(); notes(); set(true);
}

function set(r) {
  region = r;
  $("m-effort").setAttribute("aria-pressed", String(!r));
  $("m-region").setAttribute("aria-pressed", String(r));
  totals(); cards();
}

function totalCard(title, t, base, kind) {
  const win = (kind === "effort_only" && !region) || (kind === "effort_and_region" && region);
  const dim = (kind === "effort_only" && region) ? false : false;
  const ch = replay.totals.change_pct[kind];
  const w = Math.max(3, (t.usd / base.usd) * 100);
  return el("div", { class: "tc" + (win ? " win" : "") + (dim ? " dim" : "") },
    el("div", { class: "t" }, title, kind === "effort_and_region" ? chip("SIMULATED") : null),
    el("span", { class: "big-n" }, usd(t.usd, 3)),
    el("div", { class: "bar", "aria-hidden": "true" }, el("i", { style: `width:${w}%` })),
    el("small", {}, el("b", { style: "font-weight:400;color:var(--tx)" }, ch ? `${pct(ch.usd)} cost` : "Reference"), ch ? el("span", {}, " · ", chip(replay.labels.usd)) : el("span", {}, " · ", chip(replay.labels.usd))),
    el("small", {}, `Energy ${sig(t.wh_low, 3)} to ${sig(t.wh_high, 3)} Wh `, chip(replay.labels.joules), ch ? ` · mid ${pct(ch.j_mid)}` : ""));
}

function totals() {
  const T = replay.totals, box = clear($("totals"));
  box.append(totalCard("Always xhigh, home region", T.baseline, T.baseline, "baseline"),
    totalCard("Router: effort only", T.effort_only, T.baseline, "effort_only"),
    totalCard("Router: effort + region", T.effort_and_region, T.baseline, "effort_and_region"));
  // the baseline has no change row
  box.firstChild.querySelector("small").firstChild.textContent = "Reference";
}

function row(k, a, b, labelA) { return el("div", {}, k, el("span", {}, a, b ? " → " + b : "", labelA ? chip(labelA) : null)); }

function cards() {
  const box = clear($("cards"));
  for (const c of replay.classes) {
    const pick = region ? c.effort_and_region : c.effort_only, base = c.baseline, d = decisions[c.class];
    const ex = c.excluded || [];
    box.append(el("div", { class: "dc" },
      el("div", { class: "hd" }, el("h3", {}, c.class), el("span", {}, `${c.requests} requests`)),
      el("div", { class: "eff", "aria-label": `effort ${base.effort} to ${pick.effort}` }, el("span", { class: "e" }, base.effort), el("span", { class: "arr" }, "→"), el("span", { class: "e to" }, pick.effort),
        el("span", { class: "note", style: "margin-left:6px" }, `passes the check (${num(pick.pass_rate * 100, 0)}%)`)),
      region ? el("div", { class: "rg" }, `region ${base.region} → ${pick.region}`, chip("SIMULATED"), pick.tie_broken_by_grid ? el("span", { style: "color:var(--mu)" }, "· tie broken by grid") : null) : null,
      el("p", { class: "why" }, pick.reason),
      el("div", { class: "kv" },
        row("Cost, these requests", usd(base.usd, 4), usd(pick.usd, 4), replay.labels.usd),
        row("Energy, mid estimate", `${num(base.j_mid, 0)} J`, `${num(pick.j_mid, 0)} J`, replay.labels.joules),
        region ? row("Grid used", `${num(base.gco2_per_kwh, 0)}`, `${num(pick.gco2_per_kwh, 0)} gCO₂/kWh`, replay.labels.region) : null),
      ex.length ? el("div", { class: "note", style: "margin-top:10px" }, "Left out: ", ...ex.map((e) => el("span", { class: "ex" }, `${e.id.replace(/@.*/, "")} ${why(e.why)}`))) : null,
      ranked(d)));
  }
}

function ranked(d) {
  const rows = d.ranked.filter((r) => (region ? true : r.region === replay.home_region || r.status === "excluded"));
  return el("details", {}, el("summary", {}, `All ${rows.length} options the router looked at`),
    el("table", { class: "rk" }, el("tr", {}, ...["option", "status", "J / good answer", "USD / req"].map((h) => el("th", {}, h))),
      ...rows.map((r) => el("tr", { class: r.status }, el("td", {}, r.id), el("td", {}, r.status === "excluded" ? "left out" : r.status), el("td", { class: "r" }, r.j_per_good_answer ? num(r.j_per_good_answer, 1) : "–"), el("td", { class: "r" }, r.usd_per_req ? usd(r.usd_per_req, 5) : "–")))));
}

function gridTable() {
  const t = $("grid");
  t.append(el("tr", {}, el("th", {}, "Region"), el("th", {}, "gCO₂ per kWh")));
  for (const g of grid) t.append(el("tr", { class: g.region === replay.home_region ? "home" : "" }, el("td", {}, g.region), el("td", {}, num(g.gco2_per_kwh, 0))));
  $("gridnote").textContent = `${replay.grid.source}. As of ${replay.grid.as_of}. ${replay.grid.kind}.`;
}

function notes() { $("notes").append(...replay.notes.map((n) => el("li", {}, n))); }

boot().catch((e) => { $("cards").textContent = "Could not load the router data: " + e.message; });
