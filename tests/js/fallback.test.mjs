// fallback.js patches window.fetch on the live (non-static) dashboard. It must read the
// request target from a string, a Request (.url) or a URL object (.href) without throwing.
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const src = readFileSync(new URL("../../web/dash/fallback.js", import.meta.url), "utf8");

// fallback.js is a classic-script IIFE that references the bare global `window`; run it with
// `window` bound to a stub so we can observe the patched fetch it installs.
function loadFallback(realFetch) {
  const win = { fetch: realFetch };
  new Function("window", src)(win);
  return win;
}

test("patched fetch accepts a URL object without throwing (regression for u.url)", async () => {
  const calls = [];
  const win = loadFallback(async (u) => (calls.push(u), { status: 200 }));
  // A URL object has no .url; the old code did `u.url` -> undefined -> p.startsWith threw.
  const u = new URL("https://ak9381.github.io/wattnot/dash/receipts/run2/rcpt.json");
  const res = await win.fetch(u);
  assert.equal(res.status, 200);
  assert.equal(calls.at(-1), u); // a non-/api/dash target passes the original object through
});

test("patched fetch still routes a 5xx /api/dash GET to the published replay JSON", async () => {
  const calls = [];
  const win = loadFallback(async (u) => {
    calls.push(u);
    return String(u).startsWith("http") ? { status: 200 } : { status: 500 };
  });
  const res = await win.fetch("/api/dash/story");
  assert.equal(res.status, 200);
  assert.equal(calls.at(-1), "https://ak9381.github.io/wattnot/api/dash/story.json");
});
