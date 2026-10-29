// Static replay: serve the API answers recorded by scripts/build_pages.py from
// ./api/*.json. On GitHub Pages there is no live backend, so this shim stands in
// for window.fetch. Nothing here is live; the page says so in a banner.
//
// Loaded as a classic <script> in the browser (so document.currentScript works)
// and imported for its helpers by static-api.test.mjs under Node.
(function () {
  "use strict";

  // A fetch target may be a string, a Request, or a URL. Reduce any of them to a
  // plain string we can test with startsWith, without ever throwing (a URL has
  // no .url, a bare object has neither — String(u) is the last resort).
  function targetOf(u) {
    if (typeof u === "string") return u;
    if (u && typeof u.url === "string") return u.url;
    if (u && typeof u.href === "string") return u.href;
    return String(u);
  }

  function methodOf(u, init) {
    return String((init && init.method) || (u && u.method) || "GET").toUpperCase();
  }

  // Build the patched fetch. `real` is the untouched window.fetch; `base` is the
  // site root (e.g. "/wattnot/") the recorded JSON lives under.
  function makeFetch(real, base) {
    const key = (o) => JSON.stringify(Object.keys(o).sort().reduce((a, k) => ((a[k] = o[k]), a), {}));
    let index = null;
    return async (u, init = {}) => {
      const target = targetOf(u);
      const method = methodOf(u, init);
      if (target.startsWith("/api/")) {
        if (method === "POST" && target === "/api/dash/v2/run") {
          const body = JSON.parse(init.body || "{}"); delete body.compare; delete body.framework;
          index ??= await real(`${base}api/dash/v2/run/index.json`).then((r) => r.json()).catch(() => ({}));
          const f = index[key(body)];
          if (f) return real(`${base}api/dash/v2/run/${f}`);
          return new Response(JSON.stringify({ detail: "This combination of settings was not recorded for the static replay. Change one setting at a time from the default, or run the dashboard locally." }), { status: 404, headers: { "content-type": "application/json" } });
        }
        if (method === "POST" && target === "/api/voiceguide") {
          // There is no recorded voice answer on the static site. Fail (not a 200)
          // so voiceguide.js falls back to grounded answers from the signed receipt.
          return new Response(JSON.stringify({ detail: "No static replay for /api/voiceguide; answer from the signed receipt instead." }), { status: 404, headers: { "content-type": "application/json" } });
        }
        return real(`${base}${target.slice(1).split("?")[0]}.json`);
      }
      if (target.startsWith("/room/")) return real(`${base}${target.slice(1)}`);
      // URL/Request objects for receipt files and the live transcript fall here;
      // pass the original through untouched so relative-to-absolute URLs still work.
      return real(u, init);
    };
  }

  if (typeof window !== "undefined" && typeof document !== "undefined") {
    window.__WN_STATIC = true;
    const base = new URL(".", document.currentScript.src).pathname.replace(/dash\/$/, "");
    window.fetch = makeFetch(window.fetch.bind(window), base);
  }

  if (typeof module !== "undefined" && module.exports) {
    module.exports = { targetOf, methodOf, makeFetch };
  }
})();
