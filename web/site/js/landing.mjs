// Landing page. The hero letters settle into "WattNot" as a real (simulated) power trace falls.
import { flexChart } from "./flexchart.mjs";
import { verifyDocument, flipOneDigit } from "./wattnot-verify.mjs";
import { svg, el, chip, clear, getJSON, reduced, num } from "./ui.mjs";

const $ = (id) => document.getElementById(id);
const STILL = reduced();
// section heights follow the window, but never more than 1000 px per screen (very tall windows, e.g. full-page captures)
const setVh = () => document.documentElement.style.setProperty("--vh", Math.min(innerHeight, 1000) / 100 + "px");
setVh(); addEventListener("resize", setVh);
if (STILL) document.documentElement.classList.add("still");

// small seeded generator so the scatter is the same on every visit
function rng(seed) { return () => { seed |= 0; seed = (seed + 0x6D2B79F5) | 0; let t = Math.imul(seed ^ (seed >>> 15), 1 | seed); t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t; return ((t ^ (t >>> 14)) >>> 0) / 4294967296; }; }
const smooth = (x) => { x = Math.max(0, Math.min(1, x)); return x * x * (3 - 2 * x); };

async function hero(trace) {
  const host = $("letters"); if (!host) return;
  host.textContent = "";
  const pts = trace.points.filter((p) => p.t_s <= 110);
  const P = (t) => { t = Math.max(0, Math.min(110, t)); const i = Math.min(pts.length - 2, Math.floor(t)); const f = t - i; return pts[i].power_w * (1 - f) + pts[i + 1].power_w * f; };
  const hi = 276, lo = 203, target = trace.points.find((p) => p.target_w).target_w;
  const word = "WattNot", r = rng(7), ABC = "abcdefghijklmnopqrstuvwxyz";
  const L = [...word].map((ch, i) => {
    const b = el("b", {}, ch); host.append(b);
    return { b, ch, dx: (r() - .5) * 230, dy: (r() - .5) * 120, rot: (r() - .5) * 60, sc: .7 + r() * .6, lag: i * 4.5, shown: ch, next: 0 };
  });
  if (document.fonts && document.fonts.ready) await document.fonts.ready;
  // fix each slot to the width of its final letter so nothing jumps while letters change
  for (const l of L) { l.b.style.width = l.b.offsetWidth + "px"; l.b.style.textAlign = "center"; }

  const W = 900, H = 130, X = (t) => (t / 110) * W, Y = (w) => H - 12 - ((w - 190) / (290 - 190)) * (H - 24);
  const root = svg("svg", { viewBox: `0 0 ${W} ${H}`, preserveAspectRatio: "none" });
  const tgt = svg("line", { x1: 0, x2: W, y1: Y(target), y2: Y(target), stroke: "#9B8CFF", "stroke-dasharray": "6 6", "stroke-width": 1.2, opacity: .65 });
  const tl = svg("text", { x: W, y: Y(target) + 18, "text-anchor": "end", fill: "#9B8CFF", "font-size": 12, "font-family": "JetBrains Mono, monospace" }, `target ${Math.round(target)} W`);
  const ln = svg("path", { fill: "none", stroke: "#5FE3F5", "stroke-width": 2.2, "stroke-linejoin": "round", "stroke-linecap": "round", "vector-effect": "non-scaling-stroke" });
  const dot = svg("circle", { r: 5, fill: "#5FE3F5" });
  root.append(tgt, tl, ln, dot); $("pl").append(root);

  const draw = (ps) => {
    const n = Math.floor(ps); let d = "";
    for (let i = 0; i <= n && i < pts.length; i++) d += (i ? "L" : "M") + X(pts[i].t_s).toFixed(1) + "," + Y(pts[i].power_w).toFixed(1);
    if (ps > n && n < pts.length - 1) d += "L" + X(ps).toFixed(1) + "," + Y(P(ps)).toFixed(1);
    ln.setAttribute("d", d);
    dot.setAttribute("cx", X(ps).toFixed(1)); dot.setAttribute("cy", Y(P(ps)).toFixed(1));
    $("pread").textContent = Math.round(P(ps)) + " W";
    for (const l of L) {
      const k = smooth((P(ps - l.lag) - lo) / (hi - lo) * 1.15 - 0.08);
      l.b.style.transform = k < .002 ? "none" : `translate(${(l.dx * k).toFixed(1)}px,${(l.dy * k).toFixed(1)}px) rotate(${(l.rot * k).toFixed(1)}deg) scale(${(1 + (l.sc - 1) * k).toFixed(3)})`;
      l.b.style.opacity = (1 - .5 * k).toFixed(2);
      l.b.style.filter = k > .05 ? `blur(${(k * 3).toFixed(1)}px)` : "none";
      const want = k > .22 ? l.shown : l.ch;
      if (k > .22) { if (performance.now() > l.next) { l.shown = ABC[(Math.random() * 26) | 0]; l.next = performance.now() + 70; l.b.textContent = l.shown; } }
      else if (l.b.textContent !== l.ch) l.b.textContent = l.ch;
      void want;
    }
  };
  const at = new URLSearchParams(location.search).get("at"); // ?at=2.0 freezes the intro at that second (for captures)
  if (at !== null) { draw(Math.min(1, Number(at) * 1000 / 4800) * 110); return; }
  if (STILL) { draw(110); return; }
  const DUR = 4800, t0 = performance.now();
  const step = (now) => { const f = Math.min(1, (now - t0) / DUR); draw(f * 110); if (f < 1) requestAnimationFrame(step); else draw(110); };
  draw(0); requestAnimationFrame(step);
}

