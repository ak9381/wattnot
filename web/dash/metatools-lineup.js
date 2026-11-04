// Meta tools lineup: all 8 Meta models/tools WattNot uses, in the order the demo narration names them.
// Roles mirror src/wattnot/eligibility/__init__.py MODELS (no numbers here, only roles).
// Light-up mode: metatools.html?lightup=1[&step=ms] or the Play button.

export const LINEUP = [
  { id: "glimmer-30b", name: "Muse Glimmer 30B", where: "own vLLM GPU", role: "Main model on our own vLLM; power caps are tuned on it" },
  { id: "spark-open", name: "Muse Spark Open", where: "hosted GPU (Baseten / Modal)", role: "Main model, hosted on Baseten and Modal endpoints" },
  { id: "spark", name: "Muse Spark", where: "Meta API", role: "Planner and grader: plans each change, 4-call trace" },
  { id: "wonder", name: "Muse Wonder", where: "Meta API", role: "Supervisor: Pareto cap-sweep planner + Wonderchart" },
  { id: "muse-image", name: "Muse Image", where: "Meta API", role: "Builds the eye-check test set for image models", label: "SYNTHETIC" },
  { id: "muse-voice", name: "Muse Voice Transcribe", where: "Meta API", role: "Hearing check and voice Ask" },
  { id: "sam-3.1", name: "SAM 3.1", where: "Meta API", role: "Rack hotspot audit before any GPU is touched" },
  { id: "muse-code", name: "Muse Code", where: "Meta API", role: "Applies the change as a pull request (demo repo)" },
];

const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);

export function renderLineupHTML(list = LINEUP) {
  const cards = list.map((t, i) => `
    <div class="lineup-card" data-tool="${esc(t.id)}" data-order="${i}">
      <div class="lineup-num">${i + 1}</div>
      <div class="lineup-name">${esc(t.name)}</div>
      <div class="lineup-role">${esc(t.role)}</div>
      <div class="lineup-where">${esc(t.where)}${t.label ? ` <span class="lineup-chip">${esc(t.label)}</span>` : ""}</div>
    </div>`).join("");
  return `<section class="lineup glass-panel">
    <div class="lineup-head"><h2>Powered by Meta: ${list.length} tools</h2>
      <button type="button" class="lineup-play">&#9654; Play</button></div>
    <div class="lineup-grid">${cards}</div></section>`;
}

export function parseLightup(search) {
  const p = new URLSearchParams(search || "");
  const step = parseInt(p.get("step"), 10);
  return { on: p.get("lightup") === "1", step: Number.isFinite(step) && step > 0 ? step : 1400 };
}

// Returns the sequence of states: each step = index lit (or "all").
export function lightupSequence(n) {
  return [...Array(n).keys(), "all"];
}

export function playLightup(root, step = 1400) {
  const cards = [...root.querySelectorAll(".lineup-card")];
  root.classList.add("lineup-dim");
  cards.forEach((c) => c.classList.remove("lit", "done"));
  root.dataset.lightup = "running";
  lightupSequence(cards.length).forEach((s, k) => {
    setTimeout(() => {
      cards.forEach((c, i) => {
        c.classList.toggle("lit", s === "all" || i === s);
        if (s !== "all" && i < s) c.classList.add("done");
      });
      if (s === "all") { root.classList.remove("lineup-dim"); root.dataset.lightup = "done"; }
      else root.dataset.lightupStep = String(s);
    }, step * (k + 1));
  });
}

export function mountLineup(el, search) {
  el.innerHTML = renderLineupHTML();
  const root = el.querySelector(".lineup");
  const { on, step } = parseLightup(search);
  root.querySelector(".lineup-play").addEventListener("click", () => playLightup(root, step));
  if (on) { root.classList.add("lineup-dim"); playLightup(root, step); }
  return root;
}
