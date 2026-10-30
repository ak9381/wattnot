// Operator check: approve-before-apply proposals on the Run screen.
// Pure render functions (no DOM access at import time, so node can test
// them) plus GuardPanel, which polls GET /api/dash/approvals and POSTs
// approve / reject / undo. Every string from the server goes through esc().
import { esc } from "./theme.js";

// Small risk pill. Anything that is not clearly "high" reads as low.
export const riskTag = (level) =>
  level === "high"
    ? `<span class="risk high">High risk</span>`
    : `<span class="risk low">Low risk</span>`;

// Plain countdown words for seconds left. A missing value is a dash.
export const leftText = (seconds) => {
  if (seconds == null || seconds === "" || Number.isNaN(+seconds)) return "–";
  const s = Math.floor(+seconds);
  if (s <= 0) return "timed out";
  if (s < 60) return `expires in ${s} s`;
  return `${Math.floor(s / 60)} min ${s % 60} s left`;
};

const fmtVal = (v) => (v == null ? "–" : esc(String(v)));

// The change as "before -> after", one key at a time.
export const changeText = (change) => {
  const before = (change && change.before) || {};
  const after = (change && change.after) || {};
  const keys = [...new Set([...Object.keys(before), ...Object.keys(after)])];
  if (!keys.length) return "–";
  return keys.map((k) => `${esc(k)} ${fmtVal(before[k])} → ${fmtVal(after[k])}`).join("; ");
};

const PLANNER_NAMES = { "muse-spark": "Muse Spark", "muse-spark-open": "Muse Spark Open" };
const PARTNER_NAMES = { meta: "Meta API", baseten: "Baseten" };

// Who proposed the change, from item.planner ({name, model, partner, tier}).
// "" when the run did not say who proposed it.
export function plannerLine(planner) {
  if (!planner || typeof planner !== "object") return "";
  const name = PLANNER_NAMES[planner.name] || planner.name || "a planner";
  const partner = PARTNER_NAMES[planner.partner] || planner.partner;
  const tier =
    planner.tier === "strict"
      ? "strict tier: a person must approve high-risk changes"
      : planner.tier === "standard"
        ? "standard tier"
        : "";
  return `<p class="gplan">Proposed by ${esc(name)}${partner ? ` (${esc(partner)})` : ""}${tier ? ` · ${esc(tier)}` : ""}</p>`;
}

const labelPill = (label) =>
  label ? `<span class="pill ${esc(label)}">${esc(label)}</span>` : "";

// The measured evidence a proposal carries (from the sweep), as one line with its label.
export function evidenceLine(ev) {
  if (!ev || typeof ev !== "object") return "";
  const e = ev.energy_change_pct || {};
  const f = (v, d = 1) => (typeof v === "number" && Number.isFinite(v) ? v.toFixed(d) : "–");
  const parts = [`energy ${f(e.mean)}% per request (±${f(e.ci95_half)}, ${esc(String(ev.rounds ?? "–"))} rounds)`,
    `p99 ${f(ev.p99_change_pct_mean)}%`];
  if (ev.answers_checked != null) parts.push(`${esc(String(ev.answers_checked))} answers graded`);
  const w = ev.welch_per_block;
  if (w && typeof w.p_value === "number") parts.push(`Welch p=${w.p_value.toPrecision(2)} (per block)`);
  return `<p class="gev">Evidence: ${parts.join(" · ")} ${labelPill(ev.label)}</p>`;
}