async function mini(trusted) {
  const [r, b] = await Promise.all([getJSON("data/receipts/real-estimate.json"), getJSON("data/receipts/real-estimate.sigstore.json")]);
  const orig = JSON.stringify(r, null, 2), lines = orig.split("\n");
  const flipped = flipOneDigit(orig, 0), fl = flipped.split("\n");
  let li = fl.findIndex((x, i) => x !== lines[i]); if (li < 0) li = 10;
  const from = Math.max(0, li - 3), to = Math.min(lines.length, li + 4);
  let cur = null;
  const paint = async (text, hit) => {
    const L = text.split("\n").slice(from, to);
    const pre = clear($("mpre"));
    L.forEach((ln, i) => { const isHit = hit && from + i === li; pre.append(isHit ? el("span", { class: "hit" }, ln) : ln, i < L.length - 1 ? "\n" : ""); });
    let res; try { res = await verifyDocument(JSON.parse(text), b, trusted); } catch { res = { grade: "UNVERIFIABLE" }; }
    const bad = res.grade === "UNVERIFIABLE";
    $("mini").classList.toggle("bad", bad);
    $("mgrade").className = `grade ${res.grade}`; $("mgrade").textContent = res.grade;
    $("mverdict").textContent = bad ? "Does not verify. The fingerprint no longer matches, so none of the numbers can be trusted." : `Verifies. It stops at ${res.grade} because it has no public log entry.`;
    $("mflip").hidden = hit; $("mundo").hidden = !hit;
    if (hit) {
      const a = lines[li], c = fl[li]; let s = 0; while (a[s] === c[s]) s++;
      let e = 0; while (e < a.length - s && a[a.length - 1 - e] === c[c.length - 1 - e]) e++;
      const part = (t, tag) => [t.slice(0, s).trim(), el(tag, {}, t.slice(s, t.length - e)), t.slice(t.length - e)];
      clear($("mdiff")).append("was  ", ...part(a, "del"), el("br"), "now  ", ...part(c, "ins"));
    } else clear($("mdiff"));
  };
  $("mid").textContent = `${r.id || "receipt"} · sample`;
  $("mflip").onclick = () => { cur = flipped; paint(flipped, true); };
  $("mundo").onclick = () => paint(orig, false);
  await paint(orig, false);
}

async function flexSection(trace, receipt) {
  const host = $("mchart"); if (!host) return;
  const c = flexChart(host, trace, { compact: true, label: "Simulated GPU power over one flex event: it falls to the target and returns afterwards" });
  if (STILL) c.set && c.set(1);
  const io = new IntersectionObserver((es) => { for (const e of es) if (e.isIntersecting) { c.play(); io.disconnect(); } }, { threshold: .35 });
  STILL ? c.play() : io.observe(host);
  const o = receipt.flex.outcome, mean = (a) => a.reduce((s, p) => s + p.power_w, 0) / a.length;
  const before = mean(trace.points.filter((p) => p.phase === "before")), ev = mean(trace.points.filter((p) => p.phase === "event"));
  const s = (t, n, u, d) => el("div", { class: "s" }, el("div", { class: "t" }, t, chip("SIMULATED")), el("div", { class: "n" }, n, el("small", {}, " " + u)), el("div", { class: "d" }, d));
  $("stats3").append(
    s("GPU power", `${Math.round(before)} → ${Math.round(ev)}`, "W", `${num(100 * (1 - ev / before), 1)}% lower on average during the event; the ask was ${o.target_pct}%.`),
    s("Answers passing", `${Math.round(o.pass_rate.before * 100)} → ${Math.round(o.pass_rate.event * 100)}`, "%", "Checked every second against the floor you set."),
    s("p99 latency", `${(o.p99_ms.event_mean / 1000).toFixed(2)}`, `s vs ${(o.p99_ms.sla / 1000).toFixed(2)} s budget`, `Over budget for ${o.p99_ms.sla_violation_s} s of ${receipt.flex.event.duration_s}. The receipt records it.`));
}

function control() {
  const card = $("appr"), st = $("astate"), ap = $("ap"), ke = $("ke"), rb = $("rb");
  const set = (cls, msg, show) => { card.classList.remove("ok", "rolled"); if (cls) card.classList.add(cls); st.textContent = msg; ap.hidden = ke.hidden = show !== "choose"; rb.hidden = show !== "rb"; };
  ap.onclick = () => set("ok", "Applied (example). The old limit of 700 W is saved.", "rb");
  ke.onclick = () => set("", "Left as it was. Nothing was applied.", "choose");
  rb.onclick = () => set("rolled", "Rolled back (example). The limit is 700 W again.", "choose");
}

function reveal() {
  if (STILL || !("IntersectionObserver" in window)) { document.querySelectorAll(".rv").forEach((n) => n.classList.add("in")); return; }
  const io = new IntersectionObserver((es) => es.forEach((e) => { if (e.isIntersecting) { e.target.classList.add("in"); io.unobserve(e.target); } }), { threshold: .12 });
  document.querySelectorAll(".rv").forEach((n) => io.observe(n));
}

async function boot() {
  reveal();
  if (!$("mini") && !$("letters")) return; // features page
  control();
  const [trace, rcpt, tk] = await Promise.all([getJSON("data/flex/demo-evt-1.trace.json"), getJSON("data/flex/demo-evt-1.receipt.json"), getJSON("data/trusted-keys.json")]);
  const trusted = tk.keys.map((k) => k.signer);
  await Promise.all([hero(trace), mini(trusted), flexSection(trace, rcpt)]);
}
boot().catch((e) => console.error(e));
