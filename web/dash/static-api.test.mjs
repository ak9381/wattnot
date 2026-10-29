import test from "node:test";
import assert from "node:assert/strict";
import { createRequire } from "node:module";

// static-api.js installs a browser shim, but also exports its helpers for Node.
const require = createRequire(import.meta.url);
const { targetOf, methodOf, makeFetch } = require("./static-api.js");

function stubReal() {
  const calls = [];
  const real = async (u, init) => {
    calls.push({ u, init });
    return new Response(JSON.stringify({ ok: true }), { status: 200, headers: { "content-type": "application/json" } });
  };
  return { real, calls };
}

test("targetOf normalizes string, URL, Request, and anything else without throwing", () => {
  assert.equal(targetOf("/api/x"), "/api/x");
  assert.equal(targetOf(new URL("https://h/wattnot/dash/a.json")), "https://h/wattnot/dash/a.json");
  assert.equal(targetOf({ url: "https://h/api/y" }), "https://h/api/y");
  assert.equal(targetOf({ href: "https://h/api/z" }), "https://h/api/z");
  // No url/href, and null — must still return a string, never throw.
  assert.equal(typeof targetOf({}), "string");
  assert.equal(typeof targetOf(null), "string");
});

test("methodOf reads init.method, then the Request method, defaulting to GET", () => {
  assert.equal(methodOf("/x", { method: "post" }), "POST");
  assert.equal(methodOf({ url: "/x", method: "put" }, {}), "PUT");
  assert.equal(methodOf("/x", {}), "GET");
});

test("POST /api/voiceguide fails (404) in the shim so voiceguide.js falls back to grounding", async () => {
  const { real, calls } = stubReal();
  const f = makeFetch(real, "/wattnot/");
  const res = await f("/api/voiceguide", { method: "POST", body: JSON.stringify({ query: "why was 325 watts rejected" }) });
  assert.equal(res.ok, false);
  assert.equal(res.status, 404);
  // It must fail inside the shim, not fetch a missing voiceguide.json file.
  assert.equal(calls.length, 0);
});

test("URL object (receipt / live transcript fetch) passes through unchanged", async () => {
  const { real, calls } = stubReal();
  const f = makeFetch(real, "/wattnot/");
  const u = new URL("https://ak9381.github.io/wattnot/dash/receipts/run2/rcpt.json");
  const res = await f(u); // must not throw
  assert.equal(res.status, 200);
  assert.equal(calls.length, 1);
  assert.equal(calls[0].u, u); // original object, not a mangled string
});

test("Request-like object with absolute url passes through", async () => {
  const { real, calls } = stubReal();
  const f = makeFetch(real, "/wattnot/");
  const req = { url: "https://host/wattnot/dash/receipts/run2/y.json", method: "GET" };
  const res = await f(req);
  assert.equal(res.status, 200);
  assert.equal(calls[0].u, req);
});

test("GET /api/* maps to the recorded JSON under the site base", async () => {
  const { real, calls } = stubReal();
  const f = makeFetch(real, "/wattnot/");
  await f("/api/dash/story");
  assert.equal(calls.at(-1).u, "/wattnot/api/dash/story.json");
  await f("/api/dash/v2/options?foo=1");
  assert.equal(calls.at(-1).u, "/wattnot/api/dash/v2/options.json");
});

test("POST /api/dash/v2/run with an unrecorded combo returns a 404 Response", async () => {
  const { real } = stubReal(); // index.json stub resolves to {ok:true}, so no combo matches
  const f = makeFetch(real, "/wattnot/");
  const res = await f("/api/dash/v2/run", { method: "POST", body: JSON.stringify({ mode: "sim" }) });
  assert.equal(res.status, 404);
});
