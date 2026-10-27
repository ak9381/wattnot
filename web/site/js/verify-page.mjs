import { verifyDocument, flipOneDigit } from "./wattnot-verify.mjs";
import { el, chip, clear, getJSON, sig, num, pct } from "./ui.mjs";

const $ = (id) => document.getElementById(id);
const rtext = $("rtext"), btext = $("btext");
let samples = [], trusted = null, current = null, orig = "", flips = 0, seq = 0, timer = 0;

const NAMES = { schema: "Right shape", sha256: "Fingerprint matches", signature: "Signature is valid, key is trusted",
  rekor: "Public log entry", joules: "Joules re-derived", grade: "Grade re-derived" };

const pretty = (o) => JSON.stringify(o, null, 2);

async function boot() {
  const q = new URLSearchParams(location.search);
  samples = await getJSON("data/receipts/index.json");
  const tk = await getJSON("data/trusted-keys.json");
  trusted = tk.keys.map((k) => k.signer);
  $("trusted").textContent = `${trusted.join(", ")}. ${tk.note}`;
  const wrap = $("samples");
  for (const s of samples) {
    wrap.append(el("button", { class: "pill" + (s.id.startsWith("forged") ? " f" : ""), type: "button", "data-id": s.id,
      "aria-pressed": "false", onclick: () => load(s.id) }, s.title));
  }
  const ext = q.get("receipt");  // e.g. data/flex/demo-evt-1.receipt.json (bundle sits beside it)
  if (ext && /^data\/[\w./-]+\.json$/.test(ext) && !ext.includes("..")) await loadPath(ext);
  else await load(samples.some((s) => s.id === q.get("sample")) ? q.get("sample") : samples[0].id);
  if (q.get("flip")) { flip(); }
}

async function load(id) {
  current = id;
  const s = samples.find((x) => x.id === id);
  const [r, b] = await Promise.all([getJSON(`data/receipts/${id}.json`), getJSON(`data/receipts/${id}.sigstore.json`)]);
  orig = pretty(r); rtext.value = orig; btext.value = pretty(b); flips = 0;
  for (const p of $("samples").children) p.setAttribute("aria-pressed", String(p.dataset.id === id));
  $("sample-note").textContent = s.note;
  run();
}

async function loadPath(path) {
  const [r, b] = await Promise.all([getJSON(path), getJSON(path.replace(/\.json$/, ".sigstore.json"))]);
  current = null; orig = pretty(r); rtext.value = orig; btext.value = pretty(b); flips = 0;
  for (const p of $("samples").children) p.setAttribute("aria-pressed", "false");
  $("sample-note").textContent = `Loaded ${path} from this site.`;
  run();
}

function flip() { rtext.value = flipOneDigit(rtext.value, flips++); run(); }

function parse(text) { try { return [JSON.parse(text), null]; } catch (e) { return [null, e.message]; } }

async function run() {
  const my = ++seq;
  rtext.classList.toggle("edited", rtext.value !== orig);
  renderDiff();
  const [r, re] = parse(rtext.value), [b, be] = parse(btext.value);
  if (re) return render(null, { error: `The receipt is not valid JSON: ${re}` }, null);
  let res;
  try { res = await verifyDocument(r, be ? null : b, trusted); }
  catch (e) { return render(r, { error: String(e.message) }, null); }
  if (my === seq) render(r, null, res);
}

function renderDiff() {
  const box = clear($("diff"));
  if (rtext.value === orig) return;
  const a = orig.split("\n"), c = rtext.value.split("\n");
  if (a.length !== c.length) { box.append(el("span", { class: "ln" }, "Lines were added or removed.")); return; }
  const rows = [];
  for (let i = 0; i < a.length && rows.length < 3; i++) if (a[i] !== c[i]) rows.push(i);
  for (const i of rows) {
    let s = 0; while (s < a[i].length && a[i][s] === c[i][s]) s++;
    let e = 0; while (e < a[i].length - s && e < c[i].length - s && a[i][a[i].length - 1 - e] === c[i][c[i].length - 1 - e]) e++;
    const oa = a[i].trim(), oc = c[i].trim(), off = a[i].length - a[i].trimStart().length;
    const s0 = Math.max(0, s - off), e0 = Math.max(0, e - (a[i].length - a[i].trimEnd().length));
    box.append(el("span", { class: "ln" }, `line ${i + 1}  was  `, oa.slice(0, s0), el("del", {}, oa.slice(s0, oa.length - e0)), oa.slice(oa.length - e0),
      el("br"), `${" ".repeat(String(i + 1).length + 7)}now  `, oc.slice(0, s0), el("ins", {}, oc.slice(s0, oc.length - e0)), oc.slice(oc.length - e0)));
  }
}

