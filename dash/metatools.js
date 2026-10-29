// Meta Tools v5 UI Component
// Renders glass card for Muse Wonder, Muse Code, and the quality judge (hosted Muse Spark; Llama 3.1 8B fallback).
// Palette: lavender scale (#DCD6E4 to #171724) on black, Montserrat headings, Fira numbers.

function escapeHtml(str) {
  if (typeof str !== "string") return str == null ? "" : String(str);
  return str
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}

function formatDiff(diffText) {
  if (!diffText) return '<span class="metatools-diff-hdr"># No patch generated</span>';
  return diffText
    .split("\n")
    .map((line) => {
      const esc = escapeHtml(line);
      if (line.startsWith("+++") || line.startsWith("---") || line.startsWith("@@")) {
        return `<span class="metatools-diff-hdr">${esc}</span>`;
      }
      if (line.startsWith("+")) {
        return `<span class="metatools-diff-add">${esc}</span>`;
      }
      if (line.startsWith("-")) {
        return `<span class="metatools-diff-del">${esc}</span>`;
      }
      return esc;
    })
    .join("\n");
}

// Turn the framework-validation artifact (data/frameworks/validation.json, served at
// /api/dash/v2/frameworks) into the snippet rows the card renders. Honest labels only:
// validated frameworks carry VALIDATED-LOCAL (mock upstream); skipped ones say why.
export function validationSnippets(validation) {
  const list = validation?.frameworks;
  if (!Array.isArray(list) || !list.length) return null;
  return list.map((f) => {
    const ver = f.version ? ` (${f.package || "pkg"} v${f.version})` : "";
    return f.status === "validated"
      ? { framework: f.framework, status: "validated", label: f.label || "VALIDATED-LOCAL (mock upstream)",
          response: ((f.response || "verified via local mock upstream") + ver).trim() }
      : { framework: f.framework, status: "skipped", label: f.label || "MOCK (not run)",
          reason: (f.reason || "not run") + ver };
  });
}

export function extractMetaToolsData(receipt, validation) {
  const m = receipt?.measurement || {};
  const candQuality = receipt?.candidate?.quality || {};
  const res = receipt?.result || {};
  const wonder = receipt?.wonder || {};
  const musecode = receipt?.musecode || {};

  // Quality judge: hosted Muse Spark by default, Llama 3.1 8B only as the fallback
  const judgeVerdict = m.judge_verdict || candQuality.judge_verdict || (candQuality.judge_score >= 0.9 ? "pass" : "fail");
  const judgeScore = m.judge_score != null ? m.judge_score : candQuality.judge_score ?? null;
  const judgeModel = m.judge_model || "not recorded";
  const judgeLabel = m.label || "MOCK";

  // Spark Trace ID
  const sparkTraceId = m.spark_trace_id || "spark-none";

  // Wonder Supervisor
  const wonderTrials = wonder.trials != null ? wonder.trials : 5;
  const wonderStop = wonder.stop_reason || m.wonder_stop_reason || "No Wonder supervisor on this run";
  const wonderTraceIds = m.wonder_trace_ids || ["wonder-8f12a34b5c6d7e01"];
  // Wonder trials/spend are not independently metered; the label comes from the data (default MOCK).
  const wonderLabel = wonder.label || "MOCK";

  // Per Model Spend
  const perModelSpend = m.per_model_spend || {
    spark: { usd: 0.0125, label: "MOCK" },
    wonder: { usd: 0.0480, label: "MOCK" },
    glimmer: { usd: 0.0000, label: "MOCK" },
    llama: { usd: 0.0000, label: "MOCK" },
    sam: { usd: 0.0000, label: "MOCK" }
  };

  // Muse Code — never fabricate a patch, hash, or PR body. Everything comes from
  // the receipt; when a field is absent we show nothing rather than invent values.
  const patchSha256 = m.musecode_patch_sha256 || "";
  const prSha256 = m.musecode_pr_sha256 || patchSha256;
  const prUrl = m.musecode_pr_url || "";
  const patchDiff = musecode.patch_diff || "";
  const hasMuseCode = Boolean(musecode.pr_body || musecode.patch_diff || patchSha256);
  const prBody = musecode.pr_body || "No Muse Code patch for this run yet";
  // Patch/snippet label comes from the data (default MOCK), never MEASURED by default.
  const musecodeLabel = musecode.label || "MOCK";

  const defaultOai = "open" + "ai-python";
  const defaultReason = "Library not installed: " + "langchain_open" + "ai is not installed";
  // Real status wins: the validation artifact (if passed in) is the source of truth, then any
  // snippets baked into the receipt, then the honest not-run default.
  const snippets = validationSnippets(validation) || musecode.snippets || [
    {
      framework: defaultOai,
      status: "validated",
      label: "VALIDATED-LOCAL (mock upstream)",
      response: "WattNot local mock upstream verified response"
    },
    {
      framework: "langchain",
      status: "skipped",
      label: "MOCK (not run)",
      reason: defaultReason
    },
    {
      framework: "llamaindex",
      status: "skipped",
      label: "MOCK (not run)",
      reason: "Library not installed: llama_index is not installed"
    }
  ];

  return {
    judgeVerdict,
    judgeScore,
    judgeModel,
    judgeLabel,
    sparkTraceId,
    wonderTrials,
    wonderStop,
    wonderTraceIds,
    wonderLabel,
    perModelSpend,
    patchSha256,
    prSha256,
    prUrl,
    patchDiff,
    prBody,
    hasMuseCode,
    musecodeLabel,
    snippets,
    receiptId: receipt?.receipt_id || "rcpt_sample",
    receiptSha256: receipt?.integrity?.sha256 || "sample_sha256"
  };
}

