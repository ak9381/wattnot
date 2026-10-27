import { flexChart } from "./flexchart.mjs";
import { verifyDocument } from "./wattnot-verify.mjs";
import { el, chip, clear, getJSON, num, sig } from "./ui.mjs";

const $ = (id) => document.getElementById(id);
const LIMIT = { cap_floor: "the lowest power limit it is allowed to set", p99: "its latency budget", quality: "its answer-quality floor" };
let chart = null, cache = {};

async function boot() {
  $("sigchip").append(chip("SIMULATED"));
  const idx = await getJSON("data/flex/index.json");
  const q = new URLSearchParams(location.search).get("event");
  for (const id of idx.events) $("events").append(el("button", { class: "pill", type: "button", "data-id": id, "aria-pressed": "false", onclick: () => show(id) }, id));
  await show(idx.events.includes(q) ? q : idx.events[0]);
}

async function show(id) {
  for (const b of $("events").children) b.setAttribute("aria-pressed", String(b.dataset.id === id));
  const [trace, r, b] = cache[id] || (cache[id] = await Promise.all([
    getJSON(`data/flex/${id}.trace.json`), getJSON(`data/flex/${id}.receipt.json`), getJSON(`data/flex/${id}.receipt.sigstore.json`)]));
  const fx = r.flex, o = fx.outcome;
  $("evnote").textContent = `${fx.event.note} Duration ${fx.event.duration_s} s.`;
  chart = flexChart($("chart"), trace, { onHover: hover, label: `Simulated flex event ${id}: GPU power falls to the target with latency and quality tracked against their limits` });
  chart.play();
  cards(fx, o);
  receipt(id, r, b, fx);
  $("nots").replaceChildren(...[fx.scope, ...fx.not_claimed].map((t) => el("li", { style: "padding:8px 0;border-top:1px solid var(--ln)" }, t)));
}

function hover(p) {
  const box = clear($("read"));
  if (!p) { box.textContent = "Move over the chart to read a second."; return; }
  const b = (v, u) => el("b", {}, v + u);
  box.append(`t = `, b(p.t_s.toFixed(0), " s"), `  ${p.phase}  ·  power `, b(p.power_w.toFixed(0), " W"), `  ·  limit `, b(p.cap_w, " W"),
    `  ·  p99 `, b(p.p99_ms.toFixed(0), " ms"), `  ·  passing `, b((p.pass_rate * 100).toFixed(0), "%"), " ", chip("SIMULATED"));
}

function stat(title, big, unit, small, label = "SIMULATED") {
  return el("div", { class: "stat" }, el("div", { class: "t" }, title, chip(label)), el("span", { class: "big-n" }, big, el("span", { style: "font-size:18px;color:var(--mu)" }, unit)), el("small", {}, small));
}

function cards(fx, o) {
  const L = fx.label;
  const met = o.status === "full";
  $("cards").replaceChildren(
    stat("Power cut", num(o.delivered_pct, 1), "%", `${num(o.delivered_w, 0)} W less than before. The ask was ${num(o.target_pct, 0)}% (${num(o.target_w, 0)} W).` +
      (met ? " Target met." : ` Stopped short at ${LIMIT[o.limit] || o.limit}.`), L),
    stat("Time to target", o.seconds_to_target === null ? "n/a" : num(o.seconds_to_target, 0), o.seconds_to_target === null ? "" : " s", o.seconds_to_target === null ? "The target was not reached." : "From the moment the event starts, in steps of " + fx.controller.step_w + " W.", L),
    stat("p99 during event", num(o.p99_ms.event_max, 0), " ms", `Worst second. The budget is ${num(o.p99_ms.sla, 0)} ms; it was over for ${num(o.p99_ms.sla_violation_s, 0)} s. Before the event: ${num(o.p99_ms.before_mean, 0)} ms.`, L),
    stat("Answers passing", num(o.pass_rate.event * 100, 0), "%", `Before the event: ${num(o.pass_rate.before * 100, 0)}%. ${o.ramp_back_s === null ? "Power did not return to normal inside the window." : "Power returned to normal in " + num(o.ramp_back_s, 0) + " s after the event."}`, L),
  );
}

async function receipt(id, r, b, fx) {
  const box = clear($("rcpt"));
  const res = await verifyDocument(r, b, (await getJSON("data/trusted-keys.json")).keys.map((k) => k.signer));
  box.append(el("div", { class: "kv" },
    el("div", {}, "Receipt", el("span", {}, r.receipt_id)),
    el("div", {}, "Re-checked in your browser", el("span", {}, el("span", { class: `grade ${res.grade}` }, res.grade))),
    el("div", {}, "Fingerprint", el("span", { class: "hash" }, r.integrity.sha256.slice(0, 20) + "…")),
    el("div", {}, "Readings", el("span", {}, "", chip(fx.label))),
    el("div", {}, "Grid signal", el("span", {}, "", chip(fx.signal.label)))),
    el("p", { style: "margin-top:16px" }, el("a", { class: "btn sm", href: `verify.html?receipt=data/flex/${id}.receipt.json` }, "Open in the verifier →")));
}

$("replay").addEventListener("click", () => chart && chart.play());
boot().catch((e) => { $("chart").textContent = "Could not load the flex data: " + e.message; });
