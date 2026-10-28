// Token-to-expert heat strip for the Adaptive top-k block. Modelled on the
// cumulative routing heatmaps in MoEXViz ("All tokens routing") and Martin
// Alderson's MoE routing visualiser: columns are prompt steps, rows are
// experts (summed over layers), brightness is routed tokens. Underneath, two
// thin bars per step show experts used per token without (fixed top-k) and
// with adaptive top-k. Counts are MEASURED from the captured router ticks.
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
export function expertStrip(data, upto) {
  const { meta, ticks } = data, E = meta.meta.experts, K = meta.meta.top_k;
  const steps = meta.meta.steps.map((s) => s.id), S = steps.length, col = new Map(steps.map((id, i) => [id, i]));
  const M = Array.from({ length: E }, () => new Float64Array(S));
  const act = { off: new Float64Array(S), on: new Float64Array(S) }, cnt = { off: new Float64Array(S), on: new Float64Array(S) };
  for (const t of ticks.slice(0, upto)) {
    const c = col.get(t.step); if (c == null) continue;
    if (t.arm === "off") for (const [u, n] of t.r) M[u % E][c] += n;
    if (t.slots) { act[t.arm][c] += t.kept_slots; cnt[t.arm][c] += t.slots; }
  }
  let mx = 1; for (const r of M) for (const v of r) mx = Math.max(mx, v);
  const cw = 9, ch = 3, W = S * cw, H = E * ch;
  const heat = (v) => { const f = Math.sqrt(v / mx); return `rgba(${Math.round(90 + 152 * f)},${Math.round(169 + 30 * f)},${Math.round(240 - 100 * f)},${(0.06 + 0.94 * f).toFixed(2)})`; };
  let cells = "";
  for (let e = 0; e < E; e++) for (let c = 0; c < S; c++) if (M[e][c]) cells += `<rect x="${c * cw}" y="${e * ch}" width="${cw - 1}" height="${ch - 0.4}" fill="${heat(M[e][c])}"/>`;
  const bar = (arm, y, cls) => Array.from({ length: S }, (_, c) => { const a = cnt[arm][c] ? act[arm][c] / cnt[arm][c] : 0; const h = (a / K) * 14; return a ? `<rect class="${cls}" x="${c * cw}" y="${y + 14 - h}" width="${cw - 1}" height="${h}"><title>step ${c + 1} · ${arm === "on" ? "adaptive" : "fixed"} top-k: ${a.toFixed(2)} experts per token</title></rect>` : ""; }).join("");
  const sum = (arm) => { let a = 0, n = 0; for (let c = 0; c < S; c++) { a += act[arm][c]; n += cnt[arm][c]; } return n ? a / n : null; };
  const off = sum("off"), on = sum("on");
  return `<button type="button" class="xstrip" data-info="experts" aria-label="Expert routing heat strip, open the explanation">
    <svg viewBox="0 0 ${W} ${H + 40}" role="img" aria-label="Expert routing heat strip: ${E} experts by ${S} prompt steps">${cells}
      ${bar("off", H + 4, "xoff")}${bar("on", H + 22, "xon")}</svg>
    <span class="xleg"><span><i class="xoff"></i>fixed top-${K}${off != null ? `: ${off.toFixed(1)}` : ""}</span><span><i class="xon"></i>adaptive${on != null ? `: ${on.toFixed(1)}` : ""} experts / token</span></span>
    <span class="xcap">${esc(meta.meta.model)} · ${E} experts × ${S} steps · routed tokens, measured</span></button>`;
}