// One pending change: what it is, the risk with reasons, Approve / Reject.
export function pendingCard(item) {
  const risk = item.risk || {};
  const reasons = Array.isArray(risk.reasons) ? risk.reasons : [];
  return `<article class="gcard pend" data-gid="${esc(item.id)}">`
    + `<h4>${esc(item.title ?? "–")}</h4>`
    + (item.summary ? `<p class="gsum">${esc(item.summary)}</p>` : "")
    + plannerLine(item.planner)
    + `<p class="gchg">${changeText(item.change)}</p>`
    + evidenceLine(item.evidence)
    + `<p>${riskTag(risk.level)} ${labelPill(item.label)}</p>`
    + (reasons.length ? `<ul class="grsn">${reasons.map((r) => `<li>${esc(r)}</li>`).join("")}</ul>` : "")
    + `<p class="gcd" data-left="${item.seconds_left == null ? "" : esc(String(item.seconds_left))}">${esc(leftText(item.seconds_left))}</p>`
    + `<p class="gops"><button class="btn pri" data-act="approve" data-id="${esc(item.id)}">Approve</button> `
    + `<button class="btn" data-act="reject" data-id="${esc(item.id)}">Reject</button></p>`
    + `<p class="gto">No answer in time = reject.</p>`
    + `</article>`;
}

// Who approved an applied change, and how. Shown plainly; the auto-approve
// flag and the low-risk path never look like a person's click.
export const decidedLine = (item) => {
  const d = item.decision || {};
  const by = d.by || "";
  if (typeof by === "string" && by.startsWith("auto-approve-flag:")) return "auto-approved by the --auto-approve flag";
  if (by === "guardrail:low-risk") return "low risk, auto-applied";
  if (typeof by === "string" && by.startsWith("auto-policy:")) {
    // the policy's own words from the audit trail: "approved by policy, set by operator X at T"
    const note = typeof d.note === "string" ? d.note : "";
    const m = note.match(/^approved by policy, set by operator [^;]+/);
    return m ? esc(m[0]) : "approved by an auto-approve policy (no operator named)";
  }
  if (by) return `approved by ${esc(by)}${d.via ? ` <span class="muted">via ${esc(d.via)}</span>` : ""}`;
  return "applied";
};

// One applied change, with Undo while the run still allows it.
export function appliedCard(item) {
  return `<article class="gcard appl" data-gid="${esc(item.id)}">`
    + `<h4>${esc(item.title ?? "–")}</h4>`
    + plannerLine(item.planner)
    + `<p class="gchg">${changeText(item.change)}</p>`
    + `<p class="gby">${decidedLine(item)}</p>`
    + (item.undo_available
      ? `<p class="gops"><button class="btn" data-act="undo" data-id="${esc(item.id)}">Undo</button></p>`
      : "")
    + `</article>`;
}

const STATUS_WORD = {
  approved: "approved",
  rejected: "rejected",
  expired: "timed out",
  rolled_back: "undone",
  failed: "failed",
  closed: "closed",
};

// One line for a decided or undone change, with who and when.
export function recentRow(item) {
  const word = STATUS_WORD[item.status] || item.status || "decided";
  const by = item.decision && item.decision.by;
  const when = (item.decision && item.decision.at) || (item.applied && item.applied.at) || item.expires_at;
  return `<li class="gr"><b>${esc(item.title ?? item.id)}</b> ${esc(word)}`
    + `${by ? ` by ${esc(by)}` : ""}`
    + `${when ? ` <span class="muted">${esc(when)}</span>` : ""}`
    + plannerLine(item.planner)
    + `</li>`;
}

// The whole panel. "" when there is nothing at all to show.
export function panelHtml(state) {
  const pending = (state && state.pending) || [];
  const applied = (state && state.applied) || [];
  const recent = ((state && state.recent) || []).slice(0, 5);
  if (!pending.length && !applied.length && !recent.length) return "";
  const badge = state && state.mode === "mock" ? ` <span class="pill MOCK">MOCK</span>` : "";
  return `<h3>Operator check${badge}</h3>`
    + `<p class="gcount" aria-live="polite">${pending.length} pending</p>`
    + pending.map(pendingCard).join("")
    + applied.map(appliedCard).join("")
    + (recent.length ? `<h4 class="grh">Recent</h4><ul class="grl">${recent.map(recentRow).join("")}</ul>` : "");
}

