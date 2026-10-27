// The guided story: six scenes, one idea each. Every number comes from the bundle built out of
// recorded runs; the label next to a number says how it was obtained. Nothing here is invented.
import { ArchView, panelHtml, statHtml, fmt, statVal } from "./arch.js";
import { esc, pill } from "./theme.js";

const G = () => window.gsap;
const $ = (s, r = document) => r.querySelector(s);
const reduce = () => matchMedia("(prefers-reduced-motion: reduce)").matches;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const html = (s) => { const t = document.createElement("template"); t.innerHTML = s.trim(); return t.content.firstElementChild; };
export const mean = (a) => a.reduce((x, y) => x + y, 0) / a.length;

export function countUp(el, to, { dec = 0, dur = 1.4, prefix = "", suffix = "" } = {}) {
  const f = (v) => `${prefix}${v.toLocaleString("en-US", { minimumFractionDigits: dec, maximumFractionDigits: dec })}${suffix}`;
  if (!G() || reduce()) { el.textContent = f(to); return; }
  const o = { v: 0 }; el._o = o; G().to(o, { v: to, duration: dur, ease: "power2.out", onUpdate: () => (el.textContent = f(o.v)) , onComplete: () => (el.textContent = f(to)) });
}
const big = (n, unit, label, tag, o = {}) => `<div class="big"><b class="num" data-n="${n ?? ""}" data-dec="${o.dec ?? 0}" data-pre="${o.pre ?? ""}">${n == null ? "–" : esc(String(n))}${unit ? `<small>${esc(unit)}</small>` : ""}</b><span class="l">${esc(label)}</span>${tag ? pill(tag) : ""}</div>`;
const runCounts = (root) => root.querySelectorAll("[data-n]").forEach((b) => { const v = b.dataset.n; if (v === "" || Number.isNaN(+v)) return; let el = b.querySelector(":scope > span.cu"); if (!el) { el = document.createElement("span"); el.className = "cu"; b.prepend(el); [...b.childNodes].forEach((c) => c.nodeType === 3 && c.remove()); } G()?.killTweensOf(el._o); countUp(el, +v, { dec: +b.dataset.dec, prefix: b.dataset.pre, dur: 1.3 }); });

// ---- scene content ------------------------------------------------------------------------
export function derive(d) {
  const S = d.story, cap = S.levers.find((l) => l.id === "cap"), topk = S.levers.find((l) => l.id === "topk"), hosted = S.levers.find((l) => l.id === "hosted");
  return { S, cap, topk, hosted, facts: Object.fromEntries(S.task.facts.map((f) => [f.k, f])), node: (id) => d.nodes.find((n) => n.id === id), stat: (id, k) => d.nodes.find((n) => n.id === id).stats.find((s) => s.k === k) };
}
export const impactLine = (D, id = "cap") => {
  const pm = (L) => L.per_million, who = "<b>Who benefits:</b> data-center operators with a power and cooling-water budget.";
  if (id === "topk") return `${who} Serving Qwen3 with fewer experts is <b>${fmt(pm(D.topk).kwh)} kWh</b>, <b>$${fmt(pm(D.topk).usd)}</b> and about <b>${fmt(pm(D.topk).litres)} L</b> of cooling water less per million requests ${pill("ESTIMATE")} <span class="muted">(agreement gate passed)</span>.`;
  if (id === "hosted") return `${who} Teams paying per token save about <b>$${fmt(pm(D.hosted).usd)}</b> per million requests ${pill("ESTIMATE")} <span class="muted">(measured on 75 requests; the host's own meter was not available)</span>.`;
  return `${who} For the load above, 400 W instead of 700 W is <b>${fmt(pm(D.cap).kwh)} kWh</b> and about <b>${fmt(pm(D.cap).litres)} L</b> of cooling water less per million requests ${pill("ESTIMATE")} <span class="muted">(quality check pending)</span>.`;
};

