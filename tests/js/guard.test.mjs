import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import {
  riskTag, leftText, changeText, plannerLine, decidedLine,
  pendingCard, appliedCard, recentRow, panelHtml, GuardPanel,
} from "../../web/dash/guard.js";

const hostile = {
  id: "prop_1",
  title: "<img src=x onerror=alert(1)>",
  summary: "Lower the power cap from 700 W to 400 W.",
  label: "MOCK",
  risk: { level: "high", reasons: ["more than 15% below stock", "<b>evil</b>"] },
  change: { before: { power_cap_w: 700 }, after: { power_cap_w: 400 } },
  status: "pending",
  seconds_left: 580,
  decision: null,
  applies_on_approve: true,
  undo_available: false,
  planner: { name: "muse-spark-open", model: "meta-models/muse-spark-open-1026", partner: "baseten", tier: "strict" },
};

test("pendingCard has Approve and Reject buttons with the right data-id", () => {
  const h = pendingCard(hostile);
  assert.match(h, /data-act="approve" data-id="prop_1"/);
  assert.match(h, /data-act="reject" data-id="prop_1"/);
  assert.match(h, /<button[^>]*class="btn pri"[^>]*>Approve<\/button>/);
  assert.match(h, />Reject<\/button>/);
});

test("pendingCard escapes a hostile title and reason", () => {
  const h = pendingCard(hostile);
  assert.doesNotMatch(h, /<img src=x/);
  assert.doesNotMatch(h, /<b>evil<\/b>/);
  assert.match(h, /&lt;img/);
  assert.match(h, /&lt;b&gt;evil/);
});

test("pendingCard shows the risk reasons as list items", () => {
  const h = pendingCard(hostile);
  assert.match(h, /<li>more than 15% below stock<\/li>/);
  assert.equal((h.match(/<li>/g) || []).length, 2);
});

test("pendingCard shows the change as before -> after with a countdown", () => {
  const h = pendingCard(hostile);
  assert.match(h, /power_cap_w 700 → 400/);
  assert.match(h, /data-left="580"/);
  assert.match(h, /9 min 40 s left/);
  assert.match(h, /No answer in time = reject\./);
});

test("plannerLine reads plainly for strict and standard tiers", () => {
  assert.match(plannerLine(hostile.planner), /Proposed by Muse Spark Open \(Baseten\) · strict tier: a person must approve high-risk changes/);
  assert.match(
    plannerLine({ name: "muse-spark", model: "muse-spark-1.3", partner: "meta", tier: "standard" }),
    /Proposed by Muse Spark \(Meta API\) · standard tier/
  );
  assert.equal(plannerLine(null), "");
  assert.equal(plannerLine(undefined), "");
});

test("plannerLine escapes a hostile planner name", () => {
  assert.doesNotMatch(plannerLine({ name: "<b>x</b>", partner: "meta", tier: "standard" }), /<b>x<\/b>/);
});

test("leftText cases", () => {
  assert.equal(leftText(580), "9 min 40 s left");
  assert.equal(leftText(12), "expires in 12 s");
  assert.equal(leftText(0), "timed out");
  assert.equal(leftText(-3), "timed out");
  assert.equal(leftText(null), "–");
});

test("riskTag pills", () => {
  assert.match(riskTag("high"), /High risk/);
  assert.match(riskTag("low"), /Low risk/);
});

test("changeText is a dash when there is nothing to show", () => {
  assert.equal(changeText({}), "–");
  assert.equal(changeText(null), "–");
});

test("appliedCard shows Undo only when undo_available", () => {
  const base = { ...hostile, status: "applied", decision: { by: "dashboard:operator", via: "http" } };
  assert.match(appliedCard({ ...base, undo_available: true }), /data-act="undo" data-id="prop_1"/);
  assert.doesNotMatch(appliedCard({ ...base, undo_available: false }), /data-act="undo"/);
});

test("appliedCard says who approved", () => {
  const h = appliedCard({ ...hostile, status: "applied", decision: { by: "dashboard:operator", via: "http" }, undo_available: false });
  assert.match(h, /approved by dashboard:operator/);
  assert.match(h, /Proposed by Muse Spark Open/);
});

test("appliedCard names the auto-approve flag plainly", () => {
  assert.match(
    decidedLine({ decision: { by: "auto-approve-flag:dashboard", via: "pre-approved" } }),
    /auto-approved by the --auto-approve flag/
  );
  assert.match(decidedLine({ decision: { by: "guardrail:low-risk" } }), /low risk, auto-applied/);
});

test("recentRow shows timed out for an expired item", () => {
  const h = recentRow({ ...hostile, status: "expired", expires_at: "2026-01-01T00:10:00Z" });
  assert.match(h, /timed out/);
  assert.doesNotMatch(h, />expired</);
  const undone = recentRow({ ...hostile, status: "rolled_back", decision: { by: "dashboard:operator", at: "2026-01-01T00:11:00Z" } });
  assert.match(undone, /undone by dashboard:operator/);
  const approved = recentRow({ ...hostile, status: "approved", decision: { by: "dashboard:operator", at: "t" } });
  assert.match(approved, /approved by dashboard:operator/);
});

