// "Wonder reads the chart" panel. After a power sweep, WattNot plots
// joules-per-correct-answer (x) against quality (y) for every trial — kept
// trials green, rejected red — sends that *image* to Muse Wonder, and logs the
// suggestion for the next setting on the signed receipt. This panel redraws the
// scatter from the plotted points (measurement.wonder_chart.points) as inline
// SVG and shows Wonder's (or the offline fallback's) suggestion. The chart is a
// SYNTHETIC plot of trial data, never a measurement, and the read energy is an
// ESTIMATE. Pure formatting (no DOM at import time) so node can test it,
// mirroring hotspotset.js / eyetest.js / hotspot.js.

const esc = (s) =>
  String(s == null ? "" : s).replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#039;" }[c]));

const num = (v, digits) =>
  Number(v || 0).toLocaleString("en-US", { maximumFractionDigits: digits });

// Pull the wonder-chart block from a receipt (measurement.wonder_chart),
// defensively. hasData is false when a run carries no chart (or no points), so
// the panel stays hidden.
export function extractWonderChart(receipt) {
  const wc = receipt?.measurement?.wonder_chart || receipt?.wonder_chart || null;
  const points = Array.isArray(wc?.points) ? wc.points : [];
  const sugg = wc?.suggestion || {};
  const est = wc?.est_wh || {};
  const source = wc?.source || "fallback";
  const sha = String(wc?.chart_sha256 || "");
  return {
    hasData: Boolean(wc && points.length > 0),
    label: wc?.label || "SYNTHETIC",
    chartSha: sha,
    chartShaShort: sha ? sha.slice(0, 12) : "",
    source,
    offlineFallback: wc?.offline_fallback ?? source === "fallback",
    model: wc?.model || "unrecorded",
    suggestion: {
      action: sugg.action || "stop",
      capW: sugg.cap_w == null ? null : sugg.cap_w,
      clockProfile: sugg.clock_profile == null ? null : sugg.clock_profile,
    },
    reason: wc?.reason || "",
    traceId: wc?.trace_id || "",
    whValue: est.value == null ? 0.5 : est.value,
    whLabel: est.label || "ESTIMATE",
    points,
  };
}

// Redraw the scatter from the plotted points: x = joules per correct answer
// (lower is better), y = quality (0..1, higher is better). Kept points green,
// rejected red, each labelled with its power cap. Self-contained inline SVG so
// the receipt's PNG pixels never need to be shipped to the browser.
function scatterSVG(points) {
  const W = 480;
  const H = 300;
  const m = { l: 52, r: 16, t: 16, b: 44 };
  const x0 = m.l;
  const y0 = m.t;
  const x1 = W - m.r;
  const y1 = H - m.b;

  const xs = points.map((p) => Number(p.joules_per_correct) || 0);
  const ys = points.map((p) => Number(p.quality) || 0);
  let xlo = Math.min(...xs);
  let xhi = Math.max(...xs);
  let ylo = Math.min(...ys);
  let yhi = Math.max(...ys);
  const xpad = (xhi - xlo) * 0.1 || 0.1;
  const ypad = (yhi - ylo) * 0.1 || 0.05;
  xlo -= xpad;
  xhi += xpad;
  ylo = Math.max(0, ylo - ypad);
  yhi = Math.min(1, yhi + ypad);

  const sx = (v) => (xhi <= xlo ? (x0 + x1) / 2 : x0 + ((v - xlo) / (xhi - xlo)) * (x1 - x0));
  const sy = (v) => (yhi <= ylo ? (y0 + y1) / 2 : y1 - ((v - ylo) / (yhi - ylo)) * (y1 - y0));

  const grid = [0.25, 0.5, 0.75]
    .map((f) => {
      const gy = y0 + (y1 - y0) * f;
      const gx = x0 + (x1 - x0) * f;
      return (
        `<line class="wc-grid" x1="${x0}" y1="${gy.toFixed(1)}" x2="${x1}" y2="${gy.toFixed(1)}"/>` +
        `<line class="wc-grid" x1="${gx.toFixed(1)}" y1="${y0}" x2="${gx.toFixed(1)}" y2="${y1}"/>`
      );
    })
    .join("");

  const ticks = [0, 0.5, 1]
    .map((f) => {
      const xv = xlo + (xhi - xlo) * f;
      const yv = ylo + (yhi - ylo) * f;
      const px = sx(xv);
      const py = sy(yv);
      return (
        `<text class="wc-tick" x="${px.toFixed(1)}" y="${(y1 + 14).toFixed(1)}" text-anchor="middle">${esc(num(xv, 2))}</text>` +
        `<text class="wc-tick" x="${(x0 - 6).toFixed(1)}" y="${(py + 3).toFixed(1)}" text-anchor="end">${esc(num(yv, 2))}</text>`
      );
    })
    .join("");

  const dots = points
    .map((p) => {
      const kept = Boolean(p.kept);
      const px = sx(Number(p.joules_per_correct) || 0);
      const py = sy(Number(p.quality) || 0);
      return (
        `<circle class="wc-dot ${kept ? "wc-kept" : "wc-rejected"}" cx="${px.toFixed(1)}" cy="${py.toFixed(1)}" r="6"/>` +
        `<text class="wc-dotlabel" x="${(px + 9).toFixed(1)}" y="${(py + 3).toFixed(1)}">${esc(p.cap_w)}</text>`
      );
    })
    .join("");

  // legend top-right, inside the plot frame
  const lx = x1 - 96;
  const ly = y0 + 10;
  const legend =
    `<circle class="wc-dot wc-kept" cx="${lx}" cy="${ly}" r="6"/>` +
    `<text class="wc-legend" x="${lx + 11}" y="${ly + 4}">kept</text>` +
    `<circle class="wc-dot wc-rejected" cx="${lx}" cy="${ly + 18}" r="6"/>` +
    `<text class="wc-legend" x="${lx + 11}" y="${ly + 22}">rejected</text>`;

  return `
    <svg class="wc-svg" viewBox="0 0 ${W} ${H}" role="img"
         aria-label="Scatter of joules per correct answer versus quality; kept trials green, rejected red.">
      <rect class="wc-frame" x="${x0}" y="${y0}" width="${x1 - x0}" height="${y1 - y0}"/>
      ${grid}
      ${ticks}
      ${dots}
      ${legend}
      <text class="wc-axis" x="${((x0 + x1) / 2).toFixed(1)}" y="${H - 6}" text-anchor="middle">joules per correct answer (lower is better)</text>
      <text class="wc-axis" x="${-((y0 + y1) / 2).toFixed(1)}" y="14" text-anchor="middle" transform="rotate(-90)">quality (0-1, higher is better)</text>
    </svg>`;
}