const SCENES = [
  { id: "task", name: "The task", hold: 3200, build(el, D) {
    const f = D.facts;
    el.innerHTML = `<h1 class="hl" data-in>A coding assistant that ${f.conc.v} people use at once.</h1>
      <p class="lede" data-in>One H100 card. Fast answers, a small power bill, and answers that stay right. The slowest requests decide how it feels.</p>
      <div class="bigrow" data-in>${big(f.conc.v, "", "requests at the same time", "MEASURED")}${big(f.p99.v, "s", "slowest 1% of requests (p99)", "MEASURED", { dec: 2 })}${big(f.cap.v, "W", "the card's stock power cap", "MEASURED")}${big(f.req.v, "", "requests in each round", "MEASURED")}</div>
      <div class="why" data-in>${D.S.task.why.map((w) => `<p>${esc(w)}</p>`).join("")}</div>
      <p class="small" style="margin-top:1em" data-in>Not in our recorded runs yet: long prompts and agents that make many tool calls. The prompts here are short (about ${f.prompt.v} characters), so we show <b>–</b> for those.</p>
      <div class="impact" data-in>${impactLine(D)}</div>`;
    return { async enter() { runCounts(el); await sleep(2600); } };
  } },
  { id: "stock", name: "Stock run", hold: 1600, build(el, D) {
    const ts = D.samples, base = ts.filter((x) => x.cap_w === 700), mw = mean(base.map((x) => x.power_w));
    el.innerHTML = `<h2 class="hl" data-in>First, run it the way it ships.</h2>
      <div class="split"><div><div id="g2"></div><p class="cap" id="cap2" style="margin-top:12px;min-height:3em">&nbsp;</p></div>
      <div><div class="bigrow" style="flex-direction:column;gap:22px">${big(Math.round(mw), "W", "average draw under the 700 W cap", "MEASURED")}${big(+(D.cap.wh_before * 1000).toFixed(2), "mWh", "energy for one request", "MEASURED", { dec: 2 })}${big(+D.cap.p99_before_s.toFixed(2), "s", "slowest 1% of requests", "MEASURED", { dec: 2 })}</div></div></div>`;
    const view = new ArchView($("#g2", el), D.d, { ids: ["workload", "models", "telemetry"], chain: true, mode: "row", stats: 0, onSelect: D.open, onCue: (c) => { $("#cap2", el).innerHTML = esc(c.text); } });
    const cues = [
      { node: "workload", sub: "load", text: `${D.facts.req.v} requests ready: 8 public prompts × 16`, tag: "MEASURED" },
      { node: "workload", sub: "send", text: `${D.facts.conc.v} at a time to the server`, tag: "MEASURED" },
      { node: "models", sub: "fit", text: "Muse Glimmer 30B on one H100 80GB", tag: "MEASURED" },
      { node: "models", sub: "serve", text: "Served at the stock 700 W cap with normal top-k", tag: "MEASURED" },
      { node: "telemetry", sub: "sample", text: "Power read 10 times a second from the card", tag: "MEASURED" },
      { node: "telemetry", sub: "integrate", text: `Energy per request: ${(D.cap.wh_before * 1000).toFixed(2)} mWh`, tag: "MEASURED" }];
    el._view = view;
    return { view, async enter() { runCounts(el); await view.play(cues, { gap: 1000 }); }, leave() { view.stop(); } };
  } },
  { id: "wattnot", name: "WattNot steps in", hold: 1800, build(el, D) {
    const rounds = D.d.agent.rounds, r3 = rounds.find((r) => r.round === 3), r4 = rounds.find((r) => r.round === 4), g = D.d.story, ap = D.d.approvals?.[0];
    const ledger = D.d.ledger;
    el.innerHTML = `<h2 class="hl" data-in>Then an agent proposes. A person approves first.</h2>
      <div class="split" style="grid-template-columns:minmax(0,1.7fr) minmax(0,1fr);align-items:start"><div><div id="g3"></div><p class="cap" id="cap3" style="margin-top:12px;min-height:3em">&nbsp;</p></div>
      <div id="col3" style="display:flex;flex-direction:column;gap:12px">
        <div class="say" id="say" style="visibility:hidden"><b>Muse Spark</b> ${pill("SIMULATED")}<br><span id="saytxt"></span><div class="cap" style="margin-top:6px">The reply is a real recorded Muse Spark decision. The surface it tuned was a simulator, so its savings are SIMULATED.</div></div>
        <div class="acard" id="acard" style="visibility:hidden"><h4>Approve before apply</h4>
          <div class="kv"><span>change</span><b>power cap, 200–700 W on one GPU</b></div>
          <div class="kv"><span>also allowed</span><b>vLLM settings, shed up to 35% for 300 s</b></div>
          <div class="kv"><span>undo</span><b>every setting put back at the end</b></div>
          <div class="act"><button class="btn pri" id="appr">Approve</button><button class="btn" id="rej">Reject</button><span class="res" id="res"></span></div>
          <div class="cap" style="margin-top:8px">In the recorded run this grant was pre-approved through a flag on the kit script, not clicked live.</div></div></div></div>`;
    const approve = (by) => { const c = $("#acard", el); c.classList.add("ok"); $("#res", el).textContent = by; $("#appr", el).disabled = $("#rej", el).disabled = true; };
    $("#appr", el).onclick = () => approve("approved");
    $("#rej", el).onclick = () => { $("#res", el).textContent = "rejected: nothing is applied"; $("#res", el).style.color = "var(--red)"; $("#appr", el).disabled = $("#rej", el).disabled = true; };
    const say = (t) => { const s = $("#say", el); s.style.visibility = "visible"; $("#saytxt", el).innerHTML = t; G()?.fromTo(s, { opacity: 0, y: 8 }, { opacity: 1, y: 0, duration: 0.4 }); };
    const view = new ArchView($("#g3", el), D.d, { ids: ["agent", "proposals", "apply", "gates"], chain: true, mode: "row", stats: 0, onSelect: D.open, onCue: (c) => {
      $("#cap3", el).innerHTML = esc(c.text);
      if (c.key === "r3") say(`power_cap_w → <b>${r3.value} W</b>. “${esc(r3.reason)}.”`);
      if (c.key === "r4") say(`power_cap_w → <b>${r4.value} W</b>. “${esc(r4.reason)}.”`);
      if (c.key === "card") { const a = $("#acard", el); a.style.visibility = "visible"; G()?.fromTo(a, { opacity: 0, y: 8 }, { opacity: 1, y: 0, duration: 0.4 }); }
      if (c.key === "ok" && !$("#acard", el).classList.contains("ok")) { const b = $("#appr", el); b.classList.add("on"); setTimeout(() => approve("approved"), 500); }
    } });
    const cues = [
      { node: "agent", sub: "read", text: `Reads the run so far: 700 W stock, p99 ${D.cap.p99_before_s.toFixed(2)} s`, tag: "MEASURED" },
      { node: "agent", sub: "guard", text: `Checks the spend cap first: $${fmt(ledger.total_usd)} spent of $${ledger.cap_usd}`, tag: "MEASURED" },
      { node: "agent", sub: "call", text: "Muse Spark 1.3 answers through the Meta API", tag: "MEASURED", key: "r3" },
      { node: "agent", sub: "parse", text: "Reply checked against the allowed settings", key: "r4" },
      { node: "proposals", sub: "write", text: "A grant is written: what may change, how far, and for how long", key: "card" },
      { node: "proposals", sub: "decide", text: "Waits for a yes. Nothing is applied until it comes.", key: "ok" },
      { node: "proposals", sub: "audit", text: "Approval chained into the audit log", tag: "MEASURED" },
      { node: "apply", sub: "check", text: "Inside the approved bounds: go", tag: "MEASURED" },
      { node: "apply", sub: "cap", text: "Cap stepped down from 700 W to 400 W in 50 W steps", tag: "MEASURED" },
      { node: "gates", sub: "p99", text: `p99 ${D.cap.p99_change_pct > 0 ? "+" : ""}${D.cap.p99_change_pct}%: inside the 10% limit`, tag: "MEASURED" },
      { node: "gates", sub: "fixed", text: "Fixed answer check: pending (its baseline passes only about 6%)", tag: "MEASURED", ok: false },
      { node: "gates", sub: "decide", text: "Held as “measured, quality check pending”, not claimed as passed", tag: "MEASURED", ok: false }];
    return { view, async enter() { await view.play(cues, { gap: 1250 }); }, leave() { view.stop(); } };
  } },
  { id: "rerun", name: "Re-run", hold: 3000, build(el, D) {
    const S = D.samples, ps = S.map((s) => s.power_w), max = Math.max(...ps) * 1.08, mid = ps.length;
    const lo = mean(S.filter((x) => x.cap_w === 400).map((x) => x.power_w)), hi = mean(S.filter((x) => x.cap_w === 700).map((x) => x.power_w));
    const W = 800, H = 260, X = (i) => 10 + (i / (mid - 1)) * (W - 20), Y = (v) => H - 14 - (v / max) * (H - 30);
    const line = S.map((s, i) => `${i ? "L" : "M"}${X(i).toFixed(1)},${Y(s.power_w).toFixed(1)}`).join(""), capl = S.map((s, i) => `${i ? "L" : "M"}${X(i).toFixed(1)},${Y(s.cap_w).toFixed(1)}`).join("");
    el.innerHTML = `<h2 class="hl" data-in>Run it again. Less power, fewer experts.</h2>
      <div class="split" style="grid-template-columns:minmax(0,1.25fr) minmax(0,1fr);align-items:start">
        <div><div class="bigrow" style="margin:0 0 10px">${big(Math.round(hi), "W", "average draw at the stock cap", "MEASURED")}${big(Math.round(lo), "W", "average draw at 400 W", "MEASURED")}</div>
          <svg class="pwr" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" role="img" aria-label="measured power draw while the cap steps from 700 W to 400 W">
            <path d="${capl}" fill="none" stroke="#3a4753" stroke-dasharray="4 5" stroke-width="1.4" vector-effect="non-scaling-stroke"/>
            <path id="pl" d="${line}" fill="none" stroke="var(--cy)" stroke-width="2" vector-effect="non-scaling-stroke"/></svg>
          <p class="cap" style="margin-top:6px">Measured on the H100 with NVML: Muse Glimmer 30B, cap stepped 700 → 400 W. Dashed line is the cap. ${pill("MEASURED")}</p></div>
        <div><div class="cmp"><div><h5>normal top-k</h5><div class="seatgrid" id="seatA"></div></div><div><h5>adaptive top-k</h5><div class="seatgrid" id="seatB"></div></div></div>
          <div class="bigrow" style="margin:14px 0 6px">${big(+D.topk.experts[1].toFixed(2), "of 8", "experts firing per token", "MEASURED", { dec: 2 })}</div>
          <p class="cap">Counts: Qwen3-30B-A3B on the H100 (8 → ${D.topk.experts[1]}). Picture: routing captured from a Granite 3B run on CPU. Different runs; each labelled. <a href="#" id="toExp" style="color:var(--cy)">See every expert</a></p></div></div>`;
    const paint = (host, counts) => { const E = D.moe.meta.experts, Lr = D.moe.meta.layers, mx = Math.max(...counts); host.style.gridTemplateColumns = `repeat(${E},1fr)`; host.innerHTML = counts.map((c) => `<i style="${c ? `background:rgba(90,169,240,${(0.12 + 0.88 * Math.pow(c / mx, 0.6)).toFixed(2)})` : ""}"></i>`).join(""); };
    if (D.moe) { paint($("#seatA", el), D.moe.counts.off); paint($("#seatB", el), D.moe.counts.on); } else $(".cmp", el).innerHTML = `<p class="small">Expert capture not available: –</p>`;
    $("#toExp", el).onclick = (e) => { e.preventDefault(); D.drawer("experts"); };
    return { async enter() { runCounts(el); const p = $("#pl", el); if (G() && !reduce()) { const L = p.getTotalLength(); G().fromTo(p, { strokeDasharray: L, strokeDashoffset: L }, { strokeDashoffset: 0, duration: 3.2, ease: "none" }); G().fromTo(el.querySelectorAll(".seatgrid i"), { opacity: 0 }, { opacity: 1, duration: 0.6, stagger: { amount: 0.8, from: "start" } }); } await sleep(3600); } };
  } },
  { id: "receipts", name: "Before and after", hold: 2400, build(el, D) {
    el.innerHTML = `<h2 class="hl" data-in>What it saved. In real units.</h2><div class="tabs2" id="lev" role="tablist"></div><div id="svb" class="sv"></div>
      <div class="scale" id="scl"></div><div class="impact" id="imp" style="margin-top:.8em">${impactLine(D)}</div>
      <p class="cap" style="margin-top:.8em">Three separate runs, so the savings are not added together. Sources: <a style="color:var(--cy)" href="${D.S.water.url}" target="_blank" rel="noopener">LBNL water-use report</a> · <a style="color:var(--cy)" href="${D.S.price.url}" target="_blank" rel="noopener">EIA electricity prices</a></p>`;
    const W = D.S.water, P = D.S.price, tt = (t) => `data-tt="${esc(t)}"`;
    const wTT = `<b>Water ${"(ESTIMATE)"}</b><br>${W.formula}<br>Factor: ${W.factor_l_per_kwh} L/kWh. ${esc(W.source)}.<br><i>${esc(W.caveat)}</i>`;
    const pTT = `<b>Electricity cost (ESTIMATE)</b><br>${P.formula}<br>${esc(P.source)}.`;
    let cur = "cap", req = D.S.scale.req_per_day;
    const bars = (a, b, unit, dec = 2) => { const m = Math.max(a, b); const w = (x) => (m ? (x / m) * 100 : 0); return `<div class="bars"><div class="bar3"><i style="--c:#56626d;width:${w(a)}%" data-w="${w(a)}"></i><span>before ${fmt(+a.toFixed(dec))} ${unit}</span></div><div class="bar3"><i style="--c:var(--cy);width:${w(b)}%" data-w="${w(b)}"></i><span>after ${fmt(+b.toFixed(dec))} ${unit}</span></div></div>`; };
    const row = (name, sub, tag, bar, res, cls = "", ttx = "") => `<div class="n ${ttx ? "hint" : ""}" ${ttx ? tt(ttx) : ""} ${ttx ? 'tabindex="0"' : ""}>${name}<small>${sub} ${tag ? pill(tag) : ""}</small></div>${bar}<div class="r ${cls}">${res}</div>`;
    const render = () => {
      const L = D.S.levers.find((l) => l.id === cur), q = L.quality; let h = "";
      if (L.id === "hosted") {
        const en = L.energy_wh_per_75;
        h = row("Cost", "per request, same quality", "MEASURED", bars(L.usd_before, L.usd_after, "$", 5), `−${fmt(L.saved_pct)}%<small>$${fmt(L.per_million.usd)} per million requests ${"(ESTIMATE at scale)"}</small>`, "good") +
          row("Energy", "avoided across the 75 requests", "ESTIMATE", `<p class="small" style="margin:0">${fmt(en.low)}–${fmt(en.high)} Wh. Not measured on Baseten's GPUs: it is a range from model size and tokens.</p>`, `${fmt(en.low)}–${fmt(en.high)} Wh`) +
          row("Water", "from that energy range", "ESTIMATE", `<p class="small" style="margin:0">energy range × ${W.factor_l_per_kwh} L/kWh</p>`, `${fmt(+(en.low * W.factor_l_per_kwh).toFixed(2))}–${fmt(+(en.high * W.factor_l_per_kwh).toFixed(1))} mL`, "", wTT) +
          row("Slowest 1% (p99)", "slowest class", "MEASURED", bars(L.p99_before_s, L.p99_after_s, "s", 1), `${fmt(L.p99_change_pct)}%<small>shorter answers finish sooner</small>`, "good") +
          row("Quality kept", "judge and per-class checks", "MEASURED", `<p class="small" style="margin:0">${esc(q.text)}.</p>`, `100%<small>0 of 15 judged worse</small>`, "good");
      } else {
        const j = (wh) => wh * 3600, kwhM = (wh) => (wh * 1e6) / 1000;
        h = row("Cost", "electricity per million requests", "ESTIMATE", bars(kwhM(L.wh_before) * P.usd_per_kwh, kwhM(L.wh_after) * P.usd_per_kwh, "$", 2), `$${fmt(L.per_million.usd)}<small>saved per million</small>`, "", pTT) +
          row("Energy", `${fmt(+j(L.wh_before).toFixed(1))} → ${fmt(+j(L.wh_after).toFixed(1))} J per request`, "MEASURED", bars(kwhM(L.wh_before), kwhM(L.wh_after), "kWh per million", 2), `−${fmt(L.saved_pct)}%<small>${fmt(L.per_million.kwh)} kWh per million</small>`, "good") +
          row("Water", "litres per million requests", "ESTIMATE", bars(kwhM(L.wh_before) * W.factor_l_per_kwh, kwhM(L.wh_after) * W.factor_l_per_kwh, "L", 2), `${fmt(L.per_million.litres)} L<small>saved per million</small>`, "", wTT) +
          row("Slowest 1% (p99)", L.id === "topk" ? "batch time" : "request time", "MEASURED", bars(L.p99_before_s, L.p99_after_s, "s", 2), `${L.p99_change_pct > 0 ? "+" : ""}${fmt(L.p99_change_pct)}%<small>${L.p99_change_pct > 10 ? "over the limit" : "inside the 10% limit"}</small>`, L.p99_change_pct > 0 ? "" : "good") +
          row("Quality kept", q.state === "pending" ? "the fixed check cannot judge yet" : "agreement with normal top-k", "MEASURED", `<p class="small" style="margin:0">${esc(q.state === "pending" ? q.why : L.note)}</p>`, q.state === "pending" ? `pending<small>${esc(q.text)}</small>` : `${fmt(q.agreement_pct)}%<small>needs 95%: passed all gates</small>`, q.state === "pending" ? "pend" : "good");
      }
      $("#svb", el).innerHTML = h; $("#imp", el).innerHTML = impactLine(D, L.id);
      const days = req, yr = days * 365;
      const sc = L.id === "hosted" ? `<span>At <input id="req" type="number" min="0" step="1000000" value="${req}" aria-label="requests per day"> requests a day:</span><b>$${fmt(Math.round((L.per_million.usd * yr) / 1e6))} a year</b>${pill("ESTIMATE")}` : `<span>At <input id="req" type="number" min="0" step="1000000" value="${req}" aria-label="requests per day"> requests a day:</span><b>${fmt(+((L.per_million.kwh * yr) / 1e6 / 1000).toFixed(1))} MWh</b><b>$${fmt(Math.round((L.per_million.usd * yr) / 1e6))}</b><b>${fmt(+((L.per_million.litres * yr) / 1e6 / 1000).toFixed(1))} kL of water</b> a year ${pill("ESTIMATE")}`;
      $("#scl", el).innerHTML = `${sc}<span class="cap" ${tt(D.S.scale.assumptions.join(" · "))}><span class="hint">assumptions</span></span>`;
      $("#req", el).onchange = (e) => { req = Math.max(0, +e.target.value || 0); render(); };
      $("#lev", el).innerHTML = D.S.levers.map((l) => `<button class="btn ${l.id === cur ? "on" : ""}" role="tab" aria-selected="${l.id === cur}" data-l="${l.id}">${esc(l.title)} <span class="muted">· ${esc(l.model)}</span></button>`).join("");
      el.querySelectorAll("#lev button").forEach((b) => (b.onclick = () => { cur = b.dataset.l; render(); anim(); }));
      anim();
    };
    const anim = () => { const g = G(); if (!g || reduce()) return; const bs = el.querySelectorAll(".bar3 i"), rs = el.querySelectorAll(".sv .r"); g.fromTo(bs, { width: "0%" }, { width: (i, t) => `${t.dataset.w}%`, duration: 0.9, ease: "power2.out", stagger: 0.06, overwrite: true }); g.fromTo(rs, { opacity: 0, y: 6 }, { opacity: 1, y: 0, duration: 0.5, stagger: 0.08, overwrite: true, clearProps: "opacity,transform" }); };
    render();
    return { async enter() { anim(); await sleep(3800); } };
  } },
  { id: "partners", name: "Who did what", hold: 0, build(el, D) {
    const m = (n) => D.d.meta.find((x) => x.name === n), st = (p, i) => statVal(p.stats[i]);
    const ms = D.d.meta, led = D.d.ledger, bt = D.node("baseten_model"), e2 = D.node("e2e_rig"), ad = D.node("adaptive"), tl = D.node("telemetry"), rc = D.node("receipts");
    const mini = (k, v, t) => `<div class="mini"><span>${esc(k)}</span><b class="num">${esc(v)} ${t ? pill(t) : ""}</b></div>`;
    const col = (c, h, big2, unit, l2, p, minis) => `<div class="pcol" style="--c:${c}" data-in><h3>${h}</h3><div class="b2 num" data-n="${big2}" data-dec="${big2 % 1 ? 1 : 0}">${esc(String(big2))}<small>${esc(unit)}</small></div><div class="l2">${esc(l2)}</div><p>${p}</p>${minis}</div>`;
    const s = (n, k) => D.stat(n, k);
    el.innerHTML = `<h2 class="hl" data-in>Who did what.</h2><div class="pcols">
      ${col("var(--m)", "Meta", led.calls, "calls", "to Muse Spark through the Meta API", "Muse Spark proposed settings, judged answers and ordered effort levels. Muse Spark Open served the hosted run. Llama 3.1 8B and Muse Glimmer 30B were the models swept. Muse Code tasks and SAM 3.1 were run too.",
        mini("Muse Spark spend", `$${fmt(led.total_usd)} of $${led.cap_usd}`, "MEASURED") + mini("Muse Glimmer 30B, 700→400 W", `${fmt(D.cap.saved_pct * -1)}% energy`, "MEASURED") + mini("Llama 3.1 8B, 700→400 W", `${fmt(D.cap.llama.saved_pct * -1)}% energy`, "MEASURED") + mini("Muse Code calls", fmt(m("Muse Code").stats[0].v), "MEASURED") + mini("SAM 3.1 usable masks", fmt(m("SAM 3.1").stats[2].v), "MEASURED") + `<div class="go" style="margin-top:10px"><button class="btn pri" id="showMeta">Show everything Meta did</button></div>`)}
      ${col("var(--bt)", "Baseten", D.hosted.saved_pct, "% cheaper", "hosted Muse Spark Open, same quality", "Served Muse Spark Open. The effort router picked the cheapest level that passed; the cache order cut input cost.",
        mini("calls recorded", fmt(s("baseten_model", "calls").v), "MEASURED") + mini("input cost, shared text first", `−${fmt(s("cache", "save").v)}%`, "MEASURED") + mini("per-call checks matched", String(s("baseten_model", "audit").v), "MEASURED") + mini("billing meter", "–", ""))}
      ${col("var(--e2)", "E2E Networks", +s("e2e_rig", "vm").v, "h of H100", "one rented VM", "Ran the H100 kit: probe, flex events, the cap sweep and the adaptive top-k runs, then the VM was deleted.",
        mini("cap sweep", `${fmt(s("e2e_rig", "sweep").v)} s`, "MEASURED") + mini("power samples kept", fmt(s("telemetry", "rows").v), "MEASURED") + mini("rental cost", "–", ""))}
      ${col("var(--hf)", "Hugging Face", +D.topk.experts[1].toFixed(1), "of 8 experts", "per token, Qwen3-30B-A3B", "Transformers loaded the Mixture-of-Experts models. Hooks on the routers let us keep fewer experts.",
        mini("energy per token", `−${fmt(+D.topk.saved_pct)}%`, "MEASURED") + mini("Qwen3 agreement (gate 95%)", `${fmt(D.topk.quality.agreement_pct)}%, passed`, "MEASURED") + mini("Granite 3B agreement", `${fmt(+s("adaptive", "g").note.match(/[\d.]+/)[0])}%, below the gate`, "MEASURED"))}
      </div>
      <div class="prow" data-in><span><b>NVIDIA</b> NVML power at 10 Hz: ${fmt(tl.stats.find((x) => x.k === "rows").v)} samples, checksummed</span><span><b>Sigstore</b> ${s("receipts", "signed").v} of ${s("receipts", "n").v} receipts signed offline; ${s("receipts", "rekor").v} on the public log</span></div>
      <p class="small" style="margin-top:1em" data-in><b>Honest gaps:</b> the 400 W quality check is pending · Granite 3B missed its 95% gate · no recorded long-context agent session · the EU report is built from an example receipt · the 816B Muse Spark Open side run was not done (needs about 700 GB of GPU memory).</p>`;
    $("#showMeta", el).onclick = () => D.drawer("arch", { filter: "meta" });
    return { async enter() { runCounts(el); await sleep(1500); } };
  } },
];

