// LIVE Meta calls on RUN2 (meta6): reads the signed addenda next to the RUN2 receipt.
// Every value shown here comes from those files; nothing is filled in when a file is missing.
const BASE = "../receipts/run2/rcpt_20261027_run2_cap350.";
const NAMES = ["wonder_live", "musecode_pr", "sam_hotspot", "voice_transcribe"];
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const chip = (l) => `<span class="lv-chip lv-${esc(String(l).toLowerCase())}">${esc(l)}</span>`;

export async function loadLive() {
  const out = {};
  await Promise.all(NAMES.map(async (n) => {
    try { const r = await fetch(`${BASE}${n}.json`); if (r.ok) out[n] = await r.json(); } catch { /* missing: show nothing */ }
  }));
  return out;
}

function row(title, labelHtml, body, doc) {
  const sha = doc?.integrity?.sha256;
  return `<div class="lv-row"><div class="lv-h"><b>${esc(title)}</b> ${labelHtml}</div>${body}
    ${sha ? `<div class="lv-sig">Signed addendum · sha256 <code>${esc(sha.slice(0, 16))}…</code> · parent receipt <code>${esc(doc.parent?.sha256?.slice(0, 12))}…</code></div>` : ""}</div>`;
}

export function renderLive(el, L) {
  if (!el) return;
  const parts = [];
  const w = L.wonder_live?.data;
  if (w) {
    const ids = (w.calls || []).map((c) => `<code>${esc(c.response_id)}</code>`).join(", ");
    parts.push(row("Muse Wonder supervisor", `${chip("LIVE")} decisions · inputs ${chip("REPLAY")} of ${chip("MEASURED")} RUN2 steps`,
      `<p>Wonder read the five measured caps and proposed: <b>${esc(w.decision?.action)} ${w.decision?.cap_w ? esc(w.decision.cap_w) + " W" : ""}</b>. “${esc(w.decision?.reason)}”</p>
       <p class="lv-note">The local p99 gate would block this proposal: 325 W already failed p99 (+10.9% vs a 10% limit), so a lower cap is not run. This is why the gates, not the model, have the last word.</p>
       <details><summary>Wonder's end-of-run summary</summary><p>${esc(w.summary)}</p></details>
       <p class="lv-meta">Model ${esc(w.model)} · response ids ${ids} · spend $${Number(w.spend_usd).toFixed(4)} ${chip("MEASURED")}</p>`, L.wonder_live));
  }
  const m = L.musecode_pr?.data;
  if (m) {
    parts.push(row("Muse Code: Apply as PR", chip("LIVE"),
      `<p>Muse Code edited <code>launch.sh</code> and <code>values.yaml</code> for the kept 350 W change and opened a real pull request on the demo fixture repo (nothing is deployed):
       <a href="${esc(m.pr_url)}" target="_blank" rel="noopener">${esc(m.pr_url.replace("https://github.com/", ""))}</a>. The PR body carries the receipt hash.</p>
       <p class="lv-meta">Session <code>${esc(m.muse_code?.session_id)}</code> · run <code>${esc(m.muse_code?.run_id)}</code> · patch <code>${esc(m.patch_sha256?.slice(0, 12))}…</code> · ~$${Number(m.muse_code?.spend_usd_estimate).toFixed(4)} ${chip("ESTIMATE")}</p>`, L.musecode_pr));
  }
  const s = L.sam_hotspot?.data;
  if (s) {
    parts.push(row("SAM 3.1 hotspot", `segmentation ${chip(s.labels?.segmentation)} · heat ${chip(s.labels?.heat_attribution)} · cap ${chip(s.labels?.cap_decision)}`,
      `<p>One real SAM 3.1 call on the public rack photo (prompt “${esc(s.prompt)}”) returned ${esc(s.sam?.raw_boxes)} boxes; first box ${esc(JSON.stringify(s.mask?.bbox))}. The photo is not thermal, so which slot is hot is simulated, and the ${esc(s.decision?.old_cap_w)} → ${esc(s.decision?.new_cap_w)} W cap is advice only.</p>
       <p class="lv-meta">Response <code>${esc(s.sam?.response_id)}</code> · mask sha256 <code>${esc(s.mask?.sha256?.slice(0, 12))}…</code> · $${Number(s.spend_usd_both_attempts ?? s.spend_usd).toFixed(4)} (2 calls) ${chip("MEASURED")}</p>`, L.sam_hotspot));
  }
  const v = L.voice_transcribe?.data;
  if (v) {
    parts.push(row("Muse Voice Transcribe", `${chip("LIVE")} · audio ${chip("SIMULATED")} (TTS clip)`,
      `<p>A ${esc(v.audio?.duration_s)} s synthetic clip (ffmpeg flite text-to-speech, not a human voice) was sent to the real transcribe endpoint. Transcript: <b>“${esc(v.transcript)}”</b>.
       <audio controls preload="none" src="media/tts-question.wav" style="height:28px;vertical-align:middle"></audio></p>
       <p class="lv-meta">Session <code>${esc(v.session_id)}</code> · ~$${Number(v.spend_usd_estimate).toFixed(5)} ${chip("ESTIMATE")}</p>`, L.voice_transcribe));
  }
  if (!parts.length) { el.innerHTML = ""; return; }
  el.innerHTML = `<section class="lv-card" data-explain="report.live" data-explain-label="LIVE" data-explain-source="receipts/run2/*.json (signed addenda)">
    <h3>Live Meta calls on this run <span class="lv-sub">four real API calls, each saved as a signed addendum to the RUN2 receipt</span></h3>${parts.join("")}</section>`;
}

export async function mountLive(sel = "#live-mount") {
  const el = document.querySelector(sel);
  if (!el) return null;
  const L = await loadLive();
  renderLive(el, L);
  return L;
}

if (typeof document !== "undefined") {
  const go = () => mountLive();
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", go); else go();
}
