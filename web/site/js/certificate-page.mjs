import { checkCertificate } from "./certcheck.mjs";
import { flipOneDigit } from "./wattnot-verify.mjs";
import { el, chip, clear, getJSON, sig, num } from "./ui.mjs";

const $ = (id) => document.getElementById(id);
let idx, register, trusted, serial, original, cert, receiptDoc, bundleDoc, tampered = "";

async function boot() {
  $("mockchip").append(chip("MOCK"));
  [idx, register] = await Promise.all([getJSON("data/certificate/index.json"), getJSON("data/certificate/register.json")]);
  trusted = (await getJSON("data/trusted-keys.json")).keys.map((k) => k.signer);
  for (const m of idx.minted) $("serials").append(el("button", { class: "pill", type: "button", "data-s": m.serial, "aria-pressed": "false", onclick: () => pick(m.serial) }, m.serial, " ", chip(m.grade)));
  refused(); roadmap();
  const q = new URLSearchParams(location.search).get("serial");
  await pick(idx.minted.some((m) => m.serial === q) ? q : idx.minted[0].serial);
}

async function pick(s) {
  serial = s; tampered = "";
  for (const b of $("serials").children) b.setAttribute("aria-pressed", String(b.dataset.s === s));
  const m = idx.minted.find((x) => x.serial === s);
  original = await getJSON(`data/certificate/${s}.json`);
  [receiptDoc, bundleDoc] = await Promise.all([getJSON(`data/receipts/${m.receipt}.json`), getJSON(`data/receipts/${m.receipt}.sigstore.json`)]);
  $("doc").src = `data/certificate/${s}.html`;
  $("who").textContent = `${original.subject.model} · ${original.period.requests} requests`;
  cert = structuredClone(original);
  await recheck(); figures(); limits();
}

async function recheck() {
  const checks = await checkCertificate(cert, { register, receipt: receiptDoc, bundle: bundleDoc, trusted });
  const bad = checks.some((c) => !c.ok);
  const v = clear($("verdict"));
  $("verdict").classList.toggle("bad", bad);
  v.append(el("div", { class: "row" }, el("span", { class: `grade ${bad ? "UNVERIFIABLE" : cert.receipt.grade}` }, bad ? "DOES NOT CHECK OUT" : `RECEIPT ${cert.receipt.grade}`), chip("MOCK", true)),
    el("h2", {}, bad ? "Do not rely on this page." : cert.headline.split("(")[0].trim()),
    el("p", {}, bad ? "Something in the statement no longer matches what was minted." : `Serial ${cert.serial}. Restates one receipt; it adds no new measurement.`));
  const st = clear($("steps"));
  for (const c of checks) st.append(el("li", {}, el("span", { class: `ic2 ${c.ok ? "ok" : "no"}`, "aria-label": c.ok ? "passed" : "failed" }, c.ok ? "✓" : "✕"),
    el("div", {}, el("b", {}, c.name[0].toUpperCase() + c.name.slice(1)), el("div", { class: "why" + (c.ok ? "" : " err") }, c.detail))));
}

function figures() {
  const f = original.figures, L = original.labels, box = clear($("figs"));
  const wh = f.wh_per_request, rows = [
    ["Energy per request, before", [sig(wh.baseline) + " Wh", chip(L.energy)]],
    ["Energy per request, after", [sig(wh.candidate) + " Wh", chip(L.energy)]],
    ["Saving", [`${num(f.saving_pct.mean)}%`, chip(L.basis)]],
    ["95% range", [`${num(f.saving_pct.ci_low)} to ${num(f.saving_pct.ci_high)}%`, chip(L.basis)]],
  ];
  if (f.usd_per_request) rows.push(["Cost per request (USD)", [`${f.usd_per_request.baseline.toExponential(2)} → ${f.usd_per_request.candidate.toExponential(2)}`, chip(L.basis === "SIMULATED" ? "SIMULATED" : "ESTIMATE")]]);
  rows.push(["Quality pass rate", [`${f.pass_rate.baseline} → ${f.pass_rate.candidate}`, chip(L.basis)]]);
  box.append(el("div", { class: "k", style: "margin-bottom:8px" }, "The figures on it"), el("div", { class: "kv" }, rows.map(([k, v]) => el("div", {}, k, el("span", {}, ...v)))));
}
function limits() { clear($("limits")).append(...original.limits.map((t) => el("li", {}, t))); }

function refused() {
  $("refused").append(...idx.refused.map((r) => el("div", { class: "rf" }, el("b", {}, r.case), ...r.reasons.map((x) => el("span", {}, `${x.code}: ${x.detail}`)))));
}
async function roadmap() {
  const r = await getJSON("data/certificate/roadmap.json");
  $("rm-cav").textContent = r.caveat;
  $("roadmap").append(...r.items.map((i) => el("div", { class: "rm" }, el("b", {}, i.need), el("span", {}, i.why), el("span", {}, "WattNot has: ", i.wattnot_has), el("span", {}, el("em", {}, "Gap: "), i.gap))));
}

function reset() { cert = structuredClone(original); $("tmsg").textContent = "Back to the minted copy."; recheck(); }
$("t-digit").onclick = () => {
  const t = JSON.stringify(cert, null, 2), n = flipOneDigit(t, 0);
  try { cert = JSON.parse(n); $("tmsg").textContent = "Changed one digit in the in-memory copy."; } catch { /* keep */ }
  recheck();
};
$("t-mark").onclick = () => { cert = structuredClone(original); cert.status = "OK"; cert.watermark = { lines: [], permanent: false }; $("tmsg").textContent = "Removed the MOCK mark from the in-memory copy."; recheck(); };
$("t-reset").onclick = reset;
$("doc").addEventListener("load", () => { try { $("doc").style.height = $("doc").contentDocument.documentElement.scrollHeight + "px"; } catch { /* keep default */ } });
boot().catch((e) => { $("verdict").textContent = "Could not load the statement: " + e.message; });