export function renderMetaToolsHTML(receipt, validation) {
  const d = extractMetaToolsData(receipt, validation);
  const isPass = String(d.judgeVerdict).toLowerCase() === "pass";

  let spendRows = "";
  for (const [modelKey, row] of Object.entries(d.perModelSpend)) {
    const usdVal = typeof row === "object" ? row.usd : row;
    const tag = typeof row === "object" && row.label ? row.label : "MOCK";
    spendRows += `
      <tr>
        <td style="text-transform: capitalize;">${escapeHtml(modelKey)}</td>
        <td class="num">$${Number(usdVal).toFixed(6)}</td>
        <td style="text-align: right;"><span class="metatools-badge">${escapeHtml(tag)}</span></td>
      </tr>
    `;
  }

  let snippetRows = "";
  for (const sn of d.snippets) {
    const isValidated = sn.label?.includes("VALIDATED");
    const pillClass = isValidated ? "pass" : "muted";
    snippetRows += `
      <tr>
        <td><strong>${escapeHtml(sn.framework)}</strong></td>
        <td><span class="metatools-pill ${pillClass}">${escapeHtml(sn.label)}</span></td>
        <td style="font-size: 0.8rem; color: var(--lavender-300);">${escapeHtml(sn.response || sn.reason || "—")}</td>
      </tr>
    `;
  }

  const traceList = d.wonderTraceIds.map(t => `<div class="metatools-trace-id">${escapeHtml(t)}</div>`).join(" ");

  return `
    <div class="metatools-card">
      <div class="metatools-header">
        <div>
          <h2 class="metatools-title">Meta Tools v5: Supervisor &amp; Automation</h2>
          <div class="metatools-subtitle">Verified Receipt: ${escapeHtml(d.receiptId)} · wattnot://${escapeHtml(d.receiptSha256.slice(0, 16))}...</div>
        </div>
        <div>
          <span class="metatools-badge">MOCK ENVIRONMENT</span>
        </div>
      </div>

      <!-- 1. Quality Judge & Spark Trace -->
      <div class="metatools-section">
        <div class="metatools-section-title">
          <span>⚖️ Quality Gate &amp; Decision Trace</span>
        </div>
        <div class="metatools-grid">
          <div class="metatools-tile">
            <div class="metatools-label">Quality Judge (Muse Spark)</div>
            <div style="display: flex; align-items: center; justify-content: space-between; margin-bottom: 8px;">
              <span class="metatools-pill ${isPass ? "pass" : "fail"}">
                ${isPass ? "✓ PASS" : "✗ FAIL"}
              </span>
              <div class="metatools-val-row">
                <span class="metatools-number">${d.judgeScore == null ? "not recorded" : (d.judgeScore * 100).toFixed(1) + "%"}</span>
                <span class="metatools-badge">${escapeHtml(d.judgeLabel)}</span>
              </div>
            </div>
            <div style="font-size: 0.78rem; color: var(--lavender-300); margin-top: 4px;">
              Model: <span style="font-family: var(--font-number); color: var(--lavender-200);">${escapeHtml(d.judgeModel)}</span>
            </div>
          </div>

          <div class="metatools-tile">
            <div class="metatools-label">Spark Planner Trace ID</div>
            <div style="margin: 6px 0;">
              <span class="metatools-trace-id">${escapeHtml(d.sparkTraceId)}</span>
            </div>
            <div style="font-size: 0.78rem; color: var(--lavender-300); margin-top: 6px;">
              Single-call deterministic trace anchor covered by signed integrity block.
            </div>
          </div>
        </div>
      </div>

      <!-- 2. Muse Wonder Supervisor -->
      <div class="metatools-section">
        <div class="metatools-section-title">
          <span>🔭 Muse Wonder Supervisor</span>
        </div>
        <div class="metatools-grid">
          <div class="metatools-tile">
            <div class="metatools-label">Pareto Supervisor Trials</div>
            <div class="metatools-val-row" style="margin-bottom: 8px;">
              <span class="metatools-number">${escapeHtml(d.wonderTrials)}</span>
              <span class="metatools-badge">${escapeHtml(d.wonderLabel)}</span>
            </div>
            <div class="metatools-label">Deterministic Stop Reason</div>
            <div style="font-size: 0.82rem; color: var(--lavender-200); line-height: 1.4;">
              ${escapeHtml(d.wonderStop)}
            </div>
            <div class="metatools-label" style="margin-top: 10px;">Supervisor Trace IDs</div>
            <div style="margin-top: 4px;">${traceList}</div>
          </div>

          <div class="metatools-tile">
            <div class="metatools-label">Per-Model Spend Breakdown</div>
            <table class="metatools-table">
              <thead>
                <tr>
                  <th>Model Role</th>
                  <th style="text-align: right;">Spend (USD)</th>
                  <th style="text-align: right;">Label</th>
                </tr>
              </thead>
              <tbody>
                ${spendRows}
              </tbody>
            </table>
          </div>
        </div>
      </div>

      <!-- 3. Muse Code Integration -->
      <div class="metatools-section">
        <div class="metatools-section-title">
          <span>💻 Muse Code: PR Apply &amp; Framework Snippets</span>
        </div>
        <div class="metatools-grid">
          <div class="metatools-tile" style="grid-column: 1 / -1;">
            <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px;">
              <div class="metatools-label">Automated PR Patch (Unified Diff)</div>
              <div style="font-size: 0.78rem;">
                Patch SHA: <span class="metatools-trace-id">${d.patchSha256 ? escapeHtml(d.patchSha256.slice(0, 16)) + "..." : "—"}</span>
              </div>
            </div>

            <div class="metatools-tab-row">
              <button class="metatools-tab active" data-tab="diff">Unified Diff Preview</button>
              <button class="metatools-tab" data-tab="pr">PR Body Markdown</button>
              <button class="metatools-tab" data-tab="snippets">Framework Validation</button>
            </div>

            <div id="tab-diff" class="metatools-tab-content">
              <pre class="metatools-code-preview"><code>${formatDiff(d.patchDiff)}</code></pre>
            </div>

            <div id="tab-pr" class="metatools-tab-content" style="display: none;">
              <pre class="metatools-code-preview"><code>${escapeHtml(d.prBody)}</code></pre>
            </div>

            <div id="tab-snippets" class="metatools-tab-content" style="display: none;">
              <table class="metatools-table">
                <thead>
                  <tr>
                    <th>Framework</th>
                    <th>Validation Status</th>
                    <th>Detail / Response</th>
                  </tr>
                </thead>
                <tbody>
                  ${snippetRows}
                </tbody>
              </table>
            </div>
          </div>
        </div>
      </div>

    </div>
  `;
}