// ---- the story controller ----------------------------------------------------------------
export class Story {
  constructor(root, rail, D) {
    this.root = root; this.rail = rail; this.D = D; this.i = -1; this.playing = false; this.ctl = []; this.token = 0;
    SCENES.forEach((s, i) => { const el = document.createElement("section"); el.className = "scene"; el.id = `scene-${s.id}`; el.setAttribute("aria-label", s.name); el.dataset.i = i; root.appendChild(el); this.ctl[i] = s.build(el, D) || {}; this.ctl[i].el = el; });
    this.buildRail();
  }
  buildRail() {
    this.rail.innerHTML = `<button class="btn" id="prev" aria-label="previous scene">←</button><button class="btn" id="next" aria-label="next scene">→</button><div class="dots" id="dots">${SCENES.map((s, i) => `<button class="dot" data-i="${i}" aria-label="${i + 1}. ${esc(s.name)}" title="${esc(s.name)}"></button>`).join("")}</div><span class="lab" id="lab"></span><span class="keys">← → space · P play · D deep dive</span>`;
    $("#prev", this.rail).onclick = () => this.step(-1); $("#next", this.rail).onclick = () => this.step(1);
    this.rail.querySelectorAll(".dot").forEach((b) => (b.onclick = () => { this.stopAuto(); this.go(+b.dataset.i); }));
  }
  step(d) { this.stopAuto(); const n = this.i + d; if (n >= 0 && n < SCENES.length) this.go(n); }
  stopAuto() { if (this.playing) { this.playing = false; this.onPlay?.(false); } }
  paintRail() {
    this.rail.querySelectorAll(".dot").forEach((b, k) => { b.classList.toggle("past", k < this.i); b.classList.toggle("cur", k === this.i); });
    $("#lab", this.rail).textContent = `${this.i + 1} of ${SCENES.length} · ${SCENES[this.i].name}`;
    $("#prev", this.rail).disabled = this.i <= 0; $("#next", this.rail).disabled = this.i >= SCENES.length - 1;
  }
  async go(n, { instant = false } = {}) {
    if (n === this.i) return; const tok = ++this.token, from = this.i; this.i = n; this.paintRail();
    const g = G(), out = from >= 0 ? this.ctl[from] : null, inn = this.ctl[n];
    out?.leave?.(); out?.view?.stop();
    if (out) { if (g && !instant && !reduce()) g.to(out.el, { opacity: 0, y: -10, duration: 0.3, onComplete: () => { out.el.classList.remove("on"); } }); else out.el.classList.remove("on"); }
    inn.el.classList.add("on"); inn.el.scrollTop = 0;
    if (g && !instant && !reduce()) { g.fromTo(inn.el, { opacity: 0, y: 14 }, { opacity: 1, y: 0, duration: 0.5, delay: out ? 0.25 : 0 }); g.fromTo(inn.el.querySelectorAll("[data-in]"), { opacity: 0, y: 14 }, { opacity: 1, y: 0, duration: 0.5, stagger: 0.06, delay: out ? 0.3 : 0.05 }); }
    else { inn.el.style.opacity = 1; inn.el.querySelectorAll("[data-in]").forEach((e) => (e.style.opacity = 1)); }
    inn.view?.layoutEdges();
    const done = inn.enter?.();
    this.onScene?.(n);
    if (this.playing) { await done; if (tok !== this.token || !this.playing) return; await sleep(SCENES[n].hold); if (tok !== this.token || !this.playing) return; if (n < SCENES.length - 1) this.go(n + 1); else { this.playing = false; this.onPlay?.(false, true); } }
  }
  play() { this.playing = true; this.onPlay?.(true); this.i = -1; this.token++; return this.go(0); }
}
export const SCENE_NAMES = SCENES.map((s) => s.name);