function hashDiff(want, got) {
  const out = [];
  for (let i = 0; i < got.length; i++) out.push(want[i] === got[i] ? got[i] : el("span", { class: "d" }, got[i]));
  return out;
}

const SAYS = {
  VALID: "Measured, signed, and logged in a public transparency log.",
  ESTIMATE: "The maths and the signature hold. It stops at ESTIMATE because there is no public log entry, or the run was labelled ESTIMATE.",
  SIMULATED: "The maths and the signature hold, but the numbers come from simulated data. It can never rank above SIMULATED.",
  NOISE: "The maths and the signature hold, but the change sits inside run-to-run noise, so no saving is claimed.",
};

function render(r, fatal, res) {
  const v = clear($("verdict")), st = clear($("steps"));
  const bad = fatal || res.grade === "UNVERIFIABLE";
  $("verdict").classList.toggle("bad", !!bad);
  $("verdict").classList.toggle("good", !bad);
  if (fatal) {
    v.append(el("span", { class: "grade UNVERIFIABLE" }, "UNVERIFIABLE"), el("h2", {}, "Does not verify."), el("p", {}, fatal.error));
    clear($("claims")); clear($("joules")); $("claims").hidden = $("joules").hidden = true;
    return;
  }
  $("claims").hidden = $("joules").hidden = false;
  const firstBad = res.steps.find((s) => !s.ok && s.name !== "rekor" && s.name !== "grade");
  v.append(el("span", { class: `grade ${res.grade}` }, res.grade),
    el("h2", {}, bad ? "Does not verify." : "Verifies."),
    el("p", {}, bad ? (firstBad ? explain(firstBad) : "A check failed.") : SAYS[res.grade]));
  if (bad) v.append(el("p", { class: "note", style: "margin-top:10px" }, "Nothing here can be trusted until the receipt is fixed or re-signed by a key you trust."));
  for (const s of res.steps) st.append(stepRow(s, res, bad));
  renderClaims(r);
  renderJoules(r, res);
}

function explain(s) {
  if (s.name === "sha256") return "The receipt no longer matches its fingerprint. Someone changed it after it was signed.";
  if (s.name === "signature") return `The signature check failed: ${s.error}.`;
  if (s.name === "joules") return "The joules block does not match the receipt's own numbers.";
  if (s.name === "schema") return `The receipt is malformed: ${s.error}.`;
  return s.error || "A check failed.";
}

function stepRow(s, res, bad) {
  const na = s.name === "rekor" && !s.ok;
  const cls = na ? "na" : s.ok ? "ok" : "no";
  let why = [];
  if (s.name === "schema") why = s.ok ? ["Every field of spec wattnot.receipt/1 is there."] : [s.error];
  if (s.name === "sha256") {
    if (s.ok) why = [`${s.got.slice(0, 24)}… is what was signed.`];
    else if (s.got && s.expected) why = [el("div", {}, "signed:  ", el("span", { class: "hash" }, s.expected)), el("div", {}, "now:     ", el("span", { class: "hash" }, hashDiff(s.expected, s.got)))];
    else why = [s.error || "No fingerprint to compare."];
  }
  if (s.name === "signature") why = s.ok ? [`Signed by ${s.signer}, a trusted key.`] : [s.error];
  if (s.name === "rekor") why = s.ok ? [`Logged at index ${s.log_index}.`] : ["No public log entry (offline signature). The grade tops out at ESTIMATE."];
  if (s.name === "joules") why = s.ok ? ["Recomputed from the arms. It matches."] : [s.error];
  if (s.name === "grade") {
    why = [`${s.grade}.`];
    if (s.claimed && s.claimed !== s.grade) why.push(` The receipt says ${s.claimed}; that is ignored.`);
  }
  return el("li", {}, el("span", { class: `ic2 ${cls}`, "aria-label": cls === "ok" ? "passed" : cls === "no" ? "failed" : "not applicable" }, cls === "ok" ? "✓" : cls === "no" ? "✕" : "–"),
    el("div", {}, el("b", {}, NAMES[s.name] || s.name), el("div", { class: "why" + (cls === "no" ? " err" : "") }, ...why)));
}