test("panelHtml is empty when there is nothing to show", () => {
  assert.equal(panelHtml({ pending: [], applied: [], recent: [], mode: "mock" }), "");
  assert.equal(panelHtml(null), "");
});

test("panelHtml has the heading, a live pending count and the MOCK badge", () => {
  const h = panelHtml({ pending: [hostile], applied: [], recent: [], mode: "mock" });
  assert.match(h, /<h3>Operator check <span class="pill MOCK">MOCK<\/span><\/h3>/);
  assert.match(h, /aria-live="polite"/);
  const live = panelHtml({ pending: [hostile], applied: [], recent: [], mode: "live" });
  assert.match(live, /<h3>Operator check<\/h3>/);
});

// ---- GuardPanel with fakes: no browser needed

const resp = (status, body) => ({ status, ok: status >= 200 && status < 300, json: async () => body });
const flush = async (n = 5) => {
  for (let i = 0; i < n; i++) await new Promise((r) => setImmediate(r));
};
function fakeTimers() {
  const scheduled = [];
  return {
    scheduled,
    setTimeout: (fn, ms) => {
      const id = scheduled.length;
      scheduled.push({ fn, ms, cleared: false });
      return id;
    },
    clearTimeout: (id) => {
      if (scheduled[id]) scheduled[id].cleared = true;
    },
    live: () => scheduled.filter((s) => !s.cleared),
  };
}
function fakeHost() {
  const listeners = {};
  return {
    innerHTML: "",
    listeners,
    addEventListener: (t, f) => {
      (listeners[t] ||= []).push(f);
    },
    removeEventListener: (t, f) => {
      listeners[t] = (listeners[t] || []).filter((g) => g !== f);
    },
    querySelectorAll: () => [],
  };
}
// Press a delegated button: returns the pending promise and the button.
function press(host, act, id) {
  const btn = {
    disabled: false,
    getAttribute: (k) => (k === "data-act" ? act : k === "data-id" ? id : null),
  };
  const done = host.listeners.click[0]({ target: { closest: (sel) => (sel === "[data-act]" ? btn : null) } });
  return { done, btn };
}
const stateOf = (pending, extra = {}) => ({ pending, applied: [], recent: [], mode: "mock", ...extra });

test("GuardPanel renders pending and polls every 2 s with 1 s countdowns", async () => {
  const fetch = async () => resp(200, stateOf([hostile]));
  const tm = fakeTimers();
  const host = fakeHost();
  const p = new GuardPanel(host, { fetch, setTimeout: tm.setTimeout, clearTimeout: tm.clearTimeout });
  p.start();
  await flush();
  assert.match(host.innerHTML, /&lt;img/);
  assert.match(host.innerHTML, /data-act="approve"/);
  assert.ok(tm.live().some((s) => s.ms === 2000), "polls every 2 s");
  assert.ok(tm.live().some((s) => s.ms === 1000), "countdowns tick every 1 s");
  // a re-read with identical data does not rewrite the host
  const before = host.innerHTML;
  host.innerHTML = "touched";
  await p.refresh();
  assert.equal(host.innerHTML, "touched");
  void before;
  p.stop();
  assert.equal(tm.live().length, 0);
});

test("approve POSTs JSON with apply only when applies_on_approve", async () => {
  const posts = [];
  const fetch = async (url, opts) => {
    if (opts && opts.method === "POST") {
      posts.push({ url, body: JSON.parse(opts.body), ctype: opts.headers["Content-Type"] });
      return resp(200, { id: "prop_1", status: "approved" });
    }
    return resp(200, stateOf([hostile]));
  };
  const host = fakeHost();
  const p = new GuardPanel(host, { fetch, ...fakeTimers() });
  p.start();
  await flush();
  await press(host, "approve", "prop_1").done;
  await flush();
  assert.equal(posts[0].url, "/api/dash/approve");
  assert.deepEqual(posts[0].body, { id: "prop_1", apply: true });
  assert.equal(posts[0].ctype, "application/json");
  p.stop();

  const posts2 = [];
  const noApply = { ...hostile, applies_on_approve: false };
  const fetch2 = async (url, opts) => {
    if (opts && opts.method === "POST") {
      posts2.push(JSON.parse(opts.body));
      return resp(200, { id: "prop_1", status: "approved" });
    }
    return resp(200, stateOf([noApply]));
  };
  const host2 = fakeHost();
  const p2 = new GuardPanel(host2, { fetch: fetch2, ...fakeTimers() });
  p2.start();
  await flush();
  await press(host2, "approve", "prop_1").done;
  await flush();
  assert.deepEqual(posts2[0], { id: "prop_1" });
  p2.stop();
});