// Polls GET /api/dash/approvals while started and renders into host.
// fetch and the timers are injected so tests need no browser.
export class GuardPanel {
  constructor(host, deps = {}) {
    this.host = host;
    this.fetch = deps.fetch || (typeof fetch === "function" ? fetch.bind(globalThis) : null);
    this._setTimeout = deps.setTimeout || ((fn, ms) => setTimeout(fn, ms));
    this._clearTimeout = deps.clearTimeout || ((id) => clearTimeout(id));
    this.started = false;
    this._body = null;
    this._last = null;
    this._items = new Map();
    this.error = "";
    this._pollId = null;
    this._tickId = null;
    this._onClick = (e) => this.act(e);
  }
  start() {
    if (this.started) return;
    this.started = true;
    if (typeof this.host.addEventListener === "function") this.host.addEventListener("click", this._onClick);
    this.refresh();
    this._armPoll();
    this._armTick();
  }
  stop() {
    this.started = false;
    if (this._pollId != null) this._clearTimeout(this._pollId);
    if (this._tickId != null) this._clearTimeout(this._tickId);
    this._pollId = null;
    this._tickId = null;
    if (typeof this.host.removeEventListener === "function") this.host.removeEventListener("click", this._onClick);
  }
  // A fresh event may mean a fresh proposal: re-read now.
  kick() {
    if (this.started) this.refresh();
  }
  async refresh() {
    if (!this.started || !this.fetch) return;
    let r;
    try {
      r = await this.fetch("/api/dash/approvals");
    } catch {
      return this._firstFail();
    }
    if (!r || !r.ok) return this._firstFail();
    let body;
    try {
      body = await r.json();
    } catch {
      return;
    }
    this._body = body;
    this._index(body);
    const js = JSON.stringify(body);
    if (js !== this._last) {
      this._last = js;
      this._render();
    }
  }
  // The first read failed (401/404/network): stay hidden, stop quietly.
  // A later failure keeps the old panel and keeps polling.
  _firstFail() {
    if (this._last != null) return;
    this.host.innerHTML = "";
    this.stop();
  }
  _index(body) {
    this._items = new Map();
    for (const bucket of [body.pending, body.applied, body.recent]) {
      for (const item of bucket || []) this._items.set(item.id, item);
    }
  }
  _render() {
    this.host.innerHTML = panelHtml(this._body) + (this.error ? `<p class="gerr" role="alert">${esc(this.error)}</p>` : "");
  }
  _armPoll() {
    if (!this.started) return;
    this._pollId = this._setTimeout(async () => {
      this._pollId = null;
      await this.refresh();
      this._armPoll();
    }, 2000);
  }
  // Between polls the countdowns tick down locally, with no re-render.
  _armTick() {
    if (!this.started) return;
    this._tickId = this._setTimeout(() => {
      this._tickId = null;
      this._tick();
      this._armTick();
    }, 1000);
  }
  _tick() {
    if (typeof this.host.querySelectorAll !== "function") return;
    for (const el of this.host.querySelectorAll("[data-left]")) {
      const v = parseFloat(el.getAttribute("data-left"));
      if (Number.isNaN(v)) continue;
      const n = Math.max(0, v - 1);
      el.setAttribute("data-left", String(n));
      el.textContent = leftText(n);
    }
  }
  // One delegated click handler for every Approve / Reject / Undo button.
  async act(e) {
    const t = e && e.target && typeof e.target.closest === "function" ? e.target.closest("[data-act]") : null;
    if (!t) return;
    const id = t.getAttribute("data-id");
    const what = t.getAttribute("data-act");
    const item = this._items.get(id);
    let url;
    let body;
    if (what === "approve") {
      url = "/api/dash/approve";
      body = item && item.applies_on_approve ? { id, apply: true } : { id };
    } else if (what === "reject") {
      url = "/api/dash/reject";
      body = { id };
    } else if (what === "undo") {
      url = `/api/dash/undo/${encodeURIComponent(id)}`;
      body = {};
    } else {
      return;
    }
    this.error = "";
    t.disabled = true;
    try {
      const r = await this.fetch(url, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      let message = "";
      try {
        const parsed = await r.json();
        message = (parsed && parsed.message) || "";
      } catch {
        /* a non-JSON answer carries no message */
      }
      if (!r.ok) this.error = message || `the server said ${r.status}`;
      await this.refresh();
      if (this._body) this._render();
    } finally {
      t.disabled = false;
    }
  }
}