function renderClaims(r) {
  const box = clear($("claims"));
  let b, c, label, res;
  try { b = r.baseline.wh_per_req.mean; c = r.candidate.wh_per_req.mean; label = r.measurement.label; res = r.result; } catch { box.append(el("p", { class: "note" }, "No numbers to show.")); return; }
  const save = res && res.saving_pct;
  const kv = [
    ["Energy per request, before", [sig(b), " Wh", chip(label)]],
    ["Energy per request, after", [sig(c), " Wh", chip(label)]],
  ];
  if (save) kv.push(["Saving, 95% range", [`${num(save.mean)}%  (${num(save.ci_low)} to ${num(save.ci_high)})`, chip(label)]]);
  if (res && res.welch) kv.push(["Chance it is noise (Welch p)", [sig(res.welch.p_value, 3), chip(label)]]);
  const q = [r.baseline?.quality?.fixed_pass_rate, r.candidate?.quality?.fixed_pass_rate];
  if (q[0] !== undefined) kv.push(["Quality pass rate, before → after", [`${q[0]} → ${q[1]}`, chip("MEASURED")]]);
  box.append(el("div", { class: "k", style: "margin-bottom:6px" }, "What the receipt says"),
    el("p", { class: "note", style: "margin-bottom:8px" }, `${r.subject?.model ?? "model"} · ${r.subject?.host?.gpu ?? ""}`),
    el("div", { class: "kv" }, kv.map(([k, v]) => el("div", {}, k, el("span", {}, ...v)))));
}

function renderJoules(r, res) {
  const box = clear($("joules"));
  const j = res.joules;
  if (!j) { box.hidden = true; return; }
  const row = (k, a, b2, p) => el("div", {}, k, el("span", {}, `${sig(a, 5)} → ${sig(b2, 5)}`, el("em", { style: "color:var(--mu);font-style:normal" }, p ? `  (${pct(p)})` : "")));
  box.append(el("div", { class: "row", style: "justify-content:space-between" }, el("div", { class: "k", style: "margin:0" }, "Joules per answer, re-derived"), chip(j.label)),
    el("p", { class: "note", style: "margin:8px 0" }, "Energy per request × 3600, divided by the share of answers that pass. Computed here, from the numbers above."),
    el("div", { class: "kv" },
      row("J per answer", j.baseline.j_per_answer, j.candidate.j_per_answer, j.change_pct.j_per_answer),
      row("J per good answer", j.baseline.j_per_good_answer, j.candidate.j_per_good_answer, j.change_pct.j_per_good_answer)));
}

rtext.addEventListener("input", () => { clearTimeout(timer); timer = setTimeout(run, 120); });
btext.addEventListener("input", () => { clearTimeout(timer); timer = setTimeout(run, 120); });
$("flip").addEventListener("click", flip);
$("reset").addEventListener("click", () => { if (current) load(current); else { rtext.value = orig; run(); } });

async function takeFiles(files) {
  let got = false;
  for (const f of files) {
    const [doc] = parse(await f.text());
    if (!doc) continue;
    if (doc.mediaType) btext.value = pretty(doc);
    else { orig = pretty(doc); rtext.value = orig; for (const p of $("samples").children) p.setAttribute("aria-pressed", "false"); }
    got = true;
  }
  if (got) { $("sample-note").textContent = "Your own file. Nothing was uploaded."; run(); }
}
$("file").addEventListener("change", (e) => takeFiles(e.target.files));
const drop = $("drop");
for (const t of ["dragenter", "dragover"]) drop.addEventListener(t, (e) => { e.preventDefault(); drop.classList.add("on"); });
for (const t of ["dragleave", "drop"]) drop.addEventListener(t, (e) => { e.preventDefault(); drop.classList.remove("on"); });
drop.addEventListener("drop", (e) => takeFiles(e.dataTransfer.files));

boot().catch((e) => { $("verdict").textContent = "Could not load the sample receipts: " + e.message; });