test("undo POSTs to /api/dash/undo/<id>", async () => {
  const posts = [];
  const applied = { ...hostile, status: "applied", undo_available: true, decision: { by: "dashboard:operator", via: "http" } };
  const fetch = async (url, opts) => {
    if (opts && opts.method === "POST") {
      posts.push({ url, body: JSON.parse(opts.body) });
      return resp(200, { id: "prop_1", status: "rolled_back" });
    }
    return resp(200, { pending: [], applied: [applied], recent: [], mode: "mock" });
  };
  const host = fakeHost();
  const p = new GuardPanel(host, { fetch, ...fakeTimers() });
  p.start();
  await flush();
  assert.match(host.innerHTML, /Undo/);
  await press(host, "undo", "prop_1").done;
  await flush();
  assert.equal(posts[0].url, "/api/dash/undo/prop_1");
  assert.deepEqual(posts[0].body, {});
  p.stop();
});

test("the button is disabled while the request is in flight", async () => {
  let release;
  const gate = new Promise((r) => {
    release = r;
  });
  const fetch = async (url, opts) => {
    if (opts && opts.method === "POST") return gate;
    return resp(200, stateOf([hostile]));
  };
  const host = fakeHost();
  const p = new GuardPanel(host, { fetch, ...fakeTimers() });
  p.start();
  await flush();
  const { done, btn } = press(host, "reject", "prop_1");
  assert.equal(btn.disabled, true);
  release(resp(200, { id: "prop_1", status: "rejected" }));
  await done;
  await flush();
  assert.equal(btn.disabled, false);
  p.stop();
});

test("an error shows the server message in a role=alert line", async () => {
  const fetch = async (url, opts) => {
    if (opts && opts.method === "POST") return resp(409, { code: "conflict", message: "already decided" });
    return resp(200, stateOf([hostile]));
  };
  const host = fakeHost();
  const p = new GuardPanel(host, { fetch, ...fakeTimers() });
  p.start();
  await flush();
  await press(host, "approve", "prop_1").done;
  await flush();
  assert.match(host.innerHTML, /already decided/);
  assert.match(host.innerHTML, /role="alert"/);
  p.stop();
});

test("a 404 on first load keeps the panel hidden and stops polling", async () => {
  let gets = 0;
  const fetch = async () => {
    gets++;
    return resp(404, { detail: "gone" });
  };
  const tm = fakeTimers();
  const host = fakeHost();
  const p = new GuardPanel(host, { fetch, setTimeout: tm.setTimeout, clearTimeout: tm.clearTimeout });
  p.start();
  await flush();
  assert.equal(host.innerHTML, "");
  assert.equal(tm.live().length, 0);
  assert.equal(gets, 1);
  p.stop();
});

test("a network error on first load stays hidden and quiet", async () => {
  const tm = fakeTimers();
  const host = fakeHost();
  const p = new GuardPanel(host, { fetch: async () => { throw new Error("down"); }, setTimeout: tm.setTimeout, clearTimeout: tm.clearTimeout });
  p.start();
  await flush();
  assert.equal(host.innerHTML, "");
  assert.equal(tm.live().length, 0);
  p.stop();
});

test("product.js maps the approve step and mounts the guard panel", () => {
  const src = readFileSync(new URL("../../web/dash/product.js", import.meta.url), "utf8");
  assert.match(src, /approve:\s*"approve"/);
  assert.match(src, /from\s*["']\.\/guard\.js["']/);
  assert.match(src, /guardPanel/);
  assert.match(src, /proposal\.created/);
  assert.match(src, /destroyRun\(\)[^}]*guard\?\.stop/);
});

test("an operator-set policy approval names the operator and the time, never a person's click", async () => {
  const { decidedLine } = await import("../../web/dash/guard.js");
  const line = decidedLine({ decision: { by: "auto-policy:90ea3d4c6849", via: "auto-policy",
    note: "approved by policy, set by operator Akash K Raju at 2026-10-30T14:19:31Z; every threshold held" } });
  assert.equal(line, "approved by policy, set by operator Akash K Raju at 2026-10-30T14:19:31Z");
  assert.match(decidedLine({ decision: { by: "auto-policy:abc", note: "every threshold held" } }), /no operator named/);
});

test("a pending card shows the measured evidence with its label", async () => {
  const { pendingCard } = await import("../../web/dash/guard.js");
  const html = pendingCard({ id: "p1", title: "Cap GPU 0 at 400 W", change: { before: { power_cap_w: 700 }, after: { power_cap_w: 400 } },
    risk: { level: "high", reasons: [] }, label: "MEASURED",
    evidence: { label: "MEASURED", rounds: 3, energy_change_pct: { mean: -21.3, ci95_half: 1.2 }, p99_change_pct_mean: 4.1,
      answers_checked: 666, welch_per_block: { p_value: 0.0012 } } });
  assert.match(html, /Evidence: energy -21\.3% per request \(±1\.2, 3 rounds\)/);
  assert.match(html, /Welch p=0\.0012 \(per block\)/);
  assert.match(html, /pill MEASURED/);
});
