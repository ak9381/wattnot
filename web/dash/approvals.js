import { lab } from "./elig.js";
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const mark = (ok) => ok === true ? '<span class="st pass">PASS</span>' : ok === false ? '<span class="st fail">FAIL</span>' : '<span class="st wait">NO DATA</span>';

export function card(a) {
  const req = a.checks.filter((c) => c.required), canApprove = req.every((c) => c.ok === true);
  const d = a.decision;
  return `<section class="card" data-id="${esc(a.id)}">
    <h2>${esc(a.id)} <span class="pill ${a.risk === "high" ? "no" : "warn"}">risk ${esc(a.risk)}</span> ${lab("MEASURED")}</h2>
    <p class="sub">Held change: <code>${esc(JSON.stringify(a.held_change))}</code>${a.reasons ? ` · why held: ${esc([].concat(a.reasons).join("; "))}` : ""}</p>
    <h3>Verification evidence <a href="/api/launch/file?path=${encodeURIComponent(a.source)}">approval.json</a></h3>
    <ul class="checks-list">${a.checks.map((c) => `<li>${mark(c.ok)}<span class="t">${esc(c.title)}${c.required ? "" : ' <span class="hint" style="display:inline">(advisory)</span>'}</span><span class="r">${esc(c.detail)}</span></li>`).join("")}</ul>
    ${a.pr ? `<h3>Muse Code PR ${lab(a.pr.label === "MOCK" ? "MOCK" : "MEASURED")}</h3>
      <p class="sub">Files: ${esc((a.pr.files || []).join(", "))} · patch <code>${esc((a.pr.patch_sha256 || "").slice(0, 12))}</code> tied to receipt <code>${esc((a.pr.receipt_sha256 || "").slice(0, 12))}</code> · ${a.pr.url ? `<a href="${esc(a.pr.url)}" target="_blank" rel="noopener">open PR</a>` : a.pr.mock ? "patch built in MOCK mode, no PR opened" : "PR not opened"}</p>` : ""}
    ${d ? `<p><span class="pill ${d.decision === "approve" ? "yes" : "no"}">${esc(d.decision)}</span> by ${esc(d.reviewer)} at ${esc(new Date(d.at).toLocaleString())} · evidence <code>${esc(d.evidence_sha256.slice(0, 12))}</code>${d.note ? ` · "${esc(d.note)}"` : ""}</p>` : ""}
    <label>Reviewer note <input class="note" type="text" maxlength="500" style="background:var(--v-bg2);color:var(--v-cream);border:1px solid var(--v-line2);border-radius:8px;padding:6px 8px;font:inherit"></label>
    <div class="actions">
      <button class="btn" data-d="approve" ${canApprove ? "" : "disabled"}>Approve after review</button>
      <button class="btn ghost" data-d="hold">Keep held</button>
      <button class="btn ghost" data-d="reject">Reject</button>
      <span class="why">${canApprove ? "All required checks pass." : "Approve is locked: a required check failed or has no data."}</span>
    </div></section>`;
}

async function load() {
  const r = await fetch("/api/launch/approvals").then((x) => x.json());
  const host = document.getElementById("list");
  host.innerHTML = r.items.length ? r.items.map(card).join("") : '<p class="sub">Nothing held right now.</p>';
  host.querySelectorAll("[data-d]").forEach((b) => b.addEventListener("click", async () => {
    const sec = b.closest("section");
    const res = await fetch("/api/launch/approvals/decide", { method: "POST", headers: { "content-type": "application/json" },
      body: JSON.stringify({ id: sec.dataset.id, decision: b.dataset.d, note: sec.querySelector(".note").value }) });
    if (!res.ok) { sec.querySelector(".why").textContent = (await res.json()).error; return; }
    load();
  }));
}
if (!globalThis.__WN_TEST__) load();
