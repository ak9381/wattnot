// Architecture diagram for "How it works": the real WattNot loop, one clickable block per step.
// status: "built" = code in this repo; "partial" = built but not on in every run; "roadmap" = not built yet.
export const ARCH = [
  { id: "models", t: "Models under test", s: "Muse Glimmer 30B (own vLLM) · Muse Spark Open (Baseten, Modal)", st: "built", src: "src/wattnot/runner/, replays/*",
    x: "These are the AI models whose answers we save energy on. Glimmer runs on our own GPU; Spark Open runs on hosted endpoints." },
  { id: "nvml", t: "GPU telemetry (NVML)", s: "watts, joules, temperature per chunk", st: "built", src: "src/wattnot/proof/session.py, rig.py",
    x: "We read the GPU's own power counter while it answers. That is how every joule on this dashboard is MEASURED, not guessed. Hosted APIs give no meter, so their energy is an ESTIMATE." },
  { id: "spark", t: "Muse Spark planner", s: "plans the next change to try", st: "built", src: "src/wattnot/planner/spark.py",
    x: "Muse Spark looks at the readings and proposes one change to try next, with a reason. It plans; it does not decide." },
  { id: "levers", t: "Levers", s: "power cap · vLLM batch size · KV cache · FP8", st: "built", src: "src/wattnot/runner/vllm_server.py, node/leverrun.py",
    x: "The knobs WattNot can turn: a lower GPU watt limit, how many requests run together, KV cache settings and FP8 weights. Power caps need our own GPU; hosted runs use request-level savers instead." },
  { id: "gate", t: "Quality gate", s: "fixed checks · Muse Wonder supervisor · Muse Image eye check · Muse Voice hearing check", st: "built", src: "src/wattnot/proof/wonder_planner.py, blocks eye/spoken",
    x: "Every change is re-tested on the same fixed questions. Muse Wonder supervises the sweep. Image models get an eye check on test images made with Muse Image; voice gets a hearing check transcribed by Muse Voice. If answers get worse or the slowest 1% gets more than 10% slower, the change fails." },
  { id: "sam", t: "SAM 3.1 rack check", s: "outlines each rack in the room photo", st: "built", src: "src/wattnot/sam/segment.py",
    x: "SAM 3.1 finds the racks in a photo of the server room, so the saving can be pinned to real hardware (11 racks in the H100 room)." },
  { id: "apply", t: "Apply or roll back", s: "keep the change only if every gate passes", st: "built", src: "src/wattnot/proof/orchestrate.py",
    x: "If all gates pass, the change is kept. If any fails, WattNot puts the old setting back. In the H100 sweep 375 W was kept; 350, 325 and 300 W were rolled back." },
  { id: "receipt", t: "Signed receipt", s: "Sigstore signature · Rekor log upload optional", st: "partial", src: "src/wattnot/cli.py sign, proof/events.py",
    x: "The result is written to a receipt and signed so anyone can check it was not edited. The recorded replays here are signed offline; uploading to the public Rekor log is supported but was not used for these runs." },
  { id: "eu", t: "EU report", s: "EU 2024/1364 energy rows from the receipt", st: "built", src: "src/wattnot/report/eu.py",
    x: "The receipt fills the energy and saving rows of the yearly report EU data centers must file. Rows we cannot measure are marked for the operator." },
  { id: "code", t: "Muse Code PR", s: "opens the kept change as a pull request", st: "built", src: "ak9381/wattnot-demo-deploy#4",
    x: "Muse Code writes the kept setting into the deploy repo as a pull request, with the receipt attached, so a human approves it the normal way." },
  { id: "fleet", t: "Fleet-wide auto-apply", s: "push a kept change to many clusters at once", st: "roadmap", src: "not built",
    x: "Roadmap: today each change goes through one PR for one deployment." },
];
const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);
const ST = { built: "Built", partial: "Built, partly used", roadmap: "Roadmap" };

export function archHTML(run) {
  return `<section class="glass la" id="learnArch" aria-label="WattNot architecture">
  <div class="la-hd"><h2>How the loop runs</h2><p>Click a block for a plain explanation, or step through it.${run ? ` Showing run <b>${esc(run)}</b>.` : ""}</p>
    <div class="la-ctl"><button class="btn" data-la="prev">◀ Prev</button><span class="la-n">Step 0 of ${ARCH.length}</span><button class="btn pri" data-la="next">Next ▶</button></div></div>
  <ol class="la-flow">${ARCH.map((b, i) => `<li><button class="la-b la-${b.st}" data-i="${i}" aria-label="${esc(b.t)}"><span class="la-i">${i + 1}</span><b>${esc(b.t)}</b><small>${esc(b.s)}</small><em>${ST[b.st]}</em></button></li>`).join("")}</ol>
  <div class="la-x" aria-live="polite"><p class="la-hint">Pick a block to see what it does.</p></div>
  <p class="la-key"><span class="la-built">Built</span> code in this repo · <span class="la-partial">Built, partly used</span> · <span class="la-roadmap">Roadmap</span> not built yet</p>
</section>`;
}

export function wireArch(root) {
  const el = root.querySelector("#learnArch"); if (!el) return;
  let cur = -1;
  const pick = (i) => {
    cur = Math.max(0, Math.min(ARCH.length - 1, i)); const b = ARCH[cur];
    el.querySelectorAll(".la-b").forEach((x, j) => { x.classList.toggle("on", j === cur); x.classList.toggle("past", j < cur); });
    el.querySelector(".la-n").textContent = `Step ${cur + 1} of ${ARCH.length}`;
    el.querySelector(".la-x").innerHTML = `<h3>${cur + 1}. ${esc(b.t)} <em class="la-${b.st}">${ST[b.st]}</em></h3><p>${esc(b.x)}</p><p class="la-src">Where in the code: <code>${esc(b.src)}</code></p>`;
  };
  el.querySelectorAll(".la-b").forEach((x) => (x.onclick = () => pick(+x.dataset.i)));
  el.querySelector('[data-la="next"]').onclick = () => pick(cur + 1);
  el.querySelector('[data-la="prev"]').onclick = () => pick(cur - 1);
  el.__pick = pick;
}