export function renderWonderChartHTML(data) {
  if (!data.hasData) return "";

  const isTry = data.suggestion.action === "try";
  const verdictClass = isTry ? "wc-try" : "wc-stop";
  const verdict = isTry
    ? `Next &rarr; ${esc(data.suggestion.capW)} W / ${esc(data.suggestion.clockProfile)}`
    : "Stop &bull; $/quality front flattened";

  const sourceNote = data.offlineFallback
    ? `Offline deterministic fallback (no network, no paid call) &bull; model <code>${esc(data.model)}</code>`
    : `Muse Wonder read the chart &bull; model <code>${esc(data.model)}</code>`;

  const energy = `~ ${esc(num(data.whValue, 4))} Wh &bull; ${esc(data.whLabel)}`;

  return `
    <div class="wc-card" id="wonder-chart-card">
      <div class="wc-header">
        <h3 class="wc-title">
          <span>Wonder reads the chart</span>
          <span class="wc-badge">${esc(data.label)} figure</span>
          <span class="wc-badge wc-energy" title="assumed chart-read energy, not metered">${energy}</span>
        </h3>
        <span class="wc-verdict ${verdictClass}">${verdict}</span>
      </div>
      <p class="wc-sub">After the sweep, WattNot plots joules-per-correct-answer against quality (kept green, rejected red), sends the image to Muse Wonder, and uses its suggestion for the next setting. Wonder only suggests &mdash; the gates still decide, and a deterministic fallback picks the next setting offline so the sweep never stalls.</p>
      <p class="wc-source">${sourceNote}</p>
      ${scatterSVG(data.points)}
      ${data.reason ? `<p class="wc-reason"><strong>Suggestion:</strong> ${esc(data.reason)}</p>` : ""}
      <p class="wc-foot">${esc(data.label)} plot of trial data, not a measurement. chart <code>${esc(data.chartShaShort)}</code>${data.traceId ? ` &bull; trace <code>${esc(data.traceId)}</code>` : ""}</p>
    </div>`;
}

export function mountWonderChart(el, receipt) {
  if (!el) return;
  const data = extractWonderChart(receipt);
  el.innerHTML = renderWonderChartHTML(data);
  el.hidden = !data.hasData;
}