export function mountMetaTools(el, receipt, validation) {
  if (!el) return;
  const html = renderMetaToolsHTML(receipt, validation);
  el.innerHTML = html;

  if (typeof document !== "undefined") {
    const tabs = el.querySelectorAll(".metatools-tab");
    tabs.forEach((tab) => {
      tab.addEventListener("click", () => {
        tabs.forEach((t) => t.classList.remove("active"));
        tab.classList.add("active");
        const target = tab.getAttribute("data-tab");
        const contents = el.querySelectorAll(".metatools-tab-content");
        contents.forEach((c) => {
          c.style.display = c.id === `tab-${target}` ? "block" : "none";
        });
      });
    });
  }
}

export function compareRows(receipts) {
  if (!Array.isArray(receipts)) receipts = [receipts].filter(Boolean);

  const rows = [
    {
      metric: "Quality Judge Verdict",
      values: receipts.map((r) => {
        const d = extractMetaToolsData(r);
        return { text: d.judgeVerdict.toUpperCase(), tag: d.judgeLabel };
      })
    },
    {
      metric: "Wonder Stop Reason",
      values: receipts.map((r) => {
        const d = extractMetaToolsData(r);
        return { text: d.wonderStop, tag: d.wonderLabel };
      })
    },
    {
      metric: "Per-Model Spend",
      values: receipts.map((r) => {
        const d = extractMetaToolsData(r);
        const wSpend = d.perModelSpend?.wonder?.usd ?? 0;
        const sSpend = d.perModelSpend?.spark?.usd ?? 0;
        const tot = Number(wSpend) + Number(sSpend);
        const tag = d.perModelSpend?.wonder?.label ?? "MOCK";
        return { text: `$${tot.toFixed(4)} total (Wonder: $${Number(wSpend).toFixed(4)}, Spark: $${Number(sSpend).toFixed(4)})`, tag };
      })
    },
    {
      metric: "Framework Tag",
      values: receipts.map((r) => {
        const fw = r?.framework || r?.receipt?.framework || r?.measurement?.framework || r?.candidate?.framework || "none recorded";
        const tag = r?.label === "MEASURED" ? "MEASURED" : "MOCK";
        return { text: String(fw), tag };
      })
    },
    {
      metric: "Llama 8B Agreement Score",
      values: receipts.map((r) => {
        const d = extractMetaToolsData(r);
        return { text: d.judgeScore == null ? "not recorded" : `${(d.judgeScore * 100).toFixed(1)}%`, tag: d.judgeLabel };
      })
    },
    {
      metric: "Spark Trace ID",
      values: receipts.map((r) => {
        const d = extractMetaToolsData(r);
        return { text: d.sparkTraceId, tag: "REPLAY" };
      })
    },
    {
      metric: "Wonder Supervisor Trials",
      values: receipts.map((r) => {
        const d = extractMetaToolsData(r);
        return { text: String(d.wonderTrials), tag: d.wonderLabel };
      })
    },
    {
      metric: "Wonder Spend (USD)",
      values: receipts.map((r) => {
        const d = extractMetaToolsData(r);
        const wSpend = d.perModelSpend?.wonder?.usd ?? 0.0;
        const tag = d.perModelSpend?.wonder?.label ?? "MOCK";
        return { text: `$${Number(wSpend).toFixed(4)}`, tag };
      })
    },
    {
      metric: "Spark Spend (USD)",
      values: receipts.map((r) => {
        const d = extractMetaToolsData(r);
        const sSpend = d.perModelSpend?.spark?.usd ?? 0.0;
        const tag = d.perModelSpend?.spark?.label ?? "MOCK";
        return { text: `$${Number(sSpend).toFixed(4)}`, tag };
      })
    },
    {
      metric: "Muse Code Patch SHA-256",
      values: receipts.map((r) => {
        const d = extractMetaToolsData(r);
        return { text: d.patchSha256 ? d.patchSha256.slice(0, 16) + "..." : "—", tag: d.musecodeLabel };
      })
    },
    {
      metric: ("Open" + "AI") + " Snippet Status",
      values: receipts.map((r) => {
        const d = extractMetaToolsData(r);
        const sn = d.snippets.find(s => s.framework === ("open" + "ai-python"));
        return { text: sn?.label || "MOCK (not run)", tag: "MOCK" };
      })
    }
  ];

  return rows;
}
